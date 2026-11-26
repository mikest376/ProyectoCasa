#!/usr/bin/env python3
"""Operaciones de CDC: preparar PostgreSQL, comprobar secretos y registrar el conector.

Uso, desde la raíz del repositorio y con los perfiles source y streaming en marcha:

    python3 scripts/cdc.py prepare    REPLICA IDENTITY (TD-005) y publicación
    python3 scripts/cdc.py check      secretos o datos personales sin excluir (TD-004)
    python3 scripts/cdc.py register   prepare + check + alta del conector
    python3 scripts/cdc.py status     estado del conector, del slot y de los topics

Solo biblioteca estándar. No lee .env: los valores salen de los contenedores, de modo
que ningún secreto pasa por argumentos ni por la salida.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_FILE = ROOT / "infra/debezium/column-exclude.list"
CONNECTOR_FILE = ROOT / "infra/debezium/odoo-postgres-cdc.json"
SQL_DIR = ROOT / "infra/postgres/sql"
CONNECT_URL = "http://kafka-connect:8083"

# Nombres que sugieren un secreto. Es más amplio que la lista de exclusión a propósito:
# lo que coincida aquí y no esté excluido obliga a una decisión explícita.
SUSPICIOUS = re.compile(
    r"passw|passphrase|(^|_)pwd($|_)|secret|token|api_?key|private_key|access_key|credential"
    r"|(^|_)key$|_pass$|otp|webhook|hmac|certificate|oauth"
)
TEXT_TYPES = ("character varying", "text", "character", "json", "jsonb", "bytea", "uuid")

# Revisadas una a una: el nombre es sospechoso, pero el contenido no es secreto.
REVIEWED_NOT_SECRET = frozenset(
    {
        "ir_config_parameter.key",  # nombre del parámetro; su valor sí se excluye
        "ir_ui_view.key",  # identificador de la vista
        "res_users_settings.push_to_talk_key",  # tecla del teclado
    }
)

# Datos personales (TD-004): columnas que identifican a una persona en cualquier tabla...
PERSONAL = re.compile(
    r"(^|_)(email|email_normalized|email_from|email_cc|email_to|reply_to|phone|phone_sanitized"
    r"|mobile|street2?|zip|vat|login|contact_name|partner_name|display_name|acc_number"
    r"|acc_holder_name|common_name)$"
)
# ...y las que solo lo son en su tabla: nombres, títulos y notas de personas y empresas.
PERSONAL_COLUMNS = frozenset(
    {
        "res_partner.name",
        "res_partner.complete_name",
        "res_partner.commercial_company_name",
        "res_partner.company_name",
        "res_partner.website",
        "res_partner.comment",
        "res_partner.function",
        "res_users.signature",
        "crm_lead.name",
        "crm_lead.website",
        "crm_lead.description",
        "crm_lead.email_domain_criterion",  # con dominios genéricos, el correo entero
        "mail_message.record_name",
        "mail_message.subject",
        "mail_tracking_value.old_value_char",
        "mail_tracking_value.new_value_char",
        "mail_activity.res_name",
        "mail_guest.name",
        "calendar_event.name",
        "calendar_event.description",
    }
)
# Revisadas una a una: el nombre coincide, pero no identifican a una persona.
REVIEWED_NOT_PERSONAL: frozenset[str] = frozenset()


def is_personal(table: str, column: str) -> bool:
    name = f"{table}.{column}"
    return (PERSONAL.search(column) is not None or name in PERSONAL_COLUMNS) and (
        name not in REVIEWED_NOT_PERSONAL
    )


class CdcError(RuntimeError):
    """Fallo esperado: se muestra sin traza."""


def compose(*args: str, stdin: str | None = None) -> str:
    """Ejecuta docker compose y devuelve la salida estándar."""
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CdcError(f"docker compose {args[0]}: {result.stderr.strip() or result.stdout.strip()}")
    if result.stderr.strip() and "NOTICE" in result.stderr:
        print(result.stderr.strip())
    return result.stdout


def psql(sql: str) -> list[str]:
    """Ejecuta SQL en la base de Odoo como superusuario, por el socket del contenedor."""
    output = compose(
        "exec", "-T", "odoo-db", "bash", "-c",
        'exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$ODOO_DB_NAME"',
        stdin=sql,
    )
    return [line for line in output.splitlines() if line]


def connect_api(method: str, path: str, body: str | None = None) -> tuple[int, str]:
    """Llama a la API de Kafka Connect desde dentro de su contenedor."""
    args = ["exec", "-T", "kafka-connect", "curl", "-sS", "-o", "-", "-w", "\n%{http_code}", "-X", method]
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    text, _, status = compose(*args, f"{CONNECT_URL}{path}", stdin=body).rpartition("\n")
    return int(status), text


def connector_name() -> str:
    return compose("exec", "-T", "kafka-connect", "printenv", "DEBEZIUM_CONNECTOR_NAME").strip()


def exclude_patterns() -> list[str]:
    lines = (line.strip() for line in EXCLUDE_FILE.read_text(encoding="utf-8").splitlines())
    patterns = [line for line in lines if line and not line.startswith("#")]
    for pattern in patterns:
        if "," in pattern:
            raise CdcError(f"La expresión contiene una coma y Debezium la partiría: {pattern}")
        re.compile(pattern)
    return patterns


def prepare() -> None:
    psql((SQL_DIR / "replica_identity.sql").read_text(encoding="utf-8"))
    psql((SQL_DIR / "publication.sql").read_text(encoding="utf-8"))
    pending = psql(
        """
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relpersistence = 'p'
          AND (c.relreplident = 'n' OR (c.relreplident = 'd' AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary)))
        """
    )[0]
    if pending != "0":
        raise CdcError(f"Quedan {pending} tablas sin identidad de réplica")
    total, full = psql(
        """
        SELECT count(*), count(*) FILTER (WHERE c.relreplident = 'f')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relpersistence = 'p'
        """
    )[0].split("|")
    print(f"Identidad de réplica correcta: {total} tablas, {full} con FULL. Publicación lista.")


def check() -> None:
    patterns = [re.compile(pattern) for pattern in exclude_patterns()]
    rows = [
        row.split("|")
        for row in psql(
            """
            SELECT c.table_name, c.column_name, c.data_type,
                   EXISTS (
                       SELECT 1 FROM pg_index i
                       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                       WHERE i.indisprimary AND a.attname = c.column_name
                         AND i.indrelid = format('%I.%I', c.table_schema, c.table_name)::regclass
                   )
            FROM information_schema.columns c
            JOIN information_schema.tables t USING (table_schema, table_name)
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY 1, 2
            """
        )
    ]
    if not rows:
        raise CdcError("La base no tiene tablas: inicializa Odoo antes (scripts/init_odoo.sh)")

    excluded: list[str] = []
    exposed: list[str] = []
    personal: list[str] = []
    broken_keys: list[str] = []
    for table, column, data_type, is_key in rows:
        name = f"{table}.{column}"
        if any(pattern.fullmatch(f"public.{name}") for pattern in patterns):
            excluded.append(name)
            if is_key == "t":
                broken_keys.append(name)
        elif data_type not in TEXT_TYPES:
            continue
        elif SUSPICIOUS.search(column) and name not in REVIEWED_NOT_SECRET:
            exposed.append(f"{name} ({data_type})")
        elif is_personal(table, column):
            personal.append(f"{name} ({data_type})")

    print(f"Columnas revisadas: {len(rows)}. Excluidas de la captura: {len(excluded)}.")
    if broken_keys:
        raise CdcError("La lista excluye claves primarias: " + ", ".join(broken_keys))
    target = EXCLUDE_FILE.relative_to(ROOT)
    if exposed:
        raise CdcError(
            f"Columnas sospechosas que se capturarían (TD-004). Añádelas a {target} o a "
            "REVIEWED_NOT_SECRET:\n  " + "\n  ".join(exposed)
        )
    if personal:
        raise CdcError(
            f"Columnas con datos personales que se capturarían (TD-004). Añádelas a {target} o "
            "a REVIEWED_NOT_PERSONAL:\n  " + "\n  ".join(personal)
        )
    print("Ningún secreto ni dato personal queda sin excluir.")


def wait_running(name: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    state = "sin respuesta"
    while time.monotonic() < deadline:
        status, body = connect_api("GET", f"/connectors/{name}/status")
        if status == 200:
            data = json.loads(body)
            tasks = data.get("tasks", [])
            state = ", ".join([data["connector"]["state"], *(task["state"] for task in tasks)])
            failed = [task for task in tasks if task["state"] == "FAILED"]
            if failed:
                raise CdcError("La tarea ha fallado: " + failed[0].get("trace", "").splitlines()[0])
            if tasks and state == "RUNNING, RUNNING":
                return
        time.sleep(2)
    raise CdcError(f"El conector no está en RUNNING tras {timeout:.0f} s ({state})")


def register() -> None:
    prepare()
    check()
    config = json.loads(CONNECTOR_FILE.read_text(encoding="utf-8"))
    config["column.exclude.list"] = ",".join(exclude_patterns())
    name = connector_name()
    status, body = connect_api("PUT", f"/connectors/{name}/config", json.dumps(config))
    if status not in (200, 201):
        raise CdcError(f"Connect rechazó la configuración ({status}): {body[:800]}")
    print(f"Conector {name}: {'creado' if status == 201 else 'actualizado'}.")
    wait_running(name)
    print("Estado: RUNNING.")


def status() -> None:
    name = connector_name()
    code, body = connect_api("GET", f"/connectors/{name}/status")
    if code == 200:
        data = json.loads(body)
        states = [data["connector"]["state"], *(task["state"] for task in data["tasks"])]
        print(f"Conector {name}: {', '.join(states)}")
    else:
        print(f"Conector {name}: no registrado ({code})")
    for row in psql(
        """
        SELECT slot_name, active, wal_status,
               pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn))
        FROM pg_replication_slots
        """
    ):
        slot, active, wal, lag = row.split("|")
        print(f"Slot {slot}: activo={active}, wal={wal}, retraso={lag}")
    topics = compose(
        "exec", "-T", "-e", "KAFKA_HEAP_OPTS=-Xmx128m", "kafka",
        "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "kafka:19092", "--list",
    ).split()
    print(f"Topics: {len(topics)} ({sum('.public.' in topic for topic in topics)} de tablas)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Operaciones de CDC (Debezium)")
    parser.add_argument("command", choices=["prepare", "check", "register", "status"])
    command = parser.parse_args().command
    try:
        {"prepare": prepare, "check": check, "register": register, "status": status}[command]()
    except CdcError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
