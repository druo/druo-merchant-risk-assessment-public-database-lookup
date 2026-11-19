"""SUNAT (Superintendencia Nacional de Aduanas y Administracion Tributaria) service for Peru.

Flow:
1. Navigate to Consulta RUC page
2. Fill RUC in #txtRuc input
3. Click #btnAceptar (Buscar)
4. Parse result table (company info)
5. Click "Representante(s) Legal(es)" button
6. Parse representatives table — priority: GERENTE GENERAL > GERENTE > first row
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models.enums import Country, RegistrationStatus
from app.models.schemas import (
    BusinessRegistration,
    LegalRepresentative,
    LookupResponse,
)
from app.services.base import BaseLookupService
from app.services.browser import get_browser
from app.services.fingerprint import random_fingerprint

logger = logging.getLogger(__name__)

SUNAT_URL = "https://e-consultaruc.sunat.gob.pe/cl-ti-itmrconsruc/FrameCriterioBusquedaWeb.jsp"

# Cloudflare answers with its bot check instead of SUNAT's page and marks that
# response with `cf-mitigated: challenge`. The page text is localized, the header is not.
CLOUDFLARE_BLOCKED_ERROR = "SUNAT bloqueo la consulta (verificacion anti-bot de Cloudflare)"


def _parse_status_pe(raw: str) -> RegistrationStatus:
    normalized = raw.strip().upper()
    if "ACTIVO" in normalized:
        return RegistrationStatus.ACTIVE
    if "BAJA" in normalized or "INACTIVO" in normalized:
        return RegistrationStatus.INACTIVE
    return RegistrationStatus.UNKNOWN


class PeSunatService(BaseLookupService):
    async def lookup(self, tax_id: str) -> LookupResponse:
        errors: list[str] = []
        start = time.monotonic()
        logger.info("[RUC=%s] Iniciando lookup SUNAT", tax_id)

        browser = await get_browser()
        fp = random_fingerprint()
        logger.info("[RUC=%s] Fingerprint: locale=%s tz=%s viewport=%sx%s",
                    tax_id, fp["locale"], fp["timezone_id"],
                    fp["viewport"]["width"], fp["viewport"]["height"])
        context = await browser.new_context(**fp)
        page = await context.new_page()
        # SUNAT detects headless browsers — remove webdriver flag
        await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        timeout = settings.scraping_timeout

        try:
            result = await self._do_lookup(page, tax_id, timeout, errors)
            elapsed = round(time.monotonic() - start, 2)
            logger.info("[RUC=%s] Lookup completado en %ss | found=%s errors=%s",
                        tax_id, elapsed, result.found, result.errors)
            return result
        except PlaywrightTimeout:
            elapsed = round(time.monotonic() - start, 2)
            logger.error("[RUC=%s] TIMEOUT despues de %ss", tax_id, elapsed)
            errors.append("Timeout al consultar SUNAT")
            return self._empty_response(tax_id, errors)
        except Exception as exc:
            elapsed = round(time.monotonic() - start, 2)
            logger.exception("[RUC=%s] ERROR inesperado despues de %ss: %s", tax_id, elapsed, exc)
            errors.append("Error inesperado al consultar SUNAT")
            return self._empty_response(tax_id, errors)
        finally:
            await context.close()

    async def _do_lookup(
        self, page: Page, tax_id: str, timeout: int, errors: list[str]
    ) -> LookupResponse:
        # A Cloudflare bot check never settles, so it surfaces as a timeout unless flagged.
        challenged = False

        def _flag_challenge(response) -> None:
            nonlocal challenged
            if response.headers.get("cf-mitigated") == "challenge":
                challenged = True

        page.on("response", _flag_challenge)

        # Step 1: Navigate to search page
        logger.info("[RUC=%s] Step 1: Navegando a SUNAT Consulta RUC", tax_id)
        try:
            await page.goto(SUNAT_URL, wait_until="networkidle", timeout=timeout)
        except PlaywrightTimeout:
            if challenged:
                return self._blocked_response(tax_id, errors)
            raise

        # Step 2: Fill RUC and search
        logger.info("[RUC=%s] Step 2: Ingresando RUC y buscando", tax_id)
        ruc_input = page.locator("#txtRuc")
        await ruc_input.fill(tax_id)
        await page.locator("#btnAceptar").click()

        # Wait for navigation to results page
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
            await page.wait_for_timeout(2000)
        except PlaywrightTimeout:
            if challenged:
                return self._blocked_response(tax_id, errors)
            logger.warning("[RUC=%s] Timeout esperando resultados de SUNAT (%dms)", tax_id, timeout)
            errors.append(f"Timeout esperando resultados de SUNAT ({timeout}ms)")
            return self._empty_response(tax_id, errors)

        logger.info("[RUC=%s] Pagina de resultados cargada: %s", tax_id, page.url)

        # Check if results exist
        page_text = await page.locator("body").inner_text()
        ruc_pattern = f"{tax_id} - "

        # The check can also settle on its own page; that is a block, not "RUC not found"
        if challenged and ruc_pattern not in page_text:
            return self._blocked_response(tax_id, errors)

        # SUNAT shows "no es válido" or "no existe" for invalid/unknown RUCs
        if "no es válido" in page_text or "no existe" in page_text:
            logger.warning("[RUC=%s] RUC no valido o no existe segun SUNAT", tax_id)
            errors.append("RUC no valido o no existe segun SUNAT")
            return self._empty_response(tax_id, errors)

        # Must have the RUC with " - " pattern (e.g. "20100055237 - ALICORP SAA")
        if ruc_pattern not in page_text:
            logger.warning("[RUC=%s] RUC no encontrado en resultados", tax_id)
            return self._empty_response(tax_id, errors)

        # Step 3: Parse company info
        logger.info("[RUC=%s] Step 3: Parseando informacion de la empresa", tax_id)
        registration = await self._parse_company_info(page, tax_id)

        # Step 4: Click "Representante(s) Legal(es)" and parse
        logger.info("[RUC=%s] Step 4: Parseando Representante(s) Legal(es)", tax_id)
        legal_rep, legal_rep_raw = await self._parse_legal_rep(page, tax_id, errors)

        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.PE,
            found=True,
            registration=registration,
            legal_representative=legal_rep,
            legal_representative_raw=legal_rep_raw,
            errors=errors,
        )

    async def _parse_company_info(self, page: Page, tax_id: str) -> BusinessRegistration:
        """Parse company information from the SUNAT result page.

        Structure: div.list-group-item > div.row >
            div.col-sm-5 > h4.list-group-item-heading (label)
            div.col-sm-7 > p.list-group-item-text or h4 (value)
        """
        fields: dict[str, str] = {}

        # Parse all list-group-items with heading/text pairs
        items = page.locator("div.list-group-item")
        count = await items.count()
        logger.info("[RUC=%s] Encontrados %d campos en pagina de resultados", tax_id, count)

        for i in range(count):
            item = items.nth(i)
            headings = item.locator("h4.list-group-item-heading")
            values = item.locator("p.list-group-item-text, h4.list-group-item-heading + h4, div.col-sm-7 h4, div.col-sm-3 p")

            h_count = await headings.count()
            v_count = await values.count()

            for j in range(min(h_count, v_count)):
                label = (await headings.nth(j).inner_text()).strip().rstrip(":")
                value = (await values.nth(j).inner_text()).strip()
                if label and value:
                    fields[label] = value
                    logger.debug("[RUC=%s]   %s = %s", tax_id, label, value[:80])

        # Also try parsing with a simpler approach for fields missed above
        # Get pairs from col-sm-5 (label) and col-sm-7 (value)
        label_divs = page.locator("div.col-sm-5 h4.list-group-item-heading")
        value_divs = page.locator("div.col-sm-7 p.list-group-item-text, div.col-sm-7 h4.list-group-item-heading")
        l_count = await label_divs.count()
        v_count = await value_divs.count()
        for j in range(min(l_count, v_count)):
            label = (await label_divs.nth(j).inner_text()).strip().rstrip(":")
            value = (await value_divs.nth(j).inner_text()).strip()
            if label and value and label not in fields:
                fields[label] = value

        # Also try col-sm-3 pairs (for Fecha de Inscripción / Fecha de Inicio)
        label_3 = page.locator("div.col-sm-3 h4.list-group-item-heading")
        value_3 = page.locator("div.col-sm-3 p.list-group-item-text")
        l3_count = await label_3.count()
        v3_count = await value_3.count()
        for j in range(min(l3_count, v3_count)):
            label = (await label_3.nth(j).inner_text()).strip().rstrip(":")
            value = (await value_3.nth(j).inner_text()).strip()
            if label and value and label not in fields:
                fields[label] = value

        # Extract economic activities from the page text
        activities = []
        body_text = await page.locator("body").inner_text()
        for line in body_text.split("\n"):
            line = line.strip()
            if line.startswith("Principal") or line.startswith("Secundaria"):
                activities.append(line)

        # Extract legal name from RUC field: "20100055237 - ALICORP SAA"
        legal_name = None
        ruc_raw = fields.get("Número de RUC", "")
        if " - " in ruc_raw:
            legal_name = ruc_raw.split(" - ", 1)[1].strip()

        logger.info("[RUC=%s] Empresa: %s | Estado: %s | Condicion: %s",
                    tax_id, legal_name,
                    fields.get("Estado del Contribuyente"),
                    fields.get("Condición del Contribuyente"))

        return BusinessRegistration(
            legal_name=legal_name,
            tax_id=tax_id,
            status=_parse_status_pe(fields.get("Estado del Contribuyente", "")),
            taxpayer_type=fields.get("Tipo Contribuyente"),
            commercial_name=fields.get("Nombre Comercial"),
            inscription_date=fields.get("Fecha de Inscripción"),
            activity_start_date=fields.get("Fecha de Inicio de Actividades"),
            taxpayer_condition=fields.get("Condición del Contribuyente"),
            fiscal_address=fields.get("Domicilio Fiscal"),
            economic_activities=activities if activities else None,
            foreign_trade_activity=fields.get("Actividad Comercio Exterior"),
        )

    async def _parse_legal_rep(
        self, page: Page, tax_id: str, errors: list[str]
    ) -> tuple:
        """Click Representante(s) Legal(es) button and parse the table.

        Returns (LegalRepresentative or None, raw_text or None).
        Priority: GERENTE GENERAL > GERENTE > first row.
        """
        # Click the button
        rep_btn = page.locator(".btnInfRepLeg, button:has-text('Representante')")
        if await rep_btn.count() == 0:
            logger.warning("[RUC=%s] No se encontro boton Representante(s) Legal(es)", tax_id)
            errors.append("No se encontro boton Representante(s) Legal(es)")
            return None, None

        await rep_btn.first.click()
        await page.wait_for_timeout(3000)
        logger.info("[RUC=%s] Tabla de representantes legales cargada", tax_id)

        # Get the raw text of the representatives section
        raw_text = None
        rep_table = page.locator("table")
        # Find the table that contains "Documento" and "Cargo" headers
        table_count = await rep_table.count()
        target_table = None
        for i in range(table_count):
            t = rep_table.nth(i)
            text = await t.inner_text()
            if "Cargo" in text and "Documento" in text:
                target_table = t
                raw_text = text
                break

        if target_table is None:
            logger.warning("[RUC=%s] No se encontro tabla de representantes legales", tax_id)
            errors.append("No se encontro tabla de representantes legales")
            return None, raw_text

        # Parse table rows
        rows = target_table.locator("tr")
        row_count = await rows.count()
        logger.info("[RUC=%s] Encontradas %d filas en tabla de representantes", tax_id, row_count)

        representatives = []
        for i in range(1, row_count):  # Skip header row
            cells = rows.nth(i).locator("td")
            cell_count = await cells.count()
            if cell_count < 4:
                continue

            doc_type = (await cells.nth(0).inner_text()).strip()
            doc_number = (await cells.nth(1).inner_text()).strip()
            name = (await cells.nth(2).inner_text()).strip()
            cargo = (await cells.nth(3).inner_text()).strip()
            since = (await cells.nth(4).inner_text()).strip() if cell_count > 4 else None

            if name and cargo:
                representatives.append({
                    "doc_type": doc_type,
                    "doc_number": doc_number,
                    "name": name,
                    "cargo": cargo,
                    "since": since,
                })

        if not representatives:
            logger.warning("[RUC=%s] No se encontraron representantes en la tabla", tax_id)
            errors.append("No se encontraron representantes legales en la tabla")
            return None, raw_text

        # Priority: GERENTE GENERAL > GERENTE > first row
        chosen = None
        for rep in representatives:
            cargo_upper = rep["cargo"].upper()
            if "GERENTE GENERAL" in cargo_upper:
                chosen = rep
                logger.info("[RUC=%s] Encontrado GERENTE GENERAL: %s", tax_id, rep["name"])
                break

        if not chosen:
            for rep in representatives:
                cargo_upper = rep["cargo"].upper()
                if cargo_upper == "GERENTE":
                    chosen = rep
                    logger.info("[RUC=%s] Encontrado GERENTE: %s", tax_id, rep["name"])
                    break

        if not chosen:
            chosen = representatives[0]
            logger.info("[RUC=%s] Usando primer representante: %s (%s)",
                        tax_id, chosen["name"], chosen["cargo"])

        logger.info("[RUC=%s] Representante legal: %s | %s %s | cargo=%s | desde=%s",
                    tax_id, chosen["name"], chosen["doc_type"],
                    chosen["doc_number"], chosen["cargo"], chosen["since"])

        return LegalRepresentative(
            role=chosen["cargo"],
            name=chosen["name"].title(),
            id_type=chosen["doc_type"],
            id_number=chosen["doc_number"],
            since_date=chosen["since"],
        ), raw_text

    def _blocked_response(self, tax_id: str, errors: list[str]) -> LookupResponse:
        logger.warning("[RUC=%s] Cloudflare bloqueo la consulta (cf-mitigated: challenge)", tax_id)
        errors.append(CLOUDFLARE_BLOCKED_ERROR)
        return self._empty_response(tax_id, errors)

    def _empty_response(self, tax_id: str, errors: list[str]) -> LookupResponse:
        logger.info("[RUC=%s] Retornando respuesta vacia | errors=%s", tax_id, errors)
        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.PE,
            found=False,
            errors=errors,
        )
