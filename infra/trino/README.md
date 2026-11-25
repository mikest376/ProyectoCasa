# Trino 484 (nodo único)

| Fichero | Contenido |
|---|---|
| `jvm.config` | Heap fijo de 2 GB dentro de un contenedor de 2560 MiB |
| `config.properties` | Límites de memoria y duración por consulta |
| `catalog/lakehouse.properties` | Catálogo Delta Lake con metastore en ficheros ([TD-002](../../docs/TECHNICAL_DEBT.md#td-002)) |
| `access-control.json` | Qué puede hacer cada usuario |
| `Dockerfile` | Mete todo lo anterior en la imagen y quita los catálogos de ejemplo |

`jvm.config` parte del que trae la imagen oficial, con dos cambios: `-Xms1G -Xmx2G` en lugar de porcentajes de RAM, y sin `HeapDumpOnOutOfMemoryError` (cada volcado ocuparía unos 2 GB en disco). No admite comentarios fiables, así que las explicaciones van aquí.

## Control de acceso

| Usuario | Puede |
|---|---|
| `text2sql` | `SELECT` sobre `lakehouse.gold`. Nada más: ni Silver, ni `system`, ni propiedades de sesión, ni funciones de tabla del conector |
| `medallion-worker` | Leer Silver y Gold, y registrar tablas. No modifica datos |
| Cualquier otro | Nada |

Trino no autentica: el usuario es una cabecera ([TD-009](../../docs/TECHNICAL_DEBT.md#td-009)). Por eso hay dos capas más: el backend valida el SQL antes de enviarlo, y el usuario de MinIO de Trino solo puede escribir en `gold/_trino_metastore/`.

Las funciones de tabla integradas (`sequence`) no se pueden denegar en Trino: las rechaza el backend.

## Registro de tablas

Lo hace `medallion-transform` con `system.register_table`, y repara el catálogo cada 10 minutos si alguien lo cambia a mano. Para comparar Trino, Unity Catalog y lo declarado:

```bash
docker compose exec medallion-transform python -m medallion_worker catalogs
```

Al cambiar un fichero de esta carpeta: `docker compose --profile query up -d --build trino`. Antes de actualizar Trino, repite `scripts/test_integration.sh`: el metastore en ficheros no está documentado.
