"""SII (Servicio de Impuestos Internos) service for Chile.

Flow:
1. Navigate to "Consultar Situacion Tributaria de Terceros" (STC). Under load the
   SII first sends visitors to a Queue-it waiting room (salaespera.sii.cl, the
   progress bar with a walking person) and only shows the form when their turn comes.
2. Fill the RUT and click "Consultar".
3. The page POSTs to /app/stc/recurso/v1/consulta/getConsultaData/ and renders the
   JSON it gets back. We read that JSON instead of parsing the HTML.

Things the SII does differently from RUES/SUNAT:
- It never says "this RUT does not exist". An unknown RUT comes back with
  `registrado: true`, `nombre: "**"` and the alert "Rut consultado sin iniciacion
  de actividades".
- It has no legal representative.
- A dissolved company keeps its name: the SII flags it with `tiene8102`
  ("Sociedad disuelta por absorcion desde <fecha8102>") and/or `conTegi` plus a
  "Termino de giro" alert.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models.enums import Country, RegistrationStatus
from app.models.schemas import BusinessRegistration, LookupResponse
from app.services.base import BaseLookupService
from app.services.browser import get_browser
from app.services.fingerprint import random_fingerprint
from app.services.rut_validator import format_rut

logger = logging.getLogger(__name__)

SII_URL = "https://www2.sii.cl/stc/noauthz"
CONSULTA_PATH = "/app/stc/recurso/v1/consulta/getConsultaData"
RUT_INPUT = 'input[placeholder="12.345.678-9"]'
SUBMIT_BUTTON = 'input[name="Consultar"]'
WAITING_ROOM_HOSTS = ("salaespera.sii.cl", "queue-it.net")

# Name the SII shows when it has no name for the RUT
NO_NAME = "**"
# "Recomendacion General": the SII adds it to every answer, it says nothing about the RUT
GENERAL_RECOMMENDATION_GROUP = "025"
# "Termino de giro persona natural" (020) / "persona juridica" (021)
TERMINATION_GROUPS = {"020", "021"}
DISSOLUTION_REASON = "Sociedad disuelta por absorción"
TERMINATION_REASON = "Término de giro"


def _yes_no(value) -> str:
    return "SI" if value else "NO"


def _alerts(data: dict) -> list[dict]:
    """Flatten the SII alert tables, without the general recommendation."""
    return [
        alert
        for table in data.get("alertaTablas") or []
        for alert in table.get("alertas") or []
        if alert.get("grupo") != GENERAL_RECOMMENDATION_GROUP
    ]


def _alert_date(alert: dict) -> Optional[str]:
    if alert.get("indFecha") == "S" and alert.get("fechaActivacion") not in (None, "", "N"):
        return alert["fechaActivacion"]
    return None


def _alert_text(alert: dict) -> str:
    text = (alert.get("texto") or "").strip()
    since = _alert_date(alert)
    return f"{text} (desde {since})" if since else text


def _termination(data: dict, alerts: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """Return (termination_date, termination_reason), or (None, None) if the
    taxpayer is not dissolved and has no termino de giro."""
    if data.get("tiene8102"):
        return data.get("fecha8102"), DISSOLUTION_REASON
    for alert in alerts:
        if alert.get("grupo") in TERMINATION_GROUPS:
            return _alert_date(alert), (alert.get("texto") or TERMINATION_REASON).strip()
    if data.get("conTegi"):
        return None, TERMINATION_REASON
    return None, None


def parse_consulta(tax_id: str, data: dict) -> LookupResponse:
    """Turn the getConsultaData JSON into a LookupResponse.

    `found` is True only when the SII shows a name for the RUT. Status:
    CANCELADA with termino de giro or dissolution, ACTIVA with inicio de
    actividades, INACTIVA otherwise. The SII answered either way, so `errors`
    stays empty.
    """
    name = (data.get("nombre") or "").strip()
    if not data.get("registrado") or name in ("", NO_NAME):
        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.CL,
            found=False,
            raw_entries=[data],
        )

    alerts = _alerts(data)
    termination_date, termination_reason = _termination(data, alerts)

    if termination_reason:
        status = RegistrationStatus.CANCELLED
    elif data.get("tieneInicio"):
        status = RegistrationStatus.ACTIVE
    else:
        status = RegistrationStatus.INACTIVE

    activities = [
        f"{giro.get('codigo')} - {(giro.get('descripcion') or '').strip()}"
        for giro in data.get("girosNegocio") or []
    ]
    alert_texts = [_alert_text(alert) for alert in alerts]

    registration = BusinessRegistration(
        legal_name=name,
        tax_id=tax_id,
        status=status,
        activity_start_date=data.get("fechaInicioActividades"),
        economic_activities=activities or None,
        termination_date=termination_date,
        termination_reason=termination_reason,
        small_company=_yes_no(data.get("tieneEMTP")),
        foreign_currency_taxpayer=_yes_no(data.get("tieneAPME")),
        tax_alerts=alert_texts or None,
    )

    return LookupResponse(
        tax_id_input=tax_id,
        country=Country.CL,
        found=True,
        registration=registration,
        raw_entries=[data],
    )


class ClSiiService(BaseLookupService):
    async def lookup(self, tax_id: str) -> LookupResponse:
        errors: list[str] = []
        start = time.monotonic()
        logger.info("[RUT=%s] Iniciando lookup SII", tax_id)

        browser = await get_browser()
        fp = random_fingerprint()
        logger.info("[RUT=%s] Fingerprint: locale=%s tz=%s viewport=%sx%s",
                    tax_id, fp["locale"], fp["timezone_id"],
                    fp["viewport"]["width"], fp["viewport"]["height"])
        context = await browser.new_context(**fp)
        page = await context.new_page()
        await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

        try:
            result = await self._do_lookup(page, tax_id, errors)
            elapsed = round(time.monotonic() - start, 2)
            logger.info("[RUT=%s] Lookup completado en %ss | found=%s errors=%s",
                        tax_id, elapsed, result.found, result.errors)
            return result
        except PlaywrightTimeout:
            elapsed = round(time.monotonic() - start, 2)
            logger.error("[RUT=%s] TIMEOUT despues de %ss", tax_id, elapsed)
            errors.append("Timeout al consultar SII")
            return self._empty_response(tax_id, errors)
        except Exception as exc:
            elapsed = round(time.monotonic() - start, 2)
            logger.exception("[RUT=%s] ERROR inesperado despues de %ss: %s", tax_id, elapsed, exc)
            errors.append("Error inesperado al consultar SII")
            return self._empty_response(tax_id, errors)
        finally:
            await context.close()

    async def _do_lookup(self, page: Page, tax_id: str, errors: list[str]) -> LookupResponse:
        timeout = settings.scraping_timeout
        queue_timeout = settings.sii_queue_timeout

        # Step 1: open the form. Not networkidle: the Queue-it waiting room keeps
        # polling, so the network never goes idle while we are in line.
        logger.info("[RUT=%s] Step 1: Navegando a %s", tax_id, SII_URL)
        try:
            await page.goto(SII_URL, wait_until="domcontentloaded", timeout=timeout)
        except PlaywrightTimeout:
            logger.warning("[RUT=%s] SII no respondio la navegacion (%dms)", tax_id, timeout)
            errors.append(f"SII no respondio ({timeout}ms)")
            return self._empty_response(tax_id, errors)

        # Step 2: wait for the form. If the SII sent us to the waiting room, this
        # is where we wait for our turn; the queue sends the browser back to the form.
        try:
            await page.wait_for_selector(RUT_INPUT, timeout=queue_timeout)
        except PlaywrightTimeout:
            if any(host in page.url for host in WAITING_ROOM_HOSTS):
                logger.warning("[RUT=%s] Sigue en la fila de espera del SII (%dms)", tax_id, queue_timeout)
                errors.append(f"Fila de espera del SII no avanzo ({queue_timeout}ms)")
            else:
                logger.warning("[RUT=%s] SII no mostro el formulario (%dms) | url=%s",
                               tax_id, queue_timeout, page.url)
                errors.append(f"SII no mostro el formulario ({queue_timeout}ms)")
            return self._empty_response(tax_id, errors)

        # Step 3: fill the RUT, submit and read the JSON the page receives.
        logger.info("[RUT=%s] Step 3: Ingresando RUT y consultando", tax_id)
        await page.locator(RUT_INPUT).fill(format_rut(tax_id))
        try:
            async with page.expect_response(
                lambda r: CONSULTA_PATH in r.url and r.request.method == "POST",
                timeout=timeout,
            ) as response_info:
                await page.locator(SUBMIT_BUTTON).click()
            response = await response_info.value
        except PlaywrightTimeout:
            logger.warning("[RUT=%s] SII no respondio la consulta (%dms)", tax_id, timeout)
            errors.append(f"SII no respondio la consulta ({timeout}ms)")
            return self._empty_response(tax_id, errors)

        if response.status != 200:
            logger.warning("[RUT=%s] SII respondio HTTP %s", tax_id, response.status)
            errors.append(f"SII respondio HTTP {response.status}")
            return self._empty_response(tax_id, errors)

        data = await response.json()
        if data.get("captchaInvalido"):
            logger.warning("[RUT=%s] SII rechazo el captcha", tax_id)
            errors.append("SII rechazo el captcha")
            return self._empty_response(tax_id, errors)

        return parse_consulta(tax_id, data)

    def _empty_response(self, tax_id: str, errors: list[str]) -> LookupResponse:
        logger.info("[RUT=%s] Retornando respuesta vacia | errors=%s", tax_id, errors)
        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.CL,
            found=False,
            errors=errors,
        )
