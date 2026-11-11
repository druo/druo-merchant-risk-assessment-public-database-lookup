# Merchant Precheck Public Database Lookup

Microservicio REST que consulta bases de datos publicas oficiales de empresas dado un identificador tributario (NIT para Colombia, RUC para Peru, RUT para Chile) y retorna un JSON estructurado con la informacion de la empresa. El contrato completo por pais esta en `API_CONTRACT.md`.

## Paises soportados

| Pais | Identificador | Fuente oficial |
|------|---------------|----------------|
| Colombia | NIT o cedula | RUES (Registro Unico Empresarial y Social) |
| Peru | RUC | SUNAT (Consulta RUC) |
| Chile | RUT | SII (Consultar Situacion Tributaria de Terceros) |

## Inicio rapido

```bash
# Instalar dependencias
pip install -r requirements.txt
playwright install --with-deps chromium

# Correr servidor
API_KEY=test uvicorn app.main:app --reload

# Correr tests
pytest tests/ -v
```

## API

### `GET /healthz`

Liveness probe.

```json
{"status": "ok"}
```

### `POST /v1/lookup`

Requiere header `Authorization: Bearer <API_KEY>`.

**Request:**

```json
{
  "tax_id": "900123456",
  "country": "CO"
}
```

**Response:**

```json
{
  "tax_id_input": "900123456",
  "country": "CO",
  "found": true,
  "registration": {
    "business_name": "EMPRESA EJEMPLO S.A.S.",
    "tax_id": "900123456",
    "status": "ACTIVA",
    "registration_date": "2010-01-15",
    "society_type": "SOCIEDAD POR ACCIONES SIMPLIFICADA",
    "ciiu_activities": ["6201 - Actividades de desarrollo de sistemas informaticos"]
  },
  "location": {
    "address": "CL 100 # 19-61 OF 801",
    "city": "BOGOTA D.C.",
    "department": "BOGOTA D.C."
  },
  "source": {
    "name": "RUES - Registro Unico Empresarial y Social",
    "url": "https://www.rues.org.co/RM",
    "chamber_of_commerce": "CAMARA DE COMERCIO DE BOGOTA",
    "queried_at": "2026-03-14T12:00:00Z"
  },
  "raw_entries": null,
  "errors": []
}
```

## Despliegue

Disenado para desplegarse en **Railway** usando Docker (Dockerfile incluido con Playwright + Chromium).

```bash
# Docker local
docker build -t public-db-lookup .
docker run -p 8000:8000 -e API_KEY=secret public-db-lookup
```

## Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `API_KEY` | `""` | Bearer token para autenticacion |
| `PORT` | `8000` | Puerto del servidor |
| `BROWSER_HEADLESS` | `true` | Modo headless de Chromium |
| `SCRAPING_TIMEOUT` | `30000` | Timeout de scraping (ms) |
| `RATE_LIMIT` | `10/minute` | Limite de requests por IP |
