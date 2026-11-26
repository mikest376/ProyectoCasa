#!/usr/bin/env python3
"""Compara los contratos de Silver con el esquema real de Odoo (TD-011).

    python3 scripts/check_contracts.py

Con el perfil source en marcha y la imagen del worker construida. Hay que repetirlo tras instalar o
actualizar módulos de Odoo: avisa antes de que la transformación se detenga. Solo lee. Biblioteca
estándar.
"""

from __future__ import annotations

import json
import sys

import cdc

QUERY = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
"""


def compare(contracts: dict, actual: dict[str, dict[str, tuple[str, bool]]]) -> list[str]:
    """Diferencias que harían fallar la transformación."""
    problems = []
    for table, columns in contracts.items():
        if table not in actual:
            problems.append(f"{table}: no existe")
            continue
        for column, rule in columns.items():
            name = f"{table}.{column}"
            if column not in actual[table]:
                problems.append(f"{name}: no existe")
                continue
            data_type, nullable = actual[table][column]
            if data_type not in rule["types"]:
                problems.append(f"{name}: es {data_type}, se esperaba {' o '.join(rule['types'])}")
            if rule["required"] and nullable:
                problems.append(f"{name}: admite nulos y el contrato no")
    return problems


def main() -> int:
    try:
        contracts = json.loads(
            cdc.compose(
                "--profile", "apps", "run", "--rm", "-T", "--no-deps",
                "medallion-worker", "python", "-m", "medallion_worker", "contracts",
            )
        )
        actual: dict[str, dict[str, tuple[str, bool]]] = {}
        for row in cdc.psql(QUERY):
            table, column, data_type, nullable = row.split("|")
            actual.setdefault(table, {})[column] = (data_type, nullable == "YES")
    except cdc.CdcError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    problems = compare(contracts, actual)
    columns = sum(len(table) for table in contracts.values())
    print(f"Contratos: {len(contracts)} tablas y {columns} columnas. Diferencias: {len(problems)}.")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
