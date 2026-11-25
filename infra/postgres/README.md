# PostgreSQL 15 (origen de Odoo)

| Fichero | Contenido |
|---|---|
| `postgresql.conf` | CDC lógico (`wal_level=logical`), tope de WAL y memoria dentro de 512 MiB |
| `init/10-roles-and-database.sql` | Roles de Odoo y Debezium, base de datos y extensiones. Se ejecuta solo al crear el volumen |
| `sql/replica_identity.sql` | `REPLICA IDENTITY FULL` en tablas sin clave primaria ([TD-005](../../docs/TECHNICAL_DEBT.md#td-005)) |
| `sql/publication.sql` | Publicación para todas las tablas del esquema `public` |

`postgresql.conf` e `init/` van dentro de la imagen (`Dockerfile`): tras editarlos, `docker compose --profile source up -d --build`.

Los scripts de `sql/` los aplica `scripts/cdc.py prepare`. Se pueden repetir, y hay que hacerlo tras instalar módulos de Odoo.

Los de `init/` no se repiten si el volumen ya existe. Para aplicarlos de nuevo hay que borrar `odoo-db-data`, con pérdida de datos.

## Recuperar un slot invalidado

Si Debezium está parado y se superan los 4 GB de WAL retenido, PostgreSQL invalida el slot ([TD-006](../../docs/TECHNICAL_DEBT.md#td-006)) y `scripts/cdc.py status` lo muestra como `wal=lost`. Hay que repetir la carga inicial:

```bash
API=http://kafka-connect:8083/connectors/odoo-postgres-cdc
docker compose exec -T kafka-connect curl -sS -X PUT "$API/stop"
docker compose exec -T kafka-connect curl -sS -X DELETE "$API/offsets"
docker compose exec -T kafka-connect curl -sS -X DELETE "$API"
docker compose exec -T odoo-db psql -U postgres -d odoo -c "SELECT pg_drop_replication_slot('debezium_odoo')"
python3 scripts/cdc.py register
```

Los topics reciben de nuevo todas las filas con `op = 'r'`. Silver conserva el estado de mayor `source_lsn`.
