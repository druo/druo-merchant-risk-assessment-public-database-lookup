# API Contract — Public Database Lookup

## Endpoint

```
POST /v1/lookup
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

## Request

```json
{
  "tax_id": "string",
  "country": "CO | PE"
}
```

| Campo | Tipo | Requerido | Descripcion |
|-------|------|-----------|-------------|
| `tax_id` | string | Si | Identificador tributario (NIT para CO, RUC para PE) |
| `country` | string | Si | Codigo ISO 3166-1 alpha-2: `CO` o `PE` |

---

## Response — Estructura comun

```json
{
  "tax_id_input": "string",
  "country": "CO | PE",
  "found": true | false,
  "registration": { ... } | null,
  "legal_representative": { ... } | null,
  "legal_representative_raw": "string" | null,
  "raw_entries": [{ ... }] | null,
  "errors": ["string"]
}
```

| Campo | Tipo | Descripcion |
|-------|------|-------------|
| `tax_id_input` | string | El tax_id limpio que se envio a la fuente |
| `country` | string | Pais consultado |
| `found` | boolean | Si se encontro la empresa en la fuente |
| `registration` | object/null | Datos de registro de la empresa |
| `legal_representative` | object/null | Representante legal extraido |
| `legal_representative_raw` | string/null | Texto crudo de la seccion de representante legal |
| `raw_entries` | array/null | Datos crudos adicionales (multiples resultados CO) |
| `errors` | array | Lista de errores (vacio si todo OK) |

---

## Response — Colombia (CO)

### Registro exitoso

```json
{
  "tax_id_input": "901216768",
  "country": "CO",
  "found": true,
  "registration": {
    "legal_name": "ADELANTE SOLUCIONES FINANCIERAS S.A.S.",
    "tax_id": "NIT 901216768 - 4",
    "status": "ACTIVA",
    "category": "Sociedad o persona juridica principal o esal",
    "society_type": "Sociedad comercial",
    "organization_type": "Sociedades por acciones simplificadas sas",
    "chamber_of_commerce": "Bogota",
    "matricula_number": "3017053",
    "matricula_date": "2018/09/25",
    "validity_date": "Indefinido",
    "renewal_date": "2025/03/28",
    "last_renewal_year": "2025",
    "update_date": "2026/02/18",
    "social_enterprise": "N",
    "commercial_name": null,
    "taxpayer_type": null,
    "taxpayer_condition": null,
    "inscription_date": null,
    "activity_start_date": null,
    "fiscal_address": null,
    "economic_activities": null,
    "foreign_trade_activity": null
  },
  "legal_representative": {
    "role": "Representante Legal (Gerente)",
    "name": "Santiago Suarez Vallejo",
    "id_type": "C.C.",
    "id_number": "94552571",
    "since_date": null
  },
  "legal_representative_raw": "Representacion Legal: El gobierno...\nCARGO NOMBRE IDENTIFICACION\nRepresentante Santiago Suarez Vallejo C.C. No. 94552571\nLegal\n(Gerente)\n...",
  "raw_entries": null,
  "errors": []
}
```

### Campos de `registration` para Colombia

| Campo | Tipo | Fuente | Ejemplo |
|-------|------|--------|---------|
| `legal_name` | string | Nombre en tarjeta de resultados RUES | "ADELANTE SOLUCIONES FINANCIERAS S.A.S." |
| `tax_id` | string | Identificacion en detalle RUES | "NIT 901216768 - 4" |
| `status` | enum | Estado de la matricula | "ACTIVA", "INACTIVA", "CANCELADA", "DESCONOCIDO" |
| `category` | string | Categoria de la Matricula | "Sociedad o persona juridica principal o esal" |
| `society_type` | string | Tipo de Sociedad | "Sociedad comercial" |
| `organization_type` | string | Tipo Organizacion | "Sociedades por acciones simplificadas sas" |
| `chamber_of_commerce` | string | Camara de Comercio | "Bogota" |
| `matricula_number` | string | Numero de Matricula | "3017053" |
| `matricula_date` | string | Fecha de Matricula | "2018/09/25" |
| `validity_date` | string | Fecha de Vigencia | "Indefinido" |
| `renewal_date` | string | Fecha de renovacion | "2025/03/28" |
| `last_renewal_year` | string | Ultimo ano renovado | "2025" |
| `update_date` | string | Fecha de Actualizacion | "2026/02/18" |
| `social_enterprise` | string | Emprendimiento Social | "N" o "S" |

### Roles posibles de `legal_representative` para Colombia

| Rol | Descripcion |
|-----|-------------|
| `Representante Legal (Gerente)` | Formato A — con sub-rol Gerente |
| `Representante Legal` | Formato A — sin sub-rol |
| `Gerente Principal` | Formato B — tabla columnar |
| `Representante Legal Principal` | Formato D — REPRESENTANTE LEGAL PRINCIPAL |
| `Gerente General` | Formato E — Gerente General |
| `Presidente` | Formato F/G — empresas con Presidente |
| `Gerente` | Formato H — solo Gerente |

### Encontrado sin representante legal (CO)

Cuando la empresa se encuentra en RUES pero no se pudo extraer el representante legal.
El campo `legal_representative_raw` contiene el texto crudo para analisis externo (ej. por un LLM).

```json
{
  "tax_id_input": "890903938",
  "country": "CO",
  "found": true,
  "registration": {
    "legal_name": "BANCOLOMBIA S.A.",
    "tax_id": "NIT 890903938 - 8",
    "status": "ACTIVA",
    "category": "Sociedad o persona juridica principal o esal",
    "society_type": "Sociedad comercial",
    "organization_type": "Sociedades por acciones simplificadas sas",
    "chamber_of_commerce": "Medellin para antioquia",
    "matricula_number": "1234567",
    "matricula_date": "1945/01/01",
    "validity_date": "Indefinido",
    "renewal_date": "2025/03/28",
    "last_renewal_year": "2025",
    "update_date": "2026/01/15",
    "social_enterprise": "N",
    "commercial_name": null,
    "taxpayer_type": null,
    "taxpayer_condition": null,
    "inscription_date": null,
    "activity_start_date": null,
    "fiscal_address": null,
    "economic_activities": null,
    "foreign_trade_activity": null
  },
  "legal_representative": null,
  "legal_representative_raw": "CERTIFICA\n\nLa Representacion Legal de la compania...\nCARGO NOMBRE IDENTIFICACION\nPRESIDENTE ...",
  "raw_entries": null,
  "errors": ["No se pudo extraer informacion del Gerente"]
}
```

> **Nota:** Cuando `legal_representative` es `null` pero `found` es `true`, el campo `legal_representative_raw` contiene el texto original de RUES. Este texto puede ser procesado externamente (por ejemplo, por un LLM) para extraer la informacion que el regex no pudo parsear.

### No encontrado (CO)

RUES respondio y no tiene el identificador. `errors` viene **vacio**: es un hallazgo
real del registro, no una falla de la consulta.

```json
{
  "tax_id_input": "123456789",
  "country": "CO",
  "found": false,
  "registration": null,
  "legal_representative": null,
  "legal_representative_raw": null,
  "raw_entries": null,
  "errors": []
}
```

### No se pudo consultar (CO)

RUES no respondio. `found` tambien es `false`, pero `errors` trae el motivo.

```json
{
  "tax_id_input": "123456789",
  "country": "CO",
  "found": false,
  "registration": null,
  "legal_representative": null,
  "legal_representative_raw": null,
  "raw_entries": null,
  "errors": ["RUES no respondio (45000ms)"]
}
```

> **Importante para el consumidor:** `found: false` por si solo NO significa que el
> negocio no exista. Hay que mirar `errors`: vacio = el registro contesto y no lo
> tiene; con contenido = no se pudo verificar. Puntuar los dos casos igual castiga a
> un merchant legitimo cada vez que la fuente falla.

### Errores posibles (CO)

| Error | Causa |
|-------|-------|
| `RUES no respondio (45000ms)` | La pagina de busqueda no cargo |
| `Timeout al consultar RUES` | Error general de timeout |
| `No se encontro enlace 'Ver informacion'` | Tarjeta sin enlace de detalle |
| `No se encontro pestana de Representante legal` | Empresa sin esa pestana en RUES |
| `No se encontro contenido de Representante legal` | Pestana vacia |
| `No se pudo extraer informacion del Gerente` | Patron de texto no reconocido |
| `Error inesperado al consultar RUES` | Error no controlado |

---

## Response — Peru (PE)

### Registro exitoso

```json
{
  "tax_id_input": "20100055237",
  "country": "PE",
  "found": true,
  "registration": {
    "legal_name": "ALICORP SAA",
    "tax_id": "20100055237",
    "status": "ACTIVA",
    "category": null,
    "society_type": null,
    "organization_type": null,
    "chamber_of_commerce": null,
    "matricula_number": null,
    "matricula_date": null,
    "validity_date": null,
    "renewal_date": null,
    "last_renewal_year": null,
    "update_date": null,
    "social_enterprise": null,
    "commercial_name": "ALICORP",
    "taxpayer_type": "SOCIEDAD ANONIMA ABIERTA",
    "taxpayer_condition": "HABIDO",
    "inscription_date": "09/10/1992",
    "activity_start_date": "16/07/1956",
    "fiscal_address": "AV. ARGENTINA NRO. 4793 URB. PARQUE INDUSTRIAL...",
    "economic_activities": [
      "Principal - 1040 - ELABORACION DE ACEITES Y GRASAS DE ORIGEN VEGETAL Y ANIMAL",
      "Secundaria 1 - 1061 - ELABORACION DE PRODUCTOS DE MOLINERIA",
      "Secundaria 2 - 4690 - VENTA AL POR MAYOR NO ESPECIALIZADA"
    ],
    "foreign_trade_activity": "IMPORTADOR/EXPORTADOR"
  },
  "legal_representative": {
    "role": "GERENTE GENERAL",
    "name": "Uribe Arbelaez Gonzalo",
    "id_type": "CE",
    "id_number": "000594167",
    "since_date": "01/11/2025"
  },
  "legal_representative_raw": "Documento\tNro. Documento\tNombre\tCargo\tFecha Desde\nDNI\t06934557\tPAUCAR FLORES...",
  "raw_entries": null,
  "errors": []
}
```

### Campos de `registration` para Peru

| Campo | Tipo | Fuente | Ejemplo |
|-------|------|--------|---------|
| `legal_name` | string | Razon social (del RUC) | "ALICORP SAA" |
| `tax_id` | string | Numero de RUC | "20100055237" |
| `status` | enum | Estado del Contribuyente | "ACTIVA", "INACTIVA", "DESCONOCIDO" |
| `commercial_name` | string | Nombre Comercial | "ALICORP" |
| `taxpayer_type` | string | Tipo Contribuyente | "SOCIEDAD ANONIMA ABIERTA" |
| `taxpayer_condition` | string | Condicion del Contribuyente | "HABIDO", "NO HABIDO" |
| `inscription_date` | string | Fecha de Inscripcion | "09/10/1992" |
| `activity_start_date` | string | Fecha de Inicio de Actividades | "16/07/1956" |
| `fiscal_address` | string | Domicilio Fiscal | "AV. ARGENTINA NRO. 4793..." |
| `economic_activities` | array | Actividad(es) Economica(s) | ["Principal - 1040 - ..."] |
| `foreign_trade_activity` | string | Actividad Comercio Exterior | "IMPORTADOR/EXPORTADOR" |

### Tipos de documento en `legal_representative` para Peru

| id_type | Descripcion |
|---------|-------------|
| `DNI` | Documento Nacional de Identidad |
| `CE` | Carnet de Extranjeria |
| `PASAPORTE` | Pasaporte |

### Prioridad de seleccion de representante legal (PE)

1. **GERENTE GENERAL** — se busca primero
2. **GERENTE** — si no hay GERENTE GENERAL
3. **Primer registro** — si no hay ninguno de los anteriores

### No encontrado (PE)

```json
{
  "tax_id_input": "20000000001",
  "country": "PE",
  "found": false,
  "registration": null,
  "legal_representative": null,
  "legal_representative_raw": null,
  "raw_entries": null,
  "errors": ["RUC no valido o no existe segun SUNAT"]
}
```

### Errores posibles (PE)

| Error | Causa |
|-------|-------|
| `Timeout esperando resultados de SUNAT (45000ms)` | SUNAT no respondio a tiempo |
| `Timeout al consultar SUNAT` | Error general de timeout |
| `RUC no valido o no existe segun SUNAT` | RUC invalido o inexistente |
| `No se encontro boton Representante(s) Legal(es)` | Boton no disponible |
| `No se encontro tabla de representantes legales` | Tabla no cargo |
| `No se encontraron representantes legales en la tabla` | Tabla vacia |
| `Error inesperado al consultar SUNAT` | Error no controlado |

---

## Diferencias clave entre CO y PE

| Aspecto | Colombia (CO) | Peru (PE) |
|---------|---------------|-----------|
| Fuente | RUES (rues.org.co) | SUNAT (sunat.gob.pe) |
| Identificador | NIT (6-9 digitos) | RUC (11 digitos) |
| Campos de registro | Matricula, Camara Comercio, etc. | Tipo contribuyente, Condicion, Domicilio, etc. |
| Rep. legal — fuente | Texto libre (multiples formatos) | Tabla estructurada |
| Rep. legal — `since_date` | No disponible (null) | Disponible |
| Rep. legal — roles | Gerente, Presidente, Rep. Legal | GERENTE GENERAL, GERENTE, APODERADO |
| Campos null | Campos PE son null | Campos CO son null |

---

## Health Check

```
GET /healthz
```

```json
{
  "status": "ok"
}
```

## Autenticacion

Todos los requests a `/v1/lookup` requieren header:
```
Authorization: Bearer <API_KEY>
```

| Status | Causa |
|--------|-------|
| 401 | API key invalida |
| 403 | Sin header Authorization |
| 422 | NIT/RUC invalido o pais no soportado |
| 429 | Rate limit excedido (10/min por IP) |

## Rate Limiting

- 10 requests por minuto por IP en `/v1/lookup`
- Configurable via variable de entorno `RATE_LIMIT`
