"""SII (Chile): parsing the getConsultaData JSON and the lookup flow.

The fixtures in tests/fixtures/sii/ are real SII answers captured on 2026-11-11:
- activa_druo_chile: company with inicio de actividades, no alerts.
- disuelta_ki_spa: company dissolved by absorption (tiene8102) with termino de giro.
- sin_inicio_de_actividades: RUT unknown to the SII. The SII never says "does not
  exist": it answers with nombre "**" and "Rut consultado sin iniciacion de actividades".
- termino_giro_persona_natural: persona natural with termino de giro, nombre "**".
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models.enums import Country, RegistrationStatus
from app.services import cl_sii_service
from app.services.cl_sii_service import ClSiiService, parse_consulta

FIXTURES = Path(__file__).parent / "fixtures" / "sii"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TestParseConsulta:
    def test_active_company(self):
        result = parse_consulta("78490274-9", load("activa_druo_chile"))
        reg = result.registration

        assert result.found is True
        assert result.country == Country.CL
        assert result.errors == []
        assert reg.legal_name == "DRUO CHILE SPA."
        assert reg.tax_id == "78490274-9"
        assert reg.status == RegistrationStatus.ACTIVE
        assert reg.activity_start_date == "17-08-2026"
        assert reg.economic_activities == [
            "649900 - OTRAS ACTIVIDADES DE SERVICIOS FINANCIEROS, EXCEPTO LAS DE SEGUROS Y FONDOS DE PENSIONES N.C.P.",
            "661909 - OTRAS ACTIVIDADES AUXILIARES DE LAS ACTIVIDADES DE SERVICIOS FINANCIEROS N.C.P.",
        ]
        assert reg.termination_date is None
        assert reg.termination_reason is None
        assert reg.small_company == "SI"
        assert reg.foreign_currency_taxpayer == "NO"
        # The general recommendation comes with every RUT, it is not an alert
        assert reg.tax_alerts is None
        assert result.legal_representative is None
        assert result.legal_representative_raw is None
        assert result.raw_entries == [load("activa_druo_chile")]

    def test_dissolved_company_is_found_and_cancelled(self):
        """A dissolved company keeps its name: it exists, it is not active."""
        result = parse_consulta("78827280-4", load("disuelta_ki_spa"))
        reg = result.registration

        assert result.found is True
        assert result.errors == []
        assert reg.legal_name == "KI SPA"
        assert reg.status == RegistrationStatus.CANCELLED
        assert reg.termination_date == "08-02-2024"
        assert reg.termination_reason == "Sociedad disuelta por absorción"
        assert reg.activity_start_date is None
        assert reg.small_company == "NO"
        assert reg.tax_alerts == [
            "Término de giro persona jurídica (desde 08-02-2024)",
            "Rut consultado sin iniciación de actividades",
        ]

    def test_unknown_rut_is_not_found_without_errors(self):
        """The SII answered: "not found" is a finding, not a failure."""
        data = load("sin_inicio_de_actividades")
        result = parse_consulta("99999990-5", data)

        assert result.found is False
        assert result.registration is None
        assert result.errors == []
        assert result.raw_entries == [data]

    def test_persona_natural_without_name_is_not_found(self):
        data = load("termino_giro_persona_natural")
        result = parse_consulta("1234567-4", data)

        assert result.found is False
        assert result.errors == []
        # The termino de giro alert still reaches the consumer through raw_entries
        assert result.raw_entries == [data]

    def test_not_registered_is_not_found(self):
        data = {**load("activa_druo_chile"), "registrado": False}
        result = parse_consulta("78490274-9", data)
        assert result.found is False
        assert result.errors == []

    def test_named_without_inicio_nor_termination_is_inactive(self):
        data = {
            **load("sin_inicio_de_actividades"),
            "nombre": "EMPRESA SIN INICIO SPA",
        }
        result = parse_consulta("99999990-5", data)
        assert result.found is True
        assert result.registration.status == RegistrationStatus.INACTIVE
        assert result.registration.termination_reason is None

    def test_con_tegi_alone_is_cancelled(self):
        data = {
            **load("activa_druo_chile"),
            "conTegi": True,
            "alertaTablas": [],
        }
        result = parse_consulta("78490274-9", data)
        assert result.registration.status == RegistrationStatus.CANCELLED
        assert result.registration.termination_reason == "Término de giro"
        assert result.registration.termination_date is None

    @pytest.mark.parametrize(
        "grupo, texto",
        [
            ("020", "Término de giro persona natural"),
            ("021", "Término de giro persona jurídica"),
        ],
    )
    def test_termination_alert_alone_is_cancelled(self, grupo, texto):
        """Without tiene8102 nor conTegi, the 020/021 alert still marks the taxpayer
        as cancelled, with the alert's text and date."""
        alert = {
            "clasificacion": 2,
            "grupo": grupo,
            "grave": "N",
            "indFecha": "S",
            "fechaActivacion": "15-03-2021",
            "texto": texto,
        }
        data = {
            **load("activa_druo_chile"),
            "tiene8102": False,
            "conTegi": False,
            "alertaTablas": [{"titulo": "AVISO", "alertas": [alert]}],
        }
        reg = parse_consulta("78490274-9", data).registration

        assert reg.status == RegistrationStatus.CANCELLED
        assert reg.termination_reason == texto
        assert reg.termination_date == "15-03-2021"
        assert reg.tax_alerts == [f"{texto} (desde 15-03-2021)"]

    def test_foreign_currency_taxpayer(self):
        data = {**load("activa_druo_chile"), "tieneAPME": True}
        reg = parse_consulta("78490274-9", data).registration
        assert reg.foreign_currency_taxpayer == "SI"


class FakeResponse:
    def __init__(self, status: int = 200, data: dict | None = None):
        self.status = status
        self._data = data or {}

    async def json(self):
        return self._data


class FakeResponseInfo:
    def __init__(self, response):
        self._response = response

    @property
    def value(self):
        async def _value():
            return self._response
        return _value()


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    async def fill(self, value):
        self.page.filled = value

    async def click(self):
        self.page.clicked = True


class FakePage:
    """Page stub for ClSiiService._do_lookup.

    `goto_fails`: the SII never answers the navigation.
    `form_appears`: the RUT input shows up (False = stuck before the form).
    `url`: where the browser is when the form wait runs out.
    `response`: what getConsultaData returns (None = it never answers).
    """

    def __init__(self, goto_fails=False, form_appears=True,
                 url="https://www2.sii.cl/stc/noauthz", response=None):
        self.goto_fails = goto_fails
        self.form_appears = form_appears
        self.url = url
        self.response = response
        self.goto_wait_until = None
        self.form_wait_timeout = None
        self.filled = None
        self.clicked = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_wait_until = wait_until
        if self.goto_fails:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    async def wait_for_selector(self, selector, timeout=None):
        self.form_wait_timeout = timeout
        if not self.form_appears:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    def locator(self, selector):
        return FakeLocator(self, selector)

    @asynccontextmanager
    async def expect_response(self, predicate, timeout=None):
        yield FakeResponseInfo(self.response)
        if self.response is None:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")


@pytest.mark.asyncio
class TestDoLookup:
    async def test_success_reads_the_json(self):
        page = FakePage(response=FakeResponse(200, load("activa_druo_chile")))
        result = await ClSiiService()._do_lookup(page, "78490274-9", [])

        assert page.filled == "78.490.274-9"
        assert page.clicked is True
        assert result.found is True
        assert result.registration.legal_name == "DRUO CHILE SPA."

    async def test_navigation_does_not_wait_for_networkidle(self):
        """The waiting room keeps polling: networkidle would never come."""
        page = FakePage(response=FakeResponse(200, load("activa_druo_chile")))
        await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert page.goto_wait_until == "domcontentloaded"

    async def test_form_wait_uses_queue_timeout(self):
        page = FakePage(response=FakeResponse(200, load("activa_druo_chile")))
        await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert page.form_wait_timeout == settings.sii_queue_timeout

    async def test_navigation_timeout(self):
        result = await ClSiiService()._do_lookup(FakePage(goto_fails=True), "78490274-9", [])
        assert result.found is False
        assert result.errors == [f"SII no respondio ({settings.scraping_timeout}ms)"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://salaespera.sii.cl/?c=sii&e=consultaterceros",
            "https://sii.queue-it.net/?c=sii&e=consultaterceros",
        ],
    )
    async def test_stuck_in_waiting_room(self, url):
        page = FakePage(form_appears=False, url=url)
        result = await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert result.found is False
        assert result.errors == [f"Fila de espera del SII no avanzo ({settings.sii_queue_timeout}ms)"]

    async def test_form_never_shows_outside_waiting_room(self):
        page = FakePage(form_appears=False)
        result = await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert result.errors == [f"SII no mostro el formulario ({settings.sii_queue_timeout}ms)"]

    async def test_consulta_never_answers(self):
        result = await ClSiiService()._do_lookup(FakePage(response=None), "78490274-9", [])
        assert result.found is False
        assert result.errors == [f"SII no respondio la consulta ({settings.scraping_timeout}ms)"]

    async def test_http_error(self):
        page = FakePage(response=FakeResponse(406))
        result = await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert result.errors == ["SII respondio HTTP 406"]

    async def test_captcha_rejected(self):
        page = FakePage(response=FakeResponse(200, {"captchaInvalido": True}))
        result = await ClSiiService()._do_lookup(page, "78490274-9", [])
        assert result.found is False
        assert result.errors == ["SII rechazo el captcha"]


class FakeContext:
    def __init__(self):
        self.closed = False

    async def new_page(self):
        class _Page:
            async def add_init_script(self, script):
                pass

        return _Page()

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()

    async def new_context(self, **kwargs):
        return self.context


@pytest.mark.asyncio
class TestLookupOuterHandlers:
    """Failures that escape _do_lookup still come back as "could not verify",
    and the browser context is always closed."""

    @pytest.fixture
    def browser(self, monkeypatch):
        browser = FakeBrowser()

        async def fake_get_browser():
            return browser

        monkeypatch.setattr(cl_sii_service, "get_browser", fake_get_browser)
        return browser

    @pytest.mark.parametrize(
        "exc, expected",
        [
            (PlaywrightTimeout("Timeout 45000ms exceeded."), "Timeout al consultar SII"),
            (RuntimeError("boom"), "Error inesperado al consultar SII"),
        ],
    )
    async def test_unhandled_failure_is_reported(self, browser, monkeypatch, exc, expected):
        async def failing_do_lookup(self, page, tax_id, errors):
            raise exc

        monkeypatch.setattr(ClSiiService, "_do_lookup", failing_do_lookup)
        result = await ClSiiService().lookup("78490274-9")

        assert result.found is False
        assert result.country == Country.CL
        assert result.errors == [expected]
        assert browser.context.closed is True

    async def test_context_closed_on_success(self, browser, monkeypatch):
        async def ok_do_lookup(self, page, tax_id, errors):
            return parse_consulta(tax_id, load("activa_druo_chile"))

        monkeypatch.setattr(ClSiiService, "_do_lookup", ok_do_lookup)
        result = await ClSiiService().lookup("78490274-9")

        assert result.found is True
        assert browser.context.closed is True
