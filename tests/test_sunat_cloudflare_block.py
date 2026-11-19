"""SUNAT behind Cloudflare: a bot check is not a timeout.

Cloudflare answers SUNAT's search with its bot check (403 + `cf-mitigated: challenge`)
instead of the results. In the old headless shell the check never finishes, the page
never goes idle and every RUC came back as "Timeout esperando resultados de SUNAT",
which reads like SUNAT being slow, not like the service being blocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.models.schemas import BusinessRegistration
from app.services import browser as browser_module
from app.services.pe_sunat_service import CLOUDFLARE_BLOCKED_ERROR, PeSunatService

RUC = "20613643177"
CHALLENGE_TEXT = "e-consultaruc.sunat.gob.pe\nVerificación de seguridad en curso"
RESULTS_TEXT = f"Resultado de la Búsqueda\nNúmero de RUC:\n{RUC} - TUMI SOLUCIONES S.A.C."


class FakeResponse:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class FakeLocator:
    def __init__(self, page: "FakePage"):
        self._page = page

    async def fill(self, value: str) -> None:
        pass

    async def click(self) -> None:
        self._page.emit(self._page.search_response)

    async def inner_text(self) -> str:
        return self._page.body_text


class FakePage:
    """Page stub for PeSunatService._do_lookup.

    `search_response` is what Cloudflare/SUNAT answers to the search click,
    `landing_response` what it answers to the first navigation. `idle` says
    whether the page ever reaches networkidle after the search.
    """

    def __init__(
        self,
        search_response: FakeResponse,
        body_text: str,
        idle: bool = True,
        landing_response: FakeResponse | None = None,
    ):
        self.search_response = search_response
        self.landing_response = landing_response or FakeResponse({})
        self.body_text = body_text
        self.idle = idle
        self.url = "https://e-consultaruc.sunat.gob.pe/cl-ti-itmrconsruc/jcrS00Alias"
        self._handlers: list = []

    def on(self, event: str, handler) -> None:
        assert event == "response"
        self._handlers.append(handler)

    def emit(self, response: FakeResponse) -> None:
        for handler in self._handlers:
            handler(response)

    async def goto(self, url, wait_until=None, timeout=None):
        self.emit(self.landing_response)
        if self.landing_response.headers.get("cf-mitigated") == "challenge":
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self)

    async def wait_for_load_state(self, state=None, timeout=None):
        if not self.idle:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    async def wait_for_timeout(self, ms) -> None:
        pass


CHALLENGE = FakeResponse({"cf-mitigated": "challenge", "server": "cloudflare"})
NORMAL = FakeResponse({"server": "cloudflare"})


def _service() -> PeSunatService:
    """Service with the result-page parsers stubbed out."""
    service = PeSunatService()
    service._parse_company_info = AsyncMock(
        return_value=BusinessRegistration(legal_name="TUMI SOLUCIONES S.A.C.", tax_id=RUC)
    )
    service._parse_legal_rep = AsyncMock(return_value=(None, None))
    return service


@pytest.mark.asyncio
class TestCloudflareBlock:
    async def test_challenge_that_never_settles_is_reported_as_blocked(self):
        """The production case: 403 challenge, page never idle."""
        page = FakePage(CHALLENGE, CHALLENGE_TEXT, idle=False)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is False
        assert errors == [CLOUDFLARE_BLOCKED_ERROR]

    async def test_challenge_page_that_settles_is_not_a_silent_not_found(self):
        """Without the check this returned found=false with errors=[]."""
        page = FakePage(CHALLENGE, CHALLENGE_TEXT, idle=True)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is False
        assert errors == [CLOUDFLARE_BLOCKED_ERROR]

    async def test_challenge_on_the_landing_page_is_reported_as_blocked(self):
        page = FakePage(NORMAL, CHALLENGE_TEXT, landing_response=CHALLENGE)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is False
        assert errors == [CLOUDFLARE_BLOCKED_ERROR]

    async def test_challenge_that_resolves_to_results_still_finds_the_company(self):
        page = FakePage(CHALLENGE, RESULTS_TEXT, idle=True)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is True
        assert result.registration.legal_name == "TUMI SOLUCIONES S.A.C."
        assert errors == []

    async def test_real_timeout_without_challenge_keeps_the_timeout_error(self):
        page = FakePage(NORMAL, "", idle=False)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is False
        assert errors == ["Timeout esperando resultados de SUNAT (45000ms)"]

    async def test_normal_search_finds_the_company(self):
        page = FakePage(NORMAL, RESULTS_TEXT, idle=True)
        errors: list[str] = []

        result = await _service()._do_lookup(page, RUC, 45000, errors)

        assert result.found is True
        assert errors == []


@pytest.mark.asyncio
async def test_browser_launches_full_chromium_in_new_headless_mode():
    """The headless shell (Playwright's default) is what Cloudflare stops on SUNAT;
    full Chromium in new headless mode goes through. Shared by RUES, SUNAT and SII."""
    launch = AsyncMock(return_value=MagicMock())
    playwright = MagicMock()
    playwright.chromium.launch = launch
    starter = MagicMock()
    starter.start = AsyncMock(return_value=playwright)

    with patch.object(browser_module, "async_playwright", return_value=starter), \
            patch.object(browser_module, "_browser", None), \
            patch.object(browser_module, "_playwright_instance", None):
        await browser_module.get_browser()

    assert launch.call_args.kwargs["channel"] == "chromium"
