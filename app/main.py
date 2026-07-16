"""FastAPI application — Public Database Lookup Service.

Endpoints:
  GET  /healthz     → {"status": "ok"}
  POST /v1/lookup   → LookupResponse
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.models.enums import Country
from app.models.schemas import LookupRequest, LookupResponse
from app.services.browser import get_browser, shutdown_browser
from app.services.co_rues_service import CoRuesService
from app.services.nit_validator import validate_nit
from app.services.pe_sunat_service import PeSunatService
from app.services.ruc_validator import validate_ruc

# ---------------------------------------------------------------------------
# Version — single source of truth: VERSION file at project root
# ---------------------------------------------------------------------------
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth — Bearer token validated against API_KEY env var
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> None:
    if not settings.api_key:
        raise HTTPException(
            status_code=500,
            detail="API_KEY not configured on server",
        )
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Responses — always declare the charset
# ---------------------------------------------------------------------------
class UTF8JSONResponse(JSONResponse):
    """JSON response that declares `charset=utf-8` in its Content-Type.

    JSON is UTF-8 by RFC 8259 and Starlette therefore omits the charset param,
    but clients that sniff the encoding instead of assuming it can guess wrong:
    the RUES text we return pads its tables with hundreds of NBSP (bytes C2 A0),
    which reads as a strong GBK signal, and n8n's HTTP node decoded a response
    as GB18030 — turning "máxima" into "m谩xima". Declaring the charset removes
    the ambiguity for every consumer.
    """

    media_type = "application/json; charset=utf-8"


# ---------------------------------------------------------------------------
# Rate limiting — keyed by client IP
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)


def _rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    return UTF8JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


# ---------------------------------------------------------------------------
# Lifespan — warm-up / tear-down the shared browser
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting up: initializing Playwright browser...")
    try:
        await get_browser()
        logger.info("Browser ready.")
    except Exception:
        logger.exception("Failed to initialize browser at startup — will retry on first request")
    yield
    logger.info("Shutting down: closing browser...")
    await shutdown_browser()
    logger.info("Browser closed.")


# ---------------------------------------------------------------------------
# Service dispatch — one service per country
# ---------------------------------------------------------------------------
_services = {
    Country.CO: CoRuesService(),
    Country.PE: PeSunatService(),
}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Merchant Precheck Public Database Lookup",
    description="Consulta bases de datos publicas oficiales de empresas por identificador tributario.",
    version=__version__,
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/healthz")
async def healthz():
    """Liveness / readiness probe."""
    return {"status": "ok"}


@app.post(
    "/v1/lookup",
    response_model=LookupResponse,
    dependencies=[Depends(verify_api_key)],
)
@limiter.limit(settings.rate_limit)
async def lookup(request: Request, body: LookupRequest = Body(...)):
    """Consulta una base de datos publica oficial por identificador tributario."""
    logger.info("Lookup request | tax_id=%s country=%s", body.tax_id, body.country.value)

    # Validate tax ID based on country
    if body.country == Country.CO:
        cleaned, validation_errors = validate_nit(body.tax_id)
        if validation_errors:
            raise HTTPException(
                status_code=422,
                detail=f"NIT invalido: {'; '.join(validation_errors)}",
            )
        tax_id = cleaned
    elif body.country == Country.PE:
        cleaned, validation_errors = validate_ruc(body.tax_id)
        if validation_errors:
            raise HTTPException(
                status_code=422,
                detail=f"RUC invalido: {'; '.join(validation_errors)}",
            )
        tax_id = cleaned
    else:
        raise HTTPException(status_code=422, detail=f"Pais no soportado: {body.country.value}")

    service = _services.get(body.country)
    if not service:
        raise HTTPException(status_code=422, detail=f"Pais no soportado: {body.country.value}")

    result = await service.lookup(tax_id)
    logger.info(
        "Lookup complete | tax_id=%s found=%s errors=%s",
        body.tax_id,
        result.found,
        result.errors,
    )
    return result


# ---------------------------------------------------------------------------
# Standalone runner (python -m app.main)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
