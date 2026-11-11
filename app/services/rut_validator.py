"""Validacion de RUT chileno (formato + digito verificador modulo 11).

El RUT (Rol Unico Tributario) es un numero de 6 a 8 digitos mas un digito
verificador que puede ser 0-9 o K. Se escribe como "78.490.274-9", pero tambien
llega sin puntos ("78490274-9") o sin guion ("784902749").
"""

from __future__ import annotations

import re


def compute_check_digit(body: str) -> str:
    """Compute the RUT check digit (modulo 11, weights 2..7 from the right)."""
    total = 0
    weight = 2
    for digit in reversed(body):
        total += int(digit) * weight
        weight = 2 if weight == 7 else weight + 1
    remainder = 11 - total % 11
    if remainder == 11:
        return "0"
    if remainder == 10:
        return "K"
    return str(remainder)


def validate_rut(raw: str) -> tuple[str, list[str]]:
    """Validate a Chilean RUT.

    Returns:
        (cleaned_rut, errors)
        cleaned_rut is "<body>-<dv>" without dots, e.g. "78490274-9".
        If errors is empty, the RUT is valid.
    """
    stripped = re.sub(r"[\s.]", "", raw.strip()).upper()

    if not stripped:
        return "", ["RUT vacio"]

    if "-" in stripped:
        body, _, dv = stripped.partition("-")
    else:
        body, dv = stripped[:-1], stripped[-1:]

    cleaned = f"{body}-{dv}"

    if not body.isdigit() or not re.fullmatch(r"[0-9K]", dv):
        return cleaned, ["RUT contiene caracteres invalidos"]

    if len(body) < 6 or len(body) > 8:
        return cleaned, [f"RUT tiene longitud invalida: {len(body)} digitos (esperado 6-8)"]

    expected = compute_check_digit(body)
    if dv != expected:
        return cleaned, [f"Digito verificador invalido: {dv} (esperado {expected})"]

    return cleaned, []


def format_rut(cleaned: str) -> str:
    """Format a cleaned RUT ("78490274-9") the way the SII form shows it ("78.490.274-9")."""
    body, _, dv = cleaned.partition("-")
    return f"{int(body):,}".replace(",", ".") + f"-{dv}"
