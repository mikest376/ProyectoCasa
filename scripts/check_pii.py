#!/usr/bin/env python3
"""Comprueba que ningún dato personal de Odoo está en claro en MinIO (TD-004).

    python3 scripts/check_pii.py

Saca de PostgreSQL los nombres, correos, teléfonos, direcciones, NIF, usuarios y cuentas
bancarias de Odoo, y los busca en las tres capas del lakehouse: datos, registro de Delta
(_delta_log, con sus estadísticas), checkpoints y ficheros sustituidos que sigan guardados.
Los valores viajan por la entrada estándar del worker y no se muestran: solo dónde aparecen.

Con los perfiles source y apps en marcha. Solo lee. Biblioteca estándar.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import cdc  # noqa: E402

# Datos de personas físicas: los nombres de empresa no son datos personales y aparecen,
# legítimamente, en otras tablas (cuentas analíticas, res_company). Tampoco cuentan los
# usuarios técnicos (public, plantillas, OdooBot), cuyos nombres y logins son de Odoo.
# Seis caracteres como mínimo: los valores cortos aparecen en cualquier sitio.
VALUES_SQL = """
WITH excluded AS (
    SELECT partner_id AS id FROM res_company
    UNION SELECT partner_id FROM res_users WHERE NOT active OR login IN ('public', '__system__')
),
people AS (
    SELECT * FROM res_partner WHERE NOT is_company AND id NOT IN (SELECT id FROM excluded)
)
SELECT DISTINCT value FROM (
    SELECT unnest(ARRAY[name, complete_name, email, email_normalized, phone, mobile, street,
                        street2, vat]) AS value
    FROM people
    UNION ALL
    SELECT unnest(ARRAY[contact_name, email_from, email_cc, phone, mobile, street, street2])
    FROM crm_lead
    UNION ALL SELECT login FROM res_users WHERE login LIKE '%@%'
    UNION ALL SELECT acc_number FROM res_partner_bank
    UNION ALL SELECT email_from FROM mail_message WHERE email_from LIKE '%@%'
    UNION ALL
    SELECT common_name FROM calendar_attendee WHERE partner_id NOT IN (SELECT id FROM excluded)
) AS personal
WHERE length(value) >= 6 AND value NOT IN (SELECT name FROM res_company)
"""


def main() -> int:
    try:
        values = cdc.psql(VALUES_SQL)
    except cdc.CdcError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Valores personales de Odoo buscados: {len(values)} (no se muestran)")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "medallion-transform", "python", "-m", "medallion_worker",
         "scan-personal"],
        cwd=ROOT,
        input=json.dumps({"values": values}),
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if not lines:
        print(f"ERROR: el worker no ha respondido (código {result.returncode})", file=sys.stderr)
        return 1
    report = json.loads(lines[-1])
    print(f"Tablas Delta revisadas en Bronze, Silver y Gold: {report['tables']}")
    for problem in report["problems"]:
        print(f"  FALLO  {problem}")
    failures = len(report["problems"]) + (report["tables"] == 0)
    print(f"\nResultado: {'datos personales en MinIO' if failures else 'sin datos personales en claro'} "
          f"({failures} fallos)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
