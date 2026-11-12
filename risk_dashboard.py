"""Risikodashboard for Risiko AS.

Denne modulen er uavhengig av app.py og rag.py, slik at C kan endre
søke-/svarlogikken uten at risikodashboardet får merge-konflikter.

Når det skal integreres løsningen på main, er det nok å legge til:

    from risk_dashboard import risk_bp
    app.register_blueprint(risk_bp)

Ruter som registreres:
    GET /risiko       -> templates/risiko.html
    GET /api/risiko   -> aggregerte risikodata i JSON

Testgrunnlag:
    data/risiko_tester.json (valgfri, opprettes når B har testresultater)

Format, f.eks.:
    {
      "tester": [
        {
          "id": "T01",
          "fase": "for",
          "sporsmal": "Hvordan får jeg tilgang til VPN?",
          "kategori": "IT-tilgang",
          "forventet_handtering": "svar",
          "faktisk_handtering": "svar",
          "svar_korrekt": true,
          "kunnskapsstyrke": "middels",
          "confidence_pct": 64,
          "kommentar": "Riktig veiledning, men krever kontroll av oppdatert kilde."
        }
      ]
    }

Feltet 'svar_korrekt' må vurderes manuelt mot en fasit.
'kunnskapsstyrke' må også vurderes manuelt ut fra kildenes kvalitet,
relevans og hvor mye kunnskap vi mangler. BM25-relevans er IKKE en
sannsynlighet for at svaret er korrekt.

I 'fase' støttes 'for'/'etter' (og 'før' som alternativ stavemåte).
Forventet/faktisk håndtering: 'svar' eller 'avsta' ('avstå' støttes også).
Kategorinavn bør følge KATEGORIER nedenfor.

Feil i datafilen gir varsler i dashboardet; appen skal ikke krasje.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
TESTDATA_PATH = BASE_DIR / "data" / "risiko_tester.json"

risk_bp = Blueprint("risk", __name__)

# FORELØPIG konsekvensinndeling. Må avklares før levering.
# Endre bare denne listen hvis A reviderer kategoriene/konsekvensene.
KATEGORIER = [
    {
        "id": "hms", "navn": "HMS", "konsekvens": "hoy",
        "dokument": "hms-rutiner.txt",
    },
    {
        "id": "ferie", "navn": "Ferie og fravær", "konsekvens": "hoy",
        "dokument": "ferie-og-fravaer.txt",
    },
    {
        "id": "it", "navn": "IT-tilgang", "konsekvens": "lav",
        "dokument": "it-tilgang-oppsett.txt",
    },
    {
        "id": "verktoy", "navn": "Interne verktøy", "konsekvens": "lav",
        "dokument": "interne-verktoy.txt",
    },
    {
        "id": "onboarding", "navn": "Generell onboarding", "konsekvens": "lav",
        "dokument": "velkomstguide.txt",
    },
]

KATEGORI_NAVN = {k["navn"]: k for k in KATEGORIER}
KATEGORI_ALIAS = {
    "hms": "HMS",
    "ferie/fravær": "Ferie og fravær",
    "ferie og fravaer": "Ferie og fravær",
    "it": "IT-tilgang",
    "it/oppsett": "IT-tilgang",
    "interne verktoy": "Interne verktøy",
    "onboarding": "Generell onboarding",
}


def _standardiser_verdi(value: object, alternativer: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    tekst = value.strip().lower()
    tekst = {"før": "for", "avstå": "avsta"}.get(tekst, tekst)
    return tekst if tekst in alternativer else None


def _les_testgrunnlag(path: Path) -> tuple[list[dict], list[str]]:
    """Les manuelle tester uten å hevde at manglende vurderinger er feil."""
    if not path.is_file():
        return [], ["Ingen testdata er lagt inn ennå. Venter på at (Del B) leverer data/risiko_tester.json."]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], ["Kunne ikke lese testdata (ugyldig JSON eller filtilgang)."]

    if not isinstance(payload, dict) or not isinstance(payload.get("tester"), list):
        return [], ["Testdata må være et JSON-objekt med en liste kalt 'tester'."]

    tester = []
    varsler = []
    for nummer, rad in enumerate(payload["tester"], start=1):
        if not isinstance(rad, dict):
            varsler.append(f"Test {nummer} ble hoppet over: forventet et objekt.")
            continue

        fase = _standardiser_verdi(rad.get("fase"), {"for", "etter"})
        forventet = _standardiser_verdi(rad.get("forventet_handtering"), {"svar", "avsta"})
        faktisk = _standardiser_verdi(rad.get("faktisk_handtering"), {"svar", "avsta"})
        sporsmal = rad.get("sporsmal")
        kategori_inn = rad.get("kategori")
        if isinstance(kategori_inn, str):
            kategori = KATEGORI_ALIAS.get(kategori_inn.strip().lower(), kategori_inn.strip())
        else:
            kategori = None

        if fase is None or forventet is None or not isinstance(sporsmal, str) or not sporsmal.strip() or kategori not in KATEGORI_NAVN:
            varsler.append(f"Test {nummer} ble hoppet over: ugyldig fase, kategori, spørsmål eller forventet håndtering.")
            continue

        korrekt = rad.get("svar_korrekt")
        if not isinstance(korrekt, bool):
            korrekt = None
        styrke = _standardiser_verdi(rad.get("kunnskapsstyrke"), {"sterk", "middels", "svak"})
        confidence = rad.get("confidence_pct")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
            confidence = None
        else:
            confidence = round(confidence)

        # Resultat beregnes fra forventet/faktisk handling og manuell fasit.
        if faktisk is None:
            resultat = "ikke_vurdert"
        elif forventet == "avsta":
            resultat = "korrekt_avsta" if faktisk == "avsta" else "uonsket_svar"
        elif faktisk == "avsta":
            resultat = "feil_avsta"
        elif korrekt is True:
            resultat = "korrekt_svar"
        elif korrekt is False:
            resultat = "feil_svar"
        else:
            resultat = "ikke_vurdert"

        definisjon = KATEGORI_NAVN[kategori]
        tester.append({
            "id": str(rad.get("id") or f"T{nummer:02d}"),
            "fase": fase,
            "sporsmal": sporsmal.strip(),
            "kategori": kategori,
            "konsekvens": definisjon["konsekvens"],
            "forventet_handtering": forventet,
            "faktisk_handtering": faktisk,
            "svar_korrekt": korrekt,
            "kunnskapsstyrke": styrke,
            "confidence_pct": confidence,
            "kommentar": str(rad.get("kommentar") or ""),
            "resultat": resultat,
        })

    return tester, varsler


def _statistikk(tester: list[dict]) -> dict:
    """Oppsummer kun vurderte spørsmål; ingen fiktive nullresultater."""
    vurderte = [t for t in tester if t["resultat"] != "ikke_vurdert"]
    korrekte = [t for t in vurderte if t["resultat"] in ("korrekt_svar", "korrekt_avsta")]
    risikofylte = [t for t in vurderte if t["konsekvens"] == "hoy" and t["resultat"] in ("feil_svar", "uonsket_svar")]
    fordelinger = {nivaa: sum(t["kunnskapsstyrke"] == nivaa for t in tester) for nivaa in ("sterk", "middels", "svak")}
    return {
        "antall": len(tester),
        "vurderte": len(vurderte),
        "korrekte": len(korrekte),
        "feil": len(vurderte) - len(korrekte),
        "andel_korrekt": round(100 * len(korrekte) / len(vurderte)) if vurderte else None,
        "risikofylte_feil": len(risikofylte),
        "kunnskapsstyrke": fordelinger,
    }


def bygg_risikodata(
    testdata_path: Path = TESTDATA_PATH,
    knowledge_dir: Path = KNOWLEDGE_DIR,
) -> dict:
    """Bygg et JSON-serialiserbart dashboard uten å kalle chatboten."""
    tester, varsler = _les_testgrunnlag(Path(testdata_path))
    alle_filer = list(Path(knowledge_dir).glob("*.txt")) if Path(knowledge_dir).is_dir() else []

    kategorier = []
    for kategori in KATEGORIER:
        navn = kategori["navn"]
        relaterte = [t for t in tester if t["kategori"] == navn]
        kategorier.append({
            **kategori,
            "kilde_finnes": (Path(knowledge_dir) / kategori["dokument"]).is_file(),
            "for": _statistikk([t for t in relaterte if t["fase"] == "for"]),
            "etter": _statistikk([t for t in relaterte if t["fase"] == "etter"]),
        })

    return {
        "tittel": "Risikoanalyse av onboarding-assistent",
        "metode": "Testresultater vurderes manuelt mot fasit. Kunnskapsstyrke vurderes separat. Confidence fra søkealgoritmen er ikke en sannsynlighet for riktig svar.",
        "forelopig_risikoklassifisering": True,
        "kunnskapskilder": len(alle_filer),
        "kategorier": kategorier,
        "statistikk": {
            "for": _statistikk([t for t in tester if t["fase"] == "for"]),
            "etter": _statistikk([t for t in tester if t["fase"] == "etter"]),
        },
        "tester": tester,
        "varsler": varsler,
    }


@risk_bp.get("/risiko")
def risiko_side():
    return render_template("risiko.html")


@risk_bp.get("/api/risiko")
def risiko_api():
    return jsonify(bygg_risikodata())


if __name__ == "__main__":
    # Forhåndsvisning på egen branch uten å endre app.py for lokal testing.
    from flask import Flask, redirect, url_for

    preview_app = Flask(__name__)
    preview_app.register_blueprint(risk_bp)

    @preview_app.get("/")
    def preview_index():
        return redirect(url_for("risk.risiko_side"))

    port = int(os.environ.get("PORT", "5001"))
    print(f"Åpne http://localhost:{port}/risiko i nettleseren.")
    preview_app.run(host="127.0.0.1", port=port, debug=False)
