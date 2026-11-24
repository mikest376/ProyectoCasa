# Trino 484 (nodo único)

| Fichero | Contenido |
|---|---|
| `jvm.config` | Heap fijo de 4 GB dentro de un contenedor de 5120 MiB |
| `config.properties` | Límites de memoria y duración por consulta |
| `catalog/lakehouse.properties.example` | Plantilla del catálogo Delta Lake, inactiva hasta la Fase 3 |

`jvm.config` parte del que trae la imagen oficial, con dos cambios:

- `-Xms1G -Xmx4G` en lugar de porcentajes de RAM.
- Sin `HeapDumpOnOutOfMemoryError`: cada volcado ocuparía unos 4 GB en disco.

No admite comentarios fiables, así que las explicaciones van aquí.

## Fase 3

- Activar el catálogo: copiar la plantilla a `lakehouse.properties`. El metastore en ficheros sobre MinIO es la opción decidida ([TD-002](../../docs/TECHNICAL_DEBT.md#td-002)).
- Reglas de acceso: usuario `text2sql` con `SELECT` solo sobre Gold.
