"""Tests for NIT validator.

Con guion, el DV explicito se descarta y se retorna la base.
Sin guion, el numero se retorna INTACTO (un valor de 10 digitos es ambiguo:
cedula de 10 digitos vs NIT base+DV); co_rues_service decide en tiempo de
busqueda (full primero, fallback a los primeros 9 digitos).
"""

from app.services.nit_validator import compute_check_digit, validate_nit


class TestValidateNitFormats:
    """Acepta NIT/cedula con/sin DV, con/sin guion."""

    def test_9_digits_no_dv(self):
        nit, errors = validate_nit("900123456")
        assert nit == "900123456"
        assert errors == []

    def test_with_hyphen_and_dv(self):
        nit, errors = validate_nit("900123456-1")
        assert nit == "900123456"
        assert errors == []

    def test_10_digits_no_hyphen_kept_full(self):
        # No-hyphen 10-digit es ambiguo (cedula vs NIT+DV); ya no se trunca.
        # El fallback de co_rues_service prueba los primeros 9 si RUES no da card.
        nit, errors = validate_nit("9001234561")
        assert nit == "9001234561"
        assert errors == []

    def test_10_digit_cedula_kept_full(self):
        # Persona natural: la cedula de 10 digitos se preserva intacta.
        nit, errors = validate_nit("1010183001")
        assert nit == "1010183001"
        assert errors == []

    def test_cedula_with_hyphen_and_dv(self):
        # Cedula con DV explicito: se descarta el DV, se busca el numero completo.
        nit, errors = validate_nit("1010183001-1")
        assert nit == "1010183001"
        assert errors == []

    def test_with_dots_and_hyphen(self):
        nit, errors = validate_nit("900.123.456-7")
        assert nit == "900123456"
        assert errors == []

    def test_with_dots_no_dv(self):
        nit, errors = validate_nit("900.123.456")
        assert nit == "900123456"
        assert errors == []

    def test_with_spaces(self):
        nit, errors = validate_nit(" 900 123 456 ")
        assert nit == "900123456"
        assert errors == []

    def test_known_nit_with_hyphen(self):
        nit, errors = validate_nit("860002964-8")
        assert nit == "860002964"
        assert errors == []

    def test_known_nit_no_hyphen_with_dv(self):
        # 10-digit no-hyphen se retorna completo; el fallback de RUES quita el DV.
        nit, errors = validate_nit("8600029648")
        assert nit == "8600029648"
        assert errors == []

    def test_known_nit_no_dv(self):
        nit, errors = validate_nit("860002964")
        assert nit == "860002964"
        assert errors == []

    def test_minimum_length(self):
        nit, errors = validate_nit("123456")
        assert nit == "123456"
        assert errors == []


class TestValidateNitErrors:
    def test_empty_nit(self):
        _, errors = validate_nit("")
        assert len(errors) == 1
        assert "vacio" in errors[0].lower()

    def test_non_numeric(self):
        _, errors = validate_nit("900ABC456")
        assert len(errors) == 1
        assert "no numericos" in errors[0].lower()

    def test_too_short(self):
        _, errors = validate_nit("12345")
        assert len(errors) == 1
        assert "longitud" in errors[0].lower()

    def test_too_long_base(self):
        _, errors = validate_nit("12345678901")
        assert len(errors) == 1
        assert "longitud" in errors[0].lower()

    def test_multi_digit_dv(self):
        _, errors = validate_nit("900123456-12")
        assert len(errors) == 1
        assert "digito de verificacion" in errors[0].lower()


class TestComputeCheckDigit:
    def test_known_nit_860002964(self):
        assert compute_check_digit("860002964") == 8

    def test_known_nit_900123456(self):
        result = compute_check_digit("900123456")
        assert 0 <= result <= 9

    def test_short_nit_padded(self):
        result = compute_check_digit("123456")
        assert 0 <= result <= 9

    def test_result_range(self):
        for nit in ["800000000", "900000000", "100000000", "860002964"]:
            dv = compute_check_digit(nit)
            assert 0 <= dv <= 9, f"DV for {nit} out of range: {dv}"
