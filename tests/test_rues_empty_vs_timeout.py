"""RUES: "no hay resultados" no es lo mismo que "RUES no respondio".

Antes ambos casos devolvian 0 tarjetas y el caller escribia siempre
"Timeout esperando resultados de RUES", asi que un identificador legitimamente
ausente del registro se reportaba como falla de infraestructura. El consumidor
no tenia forma de distinguirlos y castigaba igual a los dos.
"""

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.services.co_rues_service import CoRuesService


class FakeLocator:
    def __init__(self, count: int):
        self._count = count

    async def count(self) -> int:
        return self._count


class FakePage:
    """Page stub for _search_cards.

    `goto_fails` simulates RUES never answering the navigation; `cards` is how many
    result cards the loaded page renders (0 = loaded fine, nothing matched).
    """

    def __init__(self, cards: int = 0, goto_fails: bool = False):
        self.cards = cards
        self.goto_fails = goto_fails
        self.selector_waits: list[int] = []

    async def goto(self, url, wait_until=None, timeout=None):
        if self.goto_fails:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    async def wait_for_selector(self, selector, timeout=None):
        self.selector_waits.append(timeout)
        if self.cards == 0:
            raise PlaywrightTimeout(f"Timeout {timeout}ms exceeded.")

    def locator(self, selector):
        return FakeLocator(self.cards)


@pytest.mark.asyncio
class TestSearchCards:
    async def test_cards_found_is_reachable(self):
        page = FakePage(cards=2)
        count, reachable = await CoRuesService()._search_cards(page, "900123456", 45000)
        assert (count, reachable) == (2, True)

    async def test_page_loads_without_matches_is_reachable(self):
        """RUES contesto y no tiene nada: respuesta valida, no una falla."""
        page = FakePage(cards=0)
        count, reachable = await CoRuesService()._search_cards(page, "900123456", 45000)
        assert (count, reachable) == (0, True)

    async def test_navigation_timeout_is_unreachable(self):
        page = FakePage(goto_fails=True)
        count, reachable = await CoRuesService()._search_cards(page, "900123456", 45000)
        assert (count, reachable) == (0, False)

    async def test_grace_period_replaces_full_timeout(self):
        """Esperar los 45s completos solo retrasa el "no hay resultados"."""
        page = FakePage(cards=0)
        await CoRuesService()._search_cards(page, "900123456", 45000)
        assert page.selector_waits == [8000]

    async def test_grace_never_exceeds_the_scraping_timeout(self):
        page = FakePage(cards=0)
        await CoRuesService()._search_cards(page, "900123456", 3000)
        assert page.selector_waits == [3000]


@pytest.mark.asyncio
class TestDoLookupErrorContract:
    """`errors` vacio = RUES contesto. `errors` con algo = no se pudo consultar."""

    @staticmethod
    def _service_with(results):
        """CoRuesService whose _search_cards returns `results` in order."""
        service = CoRuesService()
        calls = []

        async def fake_search(page, query, timeout):
            calls.append(query)
            return results[len(calls) - 1]

        service._search_cards = fake_search
        return service, calls

    async def test_not_registered_reports_no_error(self):
        # 10 digitos: busca completo y luego la base de 9. RUES contesta en ambas.
        service, calls = self._service_with([(0, True), (0, True)])
        errors: list[str] = []

        result = await service._do_lookup(FakePage(), "1077724279", 45000, errors)

        assert result.found is False
        assert errors == []
        assert calls == ["1077724279", "107772427"]

    async def test_unreachable_reports_error_and_skips_fallback(self):
        service, calls = self._service_with([(0, False)])
        errors: list[str] = []

        result = await service._do_lookup(FakePage(), "1077724279", 45000, errors)

        assert result.found is False
        assert errors == ["RUES no respondio (45000ms)"]
        # Reintentar con la base solo duplica la caida.
        assert calls == ["1077724279"]

    async def test_unreachable_fallback_degrades_the_verdict(self):
        """El id completo dio vacio pero la base no se pudo consultar: degradado,
        no "no registrado"."""
        service, _ = self._service_with([(0, True), (0, False)])
        errors: list[str] = []

        await service._do_lookup(FakePage(), "1077724279", 45000, errors)

        assert errors == ["RUES no respondio (45000ms)"]
