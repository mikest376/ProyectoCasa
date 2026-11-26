# Debezium (CDC)

| Fichero | Contenido |
|---|---|
| `odoo-postgres-cdc.json` | Configuración del conector, sin contraseñas |
| `column-exclude.list` | Columnas que no se capturan: secretos y datos personales ([TD-004](../../docs/TECHNICAL_DEBT.md#td-004)) |

Se aplican con `scripts/cdc.py`:

```bash
python3 scripts/cdc.py check       # secretos o datos personales del esquema real sin excluir
python3 scripts/cdc.py register    # prepara PostgreSQL, comprueba y da de alta el conector
python3 scripts/cdc.py status      # conector, slot y topics
```

- Captura global del esquema `public`, sin `table.include.list`. Las propiedades y su motivo están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#ingesta-cdc).
- Los valores `${env:...}` los resuelve Connect desde el entorno de su contenedor, y solo si empiezan por `DEBEZIUM_`.
- El broker no crea topics (`auto.create.topics.enable=false`): los crea el conector con `topic.creation.default.*`.

## Añadir una columna sensible

1. Añade la expresión regular a `column-exclude.list`. Si es un dato personal con nombre propio de su tabla, añádela también a `PERSONAL_COLUMNS` en `scripts/cdc.py`, para que `check` la vigile.
2. Ejecuta `python3 scripts/cdc.py register`.
3. Lo ya publicado sigue en Kafka hasta que caduque, y en Bronze de forma permanente: si contenía un secreto, cámbialo.

Los datos personales ya guardados se limpiaron una vez, como excepción aprobada a la regla 4, con `python -m medallion_worker redact-bronze` y `redact-lakehouse`. Repetirlo exige otra aprobación. `python3 scripts/check_pii.py` comprueba que no queda ninguno en MinIO.
