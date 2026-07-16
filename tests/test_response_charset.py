"""Tests for the JSON response charset.

Sin el charset declarado, un cliente que olfatea el encoding puede leer mal
el texto del RUES: sus tablas vienen con cientos de NBSP (bytes C2 A0), que
parecen GBK. n8n decodifico una respuesta como GB18030 y "maxima" llego como
"m谩xima", rompiendo la extraccion del representante legal aguas abajo.
"""

from app.main import UTF8JSONResponse, app


class TestJsonCharset:
    """El Content-Type siempre declara utf-8, para que nadie tenga que adivinar."""

    def test_response_declares_utf8_charset(self):
        response = UTF8JSONResponse(content={"status": "ok"})
        assert response.headers["content-type"] == "application/json; charset=utf-8"

    def test_app_uses_utf8_response_by_default(self):
        assert app.router.default_response_class is UTF8JSONResponse

    def test_accented_text_survives_as_utf8(self):
        response = UTF8JSONResponse(content={"raw": "la máxima autoridad"})
        assert "la máxima autoridad" in response.body.decode("utf-8")
