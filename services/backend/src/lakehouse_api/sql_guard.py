"""Validación del SQL propuesto por el modelo (CLAUDE.md, regla 9).

El SQL es una entrada no confiable. Se analiza como AST con el dialecto de Trino y solo se acepta
lo que está permitido: una sentencia SELECT (o WITH ... SELECT) sobre las tablas de Gold. No se usan
expresiones regulares. Lo que se ejecuta es lo que se genera a partir del árbol validado, nunca el
texto recibido.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "trino"
MAX_LENGTH = 5000
MAX_NODES = 1500

# Todo lo que no sea leer. El árbol de un SELECT no debería contenerlos: se comprueba igualmente.
FORBIDDEN: tuple[type, ...] = (
    exp.DDL,
    exp.DML,
    exp.Alter,
    exp.Command,
    exp.Drop,
    exp.Into,
    exp.Lock,
    exp.Merge,
    exp.Parameter,
    exp.Placeholder,
    exp.SessionParameter,
    exp.Set,
    exp.TruncateTable,
    exp.Use,
)
# Relaciones que no son tablas de Gold: funciones de tabla, listas de valores, UNNEST.
FORBIDDEN_RELATIONS: tuple[type, ...] = (
    exp.Lateral,
    exp.TableFromRows,
    exp.TableSample,
    exp.Unnest,
    exp.Values,
)


class SqlRejectedError(ValueError):
    """El SQL no está permitido. El mensaje se puede enseñar al usuario y al modelo."""


@dataclass(frozen=True, slots=True)
class SafeSql:
    sql: str
    tables: tuple[str, ...]


class SqlGuard:
    def __init__(
        self,
        tables: Mapping[str, Collection[str]],
        max_rows: int,
        catalog: str = "lakehouse",
        schema: str = "gold",
    ) -> None:
        """tables: columnas de cada tabla permitida."""
        self._tables = {
            table.lower(): {column.lower() for column in columns}
            for table, columns in tables.items()
        }
        self._max_rows = max_rows
        self._catalog = catalog
        self._schema = schema

    def check(self, sql: str) -> SafeSql:
        tree = self._parse(sql)
        self._check_nodes(tree)
        tables = self._qualify(tree)
        if not tables:
            raise SqlRejectedError("la consulta no usa ninguna tabla de gold")
        self._expand_group_by_aliases(tree, tables)
        limited = self._limit(tree)
        return SafeSql(limited.sql(dialect=DIALECT, comments=False), tuple(sorted(tables)))

    def _parse(self, sql: str) -> exp.Select:
        text = sql.strip().rstrip(";").strip()
        if not text:
            raise SqlRejectedError("la consulta está vacía")
        if len(text) > MAX_LENGTH:
            raise SqlRejectedError("la consulta es demasiado larga")
        try:
            statements = [item for item in sqlglot.parse(text, read=DIALECT) if item is not None]
        except (SqlglotError, RecursionError) as error:
            raise SqlRejectedError("el SQL no es válido") from error
        if len(statements) != 1:
            raise SqlRejectedError("solo se admite una sentencia")
        statement = statements[0]
        if not isinstance(statement, exp.Select):
            raise SqlRejectedError("solo se admite SELECT o WITH ... SELECT")
        return statement

    def _check_nodes(self, tree: exp.Select) -> None:
        for number, node in enumerate(tree.walk()):
            if number > MAX_NODES:
                raise SqlRejectedError("la consulta es demasiado compleja")
            if isinstance(node, FORBIDDEN):
                raise SqlRejectedError(f"operación no permitida: {node.key}")
            if isinstance(node, FORBIDDEN_RELATIONS):
                raise SqlRejectedError("no se admiten funciones de tabla, UNNEST ni VALUES")
            if isinstance(node, exp.CTE) and not isinstance(
                node.this, (exp.Select, exp.SetOperation)
            ):
                raise SqlRejectedError("las subconsultas de WITH deben ser SELECT")

    def _qualify(self, tree: exp.Select) -> set[str]:
        """Comprueba cada tabla contra la lista y la deja como catálogo.esquema.tabla."""
        ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
        used: set[str] = set()
        for table in tree.find_all(exp.Table):
            # Una función en lugar de un nombre es una función de tabla.
            if not isinstance(table.this, exp.Identifier):
                raise SqlRejectedError("no se admiten funciones de tabla")
            if any(table.args.get(name) for name in ("version", "when", "partition", "changes")):
                raise SqlRejectedError("no se admiten versiones anteriores de una tabla")
            name, schema, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
            if not schema and not catalog and name in ctes:
                continue
            if catalog not in ("", self._catalog) or schema not in ("", self._schema):
                raise SqlRejectedError(f"solo se puede consultar el esquema {self._schema}")
            if name not in self._tables:
                raise SqlRejectedError(f"la tabla {name} no existe en {self._schema}")
            used.add(name)
            table.set("this", exp.to_identifier(name))
            table.set("db", exp.to_identifier(self._schema))
            table.set("catalog", exp.to_identifier(self._catalog))
        return used

    def _expand_group_by_aliases(self, tree: exp.Select, tables: set[str]) -> None:
        """Trino no admite alias en GROUP BY: se sustituyen por su expresión.

        Es un error frecuente del modelo y no cambia el significado. Un alias que coincide con una
        columna real se deja como está.
        """
        real = set().union(*(self._tables[name] for name in tables))
        for select in tree.find_all(exp.Select):
            group = select.args.get("group")
            if group is None:
                continue
            aliases = {
                item.alias.lower(): item.this
                for item in select.expressions
                if isinstance(item, exp.Alias) and not item.this.find(exp.AggFunc)
            }
            group.set(
                "expressions",
                [
                    aliases[item.name.lower()].copy()
                    if isinstance(item, exp.Column)
                    and not item.table
                    and item.name.lower() in aliases
                    and item.name.lower() not in real
                    else item
                    for item in group.expressions
                ],
            )

    def _limit(self, tree: exp.Select) -> exp.Select:
        """LIMIT obligatorio: se añade si falta y se reduce si es mayor que el máximo."""
        requested = self._max_rows
        limit = tree.args.get("limit")
        if limit is not None:
            value = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
            if isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit():
                requested = min(int(value.this), self._max_rows)
        tree.set("limit", None)
        return tree.limit(requested, copy=False)
