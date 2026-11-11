# CLAUDE.md — Merchant Precheck Public Database Lookup

## Que es este proyecto

Microservicio REST (FastAPI + Playwright) que consulta los registros publicos oficiales de empresas dado un identificador tributario y retorna un JSON estructurado con la informacion de la empresa y su representante legal:

- **Colombia (CO):** NIT o cedula contra el RUES (Registro Unico Empresarial y Social).
- **Peru (PE):** RUC contra la Consulta RUC de SUNAT.
- **Chile (CL):** RUT contra "Consultar Situacion Tributaria de Terceros" del SII (sin representante legal: el SII no lo publica).

Las tres fuentes son paginas web sin API publica, asi que se consultan con Chromium headless via Playwright. El contrato completo de request/response por pais esta en `API_CONTRACT.md`.

## Tech Stack

- Python 3.12+ / FastAPI / Uvicorn
- Playwright (Chromium headless) para RUES, SUNAT y SII
- Pydantic v2 (modelos + settings)
- slowapi (rate limiting)
- Docker (python:3.12-bookworm + Playwright)
- pytest (tests)

## Estructura del proyecto

```
app/
  main.py                  # FastAPI app, endpoints, auth (Bearer token), lifespan (browser singleton), validacion por pais
  config.py                # Configuracion via env vars (Pydantic Settings)
  models/
    enums.py               # Country (CO, PE, CL), RegistrationStatus
    schemas.py             # LookupRequest, LookupResponse, sub-modelos
  services/
    base.py                # ABC BaseLookupService
    browser.py             # Playwright browser singleton (lifespan)
    fingerprint.py         # Fingerprint aleatorio por consulta (user agent, viewport, locale, timezone)
    nit_validator.py       # Validacion NIT/cedula + digito de verificacion (CO)
    ruc_validator.py       # Validacion RUC: 11 digitos, prefijo 10/15/17/20 (PE)
    rut_validator.py       # Validacion RUT: 6-8 digitos + DV modulo 11 (0-9/K) (CL)
    co_rues_service.py     # Scraping RUES (CO)
    pe_sunat_service.py    # Scraping SUNAT (PE)
    cl_sii_service.py      # Consulta SII: llena el formulario y lee el JSON de getConsultaData (CL)
tests/
  test_nit_validator.py          # Validador de NIT/cedula
  test_rues_empty_vs_timeout.py  # "RUES sin resultados" vs "RUES no respondio"
  test_response_charset.py       # Content-Type con charset=utf-8
  test_rut_validator.py          # Validador de RUT
  test_cl_sii_service.py         # Parseo del JSON del SII y flujo (sala de espera, timeouts, captcha)
  test_lookup_endpoint_cl.py     # Endpoint con country CL: 422 por RUT invalido y ruteo al servicio
  fixtures/sii/                  # Respuestas reales del SII (activa, disuelta, sin inicio, termino de giro)
  test_co_companies.py           # Script batch (no pytest) contra el servidor local: python3 tests/test_co_companies.py
  REPORT_CO_TEST.md              # Resultado de la corrida contra empresas CO reales
API_CONTRACT.md            # Contrato de request/response, errores posibles por pais
VERSION                    # Version SemVer (single source of truth)
postman_collection.json    # Coleccion de Postman para probar el endpoint
Dockerfile                 # Imagen con Chromium para Railway
Procfile                   # Comando de inicio
railway.toml               # Deploy en Railway (Dockerfile builder, healthcheck /healthz)
requirements.txt           # Dependencias Python
CONTRIBUTING.md            # Guia de contribucion, gitflow, conventional commits
```

## Endpoints

| Metodo | Ruta | Auth | Descripcion |
|--------|------|------|-------------|
| GET | `/healthz` | No | Liveness probe |
| POST | `/v1/lookup` | Bearer token | Consulta de empresa por `tax_id` + `country` (`CO`, `PE` o `CL`) |

Un `tax_id` con formato invalido responde `422` antes de abrir el navegador (`NIT invalido: ...` / `RUC invalido: ...` / `RUT invalido: ...`).

## Variables de entorno

| Variable | Default | Requerida | Descripcion |
|----------|---------|-----------|-------------|
| `API_KEY` | `""` | Si (prod) | Bearer token para auth. Sin valor, `/v1/lookup` responde 500 |
| `PORT` | `8000` | No | Puerto del servidor (Railway lo inyecta automaticamente) |
| `HOST` | `0.0.0.0` | No | Direccion de bind |
| `LOG_LEVEL` | `info` | No | Nivel de logging |
| `RATE_LIMIT` | `10/minute` | No | Limite de requests por IP |
| `BROWSER_HEADLESS` | `true` | No | Modo headless de Chromium |
| `SCRAPING_TIMEOUT` | `45000` | No | Timeout de scraping en milisegundos (RUES, SUNAT y SII) |
| `EMPTY_RESULT_GRACE` | `8000` | No | Espera por tarjetas de resultado despues de que la pagina ya cargo. Agotarla significa "el registro no tiene el identificador", no timeout (solo RUES) |
| `SII_QUEUE_TIMEOUT` | `120000` | No | Cuanto esperar en la sala de espera de Queue-it del SII a que aparezca el formulario (solo CL) |

## Paises soportados

| Pais | Identificador | Fuente oficial | Validador | Servicio |
|------|---------------|----------------|-----------|----------|
| Colombia | NIT (con o sin DV) o cedula de 6 a 10 digitos | RUES (`rues.org.co`) | `nit_validator.py` | `co_rues_service.py` |
| Peru | RUC de 11 digitos | SUNAT (`e-consultaruc.sunat.gob.pe`) | `ruc_validator.py` | `pe_sunat_service.py` |
| Chile | RUT de 6 a 8 digitos + DV (0-9 o K), con o sin puntos y guion | SII (`www2.sii.cl/stc/noauthz`) | `rut_validator.py` | `cl_sii_service.py` |

## Comportamiento por pais

### Colombia (RUES)

- **10 digitos sin guion es ambiguo:** puede ser una cedula de 10 digitos o un NIT de 9 con el DV pegado. El validador no recorta nada; el servicio busca el numero completo y, solo si RUES contesto sin resultados, reintenta con los primeros 9 digitos.
- **Persona natural (cedula):** el representante legal se arma con el titular de "Informacion general", porque la pestana "Representante legal" sale vacia. Las empresas (NIT) siguen leyendo esa pestana.
- **"Sin resultados" no es lo mismo que "no respondio":** si RUES contesta y no tiene el identificador, `found: false` con `errors: []`. Si RUES no responde, `found: false` con `errors: ["RUES no respondio (45000ms)"]`. El consumidor distingue por `errors`, no por `found`.

### Peru (SUNAT)

- Flujo: Consulta RUC, tabla de la empresa y luego el boton "Representante(s) Legal(es)".
- Representante legal por prioridad: GERENTE GENERAL, luego GERENTE, luego la primera fila de la tabla.
- SUNAT detecta navegadores headless: el servicio borra el flag `navigator.webdriver` en cada pagina.
- A diferencia de CO, un RUC que no existe responde `found: false` con `errors: ["RUC no valido o no existe segun SUNAT"]`, asi que en PE `errors` no separa "no existe" de "no se pudo consultar".

### Chile (SII)

- **Sala de espera:** con mucho trafico el SII manda primero a Queue-it (`salaespera.sii.cl`, la barra con una persona caminando). El servicio navega con `domcontentloaded` (no `networkidle`: la sala de espera nunca deja de hacer polling) y espera el formulario hasta `SII_QUEUE_TIMEOUT`.
- **Se lee el JSON, no el HTML:** el servicio llena el RUT, hace clic en Consultar y captura la respuesta de `POST /app/stc/recurso/v1/consulta/getConsultaData/`. El JSON completo va en `raw_entries`.
- **reCAPTCHA:** el SII lo tiene configurado pero hoy viene apagado (`/app/stc/recurso/v1/recaptcha/key` responde `enable: false`). Si lo prenden, el JS de la pagina genera el token; si el SII lo rechaza, el error es `SII rechazo el captcha`.
- **El SII nunca dice "no existe":** un RUT desconocido vuelve con `nombre: "**"` y la observacion "Rut consultado sin iniciacion de actividades". Por eso `found: true` solo si el SII muestra un nombre, y con `found: false` el `errors` queda vacio (igual que CO).
- **Status:** `CANCELADA` si la sociedad esta disuelta (`tiene8102`, "Sociedad disuelta por absorcion") o tiene termino de giro (`conTegi`, avisos grupo 020/021); `ACTIVA` con inicio de actividades; `INACTIVA` con nombre pero sin inicio ni termino de giro. Una sociedad disuelta conserva su nombre: existe, pero no esta activa.
- **Avisos:** la "Recomendacion General" (grupo 025) sale para todo RUT y se descarta de `tax_alerts`.
- **Sin representante legal:** `legal_representative` y `legal_representative_raw` siempre `null`, sin error.

## Extensibilidad

Agregar un nuevo pais requiere:
1. Crear `app/services/<pais>_<fuente>_service.py` implementando `BaseLookupService`
2. Crear el validador de su identificador tributario (como `ruc_validator.py`)
3. Agregar el pais al enum `Country`
4. Agregar la validacion en el endpoint y la entrada al dict `_services` en `main.py`
5. Documentar su respuesta y sus errores en `API_CONTRACT.md`

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
- Campos opcionales con `None` cuando el dato no esta disponible; los campos de `registration` del otro pais siempre vienen `null`
- `errors` siempre es un array (vacio `[]` si no hay errores)
- Browser singleton via lifespan de FastAPI; cada consulta abre su propio context con un fingerprint aleatorio
- Timeout de 45s para scraping (`SCRAPING_TIMEOUT`)
- Toda respuesta declara `Content-Type: application/json; charset=utf-8`. Sin el charset, el nodo HTTP de n8n decodificaba como GB18030 por los NBSP del texto de RUES ("máxima" salia "m谩xima")
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
- Formato: `MAJOR.MINOR.PATCH` (ej. `0.1.2`).

### Conventional Commits

Formato: `<tipo>: <descripcion>` — tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`.

Ver `CONTRIBUTING.md` para detalles completos.

## Git

- Branch principal: `main`, integracion: `develop`
- Este proyecto es un repo git independiente en `~/Documents/repos/merchant-precheck-public-database-lookup/`
- Remote: `https://github.com/druo/druo-merchant-risk-assessment-public-database-lookup.git`
