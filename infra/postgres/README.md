# PostgreSQL 15 (origen de Odoo)

| Fichero | Contenido |
|---|---|
| `postgresql.conf` | CDC lógico (`wal_level=logical`), tope de WAL y memoria dentro de 1024 MiB |
| `init/` | Scripts que PostgreSQL ejecuta solo al crear el volumen, por orden alfabético |

## Fase 1

- `init/`: rol `debezium` con `REPLICATION` y `SELECT`, sin escritura.
- Comprobación de tablas sin clave primaria antes de crear la publicación ([TD-005](../../docs/TECHNICAL_DEBT.md#td-005)).
- Publicación para todas las tablas.

Los scripts de `init/` no se repiten si el volumen ya existe. Para aplicarlos de nuevo hay que borrar `odoo-db-data`, con pérdida de datos.
