# frontend

Interfaz para preguntar a los datos en lenguaje natural.

| | |
|---|---|
| Tecnología | Next.js 16 (App Router), React 19, Tailwind CSS 4, Recharts 3 |
| Componentes | Propios, al estilo de Tremor Raw: el código está en `src/components`, sin `@tremor/react` ([TD-019](../../docs/TECHNICAL_DEBT.md#td-019)) |
| Datos | Solo FastAPI: `/api/*` |
| Memoria | 256 MiB |

| Fichero | Función |
|---|---|
| `src/app/page.tsx` | Componente de servidor: modelo activo, ejemplos y tablas, por la red interna |
| `src/components/workspace.tsx` | Caja de pregunta, ejemplos por dominio, historial y espera |
| `src/components/answer-card.tsx` | Resultado: vista de gráfico o tabla, medida, tiempos, modelo y errores |
| `src/components/charts.tsx` | KPI, barras, línea y área |
| `src/components/data-table.tsx`, `sql-panel.tsx` | Tabla de datos y SQL ejecutado |
| `src/components/ui.tsx` | Tarjeta, botón, etiqueta, aviso, interruptor e iconos |
| `src/lib/api.ts` | Contrato con la API. Toda respuesta se comprueba antes de usarla |
| `src/lib/format.ts` | Números, fechas, etiquetas y SQL, en español |

## Reglas de la interfaz

- Cada respuesta enseña siempre el **SQL ejecutado**, los tiempos y el modelo que ha respondido. Si el SQL se rechaza, enseña el propuesto e indica que no se ha ejecutado.
- Los decimales llegan como texto exacto y solo se convierten para dibujarlos.
- Un gráfico, una medida y un color. Todo valor está también en la tabla.
- Claro y oscuro con colores validados para cada uno. Sin fuentes ni scripts de terceros.
- Tras Caddy, la interfaz y la API comparten origen. En desarrollo, la API va en el puerto 8000.

Lint, pruebas, tipos y compilación:

```bash
docker build --target build services/frontend
```
