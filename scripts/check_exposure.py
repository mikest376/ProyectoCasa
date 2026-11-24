#!/usr/bin/env python3
"""Comprobación previa a exponer el entorno (defensa del TFG, TD-009 y TD-022).

    python3 scripts/check_exposure.py

Con los perfiles source, apps y caddy en marcha, comprueba que fuera de 127.0.0.1 solo
escucha Caddy, que exige contraseña, y que Odoo no conserva accesos por defecto. Solo
lee: no cambia nada. Termina con error si no es seguro exponer. Biblioteca estándar.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "::1"}
BLOCKED = ("/web/database/manager", "/web/database/selector", "/xmlrpc/2/common", "/jsonrpc")

failures = 0


def report(good: bool, text: str) -> None:
    global failures
    print(f"  {'OK   ' if good else 'FALLO'}  {text}")
    failures += not good


def compose(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def published_ports() -> list[tuple[str, str, int, int]]:
    ports = []
    for line in compose("--profile", "*", "ps", "--format", "json").splitlines():
        service = json.loads(line)
        for port in service.get("Publishers") or []:
            if port.get("PublishedPort"):
                ports.append(
                    (service["Service"], port.get("URL", ""), port["PublishedPort"], port["TargetPort"])
                )
    return ports


def check_ports(ports: list[tuple[str, str, int, int]]) -> None:
    print("Puertos publicados")
    external = [port for port in ports if port[1] not in LOOPBACK]
    intruders = [f"{name} {url}:{published}" for name, url, published, _ in external if name != "caddy"]
    report(not intruders, "fuera de 127.0.0.1 solo puede escuchar Caddy" + (f": {intruders}" if intruders else ""))
    for name, url, published, target in external:
        if name == "caddy":
            report(target in (80, 443), f"Caddy escucha en {url}:{published}")


def https_status(host: str, address: str, port: int, path: str, auth: str | None = None) -> int:
    """Estado HTTP a través de Caddy, sin seguir redirecciones ni resolver el nombre por DNS.

    El certificado no se valida porque puede ser propio (CADDY_TLS=internal).
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(host, port, timeout=10)
    connection.sock = context.wrap_socket(
        socket.create_connection((address, port), timeout=10), server_hostname=host
    )
    try:
        headers = {"Host": host}
        if auth:
            headers["Authorization"] = f"Basic {auth}"
        connection.request("GET", path, headers=headers)
        return connection.getresponse().status
    finally:
        connection.close()


def check_caddy(ports: list[tuple[str, str, int, int]]) -> None:
    print("Caddy")
    https = next(((url, pub) for name, url, pub, target in ports if name == "caddy" and target == 443), None)
    if https is None:
        report(False, "Caddy no está en marcha (docker compose --profile caddy up -d)")
        return
    address = "127.0.0.1" if https[0] in ("0.0.0.0", "") else https[0]
    environment = dict(
        line.partition("=")[::2] for line in compose("exec", "-T", "caddy", "env").splitlines()
    )
    hosts = {"frontend": environment["CADDY_APP_HOST"], "odoo": environment["CADDY_ODOO_HOST"]}
    for name, host in hosts.items():
        for path in ("/", "/api/health") if name == "frontend" else ("/", "/web/login"):
            report(https_status(host, address, https[1], path) == 401, f"{host}{path} exige contraseña")
    wrong = base64.b64encode(b"admin:admin").decode()
    report(https_status(hosts["odoo"], address, https[1], "/", wrong) == 401, "rechaza admin/admin")
    report(environment.get("CADDY_TLS") != "internal", "certificado público (CADDY_TLS distinto de internal)")

    password = os.environ.get("CADDY_CHECK_PASSWORD")
    if password:
        user = environment["CADDY_BASIC_AUTH_USER"]
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        for path in BLOCKED:
            report(
                https_status(hosts["odoo"], address, https[1], path, auth) == 404,
                f"{path} bloqueada incluso con contraseña",
            )
    else:
        print("  --     rutas bloqueadas: define CADDY_CHECK_PASSWORD para comprobarlas")


ODOO_PROBE = r"""
import json, urllib.request
def call(path, params):
    request = urllib.request.Request(
        "http://localhost:8069" + path,
        data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(request, timeout=20))
import os
db = os.environ["ODOO_DB_NAME"]
out = {}
for login in ("admin", "demo", "portal"):
    answer = call("/web/session/authenticate", {"db": db, "login": login, "password": login})
    out[login] = bool((answer.get("result") or {}).get("uid"))
out["list_db"] = "error" not in call("/jsonrpc", {"service": "db", "method": "list", "args": []})
print(json.dumps(out))
"""


def check_odoo() -> None:
    print("Odoo")
    result = json.loads(compose("exec", "-T", "odoo", "python3", "-", stdin=ODOO_PROBE))
    for login in ("admin", "demo", "portal"):
        report(not result[login], f"{login}/{login} no permite entrar")
    report(not result["list_db"], "el gestor de bases de datos está desactivado")


def main() -> int:
    try:
        ports = published_ports()
        check_ports(ports)
        check_caddy(ports)
        check_odoo()
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        report(False, f"no se pudo completar: {error}")
    print(f"\nResultado: {'NO EXPONER' if failures else 'listo para exponer'} ({failures} fallos)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
