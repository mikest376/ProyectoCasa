# Caddy (entrada externa)

Único servicio que puede escuchar fuera de `127.0.0.1`, y solo cuando se pide de forma explícita. Las instrucciones y precauciones están en el [README principal](../../README.md#exposición-para-la-defensa-del-tfg).

| Host | Destino | Notas |
|---|---|---|
| `CADDY_APP_HOST` | `frontend:3000`; `/api/*` a `backend:8000` | Cuerpo máximo de 64 KB |
| `CADDY_ODOO_HOST` | `odoo:8069` | Sin gestor de bases de datos, API externa, registro ni recuperación de contraseña |

- Todo exige contraseña (`basic_auth`). Sin credenciales válidas no pasa nadie: o Caddy no arranca, o responde 401 a todo.
- Caddy solo comparte red con Odoo, el frontend y FastAPI. No puede alcanzar PostgreSQL, Kafka, MinIO, Trino ni Ollama.
- La API de administración está desactivada: para aplicar cambios hay que reiniciar el contenedor.
- `CADDY_TLS=internal` emite certificados con una CA propia (aviso en el navegador). Con un correo electrónico, usa Let's Encrypt y necesita un dominio público.
- Docker Desktop no conserva la IP del cliente: el filtrado por IP debe hacerse en el router o en el firewall de Windows.

Generar el hash de la contraseña (la pide sin mostrarla):

```bash
docker run --rm -it caddy:2.11.9-alpine caddy hash-password
```

En `.env`, el hash va entre comillas simples: contiene `$`.
