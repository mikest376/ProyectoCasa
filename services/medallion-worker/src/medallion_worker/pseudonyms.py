"""Seudónimos deterministas para los datos personales que Debezium no captura (TD-004).

El seudónimo se deriva de la clave primaria con una sal secreta (PII_HASH_SALT): la misma fila
recibe siempre el mismo, así que Gold sigue agrupando y cruzando, pero sin la sal no se puede
saber a qué id corresponde. Del valor original no se deriva nada: nunca llega al lakehouse.

Ocho caracteres hexadecimales bastan para distinguir los registros de la demo. Las uniones se
hacen siempre por id: una colisión solo afectaría a lo que se muestra.
"""

import hashlib
import hmac

from medallion_worker.contracts import Contract

LENGTH = 8


def pseudonym(salt: bytes, table: str, key: int) -> str:
    """HMAC-SHA256 de tabla:id, recortado. Distinto por tabla aunque el id coincida."""
    return hmac.new(salt, f"{table}:{key}".encode(), hashlib.sha256).hexdigest()[:LENGTH]


def personal_values(contract: Contract, key: int, salt: bytes) -> dict[str, str | None]:
    """Valor de cada columna personal del contrato: prefijo y seudónimo, o vacío."""
    return {
        column.name: (
            f"{column.pseudonym}_{pseudonym(salt, contract.table, key)}"
            if column.pseudonym
            else None
        )
        for column in contract.columns
        if column.pseudonym is not None
    }
