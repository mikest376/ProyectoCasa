# Unity Catalog OSS 0.7.0

Catálogo de referencia del lakehouse. En la Fase 0 usa la configuración de la imagen: base H2 en el volumen `unity-catalog-db` y autorización desactivada ([TD-013](../../docs/TECHNICAL_DEBT.md#td-013)).

- Las propiedades de `server.properties` se pueden sobrescribir con variables de entorno del mismo nombre. No hace falta montar el fichero ni escribir claves en él.
- El heap se fija con `JAVA_TOOL_OPTIONS`, porque la imagen no lo limita.

## Fase 3

- Catálogo `lakehouse` con los esquemas `bronze`, `silver` y `gold`.
- Registro de tablas Delta externas con descripciones de tabla y columna.
- Primera prueba: registrar y leer una tabla externa en MinIO.
