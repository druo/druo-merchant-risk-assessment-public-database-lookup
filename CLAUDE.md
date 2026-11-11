# CLAUDE.md — Merchant Precheck Public Database Lookup

## Que es este proyecto

Microservicio REST (FastAPI + Playwright) que consulta bases de datos publicas oficiales de empresas dado un identificador tributario (NIT para Colombia) y retorna un JSON estructurado con la informacion de la empresa. Hace web scraping al RUES (Registro Unico Empresarial y Social) ya que es una SPA que requiere renderizado con Playwright.

## Tech Stack

- Python 3.12+ / FastAPI / Uvicorn
- Playwright (Chromium headless) para scraping de RUES
- Pydantic v2 (modelos + settings)
- slowapi (rate limiting)
- Docker (python:3.12-bookworm + Playwright)
- pytest (tests)

## Estructura del proyecto

```
app/
  main.py                  # FastAPI app, endpoints, auth (Bearer token), lifespan (browser singleton)
  config.py                # Configuracion via env vars (Pydantic Settings)
  models/
    enums.py               # Country, RegistrationStatus
    schemas.py             # LookupRequest, LookupResponse, sub-modelos
  services/
    base.py                # ABC BaseLookupService
    browser.py             # Playwright browser singleton (lifespan)
    nit_validator.py       # Validacion formato NIT + digito de verificacion
    co_rues_service.py     # Scraping RUES con Playwright
tests/
  test_nit_validator.py    # Tests del validador de NIT
Dockerfile                 # Imagen con Chromium para Railway
Procfile                   # Comando de inicio para Railway
railway.toml               # Configuracion de deploy en Railway (Dockerfile builder)
requirements.txt           # Dependencias Python
CONTRIBUTING.md            # Guia de contribucion, gitflow, conventional commits
```

## Endpoints

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/healthz` | No | Liveness probe |
| POST | `/v1/lookup` | Bearer token | Consulta de empresa por NIT + pais |

## Variables de entorno

| Variable | Default | Requerida | Descripcion |
|----------|---------|-----------|-------------|
| `API_KEY` | `""` | Si (prod) | Bearer token para auth |
| `PORT` | `8000` | No | Puerto del servidor (Railway lo inyecta automaticamente) |
| `HOST` | `0.0.0.0` | No | Direccion de bind |
| `LOG_LEVEL` | `info` | No | Nivel de logging |
| `RATE_LIMIT` | `10/minute` | No | Limite de requests por IP |
| `BROWSER_HEADLESS` | `true` | No | Modo headless de Chromium |
| `SCRAPING_TIMEOUT` | `45000` | No | Timeout de scraping en milisegundos |
| `EMPTY_RESULT_GRACE` | `8000` | No | Espera por tarjetas de resultado despues de que la pagina ya cargo. Agotarla significa "el registro no tiene el identificador", no timeout |

## Paises soportados

| Pais | Identificador | Fuente oficial | Servicio |
|------|---------------|----------------|----------|
| Colombia | NIT | RUES | `co_rues_service.py` |

## Extensibilidad

Agregar un nuevo pais requiere:
1. Crear `app/services/<country>_service.py` implementando `BaseLookupService`
2. Crear validador de ID tributario (ej. `ruc_validator.py`)
3. Agregar el pais al enum `Country`
4. Agregar entrada al dict `_services` en `main.py`

## Comandos

```bash
# Instalar dependencias
pip install -r requirements.txt
playwright install --with-deps chromium

# Correr servidor
API_KEY=test uvicorn app.main:app --reload

# Correr tests
pytest tests/ -v

# Docker
docker build -t public-db-lookup .
docker run -p 8000:8000 -e API_KEY=secret public-db-lookup
```

## Convenciones de codigo

- Modelos Pydantic para request/response (contrato JSON estricto)
- Campos opcionales con `None` cuando el dato no esta disponible
- `errors` siempre es un array (vacio `[]` si no hay errores)
- Browser singleton via lifespan de FastAPI
- Timeout de 30s para scraping RUES
- Rate limiting: 10 requests/minuto por IP en `/v1/lookup`

## Gitflow y versionamiento

### Branching (Gitflow simplificado)

```
main ← produccion, cada merge crea release automatico
  └── develop ← integracion de features
       └── feature/* ← desarrollo
       └── fix/* ← correcciones
```

- **PRs siempre**: `feature/*` → `develop` → `main`. Nunca push directo a `main` o `develop`.

### Versionamiento (SemVer)

- Archivo `VERSION` en la raiz es el single source of truth.
- `app/main.py` lee la version de `VERSION` automaticamente.
- Formato: `MAJOR.MINOR.PATCH` (ej. `0.1.0`).

### Conventional Commits

Formato: `<tipo>: <descripcion>` — tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`.

Ver `CONTRIBUTING.md` para detalles completos.

## Git

- Branch principal: `main`, integracion: `develop`
- Este proyecto es un repo git independiente dentro de `agents-hub/projects/`
- Remote: `https://github.com/katerinearias-druo/merchant-precheck-public-database-lookup.git`
