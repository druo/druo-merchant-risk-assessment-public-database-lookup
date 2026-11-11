from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import Country, RegistrationStatus


class LookupRequest(BaseModel):
    tax_id: str = Field(..., description="Identificador tributario (NIT para CO, RUC para PE, RUT para CL)")
    country: Country = Field(..., description="Codigo de pais ISO 3166-1 alpha-2")


class BusinessRegistration(BaseModel):
    legal_name: Optional[str] = None
    tax_id: Optional[str] = None
    status: Optional[RegistrationStatus] = None
    # Colombia (RUES)
    category: Optional[str] = None
    society_type: Optional[str] = None
    organization_type: Optional[str] = None
    chamber_of_commerce: Optional[str] = None
    matricula_number: Optional[str] = None
    matricula_date: Optional[str] = None
    validity_date: Optional[str] = None
    renewal_date: Optional[str] = None
    last_renewal_year: Optional[str] = None
    update_date: Optional[str] = None
    social_enterprise: Optional[str] = None
    # Peru (SUNAT)
    commercial_name: Optional[str] = None
    taxpayer_type: Optional[str] = None
    taxpayer_condition: Optional[str] = None
    inscription_date: Optional[str] = None
    activity_start_date: Optional[str] = None
    fiscal_address: Optional[str] = None
    economic_activities: Optional[list[str]] = None
    foreign_trade_activity: Optional[str] = None
    # Chile (SII) — also fills activity_start_date and economic_activities
    termination_date: Optional[str] = None
    termination_reason: Optional[str] = None
    small_company: Optional[str] = None
    foreign_currency_taxpayer: Optional[str] = None
    tax_alerts: Optional[list[str]] = None


class LegalRepresentative(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    since_date: Optional[str] = None


class LookupResponse(BaseModel):
    tax_id_input: str
    country: Country
    found: bool
    registration: Optional[BusinessRegistration] = None
    legal_representative: Optional[LegalRepresentative] = None
    legal_representative_raw: Optional[str] = None
    raw_entries: Optional[list[dict]] = None
    errors: list[str] = Field(default_factory=list)
