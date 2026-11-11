"""Tests for the Chilean RUT validator."""

import pytest

from app.services.rut_validator import compute_check_digit, format_rut, validate_rut


class TestComputeCheckDigit:
    @pytest.mark.parametrize(
        "body, expected",
        [
            ("78490274", "9"),   # DRUO CHILE SPA.
            ("78827280", "4"),   # KI SPA
            ("1234567", "4"),
            ("99999990", "5"),
            ("10000013", "K"),   # remainder 10 -> K
            ("10000004", "0"),   # remainder 11 -> 0
        ],
    )
    def test_known_check_digits(self, body, expected):
        assert compute_check_digit(body) == expected


class TestValidateRut:
    @pytest.mark.parametrize(
        "raw",
        ["78.490.274-9", "78490274-9", "784902749", " 78.490.274-9 ", "78 490 274-9"],
    )
    def test_accepts_common_formats(self, raw):
        assert validate_rut(raw) == ("78490274-9", [])

    def test_lowercase_k_is_accepted(self):
        assert validate_rut("10.000.013-k") == ("10000013-K", [])

    def test_wrong_check_digit(self):
        cleaned, errors = validate_rut("78.490.274-1")
        assert cleaned == "78490274-1"
        assert errors == ["Digito verificador invalido: 1 (esperado 9)"]

    def test_empty(self):
        assert validate_rut("  ") == ("", ["RUT vacio"])

    @pytest.mark.parametrize("raw", ["78A90274-9", "78490274-X", "78490274-"])
    def test_invalid_characters(self, raw):
        _, errors = validate_rut(raw)
        assert errors == ["RUT contiene caracteres invalidos"]

    @pytest.mark.parametrize("raw", ["12345-6", "123456789-0"])
    def test_invalid_length(self, raw):
        _, errors = validate_rut(raw)
        assert len(errors) == 1 and "longitud invalida" in errors[0]


class TestFormatRut:
    @pytest.mark.parametrize(
        "cleaned, expected",
        [("78490274-9", "78.490.274-9"), ("1234567-4", "1.234.567-4"), ("10000013-K", "10.000.013-K")],
    )
    def test_formats_like_the_sii_form(self, cleaned, expected):
        assert format_rut(cleaned) == expected
