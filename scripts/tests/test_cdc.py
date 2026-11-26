#!/usr/bin/env python3
"""Prueba de scripts/cdc.py contra un PostgreSQL desechable (TD-004 y TD-005).

    python3 scripts/tests/test_cdc.py

Usa la imagen, la configuración, los roles y el SQL reales, con tablas simuladas en lugar de
Odoo. Sin red, sin puertos y con los datos en memoria: cabe en la integración continua.
Biblioteca estándar.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import cdc  # noqa: E402

IMAGE = "postgres:15.20-bookworm@sha256:0cb925629e7eb35c77276f3165440d1670c35c2ddbe92b346c579299b75a520f"
CONTAINER = f"cdc-test-{os.getpid()}"
ENVIRONMENT = {
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": secrets.token_hex(16),
    "ODOO_DB_NAME": "odoo",
    "ODOO_DB_USER": "odoo",
    "ODOO_DB_PASSWORD": secrets.token_hex(16),
    "DEBEZIUM_DB_USER": "debezium",
    "DEBEZIUM_DB_PASSWORD": secrets.token_hex(16),
    "DEBEZIUM_PUBLICATION_NAME": "debezium_odoo_pub",
}
NO_IDENTITY = "does not have a replica identity"

SCHEMA = """
CREATE TABLE sale_order (id serial PRIMARY KEY, name text);
CREATE TABLE rel_without_key (a int, b int);
CREATE TABLE rel_identity_off (id int PRIMARY KEY, b int);
ALTER TABLE rel_identity_off REPLICA IDENTITY NOTHING;
CREATE TABLE parted (a int, b int) PARTITION BY RANGE (a);
CREATE TABLE parted_1 PARTITION OF parted FOR VALUES FROM (0) TO (100);
CREATE UNLOGGED TABLE cache_unlogged (a int);
CREATE TABLE res_users (id serial PRIMARY KEY, login text, password text, totp_secret text);
CREATE TABLE ir_config_parameter (id serial PRIMARY KEY, key text, value text);
INSERT INTO sale_order (name) VALUES ('S1'), ('S2');
INSERT INTO rel_without_key VALUES (1, 1), (2, 2);
INSERT INTO rel_identity_off VALUES (1, 1), (2, 2);
INSERT INTO parted VALUES (1, 1), (2, 2);
"""


def docker(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **ENVIRONMENT},
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {args[0]}: código {result.returncode}: {result.stderr.strip()}")
    return result


def execute(sql: str, role: str = "postgres") -> subprocess.CompletedProcess[str]:
    """Ejecuta SQL como un rol concreto, para respetar sus permisos."""
    return docker(
        "exec", "-i", CONTAINER, "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "odoo",
        stdin=f"SET ROLE {role};\n{sql}",
        check=False,
    )


def in_container(*args: str, stdin: str | None = None) -> str:
    """Sustituye a docker compose: cdc.py ejecuta sus órdenes en el contenedor de prueba."""
    assert args[:3] == ("exec", "-T", "odoo-db"), args
    result = docker("exec", "-i", CONTAINER, *args[3:], stdin=stdin, check=False)
    if result.returncode != 0:
        raise cdc.CdcError(result.stderr.strip())
    return result.stdout


class CdcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.addClassCleanup(docker, "rm", "-f", "-v", CONTAINER, check=False)
        variables = [part for name in ENVIRONMENT for part in ("-e", name)]
        docker(
            "run", "-d", "--name", CONTAINER, "--network", "none", "--memory", "768m",
            "--tmpfs", "/var/lib/postgresql/data", *variables,
            "-v", f"{ROOT}/infra/postgres/postgresql.conf:/etc/postgresql/postgresql.conf:ro",
            "-v", f"{ROOT}/infra/postgres/init:/docker-entrypoint-initdb.d:ro",
            IMAGE, "postgres", "-c", "config_file=/etc/postgresql/postgresql.conf",
        )
        deadline = time.monotonic() + 90
        # Por TCP: durante la inicialización, PostgreSQL solo escucha en el socket local.
        while docker("exec", CONTAINER, "pg_isready", "-h", "127.0.0.1", check=False).returncode:
            if time.monotonic() > deadline:
                raise TimeoutError(docker("logs", "--tail", "20", CONTAINER, check=False).stderr)
            time.sleep(0.5)
        cdc.compose = in_container

    def setUp(self) -> None:
        self.sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION odoo;", "postgres")
        self.sql("DROP PUBLICATION IF EXISTS debezium_odoo_pub;", "postgres")
        self.sql(
            "GRANT USAGE ON SCHEMA public TO debezium;"
            "ALTER DEFAULT PRIVILEGES FOR ROLE odoo IN SCHEMA public"
            " GRANT SELECT ON TABLES TO debezium;",
            "postgres",
        )
        self.sql(SCHEMA)

    def sql(self, statement: str, role: str = "odoo") -> list[str]:
        result = execute(statement, role)
        self.assertEqual(result.returncode, 0, f"código {result.returncode}: {result.stderr}")
        return result.stdout.split()

    def assert_blocked(self, statement: str) -> None:
        result = execute(statement, "odoo")
        self.assertNotEqual(result.returncode, 0, f"debía fallar: {statement}")
        self.assertIn(NO_IDENTITY, result.stderr)

    def identity(self, table: str) -> str:
        return self.sql(f"SELECT relreplident FROM pg_class WHERE oid = '{table}'::regclass;")[0]

    def test_a_publication_without_prepare_blocks_writes(self) -> None:
        """Control: sin la preparación, el fallo de TD-005 existe."""
        self.sql("CREATE PUBLICATION p FOR TABLES IN SCHEMA public;", "postgres")
        self.addCleanup(execute, "DROP PUBLICATION p;")
        self.assert_blocked("UPDATE rel_without_key SET b = 9;")
        self.assert_blocked("DELETE FROM rel_identity_off;")
        self.sql("UPDATE sale_order SET name = 'x';")

    def test_prepare_lets_odoo_write_in_every_table(self) -> None:
        cdc.prepare()
        for table in ("rel_without_key", "rel_identity_off", "parted", "parted_1"):
            self.assertEqual(self.identity(table), "f", table)
        self.assertEqual(self.identity("sale_order"), "d")
        self.sql(
            "UPDATE sale_order SET name = 'x'; DELETE FROM sale_order;"
            "UPDATE rel_without_key SET b = 9; DELETE FROM rel_without_key;"
            "UPDATE rel_identity_off SET b = 9; DELETE FROM rel_identity_off;"
            "UPDATE parted SET b = 9; DELETE FROM parted;"
        )

    def test_prepare_publishes_the_whole_schema_once(self) -> None:
        cdc.prepare()
        cdc.prepare()
        self.assertEqual(self.sql("SELECT pubname FROM pg_publication;"), ["debezium_odoo_pub"])
        published = self.sql("SELECT tablename FROM pg_publication_tables;")
        self.assertIn("rel_without_key", published)
        self.assertNotIn("cache_unlogged", published)

    def test_tables_from_new_modules_need_prepare_again(self) -> None:
        """Por eso init_odoo.sh lo repite tras instalar módulos."""
        cdc.prepare()
        self.sql("CREATE TABLE new_module_rel (a int); INSERT INTO new_module_rel VALUES (1);")
        self.assert_blocked("UPDATE new_module_rel SET a = 2;")
        cdc.prepare()
        self.sql("UPDATE new_module_rel SET a = 2;")

    def test_debezium_reads_but_does_not_write(self) -> None:
        self.assertEqual(self.sql("SELECT count(*) FROM sale_order;", "debezium"), ["2"])
        self.assertNotEqual(execute("INSERT INTO sale_order (name) VALUES ('x');", "debezium").returncode, 0)
        roles = self.sql("SELECT rolname FROM pg_roles WHERE rolsuper;")
        self.assertEqual(roles, ["postgres"])

    def test_check_accepts_a_covered_schema(self) -> None:
        cdc.check()

    def test_check_stops_on_an_uncovered_secret(self) -> None:
        self.sql("CREATE TABLE new_module (id int PRIMARY KEY, oauth_client text);")
        with self.assertRaisesRegex(cdc.CdcError, r"new_module\.oauth_client"):
            cdc.check()

    def test_check_protects_primary_keys(self) -> None:
        self.sql("CREATE TABLE keyed (api_key text PRIMARY KEY);")
        with self.assertRaisesRegex(cdc.CdcError, r"claves primarias: keyed\.api_key"):
            cdc.check()

    def test_check_stops_on_uncovered_personal_data(self) -> None:
        """TD-004: sin las líneas de datos personales, el alta del conector se bloquea."""
        self.sql("CREATE TABLE res_partner (id serial PRIMARY KEY, name text, email text, city text);")
        only_secrets = [p for p in cdc.exclude_patterns() if "login" not in p and "res_partner" not in p]
        with mock.patch.object(cdc, "exclude_patterns", return_value=only_secrets):
            with self.assertRaisesRegex(cdc.CdcError, r"datos personales") as raised:
                cdc.check()
        for column in ("res_partner.name", "res_partner.email", "res_users.login"):
            self.assertIn(column, str(raised.exception))
        self.assertNotIn("res_partner.city", str(raised.exception))

    def test_the_real_list_covers_personal_data_and_nothing_else(self) -> None:
        self.sql("CREATE TABLE res_partner (id serial PRIMARY KEY, name text, email text, city text);")
        cdc.check()
        patterns = [re.compile(pattern) for pattern in cdc.exclude_patterns()]

        def excluded(name: str) -> bool:
            return any(pattern.fullmatch(f"public.{name}") for pattern in patterns)

        for name in ("res_partner.name", "res_partner.email", "crm_lead.contact_name",
                     "res_users.login", "res_partner_bank.acc_number", "mail_mail.email_to"):
            self.assertTrue(excluded(name), name)
        for name in ("res_partner.city", "sale_order.name", "crm_lead.email_state",
                     "res_partner.id", "product_template.name"):
            self.assertFalse(excluded(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
