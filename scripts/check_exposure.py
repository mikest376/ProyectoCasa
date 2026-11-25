#!/usr/bin/env python3
"""Comprobación previa a exponer el entorno (defensa del TFG, TD-009 y TD-022).

    python3 scripts/check_exposure.py

Con los perfiles source, apps y caddy en marcha, comprueba que fuera de 127.0.0.1 solo
escucha Caddy, que sirve certificados públicos válidos y exige contraseña, y que Odoo no
conserva accesos por defecto. Odoo puede tener nombre propio o compartir el del frontend
en otro puerto (CADDY_ODOO_HOST=nombre:8443). Solo lee: no cambia nada. Termina con error
si no es seguro exponer. Biblioteca estándar.

Prueba desde este equipo y sin resolver el nombre por DNS: no sustituye a la prueba desde
fuera de casa.
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
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "::1"}
CADDY_PORTS = {80, 443, 8443}
BLOCKED = ("/web/database/manager", "/web/database/selector", "/xmlrpc/2/common", "/jsonrpc")

failures = 0


class Port(NamedTuple):
    """Puerto publicado en el host por un servicio."""

    service: str
    address: str
    published: int
    target: int


class Site(NamedTuple):
    """Sitio del Caddyfile y dirección del host por la que se llega a él."""

    host: str
    address: str
    published: int

    @property
    def authority(self) -> str:
        """Valor de la cabecera Host: el puerto solo aparece si no es el 443."""
        return self.host if self.published == 443 else f"{self.host}:{self.published}"


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


def published_ports() -> list[Port]:
    ports = []
    for line in compose("--profile", "*", "ps", "--format", "json").splitlines():
        service = json.loads(line)
        for port in service.get("Publishers") or []:
            if port.get("PublishedPort"):
                ports.append(
                    Port(service["Service"], port.get("URL", ""), port["PublishedPort"], port["TargetPort"])
                )
    return ports


def check_ports(ports: list[Port]) -> None:
    print("Puertos publicados")
    external = [port for port in ports if port.address not in LOOPBACK]
    intruders = [f"{port.service} {port.address}:{port.published}" for port in external if port.service != "caddy"]
    report(not intruders, "fuera de 127.0.0.1 solo puede escuchar Caddy" + (f": {intruders}" if intruders else ""))
    for port in external:
        if port.service == "caddy":
            report(port.target in CADDY_PORTS, f"Caddy escucha en {port.address}:{port.published}")


def tls_connection(site: Site, verify: bool) -> ssl.SSLSocket:
    """Conexión TLS con el sitio. Con verify, exige cadena pública y nombre correctos."""
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((site.address, site.published), timeout=10)
    try:
        # El SNI es solo el nombre: con el puerto, Caddy no encuentra certificado y
        # corta con TLSV1_ALERT_INTERNAL_ERROR.
        return context.wrap_socket(raw, server_hostname=site.host)
    except OSError:
        raw.close()
        raise


def https_get(site: Site, path: str, auth: str | None = None) -> tuple[int, list[str]]:
    """Estado y cookies a través de Caddy, sin seguir redirecciones. El certificado se valida aparte."""
    connection = http.client.HTTPSConnection(site.host, site.published, timeout=10)
    connection.sock = tls_connection(site, verify=False)
    try:
        headers = {"Host": site.authority}
        if auth:
            headers["Authorization"] = f"Basic {auth}"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.headers.get_all("Set-Cookie") or []
    finally:
        connection.close()


def expect(site: Site, path: str, status: int, text: str, auth: str | None = None) -> None:
    """Un fallo de conexión cuenta como fallo de esta comprobación, sin cortar las demás."""
    try:
        received, _ = https_get(site, path, auth)
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
        return
    report(received == status, text if received == status else f"{text} (respondió {received})")


def check_cookies(site: Site, auth: str) -> None:
    """Sin Secure, el navegador envía la sesión en claro al abrir http://nombre."""
    text = f"{site.authority}: cookies de Odoo con Secure y sesión con HttpOnly"
    try:
        _, cookies = https_get(site, "/web/login", auth)
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
        return
    attributes = {
        cookie.partition("=")[0]: {part.strip().lower() for part in cookie.split(";")[1:]} for cookie in cookies
    }
    insecure = [name for name, found in attributes.items() if "secure" not in found]
    good = not insecure and "httponly" in attributes.get("session_id", ())
    report(good, text if good else f"{text} (recibidas: {sorted(attributes)}, sin Secure: {insecure})")


def check_certificate(site: Site, tls_mode: str) -> None:
    if tls_mode == "internal":
        report(False, f"{site.authority}: certificado público (CADDY_TLS=internal usa una CA propia)")
        return
    try:
        with tls_connection(site, verify=True) as connection:
            certificate = connection.getpeercert() or {}
    except OSError as error:
        report(False, f"{site.authority}: certificado público válido ({error})")
        return
    issuer = dict(pair[0] for pair in certificate.get("issuer", ())).get("organizationName", "?")
    report(True, f"{site.authority}: certificado válido de {issuer}, hasta {certificate.get('notAfter', '?')}")


def check_redirect(port: Port, host: str) -> None:
    """El puerto 80 no sirve contenido: solo redirige a HTTPS."""
    text = f"el puerto {port.published} solo redirige a HTTPS"
    connection = http.client.HTTPConnection(port.address, port.published, timeout=10)
    try:
        connection.request("GET", "/web/login", headers={"Host": host})
        response = connection.getresponse()
        location = response.getheader("Location", "")
        report(
            response.status in (301, 308) and location.startswith((f"https://{host}/", f"https://{host}:")),
            f"{text} ({response.status} a {location or 'ningún sitio'})",
        )
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
    finally:
        connection.close()


def caddy_site(variable: str, value: str, published: dict[int, Port]) -> Site | None:
    """Interpreta "nombre" o "nombre:puerto", como en el Caddyfile."""
    # Caddy normaliza los nombres a minúsculas; el SNI y las redirecciones también.
    host, _, number = value.lower().partition(":")
    target = int(number or 443)
    port = published.get(target)
    if port is None:
        report(False, f"{variable}={value}: Caddy no publica el puerto {target}")
        return None
    return Site(host, port.address, port.published)


def check_caddy(ports: list[Port]) -> None:
    print("Caddy")
    published = {
        port.target: port._replace(address="127.0.0.1" if port.address in ("0.0.0.0", "") else port.address)
        for port in ports
        if port.service == "caddy"
    }
    if not published:
        report(False, "Caddy no está en marcha (docker compose --profile caddy up -d)")
        return
    environment = dict(
        line.partition("=")[::2] for line in compose("exec", "-T", "caddy", "env").splitlines()
    )
    app = caddy_site("CADDY_APP_HOST", environment["CADDY_APP_HOST"], published)
    odoo = caddy_site("CADDY_ODOO_HOST", environment["CADDY_ODOO_HOST"], published)
    if 80 in published and app:
        check_redirect(published[80], app.host)

    wrong = base64.b64encode(b"admin:admin").decode()
    for site, paths in ((app, ("/", "/api/health")), (odoo, ("/", "/web/login", "/websocket"))):
        if site is None:
            continue
        check_certificate(site, environment.get("CADDY_TLS", "internal"))
        for path in paths:
            expect(site, path, 401, f"{site.authority}{path} exige contraseña")
        expect(site, "/", 401, f"{site.authority} rechaza admin/admin", wrong)

    password = os.environ.get("CADDY_CHECK_PASSWORD")
    if not password:
        print("  --     rutas bloqueadas y cookies: define CADDY_CHECK_PASSWORD para comprobarlas")
    elif odoo:
        user = environment["CADDY_BASIC_AUTH_USER"]
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        check_cookies(odoo, auth)
        for path in BLOCKED:
            expect(odoo, path, 404, f"{odoo.authority}{path} bloqueada incluso con contraseña", auth)


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
