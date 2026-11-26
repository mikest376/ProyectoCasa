# Unity Catalog OSS 0.7.0

Catálogo de referencia del lakehouse: de sus descripciones sale el esquema que se enseña al LLM. Usa la configuración de la imagen: base H2 en el volumen `unity-catalog-db` y autorización desactivada ([TD-013](../../docs/TECHNICAL_DEBT.md#td-013)).

- Catálogo `lakehouse`, con los esquemas `silver` y `gold`. Los crea y los mantiene `medallion-transform`.
- Tablas Delta externas en MinIO, con descripción de tabla y de cada columna. Solo metadatos: no lee los datos ni necesita credenciales de S3.
- Si cambia una definición, la tabla se vuelve a registrar; si sobra, se retira.
- Las propiedades de `server.properties` se pueden sobrescribir con variables de entorno del mismo nombre.
- El heap se fija con `JAVA_TOOL_OPTIONS`, porque la imagen no lo limita.
