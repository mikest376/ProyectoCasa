# Debezium (CDC)

Definición del conector de PostgreSQL. Llega en la **Fase 1**.

- Captura global del esquema `public`, sin `table.include.list`.
- Las propiedades previstas están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#ingesta-cdc).
- El JSON del conector no contiene contraseñas: usa `${env:DEBEZIUM_DB_PASSWORD}`, que Connect resuelve desde el entorno del contenedor.
- El broker no crea topics (`auto.create.topics.enable=false`), así que el conector debe definir `topic.creation.default.partitions` y `topic.creation.default.replication.factor`.
- Antes del primer snapshot hay que excluir las columnas con secretos ([TD-004](../../docs/TECHNICAL_DEBT.md#td-004)).
