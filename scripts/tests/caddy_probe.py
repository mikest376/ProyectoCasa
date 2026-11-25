#!/usr/bin/env python3
"""Servidor simulado y cliente de la prueba de Caddy. Los ejecuta test_caddy.sh.

    caddy_probe.py serve    responde en lugar de Odoo, el frontend y FastAPI
    caddy_probe.py check    prueba el Caddyfile real desde la misma red

Biblioteca estándar. No usa credenciales del proyecto.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP, ODOO = "app.localhost", "odoo.localhost"
UPSTREAMS = {"odoo": 8069, "backend": 8000, "frontend": 3000}
# La primera es la cookie real de Odoo 17. Las demás, variantes que no deben estropearse.
COOKIES = (
    "session_id=abc; Expires=Thu, 25 Feb 2027 12:00:00 GMT; Max-Age=7776000; HttpOnly; Path=/",
    "frontend_lang=es_ES; Path=/",
    "ya_segura=1; Secure; HttpOnly; Path=/",
    "ya_segura_al_final=1; Path=/; secure",
    "insecure=Secure_no_es_atributo; Path=/",
)
BLOCKED = (
    "/web/database/manager",
    "/web/database/selector",
    "/WEB/DATABASE/manager",
    "//web/database/manager",
    "/web/%64atabase/manager",
    "/x/../web/database/manager",
    "/xmlrpc/2/common",
    "/jsonrpc",
    "/es/jsonrpc",
    "/web/signup",
    "/web/reset_password",
    "/web/tests",
)

failures = 0


def report(good: bool, text: str) -> None:
    global failures
    print(f"  {'OK   ' if good else 'FALLO'}  {text}")
    failures += not good


class Upstream(BaseHTTPRequestHandler):
    """Devuelve las cabeceras recibidas y, si es Odoo, cookies sin Secure."""

    def do_GET(self) -> None:
        service = self.server.service  # type: ignore[attr-defined]
        body = json.dumps(
            {"service": service, "path": self.path, "headers": sorted(h.lower() for h in self.headers)}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if service == "odoo":
            for cookie in COOKIES:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Sin registro de accesos."""


def serve() -> int:
    for service, port in UPSTREAMS.items():
        server = ThreadingHTTPServer(("0.0.0.0", port), Upstream)  # noqa: S104
        server.service = service  # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()
    return 0


class Reply:
    def __init__(self, response: http.client.HTTPResponse) -> None:
        self.status = response.status
        self.cookies = response.headers.get_all("Set-Cookie") or []
        self.headers = response.headers
        text = response.read()
        self.json = json.loads(text) if self.status == 200 else {}


def get(host: str, path: str, *, auth: bool = True, cookie: str | None = None) -> Reply:
    context = ssl.create_default_context()
    # CA propia de Caddy: aquí se prueban las cabeceras, no el certificado.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(host, 443, timeout=10, context=context)
    headers = {}
    if auth:
        pair = f"ejemplo:{os.environ['PROBE_PASSWORD']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    if cookie:
        headers["Cookie"] = cookie
    try:
        connection.request("GET", path, headers=headers)
        return Reply(connection.getresponse())
    finally:
        connection.close()


def wait_ready(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if get(ODOO, "/", auth=True).status == 200 and get(APP, "/", auth=True).status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("Caddy no responde")
        time.sleep(0.5)


def check_cookies() -> None:
    received = get(ODOO, "/web/login").cookies
    report(len(received) == len(COOKIES), f"llegan las {len(COOKIES)} cookies de Odoo")
    for original, cookie in zip(COOKIES, received, strict=False):
        name = cookie.split("=", 1)[0]
        attributes = [part.strip() for part in cookie.split(";")]
        count = sum(attribute.lower() == "secure" for attribute in attributes)
        report(count == 1, f"cookie {name}: un único Secure")
        # Nada más cambia: ni el valor ni el resto de atributos.
        plain = [p.strip() for p in original.split(";") if p.strip().lower() != "secure"]
        kept = [p for p in attributes if p.lower() != "secure"]
        report(kept == plain, f"cookie {name}: el resto no cambia")
    report(bool(re.search(r"HttpOnly", received[0])) if received else False, "session_id conserva HttpOnly")


def check_access() -> None:
    for host, path in ((APP, "/"), (APP, "/api/health"), (ODOO, "/"), (ODOO, "/web/login")):
        report(get(host, path, auth=False).status == 401, f"{host}{path} exige contraseña")
    for path in BLOCKED:
        report(get(ODOO, path).status == 404, f"{path} bloqueada con contraseña")


def check_forwarding() -> None:
    session = "session_id=abc"
    odoo = get(ODOO, "/web", cookie=session).json
    report("authorization" not in odoo["headers"], "Odoo no recibe la contraseña de acceso")
    report("cookie" in odoo["headers"], "Odoo recibe su cookie")
    for path, service in (("/", "frontend"), ("/api/health", "backend")):
        answer = get(APP, path, cookie=session).json
        report(answer["service"] == service, f"{path} llega a {service}")
        report("cookie" not in answer["headers"], f"{service} no recibe cookies")
        report("authorization" not in answer["headers"], f"{service} no recibe la contraseña")


def check() -> int:
    wait_ready()
    check_cookies()
    check_access()
    check_forwarding()
    print(f"Resultado: {failures} fallos")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit({"serve": serve, "check": check}[sys.argv[1]]())
