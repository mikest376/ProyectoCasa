"""El SQL del modelo no es de confianza: qué se acepta, qué se rechaza y qué se ejecuta."""

import pytest
import sqlglot

from lakehouse_api.sql_guard import MAX_LENGTH, SqlGuard, SqlRejectedError

TABLES = {
    "fact_sales_orders": ["order_date", "customer_name", "amount_total"],
    "dim_customer": ["customer_name", "city"],
    "agg_stock_by_product": ["product_name"],
}


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard(TABLES, max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM gold.dim_customer",
        "select count(*) from dim_customer",
        "SELECT * FROM lakehouse.gold.dim_customer;",
        "SELECT city, count(*) AS n FROM gold.dim_customer GROUP BY city HAVING count(*) > 1",
        "WITH t AS (SELECT city FROM gold.dim_customer) SELECT city, count(*) FROM t GROUP BY 1",
        "SELECT a.customer_name FROM gold.dim_customer a "
        "JOIN gold.fact_sales_orders b ON a.customer_id = b.customer_id",
        "SELECT * FROM (SELECT city FROM gold.dim_customer) x",
        "SELECT customer_name FROM gold.dim_customer WHERE customer_id IN "
        "(SELECT customer_id FROM gold.fact_sales_orders)",
        "SELECT date_trunc('month', order_date) AS m, sum(amount_total) FROM "
        "gold.fact_sales_orders WHERE order_date >= date_add('month', -3, current_date) GROUP BY 1",
        "SELECT sum(amount_total) OVER (PARTITION BY customer_id) FROM gold.fact_sales_orders",
        "SELECT CASE WHEN is_confirmed THEN 1 ELSE 0 END FROM gold.fact_sales_orders",
        "SELECT x.n FROM (SELECT 1 AS n FROM gold.dim_customer UNION ALL "
        "SELECT 2 FROM gold.dim_customer) x",
        'SELECT "customer_name" FROM "gold"."dim_customer"',
        "SELECT city FROM GOLD.DIM_CUSTOMER -- comentario",
        # Equivale a SELECT * FROM: se ejecuta la forma canónica.
        "TABLE gold.dim_customer",
    ],
)
def test_read_queries_on_gold_are_accepted(guard: SqlGuard, sql: str) -> None:
    safe = guard.check(sql)
    tree = sqlglot.parse_one(safe.sql, read="trino")
    tables = {t.sql("trino") for t in tree.find_all(sqlglot.exp.Table) if t.db}
    assert tables
    assert all(table.startswith(("lakehouse.gold.", '"lakehouse"."gold".')) for table in tables)
    assert "--" not in safe.sql


@pytest.mark.parametrize(
    "sql",
    [
        # Escritura, DDL y órdenes
        "DROP TABLE gold.dim_customer",
        "DELETE FROM gold.dim_customer",
        "INSERT INTO gold.dim_customer SELECT * FROM gold.dim_customer",
        "UPDATE gold.dim_customer SET city = 'x'",
        "MERGE INTO gold.dim_customer USING gold.fact_sales_orders ON true "
        "WHEN MATCHED THEN DELETE",
        "CREATE TABLE gold.x AS SELECT * FROM gold.dim_customer",
        "CREATE VIEW gold.v AS SELECT * FROM gold.dim_customer",
        "ALTER TABLE gold.dim_customer ADD COLUMN x int",
        "TRUNCATE TABLE gold.dim_customer",
        "GRANT SELECT ON gold.dim_customer TO public",
        "CALL lakehouse.system.unregister_table('gold', 'dim_customer')",
        "SET SESSION query_max_run_time = '9h'",
        "EXECUTE consulta",
        "PREPARE consulta FROM SELECT * FROM gold.dim_customer",
        "DESCRIBE gold.dim_customer",
        "SHOW TABLES FROM gold",
        "EXPLAIN SELECT * FROM gold.dim_customer",
        "USE lakehouse.silver",
        "COMMENT ON TABLE gold.dim_customer IS 'x'",
        # Varias sentencias
        "SELECT * FROM gold.dim_customer; DROP TABLE gold.dim_customer",
        "SELECT * FROM gold.dim_customer; SELECT 1",
        # Otros esquemas y catálogos
        "SELECT * FROM silver.res_users",
        "SELECT * FROM lakehouse.silver.res_users",
        "SELECT * FROM bronze.odoo",
        "SELECT * FROM system.runtime.queries",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM lakehouse.information_schema.columns",
        "SELECT * FROM system.metadata.catalogs",
        "SELECT * FROM tpch.tiny.customer",
        "SELECT * FROM gold.no_existe",
        'SELECT * FROM "gold"."dim_customer$history"',
        "SELECT * FROM gold.dim_customer WHERE EXISTS (SELECT 1 FROM silver.res_users)",
        "WITH t AS (SELECT * FROM silver.res_users) SELECT * FROM t, gold.dim_customer",
        "SELECT (SELECT count(*) FROM system.runtime.nodes) FROM gold.dim_customer",
        "SELECT * FROM gold.dim_customer JOIN silver.res_users ON true",
        # Funciones de tabla y relaciones que no son tablas
        "SELECT * FROM TABLE(lakehouse.system.table_changes('gold', 'dim_customer', 0))",
        "SELECT * FROM TABLE(sequence(1, 1000000000))",
        "SELECT * FROM gold.dim_customer, TABLE(sequence(1, 10))",
        "SELECT * FROM UNNEST(sequence(1, 100000)) AS t(n), gold.dim_customer",
        "SELECT * FROM gold.dim_customer CROSS JOIN UNNEST(ARRAY[1, 2]) AS t(n)",
        "SELECT * FROM (VALUES 1, 2) AS t(n), gold.dim_customer",
        # Versiones anteriores
        "SELECT * FROM gold.dim_customer FOR VERSION AS OF 1",
        "SELECT * FROM gold.dim_customer FOR TIMESTAMP AS OF TIMESTAMP '2026-01-01 00:00:00'",
        # Sin tablas, vacío o roto
        "SELECT 1",
        "",
        ";",
        "esto no es sql",
        "SELECT FROM WHERE",
        "SELECT * FROM gold.dim_customer WHERE city = ?",
        "VALUES 1, 2",
        "SELECT * FROM gold.dim_customer UNION SELECT * FROM gold.dim_customer",
        f"SELECT '{'x' * MAX_LENGTH}' FROM gold.dim_customer",
        "SELECT " + " + ".join(["1"] * 900) + " FROM gold.dim_customer",
        "SELECT " + "(" * 400 + "1" + ")" * 400 + " FROM gold.dim_customer",
    ],
)
def test_everything_else_is_rejected(guard: SqlGuard, sql: str) -> None:
    with pytest.raises(SqlRejectedError):
        guard.check(sql)


@pytest.mark.parametrize(
    ("sql", "limit"),
    [
        ("SELECT * FROM gold.dim_customer", 100),
        ("SELECT * FROM gold.dim_customer LIMIT 5", 5),
        ("SELECT * FROM gold.dim_customer LIMIT 5000", 100),
        ("SELECT * FROM gold.dim_customer LIMIT ALL", 100),
        ("SELECT * FROM gold.dim_customer FETCH FIRST 7 ROWS ONLY", 7),
        ("SELECT * FROM gold.dim_customer FETCH FIRST 7000 ROWS ONLY", 100),
        ("SELECT * FROM (SELECT * FROM gold.dim_customer LIMIT 3) t", 100),
        ("WITH t AS (SELECT * FROM gold.dim_customer LIMIT 3) SELECT * FROM t", 100),
    ],
)
def test_the_limit_is_always_imposed(guard: SqlGuard, sql: str, limit: int) -> None:
    tree = sqlglot.parse_one(guard.check(sql).sql, read="trino")
    assert tree.args["limit"].expression.this == str(limit)


def test_a_cte_cannot_hide_a_table_from_another_schema(guard: SqlGuard) -> None:
    safe = guard.check(
        "WITH dim_customer AS (SELECT city FROM gold.fact_sales_orders) SELECT * FROM dim_customer"
    )
    assert safe.tables == ("fact_sales_orders",)
    with pytest.raises(SqlRejectedError, match="solo se puede consultar"):
        guard.check(
            "WITH res_users AS (SELECT 1 AS a FROM gold.dim_customer) "
            "SELECT * FROM silver.res_users"
        )


def test_aliases_in_group_by_are_expanded(guard: SqlGuard) -> None:
    safe = guard.check(
        "SELECT date_trunc('month', order_date) AS m, sum(amount_total) AS total "
        "FROM gold.fact_sales_orders GROUP BY m ORDER BY total DESC"
    )
    assert "GROUP BY DATE_TRUNC('MONTH', order_date)" in safe.sql
    # Una columna real, un ordinal o un agregado no se tocan.
    assert (
        "GROUP BY city"
        in guard.check(
            "SELECT upper(city) AS city, count(*) FROM gold.dim_customer GROUP BY city"
        ).sql
    )
    assert (
        "GROUP BY 1"
        in guard.check("SELECT city AS c, count(*) AS n FROM gold.dim_customer GROUP BY 1").sql
    )
    assert "GROUP BY n" in guard.check("SELECT count(*) AS n FROM gold.dim_customer GROUP BY n").sql


def test_what_runs_is_generated_from_the_validated_tree(guard: SqlGuard) -> None:
    safe = guard.check("select /* pista */ City from DIM_CUSTOMER -- fin")
    assert safe.sql == "SELECT City FROM lakehouse.gold.dim_customer LIMIT 100"
    assert safe.tables == ("dim_customer",)
