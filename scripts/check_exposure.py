#!/usr/bin/env python3
"""Comprobación de la exposición (demo pública del TFG, TD-009 y TD-022).

    python3 scripts/check_exposure.py

Con los perfiles source, apps y caddy en marcha, comprueba que fuera de 127.0.0.1 solo
escucha Caddy, que sirve certificados públicos válidos y exige contraseña, y que Odoo no
conserva accesos por defecto. Odoo puede tener nombre propio o compartir el del frontend
en otro puerto (CADDY_ODOO_HOST=nombre:8443).

Prueba cada sitio por la dirección publicada y, si el nombre resuelve por DNS a otra
dirección pública, también por ella: es el camino público entero, gracias al NAT de
retorno del router. Con CADDY_CHECK_PASSWORD entra además con la contraseña: interfaz,
/api, una pregunta real a Text-to-SQL (deja su traza en Langfuse), acceso y WebSocket de
Odoo, cookies y rutas bloqueadas. No cambia nada más y no imprime secretos. Termina con
error si no es seguro exponer. Biblioteca estándar.

Prueba desde este equipo: no sustituye a la prueba desde fuera de casa.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "::1"}
CADDY_PORTS = {80, 443, 8443}
BLOCKED = (
    "/web/database/manager",
    "/web/database/selector",
    "/xmlrpc/2/common",
    "/jsonrpc",
    "/web/signup",
    "/web/reset_password",
    "/web/tests",
)
QUESTION = "¿Cuántos pedidos confirmados hay?"
QUERY_TIMEOUT = 180

failures = 0


class Port(NamedTuple):
    """Puerto publicado en el host por un servicio."""

    service: str
    address: str
    published: int
    target: int


class Site(NamedTuple):
    """Sitio del Caddyfile y dirección por la que se llega a él."""

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


def tls_connection(site: Site, verify: bool, timeout: float = 10) -> ssl.SSLSocket:
    """Conexión TLS con el sitio. Con verify, exige cadena pública y nombre correctos."""
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((site.address, site.published), timeout=timeout)
    try:
        # El SNI es solo el nombre: con el puerto, Caddy no encuentra certificado y
        # corta con TLSV1_ALERT_INTERNAL_ERROR.
        return context.wrap_socket(raw, server_hostname=site.host)
    except OSError:
        raw.close()
        raise


def https_request(
    site: Site,
    method: str,
    path: str,
    auth: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """Estado, cabeceras y cuerpo a través de Caddy, sin seguir redirecciones.

    El certificado se valida aparte, en check_certificate.
    """
    connection = http.client.HTTPSConnection(site.host, site.published, timeout=timeout)
    connection.sock = tls_connection(site, verify=False, timeout=timeout)
    try:
        headers = {"Host": site.authority}
        if auth:
            headers["Authorization"] = f"Basic {auth}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
            headers["Origin"] = f"https://{site.authority}"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read(500_000)
    finally:
        connection.close()


def expect(site: Site, path: str, status: int, text: str, auth: str | None = None) -> None:
    """Un fallo de conexión cuenta como fallo de esta comprobación, sin cortar las demás."""
    try:
        received, _, _ = https_request(site, "GET", path, auth)
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
        return
    report(received == status, text if received == status else f"{text} (respondió {received})")


def expect_json(
    site: Site,
    method: str,
    path: str,
    text: str,
    auth: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any] | None:
    """Petición con contraseña que debe devolver 200 y un objeto JSON. Devuelve el objeto."""
    try:
        status, _, content = https_request(site, method, path, auth, payload, timeout)
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
        return None
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if status != 200 or not isinstance(data, dict):
        report(False, f"{text} (respondió {status})")
        return None
    return data


def check_cookies(site: Site, auth: str) -> None:
    """Sin Secure, el navegador envía la sesión en claro al abrir http://nombre."""
    text = f"{site.authority}: cookies de Odoo con Secure y sesión con HttpOnly"
    try:
        _, headers, _ = https_request(site, "GET", "/web/login", auth)
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
        return
    cookies = headers.get_all("Set-Cookie") or []
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
            version = connection.version()
    except OSError as error:
        report(False, f"{site.authority}: certificado público válido ({error})")
        return
    issuer = dict(pair[0] for pair in certificate.get("issuer", ())).get("organizationName", "?")
    report(True, f"{site.authority}: certificado válido de {issuer} ({version}), hasta {certificate.get('notAfter', '?')}")


def check_redirect(address: str, port: int, host: str) -> None:
    """El puerto 80 no sirve contenido: solo redirige a HTTPS."""
    text = f"el puerto {port} solo redirige a HTTPS"
    connection = http.client.HTTPConnection(address, port, timeout=10)
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


def check_app_with_password(site: Site, auth: str) -> None:
    """Interfaz, /api y una pregunta real: Caddy, FastAPI, Ollama y Trino."""
    text = f"{site.authority}/ con contraseña sirve la interfaz"
    try:
        status, _, content = https_request(site, "GET", "/", auth)
        report(status == 200 and b"<html" in content.lower(), f"{text} ({status})")
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")

    health = expect_json(site, "GET", "/api/health", f"{site.authority}/api/health", auth)
    if health is not None:
        report(health.get("status") == "ok", f"{site.authority}/api/health responde {health}")

    llm = expect_json(site, "GET", "/api/llm", f"{site.authority}/api/llm", auth)
    if llm is not None:
        report(
            bool(llm.get("model")),
            f"{site.authority}/api/llm: {llm.get('provider')}/{llm.get('model')}, sale del host: {llm.get('external')}",
        )

    answer = expect_json(
        site, "POST", "/api/query", f"{site.authority}/api/query", auth, {"question": QUESTION}, QUERY_TIMEOUT
    )
    if answer is not None:
        timings = answer.get("timings") or {}
        report(
            answer.get("row_count", 0) > 0 and bool(answer.get("sql")),
            f"{site.authority}/api/query responde «{QUESTION}»: {answer.get('row_count')} filas, "
            f"{answer.get('attempts')} intento(s), {timings.get('total_ms')} ms",
        )


def check_websocket(site: Site, auth: str) -> None:
    """Odoo usa un WebSocket para avisos y chat: Caddy debe dejarlo pasar."""
    text = f"{site.authority}/websocket con contraseña"
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET /websocket HTTP/1.1\r\nHost: {site.authority}\r\nAuthorization: Basic {auth}\r\n"
        f"Origin: https://{site.authority}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
        f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n"
    )
    try:
        with tls_connection(site, verify=False) as connection:
            connection.sendall(request.encode())
            status_line = connection.recv(256).split(b"\r\n", 1)[0].decode(errors="replace")
    except OSError as error:
        report(False, f"{text} (sin respuesta: {error})")
        return
    report(" 101 " in f"{status_line} ", f"{text}: {status_line}")


def check_odoo_with_password(site: Site, auth: str) -> None:
    text = f"{site.authority}/web/login con contraseña muestra el acceso de Odoo"
    try:
        status, _, content = https_request(site, "GET", "/web/login", auth)
        report(status == 200 and b'name="login"' in content, f"{text} ({status})")
    except (OSError, http.client.HTTPException) as error:
        report(False, f"{text} (sin respuesta: {error})")
    check_cookies(site, auth)
    check_websocket(site, auth)
    for path in BLOCKED:
        expect(site, path, 404, f"{site.authority}{path} bloqueada incluso con contraseña", auth)


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


def public_address(host: str, published: str) -> str | None:
    """Dirección pública del nombre según el DNS, si no es la publicada ni una privada."""
    try:
        address = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        return None
    parsed = ipaddress.ip_address(address)
    if address == published or parsed.is_private or parsed.is_loopback:
        return None
    return address


def check_path(label: str, app: Site | None, odoo: Site | None, tls_mode: str, auth: str | None) -> None:
    print(label)
    if app:
        check_redirect(app.address, 80, app.host)
    wrong = base64.b64encode(b"admin:admin").decode()
    for site, paths in ((app, ("/", "/api/health")), (odoo, ("/", "/web/login", "/websocket"))):
        if site is None:
            continue
        check_certificate(site, tls_mode)
        for path in paths:
            expect(site, path, 401, f"{site.authority}{path} exige contraseña")
        expect(site, "/", 401, f"{site.authority} rechaza admin/admin", wrong)
    if auth is None:
        print("  --     pruebas con contraseña: define CADDY_CHECK_PASSWORD para hacerlas")
        return
    if app:
        check_app_with_password(app, auth)
    if odoo:
        check_odoo_with_password(odoo, auth)


def check_caddy(ports: list[Port]) -> None:
    published = {
        port.target: port._replace(address="127.0.0.1" if port.address in ("0.0.0.0", "") else port.address)
        for port in ports
        if port.service == "caddy"
    }
    if not published:
        print("Caddy")
        report(False, "Caddy no está en marcha (docker compose --profile caddy up -d)")
        return
    environment = dict(
        line.partition("=")[::2] for line in compose("exec", "-T", "caddy", "env").splitlines()
    )
    tls_mode = environment.get("CADDY_TLS", "internal")
    password = os.environ.get("CADDY_CHECK_PASSWORD")
    auth = None
    if password:
        user = environment["CADDY_BASIC_AUTH_USER"]
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()

    app = caddy_site("CADDY_APP_HOST", environment["CADDY_APP_HOST"], published)
    odoo = caddy_site("CADDY_ODOO_HOST", environment["CADDY_ODOO_HOST"], published)
    check_path(f"Caddy por la dirección publicada ({(app or odoo or Site('', '?', 0)).address})", app, odoo, tls_mode, auth)

    anchor = app or odoo
    public = public_address(anchor.host, anchor.address) if anchor else None
    if public:
        check_path(
            f"Caddy por el nombre público ({public}: DNS, router, firewall y Docker)",
            app._replace(address=public) if app else None,
            odoo._replace(address=public) if odoo else None,
            tls_mode,
            auth,
        )


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
