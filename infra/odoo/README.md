# Odoo 17

| Fichero | Contenido |
|---|---|
| `entrypoint.sh` | Genera `odoo.conf` desde el entorno y delega en el entrypoint oficial |
| `init.sh` | Contenedor `odoo-init`: instala los módulos y aplica `harden.py` |
| `harden.py` | Cierra los accesos por defecto de una base con datos de demostración |

Configuración relevante:

- `list_db = False`: sin gestor de bases de datos, ni por web ni por RPC.
- `proxy_mode = True`: respeta las cabeceras de Caddy.
- `workers = 0`: un solo proceso multihilo, dentro de 1536 MiB.
- El usuario de PostgreSQL de Odoo es propietario de su base, sin superusuario.

La inicialización se hace con `scripts/init_odoo.sh`. `ODOO_ADMIN_PASSWORD` solo se entrega al contenedor `odoo-init`, no al servicio.
