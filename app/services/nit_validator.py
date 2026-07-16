"""Validacion de NIT colombiano (formato + digito de verificacion)."""

from __future__ import annotations

import re

# Pesos para calculo del digito de verificacion del NIT colombiano
_WEIGHTS = [71, 67, 59, 53, 47, 43, 41, 37, 29, 23, 17, 13, 7, 3]


def _strip_formatting(raw: str) -> str:
    """Remove dots and spaces, but preserve hyphens for DV detection."""
    return re.sub(r"[\s.]", "", raw.strip())


def _parse_nit(raw: str) -> tuple[str, str | None, list[str]]:
    """Parse a raw NIT string into (base, dv_or_none, errors).

    Accepts:
      - "123456789-1"  → base=123456789, dv=1   (hyphen separator)
      - "1234567891"   → base=123456789, dv=1   (10 digits, last is DV)
      - "123456789"    → base=123456789, dv=None (no DV)
      - With dots/spaces: "900.123.456-7", "900 123 456" etc.
    """
    stripped = _strip_formatting(raw)

    if not stripped:
        return "", None, ["NIT vacio"]

    # If there's a hyphen, split into base and DV
    if "-" in stripped:
        parts = stripped.split("-", maxsplit=1)
        base, dv = parts[0], parts[1]
        if not base.isdigit() or not dv.isdigit():
            return stripped.replace("-", ""), None, ["NIT contiene caracteres no numericos"]
        if len(dv) != 1:
            return base, None, [f"Digito de verificacion invalido: '{dv}' (debe ser un solo digito)"]
        return base, dv, []

    # No hyphen — all digits
    if not stripped.isdigit():
        return stripped, None, ["NIT contiene caracteres no numericos"]

    # Return the number as-is, WITHOUT stripping any digit.
    #
    # A 10-digit no-hyphen value is ambiguous:
    #   - a 10-digit cedula (persona natural)   → must search the FULL number
    #   - a 9-digit NIT base + verification digit → must search the first 9
    # We do NOT decide here. co_rues_service searches the full number first and
    # falls back to the first 9 digits if RUES returns no results.
    #
    # (Historic bug: this branch used to return stripped[:9] for len==10, which
    #  mangled cedulas like 1010183001 -> 101018300 and made RUES time out.)
    return stripped, None, []


def validate_nit(raw: str) -> tuple[str, list[str]]:
    """Validate a Colombian NIT.

    Accepts formats: "123456789-1", "1234567891", "123456789",
    and with dots/spaces: "900.123.456-7", "900 123 456", etc.

    Returns:
        (nit_base, errors)
        nit_base is always the NIT without the verification digit.
        If errors is empty, the NIT is valid.
    """
    base, dv, parse_errors = _parse_nit(raw)

    if parse_errors:
        return base, parse_errors

    errors: list[str] = []

    # Accept 6-10 digits: NIT base is typically 9 (older ones 6-8), but a
    # 10-digit cedula (persona natural) is also a valid RUES search key.
    if len(base) < 6 or len(base) > 10:
        return base, [f"NIT tiene longitud invalida: {len(base)} digitos (esperado 6-10)"]

    # When a DV was given explicitly (hyphen form), it is already discarded —
    # `base` is the search key. For no-hyphen 10-digit values the full number is
    # returned and co_rues_service handles the cedula/NIT+DV fallback.
    return base, errors


def compute_check_digit(nit_base: str) -> int:
    """Compute the NIT verification digit (digito de verificacion).

    The algorithm uses weighted modular arithmetic as defined by DIAN.
    """
    digits = [int(d) for d in nit_base.rjust(14, "0")]
    total = sum(d * w for d, w in zip(digits, _WEIGHTS))
    remainder = total % 11
    if remainder == 0:
        return 0
    if remainder == 1:
        return 1
    return 11 - remainder
