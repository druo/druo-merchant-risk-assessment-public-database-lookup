"""RUES (Registro Unico Empresarial y Social) scraping service for Colombia.

Flow:
1. GET /buscar/RM/{NIT} — search results page (cards)
2. Click "Ver información" on the first card → navigates to /detalle/{camara}/{matricula}
3. Parse "Información general" tab (registroapi fields)
4. Click "Representante legal" tab and extract the Gerente
"""

from __future__ import annotations

import logging
import re
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

RUES_SEARCH_URL = "https://www.rues.org.co/buscar/RM/{nit}"


def _parse_status(raw: str) -> RegistrationStatus:
    normalized = raw.strip().upper()
    for status in RegistrationStatus:
        if status.value in normalized:
            return status
    return RegistrationStatus.UNKNOWN


def _try_tabular_format(text: str) -> Optional[LegalRepresentative]:
    """Tabular format (Promigas style): Cargo/Nombre header with lines like:
        Presidente
        Juan Manuel Rojas Payan                          CC.        79556426

    Or role on same line as name:
        Presidente
        Juan Manuel Rojas Payan                          CC.        79556426
    """
    # Check if this is a tabular format with Cargo/Nombre header
    if not re.search(r"Cargo/Nombre\s+Identificaci", text):
        return None

    logger.info("Formato tabular (Promigas) detectado")

    # Look for Presidente entry: "Presidente\n<Name> CC. <ID>"
    match = re.search(
        r"Presidente\s*\n\s*([A-Za-záéíóúñÁÉÍÓÚÑ\s]+?)\s+"
        r"(CC\.|C\.C\.|C\.E\.|NI\.|P\.P\.)\s*(\d+)",
        text,
    )
    if match:
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        id_type = match.group(2).strip()
        # Normalize id_type
        if id_type == "CC":
            id_type = "C.C."
        elif id_type == "NI":
            id_type = "N.I.T."
        id_number = match.group(3).strip()

        logger.info("Representante legal extraído (tabular): %s | %s %s", name, id_type, id_number)
        return LegalRepresentative(
            role="Presidente",
            name=name,
            id_type=id_type,
            id_number=id_number,
        )

    return None


def _try_format_c(text: str) -> Optional[LegalRepresentative]:
    """Format C: section-based with REPRESENTACION LEGAL (PRINCIPALES) header.

    Example:
        REPRESENTACION LEGAL (PRINCIPALES) ****
        XXXXXXXXX - APELLIDO APELLIDO NOMBRE

    Entries are: ID_NUMBER - FULL_NAME (one per line).
    """
    # Find the PRINCIPALES section
    match = re.search(
        r"REPRESENTACION\s+LEGAL\s*\(PRINCIPALES\)\s*\*+\s*\n(.*?)(?:\n\s*\n|REPRESENTACION\s+LEGAL\s*\(SUPLENTES\)|FACULTADES)",
        text,
        re.DOTALL,
    )
    if not match:
        return None

    section = match.group(1).strip()
    logger.info("Formato C detectado — sección PRINCIPALES: %s", section[:100])

    # Parse first entry: ID - NAME
    entry_match = re.search(r"(\d+)\s*-\s*(.+)", section)
    if not entry_match:
        logger.warning("Formato C: no se encontró entrada ID - NOMBRE")
        return None

    id_number = entry_match.group(1).strip()
    raw_name = entry_match.group(2).strip()
    name = raw_name.title()

    logger.info("Representante legal extraído (formato C): %s | C.C. %s", name, id_number)

    return LegalRepresentative(
        role="Representante Legal",
        name=name,
        id_type="C.C.",
        id_number=id_number,
    )


def _parse_legal_rep(text: str) -> Optional[LegalRepresentative]:
    """Extract the Representante Legal (preferably Gerente) from free-text.

    Handles three formats:

    Format A (with C.C. No.):
        CARGO             NOMBRE                    IDENTIFICACIÓN
        Representante     Nombre Apellido           C.C. No. XXXXXXXXX
        Legal
        (Gerente)

    Format B (columnar, no C.C. prefix):
        CARGO NOMBRE IDENTIFICACION
        GERENTE PRINCIPAL   NOMBRE APELLIDO   X.XXX.XXX
                            APELLIDO2
                            DESIGNACION

    Format C (simple list under section header):
        REPRESENTACION LEGAL (PRINCIPALES) ****
        XXXXXXXXX - APELLIDO APELLIDO NOMBRE

        REPRESENTACION LEGAL (SUPLENTES) ****
        XXXXXXXXX - APELLIDO APELLIDO NOMBRE
    """

    # --- Try Format C first (no CARGO header needed) ---
    result_c = _try_format_c(text)
    if result_c:
        return result_c

    # --- Try tabular format (Promigas style): Presidente\nName CC. ID ---
    result_tab = _try_tabular_format(text)
    if result_tab:
        return result_tab

    # --- Formats A and B: split on CARGO header ---
    blocks = re.split(r"CARGO\s+NOMBRE\s+IDENTIFICACI[OÓ]N", text)
    designation_blocks = blocks[1:] if len(blocks) > 1 else []

    if not designation_blocks:
        logger.warning("No se encontraron bloques CARGO/NOMBRE/IDENTIFICACIÓN ni formato C")
        return None

    logger.info("Encontrados %d bloques de designación", len(designation_blocks))

    # Format A: Representante <Name> <ID_TYPE> No. <ID_NUMBER> Legal [(sub-role)]
    pattern_a = (
        r"Representante\s+"
        r"(.+?)\s+"
        r"(C\.C\.|N\.I\.T\.|C\.E\.|T\.I\.|Pasaporte)\s*No\.\s*(\d+)"
        r"\s+Legal"
        r"(?:\s+\(([^)]+)\))?"
    )

    # Format B: GERENTE PRINCIPAL <Name> <ID_NUMBER (with dots)>
    pattern_b = (
        r"GERENTE\s+PRINCIPAL\s+"
        r"(.+?)\s+"
        r"([\d][\d.]+[\d])"
    )

    # Format D: REPRESENTANTE LEGAL <Name> C.C. <ID_with_dots> [PRINCIPAL] [MoreName]
    # When lines are joined: "REPRESENTANTE LEGAL JUAN DAVID HERNANDEZ C.C. 98.696.103 PRINCIPAL BETANCUR"
    pattern_d = (
        r"REPRESENTANTE\s+LEGAL\s+"
        r"(.+?)\s+"
        r"(C\.C\.|N\.I\.T\.|C\.E\.)\s*"
        r"([\d][\d.]+[\d])"
        r"(?:\s+PRINCIPAL)?"
        r"((?:\s+[A-ZÁÉÍÓÚÑ]{2,})*)"
    )

    # Format E: Gerente General <Name> C.C./C.E./P.P. No. <ID> [Continuation name]
    pattern_e = (
        r"Gerente\s+General\s+"
        r"(.+?)\s+"
        r"(C\.C\.|N\.I\.T\.|C\.E\.|T\.I\.|P\.P\.|Pasaporte)\s*No\.\s*(\d+)"
    )

    # Format F: PRESIDENTE <Name> C.C./C.E./P.P. <ID with dots or plain>
    # e.g. "PRESIDENTE MIGUEL FERNANDO ESCOBAR C.C 70.566.823 PENAGOS"
    pattern_f = (
        r"PRESIDENTE\s+"
        r"(.+?)\s+"
        r"(C\.C\.?|C\.E\.?|N\.I\.T\.?|P\.P\.?|T\.I\.?|PPTE\.?)\s*"
        r"([\d][\d.]+[\d])"
    )

    # Format G: Presidente <Name> C.C./C.E./P.P. No. <ID>
    # e.g. "Presidente Juan Pablo Rodriguez C.E. No. 411280"
    pattern_g = (
        r"Presidente\s+"
        r"(.+?)\s+"
        r"(C\.C\.|C\.E\.|P\.P\.|N\.I\.T\.|T\.I\.|Pasaporte)\s*No\.\s*(\d+)"
    )

    # Format H: GERENTE <Name> <ID with dots> (without PRINCIPAL keyword)
    # e.g. "GERENTE CARLOS JULIO DAZA VARGAS 13.801.212"
    pattern_h = (
        r"(?<!\bPRINCIPAL\s)GERENTE\s+"
        r"(.+?)\s+"
        r"([\d][\d.]+[\d])"
    )

    # Format I: Tabular — "Nombre Identificación" header then "GERENTE\n NAME CC. ID"
    # e.g. Lifecycle: "GERENTE\n  SILVA VESGA IVAN C.C. 000000079909359"
    pattern_i = (
        r"GERENTE\s+"
        r"([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñÁÉÍÓÚÑ\s]+?)\s+"
        r"C\.C\.?\s*"
        r"(\d+)"
    )

    # Stop words that signal end of name continuation
    _STOP_WORDS = {"por", "de", "del", "la", "el", "los", "las", "en", "con", "no", "se", "al",
                   "decreto", "acta", "comunicacion", "extracto", "inscrita", "inscrito",
                   "certifica", "facultades", "funciones", "designacion"}

    chosen_result = None

    for i, block in enumerate(designation_blocks):
        lines = [l.strip() for l in block.split("\n") if l.strip() and l.strip() != "."]
        block_text = " ".join(lines)

        # Skip suplente/subgerente/vicepresidente blocks
        skip_pattern = (
            r"\s*(Suplente|Subgerente|PRIMER\s+SUPLENTE|SEGUNDO\s+SUPLENTE|"
            r"TERCER\s+SUPLENTE|GERENTE\s+SUPLENTE|VICEPRESIDENTE|"
            r"Primer\s+Vicepresidente|Segundo\s+Vicepresidente|"
            r"Tercer\s+Vicepresidente|Cuarto\s+Vicepresidente|"
            r"REPRESENTANTE\s+LEGAL\s+SUPLENTE)"
        )
        if re.match(skip_pattern, block_text, re.IGNORECASE):
            logger.debug("Bloque %d es suplente/vice, se omite", i)
            continue

        # Try Format A
        match = re.search(pattern_a, block_text)
        if match:
            sub_role = match.group(4)
            name = re.sub(r"\s+", " ", match.group(1)).strip()
            result = {
                "role": f"Representante Legal ({sub_role})" if sub_role else "Representante Legal",
                "name": name,
                "id_type": match.group(2).strip(),
                "id_number": match.group(3).strip(),
            }
            if sub_role and "Gerente" in sub_role:
                logger.info("Bloque %d: encontrado Gerente (formato A)", i)
                chosen_result = result
                break
            if chosen_result is None:
                chosen_result = result
            continue

        # Try Format B
        match = re.search(pattern_b, block_text)
        if match:
            raw_name = match.group(1).strip()
            raw_id = match.group(2).strip()

            name = re.sub(r"\s+DESIGNACION\b", "", raw_name)
            after_id = block_text[match.end():]
            extra_name = re.match(r"\s+([A-ZÁÉÍÓÚÑ\s]+?)(?:\s+DESIGNACION|\s+GERENTE|\s*$)", after_id)
            if extra_name:
                extra = extra_name.group(1).strip()
                if extra and extra != "DESIGNACION":
                    name = name + " " + extra

            name = re.sub(r"\s+", " ", name).strip()
            id_number = raw_id.replace(".", "")

            result = {
                "role": "Gerente Principal",
                "name": name.title(),
                "id_type": "C.C.",
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Gerente Principal (formato B)", i)
            chosen_result = result
            break

        # Try Format D: REPRESENTANTE LEGAL <Name> C.C. <ID> [PRINCIPAL] [MoreName]
        match = re.search(pattern_d, block_text)
        if match:
            raw_name = match.group(1).strip()
            id_type = match.group(2).strip()
            raw_id = match.group(3).strip()
            extra_name = match.group(4).strip() if match.group(4) else ""

            # Combine name parts
            full_name = raw_name
            if extra_name:
                full_name = full_name + " " + extra_name
            name = re.sub(r"\s+", " ", full_name).strip()
            id_number = raw_id.replace(".", "")

            result = {
                "role": "Representante Legal Principal",
                "name": name.title(),
                "id_type": id_type,
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Representante Legal Principal (formato D)", i)
            chosen_result = result
            break

        # Try Format E: Gerente General <Name> C.C. No. <ID> [MoreName]
        match = re.search(pattern_e, block_text)
        if match:
            raw_name = match.group(1).strip()
            id_type = match.group(2).strip()
            id_number = match.group(3).strip()

            # Grab continuation name words after ID, stop at known stop words
            after_id = block_text[match.end():]
            extra_parts = []
            for word in after_id.split():
                if word.lower() in _STOP_WORDS or not word[0].isalpha():
                    break
                extra_parts.append(word)

            full_name = raw_name
            if extra_parts:
                full_name = full_name + " " + " ".join(extra_parts)
            name = re.sub(r"\s+", " ", full_name).strip()

            result = {
                "role": "Gerente General",
                "name": name,
                "id_type": id_type,
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Gerente General (formato E)", i)
            chosen_result = result
            break

        # Try Format F: PRESIDENTE <Name> C.C/C.E/etc <ID with dots> [MoreName]
        match = re.search(pattern_f, block_text)
        if match:
            raw_name = match.group(1).strip()
            id_type = match.group(2).strip().rstrip(".")
            if not id_type.endswith("."):
                id_type = id_type + "."
            raw_id = match.group(3).strip()

            # Grab extra name after ID
            after_id = block_text[match.end():]
            extra_parts = []
            for word in after_id.split():
                if word.lower() in _STOP_WORDS or not word[0].isalpha():
                    break
                extra_parts.append(word)

            full_name = raw_name
            if extra_parts:
                full_name = full_name + " " + " ".join(extra_parts)
            name = re.sub(r"\s+", " ", full_name).strip()
            id_number = raw_id.replace(".", "")

            result = {
                "role": "Presidente",
                "name": name.title(),
                "id_type": id_type,
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Presidente (formato F)", i)
            chosen_result = result
            break

        # Try Format G: Presidente <Name> C.C./C.E./P.P. No. <ID> [MoreName]
        match = re.search(pattern_g, block_text)
        if match:
            raw_name = match.group(1).strip()
            id_type = match.group(2).strip()
            id_number = match.group(3).strip()

            after_id = block_text[match.end():]
            extra_parts = []
            for word in after_id.split():
                if word.lower() in _STOP_WORDS or not word[0].isalpha():
                    break
                extra_parts.append(word)

            full_name = raw_name
            if extra_parts:
                full_name = full_name + " " + " ".join(extra_parts)
            name = re.sub(r"\s+", " ", full_name).strip()

            result = {
                "role": "Presidente",
                "name": name,
                "id_type": id_type,
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Presidente (formato G)", i)
            chosen_result = result
            break

        # Try Format H: GERENTE <Name> <ID with dots> (no PRINCIPAL keyword)
        match = re.search(pattern_h, block_text)
        if match:
            raw_name = match.group(1).strip()
            raw_id = match.group(2).strip()

            name = re.sub(r"\s+DESIGNACION\b", "", raw_name)
            after_id = block_text[match.end():]
            extra_parts = []
            for word in after_id.split():
                w = word.strip()
                if w.upper() == "DESIGNACION" or w.lower() in _STOP_WORDS or not w[0].isalpha():
                    break
                extra_parts.append(w)
            if extra_parts:
                name = name + " " + " ".join(extra_parts)

            name = re.sub(r"\s+", " ", name).strip()
            id_number = raw_id.replace(".", "")

            result = {
                "role": "Gerente",
                "name": name.title(),
                "id_type": "C.C.",
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Gerente (formato H)", i)
            chosen_result = result
            break

        # Try Format I: GERENTE\n NAME C.C. ID (Lifecycle style)
        match = re.search(pattern_i, block_text)
        if match:
            raw_name = match.group(1).strip()
            id_number = match.group(2).strip().lstrip("0") or "0"

            name = re.sub(r"\s+", " ", raw_name).strip()

            result = {
                "role": "Gerente",
                "name": name.title(),
                "id_type": "C.C.",
                "id_number": id_number,
            }
            logger.info("Bloque %d: encontrado Gerente (formato I)", i)
            chosen_result = result
            break

    if not chosen_result:
        logger.warning("Ningún bloque matcheó como Representante Legal / Gerente")
        return None

    logger.info("Representante legal extraído: %s | %s %s | rol=%s",
                chosen_result["name"], chosen_result["id_type"],
                chosen_result["id_number"], chosen_result["role"])

    return LegalRepresentative(
        role=chosen_result["role"],
        name=chosen_result["name"],
        id_type=chosen_result["id_type"],
        id_number=chosen_result["id_number"],
    )


class CoRuesService(BaseLookupService):
    async def lookup(self, tax_id: str) -> LookupResponse:
        errors: list[str] = []
        start = time.monotonic()
        logger.info("[NIT=%s] Iniciando lookup RUES", tax_id)

        browser = await get_browser()
        fp = random_fingerprint()
        logger.info("[NIT=%s] Fingerprint: locale=%s tz=%s viewport=%sx%s",
                    tax_id, fp["locale"], fp["timezone_id"],
                    fp["viewport"]["width"], fp["viewport"]["height"])
        context = await browser.new_context(**fp)
        page = await context.new_page()
        timeout = settings.scraping_timeout

        try:
            result = await self._do_lookup(page, tax_id, timeout, errors)
            elapsed = round(time.monotonic() - start, 2)
            logger.info("[NIT=%s] Lookup completado en %ss | found=%s errors=%s",
                        tax_id, elapsed, result.found, result.errors)
            return result
        except PlaywrightTimeout:
            elapsed = round(time.monotonic() - start, 2)
            logger.error("[NIT=%s] TIMEOUT después de %ss", tax_id, elapsed)
            errors.append("Timeout al consultar RUES")
            return self._empty_response(tax_id, errors)
        except Exception as exc:
            elapsed = round(time.monotonic() - start, 2)
            logger.exception("[NIT=%s] ERROR inesperado después de %ss: %s", tax_id, elapsed, exc)
            errors.append("Error inesperado al consultar RUES")
            return self._empty_response(tax_id, errors)
        finally:
            await context.close()

    async def _do_lookup(
        self, page: Page, tax_id: str, timeout: int, errors: list[str]
    ) -> LookupResponse:
        # Step 1: Search results page.
        # A 10-digit no-hyphen value is ambiguous: it can be a 10-digit cédula
        # (persona natural) or a 9-digit NIT base + verification digit. Search the
        # full number first; only if RUES returns nothing, retry with the first 9
        # digits (the NIT-with-DV case). Cédulas are found on the first try.
        search_id = tax_id
        card_count, reachable = await self._search_cards(page, search_id, timeout)

        # Only retry with the NIT base when RUES actually answered. If it never
        # answered, a second search just doubles the outage.
        if reachable and card_count == 0 and len(tax_id) == 10 and tax_id.isdigit():
            fallback_id = tax_id[:9]
            logger.info(
                "[NIT=%s] Sin resultados con id completo; fallback a base %s",
                tax_id, fallback_id,
            )
            fb_count, fb_reachable = await self._search_cards(page, fallback_id, timeout)
            if fb_count > 0:
                search_id, card_count = fallback_id, fb_count
            elif not fb_reachable:
                # The full id answered "nothing", but we could not check the base
                # variant. Report degraded rather than "not registered".
                reachable = False

        if card_count == 0:
            if reachable:
                # RUES answered and has nothing for this identifier. That is a real
                # finding, not a failure: leave `errors` empty so callers can tell
                # "not registered" apart from "could not verify".
                logger.info("[NIT=%s] RUES sin resultados para el identificador", tax_id)
            else:
                logger.warning("[NIT=%s] RUES no respondio (%dms)", tax_id, timeout)
                errors.append(f"RUES no respondio ({timeout}ms)")
            return self._empty_response(tax_id, errors)

        cards = page.locator("div.card-result")
        logger.info("[NIT=%s] Encontrados %d resultados (id buscado=%s)",
                    tax_id, card_count, search_id)

        # Extract legal_name from the search result card
        first_card = cards.first
        name_el = first_card.locator("p.font-rues--large, .filtro__titulo")
        legal_name = None
        if await name_el.count() > 0:
            legal_name = (await name_el.first.inner_text()).strip()
            logger.info("[NIT=%s] Empresa: %s", tax_id, legal_name)

        # Step 2: Click "Ver información" to go to detail page
        logger.info("[NIT=%s] Step 2: Navegando a detalle (Ver información)", tax_id)
        ver_info = first_card.locator("a.text-end.pe-2")
        if await ver_info.count() == 0:
            logger.error("[NIT=%s] No se encontró enlace 'Ver información'", tax_id)
            errors.append("No se encontro enlace 'Ver información'")
            return self._empty_response(tax_id, errors)

        await ver_info.first.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        logger.info("[NIT=%s] Página de detalle cargada: %s", tax_id, page.url)

        # Step 3: Parse "Información general" tab (active by default)
        logger.info("[NIT=%s] Step 3: Parseando Información general", tax_id)
        registration = await self._parse_info_general(page, legal_name, tax_id)

        # Step 4: Resolve the legal representative.
        #   - Empresa (NIT): the representative lives in the "Representante legal" tab.
        #   - Persona natural (cédula): that tab shows "Información no disponible", so
        #     the representative IS the person — take it from "Información general".
        if self._is_persona_natural(registration):
            logger.info("[NIT=%s] Step 4: Persona natural — representante = titular", tax_id)
            legal_rep = self._legal_rep_from_titular(registration, legal_name, search_id)
            legal_rep_raw = None
        else:
            logger.info("[NIT=%s] Step 4: Empresa — parseando Representante legal", tax_id)
            legal_rep, legal_rep_raw = await self._parse_representante_legal(page, tax_id, errors)

        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.CO,
            found=True,
            registration=registration,
            legal_representative=legal_rep,
            legal_representative_raw=legal_rep_raw,
            errors=errors,
        )

    async def _parse_info_general(
        self, page: Page, legal_name: Optional[str], tax_id: str
    ) -> BusinessRegistration:
        """Parse all registroapi fields from the Información general tab."""
        fields: dict[str, str] = {}

        field_divs = page.locator(".tab-pane.active div.registroapi")
        count = await field_divs.count()
        logger.info("[NIT=%s] Información general: %d campos encontrados", tax_id, count)

        for i in range(count):
            div = field_divs.nth(i)
            label_el = div.locator("p.registroapi__etiqueta")
            value_el = div.locator("p.registroapi__valor")

            if await label_el.count() > 0 and await value_el.count() > 0:
                label = (await label_el.first.inner_text()).strip()
                value = (await value_el.first.inner_text()).strip()
                fields[label] = value
                logger.debug("[NIT=%s]   %s = %s", tax_id, label, value)

        return BusinessRegistration(
            legal_name=legal_name,
            tax_id=fields.get("Identificación", tax_id),
            status=_parse_status(fields.get("Estado de la matrícula", "")),
            category=fields.get("Categoria de la Matrícula"),
            society_type=fields.get("Tipo de Sociedad"),
            organization_type=fields.get("Tipo Organización"),
            chamber_of_commerce=fields.get("Cámara de Comercio"),
            matricula_number=fields.get("Número de Matrícula"),
            matricula_date=fields.get("Fecha de Matrícula"),
            validity_date=fields.get("Fecha de Vigencia"),
            renewal_date=fields.get("Fecha de renovación"),
            last_renewal_year=fields.get("Último año renovado"),
            update_date=fields.get("Fecha de Actualización"),
            social_enterprise=fields.get("Emprendimiento Social"),
        )

    async def _parse_representante_legal(
        self, page: Page, tax_id: str, errors: list[str]
    ) -> tuple:
        """Click the Representante legal tab and extract the Gerente.

        Returns (LegalRepresentative or None, raw_text or None).
        """
        rep_tab = page.locator(
            "a[data-rr-ui-event-key='pestana_representante']"
        )
        if await rep_tab.count() == 0:
            logger.warning("[NIT=%s] No se encontró pestaña de Representante legal", tax_id)
            errors.append("No se encontró pestaña de Representante legal")
            return None, None

        await rep_tab.first.click()
        await page.wait_for_timeout(2000)
        logger.info("[NIT=%s] Pestaña Representante legal cargada", tax_id)

        active_pane = page.locator(
            "#detail-tabs-tabpane-pestana_representante"
        )
        if await active_pane.count() == 0:
            active_pane = page.locator(".tab-pane.active")

        if await active_pane.count() == 0:
            logger.error("[NIT=%s] No se encontró contenido de Representante legal", tax_id)
            errors.append("No se encontró contenido de Representante legal")
            return None, None

        text = await active_pane.first.inner_text()
        logger.debug("[NIT=%s] Texto representante legal (%d chars)", tax_id, len(text))

        rep = _parse_legal_rep(text)

        if rep is None:
            logger.warning("[NIT=%s] No se pudo extraer Representante Legal del texto", tax_id)
            errors.append("No se pudo extraer información del Gerente")

        return rep, text

    async def _search_cards(self, page: Page, query: str, timeout: int) -> tuple[int, bool]:
        """Navigate to the RUES search page for `query`.

        Returns `(card_count, reachable)`. `reachable` is False only when RUES never
        answered — the navigation itself timed out. A page that loads fine and simply
        has no matches returns `(0, True)`: that is an answer, not a failure, and the
        caller must not report it as a timeout. Collapsing the two is what made every
        unregistered identifier look like an outage.
        """
        url = RUES_SEARCH_URL.format(nit=query)
        logger.info("[NIT=%s] Step 1: Navegando a %s", query, url)
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout)
        except PlaywrightTimeout:
            logger.warning("[NIT=%s] RUES no respondio la navegacion (%dms)", query, timeout)
            return 0, False

        # goto() already waited for networkidle, so the results are rendered by now.
        # Give the cards a short grace period rather than the full scraping timeout.
        grace = min(settings.empty_result_grace, timeout)
        try:
            await page.wait_for_selector("div.card-result", timeout=grace)
        except PlaywrightTimeout:
            logger.info("[NIT=%s] RUES respondio sin resultados (grace %dms)", query, grace)
            return 0, True

        return await page.locator("div.card-result").count(), True

    @staticmethod
    def _is_persona_natural(registration: BusinessRegistration) -> bool:
        """A persona natural is identified in RUES by a 'CEDULA ...' Identificación
        (de ciudadanía or de extranjería); a company shows a 'NIT ...'."""
        ident = (registration.tax_id or "").upper()
        if "CEDULA" in ident or "CÉDULA" in ident:
            return True
        if "NIT" in ident:
            return False
        # Fallback when the Identificación string is atypical/missing.
        category = (registration.category or "").upper()
        org = (registration.organization_type or "").upper()
        return "PERSONA NATURAL" in category or "PERSONA NATURAL" in org

    def _legal_rep_from_titular(
        self,
        registration: BusinessRegistration,
        legal_name: Optional[str],
        search_id: str,
    ) -> LegalRepresentative:
        """Build the legal representative for a persona natural from the person's
        own identity in 'Información general' (the merchant IS the representative)."""
        ident = registration.tax_id or ""
        id_type = "C.E." if "EXTRANJER" in ident.upper() else "C.C."
        id_number = self._extract_id_number(ident) or search_id
        name = legal_name or registration.legal_name
        logger.info(
            "[NIT=%s] Representante (persona natural): %s | %s %s",
            search_id, name, id_type, id_number,
        )
        return LegalRepresentative(
            role="Representante Legal",
            name=name,
            id_type=id_type,
            id_number=id_number,
        )

    @staticmethod
    def _extract_id_number(identificacion: str) -> Optional[str]:
        """Pull the document number from an Identificación string like
        'CEDULA DE CIUDADANIA 1010183001 - 1' → '1010183001' (drops the DV)."""
        # number + dash + single-digit verification digit at the end
        m = re.search(r"(\d[\d.]*\d|\d)\s*[-–—]\s*\d\s*$", identificacion)
        if m:
            return m.group(1).replace(".", "")
        # no DV separator — take the longest digit run
        runs = re.findall(r"\d[\d.]*\d|\d", identificacion)
        if runs:
            return max((r.replace(".", "") for r in runs), key=len)
        return None

    def _empty_response(self, tax_id: str, errors: list[str]) -> LookupResponse:
        logger.info("[NIT=%s] Retornando respuesta vacía | errors=%s", tax_id, errors)
        return LookupResponse(
            tax_id_input=tax_id,
            country=Country.CO,
            found=False,
            errors=errors,
        )
