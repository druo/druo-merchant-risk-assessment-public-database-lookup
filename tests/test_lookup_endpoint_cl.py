"""POST /v1/lookup with country CL: RUT validation and routing to the SII service.

Nothing real runs here: the SII service's lookup is replaced by a recorder.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.models.enums import Country
from app.models.schemas import LookupResponse

API_KEY = "test-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", API_KEY)
    monkeypatch.setattr(main.limiter, "enabled", False)

    calls: list[str] = []

    async def fake_lookup(tax_id: str) -> LookupResponse:
        calls.append(tax_id)
        return LookupResponse(tax_id_input=tax_id, country=Country.CL, found=False)

    monkeypatch.setattr(main._services[Country.CL], "lookup", fake_lookup)
    # Not used as a context manager: the lifespan (real browser) never starts
    return TestClient(main.app), calls


def post(client: TestClient, tax_id: str):
    return client.post(
        "/v1/lookup",
        json={"tax_id": tax_id, "country": "CL"},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )


class TestLookupEndpointCl:
    def test_invalid_rut_is_rejected_before_the_sii(self, client):
        http, calls = client
        response = post(http, "78.490.274-1")

        assert response.status_code == 422
        assert response.json()["detail"].startswith("RUT invalido:")
        assert calls == []

    def test_valid_rut_reaches_the_sii_service_cleaned(self, client):
        http, calls = client
        response = post(http, "78.490.274-9")

        assert response.status_code == 200
        assert response.json()["country"] == "CL"
        assert calls == ["78490274-9"]
