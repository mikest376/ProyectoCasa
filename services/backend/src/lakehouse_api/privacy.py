"""Posibles datos personales en una pregunta (TD-021).

Con un proveedor externo (NIM), la pregunta sale del equipo y sus términos prohíben enviar datos
personales: si parece llevarlos, no se envía. Reconoce correos, teléfonos, DNI y NIE, IBAN y
números de tarjeta. Los nombres propios no se pueden reconocer con seguridad: la interfaz avisa
antes de la primera pregunta. Prefiere un falso positivo a dejar salir un dato.
"""

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "correo electrónico": re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),
    # Nueve o más cifras, con espacios o guiones: teléfonos, no importes con puntos.
    "teléfono": re.compile(r"(?<![\w.,])(\+\d{1,3}[\s-]?)?(\d[\s-]?){8,11}\d(?![\w.,])"),
    "DNI o NIE": re.compile(r"(?<!\w)(\d{8}|[XYZxyz][-\s]?\d{7})[-\s]?[A-Za-z](?!\w)"),
    "IBAN": re.compile(
        r"(?<!\w)[A-Za-z]{2}\d{2}(\s?[A-Za-z0-9]{4}){3,7}(\s?[A-Za-z0-9]{1,3})?(?!\w)"
    ),
    "tarjeta": re.compile(r"(?<![\w.,])(\d[\s-]?){12,18}\d(?![\w.,])"),
}


def personal_data(text: str) -> list[str]:
    """Qué clases de dato personal parece contener el texto. Vacío si ninguna."""
    return [label for label, pattern in PATTERNS.items() if pattern.search(text)]
