# scripts

Utilidades de operación. Se ejecutan desde la raíz del repositorio, dentro de WSL.

| Utilidad prevista | Función | Fase |
|---|---|---|
| Arranque | Comprobar requisitos (RAM, GPU, Docker, `.env`) y levantar perfiles | 1 |
| Registro de conectores | Crear o actualizar el conector de Debezium | 1 |
| Semillas | Inicializar Odoo con módulos y datos de demostración | 1 |

Normas: `set -euo pipefail`, repetibles sin efectos secundarios, configuración desde `.env` y ningún secreto en argumentos ni en la salida.
