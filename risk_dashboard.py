"""Risikodashboard for Risiko AS.

Modulen importerer ikke rag.py, slik at svarlogikken (frosset i v1.0-frys)
og dashboardet kan endres uavhengig av hverandre. Blueprinten registreres i
app.py:

    from risk_dashboard import risk_bp
    app.register_blueprint(risk_bp)

Ruter som registreres:
    GET /risiko       -> templates/risiko.html
    GET /api/risiko   -> aggregerte risikodata i JSON

Datakilder:

1. Manuelle testresultater (B): data/risiko_tester.json (valgfri). Finnes
   ikke filen, viser dashboardet at testdata mangler. Ingen resultater
   fylles inn automatisk.

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

   'svar_korrekt' og 'kunnskapsstyrke' må vurderes manuelt mot en fasit.
   BM25-relevans er IKKE en sannsynlighet for at svaret er korrekt.

   I 'fase' støttes 'for'/'etter' (og 'før'). Forventet/faktisk håndtering:
   'svar', 'henvis' eller 'avsta' ('avstå' støttes også). 'henvis' betyr at
   chatboten svarer med henvisning til HR. Det regnes som et svar, ikke som
   avståelse, men aldri automatisk som et korrekt svar: korrekthet bygger
   bare på den manuelle vurderingen i 'svar_korrekt'.

2. Svarloggen fra appen: logs/svarlogg.jsonl (én JSON-linje per svar, skrevet
   av app.py:log_answer) med feltene tidspunkt, sporsmal, kategori,
   konsekvens ('hoy'/'lav'), confidence_pct og faktisk_handtering
   ('svar'/'henvis'/'avstå'). Dashboardet viser KUN aggregerte tall fra
   loggen. Spørsmålstekst og tidspunkt per henvendelse eksponeres ikke.
   Konsekvens hentes fra loggen (faktisk klassifisering), ikke fra
   kategorien, fordi et spørsmål kan få høy konsekvens via nøkkelord.

Feil i datafilene gir varsler i dashboardet; appen skal ikke krasje.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
TESTDATA_PATH = BASE_DIR / "data" / "risiko_tester.json"
SVARLOGG_PATH = BASE_DIR / "logs" / "svarlogg.jsonl"

risk_bp = Blueprint("risk", __name__)

# Konsekvensinndeling. Navn og konsekvens skal stemme med KATEGORIER i
# rag.py (sjekkes av tests/test_risk_dashboard.py).
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
UKJENT_KATEGORI = "Ukjent"

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

HANDTERINGER = ("svar", "henvis", "avsta")
# Håndteringer som regnes som at chatboten har svart (ikke avstått).
SVAR_HANDTERINGER = {"svar", "henvis"}


def _standardiser_verdi(value: object, alternativer: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    tekst = value.strip().lower()
    tekst = {"før": "for", "avstå": "avsta"}.get(tekst, tekst)
    return tekst if tekst in alternativer else None


def _standardiser_kategori(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    tekst = value.strip()
    return KATEGORI_ALIAS.get(tekst.lower(), tekst)


def _beregn_resultat(forventet: str, faktisk: str | None, korrekt: bool | None) -> str:
    """Resultat fra forventet/faktisk håndtering og manuell fasit.

    'henvis' regnes som et svar. Om svaret er korrekt, avgjøres bare av den
    manuelle vurderingen; mangler den, er testen ikke vurdert."""
    if faktisk is None:
        return "ikke_vurdert"
    forventet_svar = forventet in SVAR_HANDTERINGER
    faktisk_svar = faktisk in SVAR_HANDTERINGER
    if not forventet_svar:
        return "korrekt_avsta" if not faktisk_svar else "uonsket_svar"
    if not faktisk_svar:
        return "feil_avsta"
    if korrekt is True:
        return "korrekt_svar"
    if korrekt is False:
        return "feil_svar"
    return "ikke_vurdert"


def _les_testgrunnlag(path: Path) -> tuple[list[dict], list[str]]:
    """Les manuelle tester uten å hevde at manglende vurderinger er feil."""
    if not path.is_file():
        return [], ["Ingen manuelle testresultater er lagt inn ennå (data/risiko_tester.json mangler). "
                    "Korrekthet kan ikke vurderes før testene er gjennomført og vurdert mot fasit."]

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
        forventet = _standardiser_verdi(rad.get("forventet_handtering"), set(HANDTERINGER))
        faktisk = _standardiser_verdi(rad.get("faktisk_handtering"), set(HANDTERINGER))
        sporsmal = rad.get("sporsmal")
        kategori = _standardiser_kategori(rad.get("kategori"))

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
            "resultat": _beregn_resultat(forventet, faktisk, korrekt),
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


# ---------------------------------------------------------------------------
# Svarloggen (logs/svarlogg.jsonl)
# ---------------------------------------------------------------------------

def _les_svarlogg(path: Path) -> tuple[list[dict], list[str]]:
    """Les svarloggen og behold bare feltene som trengs til aggregering.

    Spørsmålstekst og tidspunkt leses ikke inn, slik at de ikke kan lekke
    ut via API-et."""
    if not path.is_file():
        return [], ["Ingen spørsmål er logget ennå. Tallene fylles inn når chatboten brukes."]

    try:
        linjer = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], ["Kunne ikke lese svarloggen (filtilgang eller tegnkoding)."]

    rader = []
    ugyldige = 0
    for linje in linjer:
        if not linje.strip():
            continue
        try:
            rad = json.loads(linje)
        except json.JSONDecodeError:
            ugyldige += 1
            continue
        if not isinstance(rad, dict):
            ugyldige += 1
            continue

        handtering = _standardiser_verdi(rad.get("faktisk_handtering"), set(HANDTERINGER))
        konsekvens = _standardiser_verdi(rad.get("konsekvens"), {"hoy", "lav"})
        kategori = _standardiser_kategori(rad.get("kategori"))
        if handtering is None or konsekvens is None or kategori is None:
            ugyldige += 1
            continue
        if kategori not in KATEGORI_NAVN:
            kategori = UKJENT_KATEGORI

        confidence = rad.get("confidence_pct")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
            confidence = None

        rader.append({
            "kategori": kategori,
            "konsekvens": konsekvens,
            "handtering": handtering,
            "confidence_pct": confidence,
        })

    varsler = []
    if ugyldige:
        varsler.append(f"{ugyldige} linje(r) i svarloggen ble hoppet over (ugyldig format).")
    if not rader and not ugyldige:
        varsler.append("Ingen spørsmål er logget ennå. Tallene fylles inn når chatboten brukes.")
    return rader, varsler


def _tell_handteringer(rader: list[dict]) -> dict:
    tellinger = {h: sum(r["handtering"] == h for r in rader) for h in HANDTERINGER}
    antall = len(rader)
    return {
        "antall": antall,
        **tellinger,
        "besvart": tellinger["svar"] + tellinger["henvis"],
        "andel_avsta": round(100 * tellinger["avsta"] / antall) if antall else None,
    }


def _snitt(verdier: list[float]) -> int | None:
    return round(sum(verdier) / len(verdier)) if verdier else None


def _loggstatistikk(rader: list[dict]) -> dict:
    """Aggregert oversikt over faktisk håndtering i appen."""
    kategorier = []
    for navn in [k["navn"] for k in KATEGORIER] + [UKJENT_KATEGORI]:
        relaterte = [r for r in rader if r["kategori"] == navn]
        definisjon = KATEGORI_NAVN.get(navn)
        kategorier.append({
            "navn": navn,
            "kategori_konsekvens": definisjon["konsekvens"] if definisjon else None,
            **_tell_handteringer(relaterte),
            "hoy_konsekvens": sum(r["konsekvens"] == "hoy" for r in relaterte),
        })

    return {
        **_tell_handteringer(rader),
        "konsekvens": {
            nivaa: _tell_handteringer([r for r in rader if r["konsekvens"] == nivaa])
            for nivaa in ("hoy", "lav")
        },
        "kategorier": kategorier,
        "snitt_confidence": {
            h: _snitt([r["confidence_pct"] for r in rader if r["handtering"] == h and r["confidence_pct"] is not None])
            for h in HANDTERINGER
        },
        # Oppfølgingspunkt, ikke automatisk regelbrudd: høy konsekvens som er
        # besvart uten HR-henvisning bør undersøkes manuelt.
        "hoy_uten_henvisning": sum(r["konsekvens"] == "hoy" and r["handtering"] == "svar" for r in rader),
    }


def bygg_risikodata(
    testdata_path: Path = TESTDATA_PATH,
    knowledge_dir: Path = KNOWLEDGE_DIR,
    svarlogg_path: Path = SVARLOGG_PATH,
) -> dict:
    """Bygg et JSON-serialiserbart dashboard uten å kalle chatboten."""
    tester, varsler = _les_testgrunnlag(Path(testdata_path))
    loggrader, loggvarsler = _les_svarlogg(Path(svarlogg_path))
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
        "svarlogg": {
            **_loggstatistikk(loggrader),
            "varsler": loggvarsler,
        },
    }


@risk_bp.get("/risiko")
def risiko_side():
    return render_template("risiko.html")


@risk_bp.get("/api/risiko")
def risiko_api():
    return jsonify(bygg_risikodata(
        testdata_path=current_app.config.get("RISIKO_TESTDATA_PATH", TESTDATA_PATH),
        svarlogg_path=current_app.config.get("SVARLOGG_PATH", SVARLOGG_PATH),
    ))


if __name__ == "__main__":
    # Frittstående forhåndsvisning av dashboardet (uten chatboten).
    from flask import Flask, redirect, url_for

    preview_app = Flask(__name__)
    preview_app.register_blueprint(risk_bp)

    @preview_app.get("/")
    def preview_index():
        return redirect(url_for("risk.risiko_side"))

    port = int(os.environ.get("PORT", "5001"))
    print(f"Åpne http://localhost:{port}/risiko i nettleseren.")
    preview_app.run(host="127.0.0.1", port=port, debug=False)
