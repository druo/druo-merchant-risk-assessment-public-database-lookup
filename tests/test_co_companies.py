"""Batch test: Colombian companies — legal representative extraction.
NITs verified from veritradecorp.com, registronit.com and other public sources.
Run: API_KEY=<the key the local server was started with> python3 tests/test_co_companies.py
"""

import json
import os
import subprocess
import sys
import time

API_URL = "http://localhost:8000/v1/lookup"
API_KEY = os.environ.get("API_KEY", "")

# NITs confirmados de fuentes publicas (veritrade, registronit, informacolombia)
COMPANIES = [
    ("899999068", "Ecopetrol"),
    ("890903938", "Bancolombia"),
    ("860034313", "Davivienda"),
    ("890903939", "Postobon"),
    ("860025900", "Alpina"),
    ("860005224", "Bavaria"),
    ("890100251", "Cementos Argos"),
    ("830095213", "Organizacion Terpel"),
    ("860500480", "Almacenes Corona"),
    ("890900608", "Almacenes Exito"),
    ("900017447", "Falabella de Colombia"),
    ("890921974", "Alkosto / Corbeta"),
    ("890900076", "Cine Colombia"),
    ("890107487", "Olimpica"),
    ("890100577", "Avianca"),
    ("860002130", "Nestle de Colombia"),
    ("890300546", "Colgate Palmolive"),
    ("830002366", "Bimbo de Colombia"),
    ("860512249", "Yanbal de Colombia"),
    ("890301884", "Colombina"),
    ("890105526", "Promigas"),
    ("811030322", "Celsia"),
    ("890900308", "Fabricato"),
    ("800153993", "Comcel / Claro"),
    ("830114921", "Colombia Movil / Tigo"),
    ("811019012", "Suramericana"),
    ("890903407", "Seguros Generales Suramericana"),
    ("900341086", "Comercial Nutresa"),
    ("830063506", "Transmilenio"),
    ("900103877", "Kinco"),
    ("890504493", "Mussi Zapatos"),
    ("901216768", "Adelante Soluciones Financieras"),
    ("900926147", "Grupo Quincena"),
    ("901987605", "Aihuevos"),
    ("900539730", "Carvajal S.A.S"),
    ("900276962", "Koba Colombia / D1"),
    ("860004871", "Curacao de Colombia"),
    ("890900943", "Colombiana de Comercio"),
    ("800088702", "EPS Suramericana"),
    ("890903790", "Seguros de Vida Suramericana"),
    ("900092385", "UNE EPM Telecomunicaciones"),
    ("830047819", "Coca-Cola Servicios Colombia"),
    ("860006764", "Provincia Ntra Sra Gracia"),
    ("890935493", "Mapei Colombia"),
    ("900047981", "Banco Falabella"),
    ("860068182", "Credicorp Capital Colombia"),
    ("800250328", "Transejes Colombia"),
    ("900169804", "Fermax Electronica Colombia"),
    ("900494355", "Supertechos Colombia"),
    ("830095262", "Qmax Solutions Colombia"),
    ("900154405", "Global Sales Solutions Colombia"),
    ("900158160", "Comercializadora Nacional Colombia"),
    ("800209179", "Colombiana de Encomiendas"),
    ("900284878", "Importadora Comercializadora Tecnologia"),
    ("800216181", "Grupo Aval Acciones y Valores"),
    ("811036875", "Servicios Generales Suramericana"),
    ("900943243", "Suramerica Comercial"),
    ("807009444", "Cooperativa Suramericana Transportes"),
    ("800182390", "Suramericana de Guantes"),
    ("900058954", "Suramericana de Partes"),
    ("900433032", "Terpel Combustibles"),
    ("890301163", "Distribuidora Colombina"),
    ("860059294", "Leasing Bancolombia"),
    ("800095254", "American Airlines Sucursal Colombia"),
    ("900102022", "Lifecycle Solutions Colombia"),
    ("830130106", "Soenergy International Colombia"),
    ("901125212", "Seisa Technologies"),
    ("900330855", "Laboratorios OSA"),
    ("890101815", "Johnson & Johnson de Colombia"),
    ("860000762", "Ladrillera Santafe"),
]


def test_one(nit: str) -> dict:
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", API_URL,
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {API_KEY}",
                "-d", json.dumps({"tax_id": nit, "country": "CO"}),
            ],
            capture_output=True, text=True, timeout=60,
        )
        return json.loads(result.stdout)
    except Exception as e:
        return {"error": str(e), "found": False, "legal_representative": None}


def main():
    if not API_KEY:
        sys.exit("Set API_KEY to the key the local server was started with.")
    total = len(COMPANIES)
    found = 0
    with_rep = 0
    without_rep_found = 0
    not_found = 0
    errors_list = []

    print(f"Testing {total} companies...\n")
    print(f"{'#':<4} {'NIT':<12} {'FOUND':<6} {'REP':<5} {'EMPRESA'}")
    print("-" * 90)

    for i, (nit, name) in enumerate(COMPANIES, 1):
        result = test_one(nit)

        is_found = result.get("found", False)
        rep = result.get("legal_representative")
        has_rep = rep is not None

        if is_found:
            found += 1
            if has_rep:
                with_rep += 1
                rep_name = rep.get("name", "?")[:30]
                print(f"{i:<4} {nit:<12} YES    YES   {name} -> {rep_name}")
            else:
                without_rep_found += 1
                errs = result.get("errors", [])
                print(f"{i:<4} {nit:<12} YES    NO    {name} | {errs}")
                errors_list.append((nit, name, errs))
        else:
            not_found += 1
            print(f"{i:<4} {nit:<12} NO     -     {name}")

        time.sleep(0.5)

    print(f"\n{'=' * 90}")
    print(f"RESULTADOS FINALES")
    print(f"{'=' * 90}")
    print(f"Total empresas testeadas:    {total}")
    print(f"Encontradas en RUES:         {found} ({found*100//total}%)")
    print(f"No encontradas en RUES:      {not_found}")
    print(f"Con representante legal:     {with_rep}")
    print(f"Sin representante legal:     {without_rep_found}")
    if found > 0:
        print(f"")
        print(f"TASA EXITO REP. LEGAL:       {with_rep}/{found} = {with_rep*100//found}%")

    if errors_list:
        print(f"\n{'='*90}")
        print("EMPRESAS SIN REPRESENTANTE LEGAL (para investigar):")
        print(f"{'='*90}")
        for nit, name, errs in errors_list:
            print(f"  {nit} - {name}: {errs}")


if __name__ == "__main__":
    main()
