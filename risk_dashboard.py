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
   avståelse, men aldri automatisk som et korrekt svar.

   Valgfrie felt fra B sin testprotokoll (Excel-konvertering):
   - 'manuell_vurdering': 'ja' / 'delvis' / 'nei'. Er autoritativ for om
     testen er riktig håndtert. Mangler den, brukes 'svar_korrekt'
     (true = ja, false = nei). Er de i konflikt, vinner 'manuell_vurdering'
     og det gis et varsel.
   - faktisk håndtering 'usikker': historisk utfall fra før-versjonen. Telles
     for seg og behandles IKKE som avståelse. Godtas bare i testdata, ikke i
     svarloggen.
   - 'konsekvens_excel' (eller 'konsekvens'): 'hoy' / 'lav'. Mangler den, er
     testen «ikke klassifisert». Konsekvens avledes ikke fra kategorien;
     kategoriens nivå vises bare separat som 'kategori_konsekvens'.
   - Kategori 'Ukjent' eller manglende kategori beholdes som det er, uten å
     gjette en kategori.
   - 'oppsummering_excel': B sine oppgitte tall per fase. Dashboardet
     sammenligner dem med tallene beregnet fra radene, og rapporterer avvik
     uten å endre vurderingene.

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
# Historiske utfall som bare finnes i testdata fra før-versjonen. 'usikker'
# er verken et svar eller en avståelse, og tolkes ikke automatisk.
HISTORISKE_HANDTERINGER = ("usikker",)
TEST_HANDTERINGER = HANDTERINGER + HISTORISKE_HANDTERINGER

# Manuell vurdering (B): ja/delvis/nei -> vurdering i dashboardet.
MANUELL_VURDERING = {"ja": "riktig", "delvis": "delvis", "nei": "feil"}
VURDERINGER = ("riktig", "delvis", "feil")
KONSEKVENSNIVAER = ("hoy", "lav")
KUNNSKAPSSTYRKER = ("sterk", "middels", "svak")

# Felt i 'oppsummering_excel' som kontrolleres mot beregnede tall.
KONTROLLFELT = (
    "antall", "riktig", "delvis", "feil", "andel_riktig_pct",
    "feilsvar_uten_kilde", "feil_kilde", "feil_avsta", "henvis",
)


def _standardiser_verdi(value: object, alternativer: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    tekst = value.strip().lower()
    tekst = {"før": "for", "avstå": "avsta", "høy": "hoy"}.get(tekst, tekst)
    return tekst if tekst in alternativer else None


def _standardiser_kategori(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    tekst = value.strip()
    return KATEGORI_ALIAS.get(tekst.lower(), tekst)


def _beregn_resultat(forventet: str, faktisk: str | None, vurdering: str | bool | None) -> str:
    """Utfallstype fra forventet/faktisk håndtering og manuell vurdering.

    'henvis' regnes som et svar. Om svaret er korrekt, avgjøres bare av den
    manuelle vurderingen; mangler den, er testen ikke vurdert. Historisk
    'usikker' får egen utfallstype og regnes ikke som avståelse.

    'vurdering' kan være 'riktig'/'delvis'/'feil' eller (bakoverkompatibelt)
    True/False fra 'svar_korrekt'."""
    if vurdering is True:
        vurdering = "riktig"
    elif vurdering is False:
        vurdering = "feil"
    if faktisk is None:
        return "ikke_vurdert"
    if faktisk in HISTORISKE_HANDTERINGER:
        return "usikker_historisk"
    forventet_svar = forventet in SVAR_HANDTERINGER
    faktisk_svar = faktisk in SVAR_HANDTERINGER
    if not forventet_svar:
        return "korrekt_avsta" if not faktisk_svar else "uonsket_svar"
    if not faktisk_svar:
        return "feil_avsta"
    return {"riktig": "korrekt_svar", "feil": "feil_svar", "delvis": "delvis_svar"}.get(vurdering, "ikke_vurdert")


def _les_vurdering(rad: dict, test_id: str) -> tuple[str | None, str | None, bool | None, str | None]:
    """Returner (vurdering, kilde, svar_korrekt, varsel).

    'manuell_vurdering' er autoritativ. 'svar_korrekt' brukes bare når den
    manuelle vurderingen mangler."""
    korrekt = rad.get("svar_korrekt")
    if not isinstance(korrekt, bool):
        korrekt = None
    manuell = rad.get("manuell_vurdering")
    manuell = manuell.strip().lower() if isinstance(manuell, str) else None

    if manuell in MANUELL_VURDERING:
        vurdering = MANUELL_VURDERING[manuell]
        varsel = None
        if korrekt is not None and (vurdering == "riktig") != korrekt:
            varsel = (f"Test {test_id}: manuell_vurdering «{manuell}» og svar_korrekt={str(korrekt).lower()} "
                      "er i konflikt. Den manuelle vurderingen er brukt.")
        return vurdering, "manuell_vurdering", korrekt, varsel
    if korrekt is not None:
        return ("riktig" if korrekt else "feil"), "svar_korrekt", korrekt, None
    varsel = None
    if manuell:
        varsel = f"Test {test_id}: ukjent manuell_vurdering «{manuell}». Testen regnes som ikke vurdert."
    return None, None, None, varsel


def _les_konsekvens(rad: dict) -> str | None:
    """Konsekvens bare fra eksplisitt klassifisering i testdataene."""
    for felt in ("konsekvens_excel", "konsekvens"):
        nivaa = _standardiser_verdi(rad.get(felt), set(KONSEKVENSNIVAER))
        if nivaa is not None:
            return nivaa
    return None


def _les_testdata(path: Path) -> tuple[list[dict], list[str], dict]:
    """Les manuelle tester uten å hevde at manglende vurderinger er feil.

    Returnerer (tester, varsler, metadata). Metadata inneholder kilde,
    testdato, versjoner, konverteringsnotater og B sin oppsummering."""
    if not path.is_file():
        return [], ["Ingen manuelle testresultater er lagt inn ennå (data/risiko_tester.json mangler). "
                    "Korrekthet kan ikke vurderes før testene er gjennomført og vurdert mot fasit."], {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], ["Kunne ikke lese testdata (ugyldig JSON eller filtilgang)."], {}

    if not isinstance(payload, dict) or not isinstance(payload.get("tester"), list):
        return [], ["Testdata må være et JSON-objekt med en liste kalt 'tester'."], {}

    metadata = {
        felt: payload.get(felt) if isinstance(payload.get(felt), str) else None
        for felt in ("kilde", "testet_dato", "for_versjon", "etter_versjon")
    }
    notater = payload.get("konverteringsnotater")
    metadata["konverteringsnotater"] = [str(n) for n in notater] if isinstance(notater, list) else []
    oppsummering = payload.get("oppsummering_excel")
    metadata["oppsummering"] = oppsummering if isinstance(oppsummering, dict) else None

    tester = []
    varsler = []
    for nummer, rad in enumerate(payload["tester"], start=1):
        if not isinstance(rad, dict):
            varsler.append(f"Test {nummer} ble hoppet over: forventet et objekt.")
            continue

        test_id = str(rad.get("id") or f"T{nummer:02d}")
        fase = _standardiser_verdi(rad.get("fase"), {"for", "etter"})
        forventet = _standardiser_verdi(rad.get("forventet_handtering"), set(HANDTERINGER))
        faktisk = _standardiser_verdi(rad.get("faktisk_handtering"), set(TEST_HANDTERINGER))
        sporsmal = rad.get("sporsmal")

        if fase is None or forventet is None or not isinstance(sporsmal, str) or not sporsmal.strip():
            varsler.append(f"Test {nummer} ble hoppet over: ugyldig fase, spørsmål eller forventet håndtering.")
            continue

        # Kategori: beholdes uten gjetning. Ukjent navn -> 'Ukjent' + varsel.
        kategori = _standardiser_kategori(rad.get("kategori"))
        if kategori is not None and kategori not in KATEGORI_NAVN and kategori != UKJENT_KATEGORI:
            varsler.append(f"Test {test_id} ({fase}): ukjent kategori «{kategori}» er vist som «{UKJENT_KATEGORI}».")
            kategori = UKJENT_KATEGORI

        vurdering, vurdering_kilde, korrekt, vurderingsvarsel = _les_vurdering(rad, test_id)
        if vurderingsvarsel:
            varsler.append(vurderingsvarsel)

        styrke = _standardiser_verdi(rad.get("kunnskapsstyrke"), set(KUNNSKAPSSTYRKER))
        confidence = rad.get("confidence_pct")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 100):
            confidence = None
        else:
            confidence = round(confidence)

        definisjon = KATEGORI_NAVN.get(kategori)
        tester.append({
            "id": test_id,
            "fase": fase,
            "sporsmal": sporsmal.strip(),
            "kategori": kategori,
            "kategori_grunnlag": str(rad.get("kategori_grunnlag") or "") or None,
            "kategori_merknad": str(rad.get("kategori_merknad") or "") or None,
            # Eksplisitt klassifisering fra testprotokollen, ellers None
            # («ikke klassifisert»). Aldri avledet fra kategorien.
            "konsekvens": _les_konsekvens(rad),
            # Kategoriens nivå, kun som indikasjon (ikke brukt i beregninger).
            "kategori_konsekvens": definisjon["konsekvens"] if definisjon else None,
            "forventet_handtering": forventet,
            "faktisk_handtering": faktisk,
            "svar_korrekt": korrekt,
            "manuell_vurdering": {v: k for k, v in MANUELL_VURDERING.items()}.get(vurdering),
            "vurdering": vurdering or "ikke_vurdert",
            "vurdering_kilde": vurdering_kilde,
            "kunnskapsstyrke": styrke,
            "confidence_pct": confidence,
            "beste_treff": str(rad.get("beste_treff") or "") or None,
            "kommentar": str(rad.get("kommentar") or ""),
            "resultat": _beregn_resultat(forventet, faktisk, vurdering),
        })

    return tester, varsler, metadata


def _les_testgrunnlag(path: Path) -> tuple[list[dict], list[str]]:
    """Bakoverkompatibel innpakning: (tester, varsler)."""
    tester, varsler, _ = _les_testdata(path)
    return tester, varsler


def _statistikk(tester: list[dict]) -> dict:
    """Oppsummer testene ut fra den manuelle vurderingen.

    Ingen fiktive nullresultater: tester uten vurdering telles som
    'ikke_vurdert', og tester uten konsekvensklassifisering tas ikke med i
    tall som forutsetter kjent konsekvens."""
    vurderte = [t for t in tester if t["vurdering"] in VURDERINGER]
    telling = {v: sum(t["vurdering"] == v for t in vurderte) for v in VURDERINGER}
    handteringer = {h: sum(t["faktisk_handtering"] == h for t in tester) for h in TEST_HANDTERINGER}

    feil = [t for t in vurderte if t["vurdering"] == "feil"]
    feil_besvart = [t for t in feil if t["faktisk_handtering"] in SVAR_HANDTERINGER]
    risikofylte = [t for t in feil_besvart if t["konsekvens"] == "hoy"]
    feil_uten_konsekvens = [t for t in feil_besvart if t["konsekvens"] is None]

    styrker = {nivaa: sum(t["kunnskapsstyrke"] == nivaa for t in tester) for nivaa in KUNNSKAPSSTYRKER}
    styrker["ikke_vurdert"] = sum(t["kunnskapsstyrke"] is None for t in tester)

    return {
        "antall": len(tester),
        "vurderte": len(vurderte),
        "ikke_vurdert": len(tester) - len(vurderte),
        **telling,
        "andel_riktig_pct": round(100 * telling["riktig"] / len(vurderte), 1) if vurderte else None,
        # Bakoverkompatible navn (korrekte = riktig).
        "korrekte": telling["riktig"],
        "andel_korrekt": round(100 * telling["riktig"] / len(vurderte)) if vurderte else None,
        "handtering": handteringer,
        "henvis": handteringer["henvis"],
        "usikker": handteringer["usikker"],
        # Avstått selv om svaret finnes (historisk 'usikker' er ikke avstått).
        "feil_avsta": sum(t["forventet_handtering"] in SVAR_HANDTERINGER and t["faktisk_handtering"] == "avsta"
                          for t in tester),
        # Svart (feil) der chatboten skulle avstått: kunnskapsbasen har ikke svaret.
        "feilsvar_uten_kilde": sum(t["forventet_handtering"] == "avsta" for t in feil_besvart),
        # Svart (feil) der svaret finnes, men feil kilde/avsnitt ble brukt.
        "feil_kilde": sum(t["forventet_handtering"] in SVAR_HANDTERINGER for t in feil_besvart),
        "konsekvens": {
            "hoy": sum(t["konsekvens"] == "hoy" for t in tester),
            "lav": sum(t["konsekvens"] == "lav" for t in tester),
            "ikke_klassifisert": sum(t["konsekvens"] is None for t in tester),
        },
        # Kun feil med kjent høy konsekvens. Feil uten klassifisering telles
        # separat og regnes verken som høy eller lav risiko.
        "risikofylte_feil": len(risikofylte),
        "feil_uten_konsekvens": len(feil_uten_konsekvens),
        "kunnskapsstyrke": styrker,
        "kunnskapsstyrke_vurdert": any(styrker[n] for n in KUNNSKAPSSTYRKER),
    }


def _kontroller_oppsummering(oppsummering: dict | None, statistikk: dict) -> dict | None:
    """Sammenlign B sin oppgitte oppsummering med tallene beregnet fra radene.

    Avvik rapporteres; vurderingene endres aldri."""
    if not isinstance(oppsummering, dict):
        return None
    kontroll = {}
    for fase, beregnet in statistikk.items():
        oppgitt = oppsummering.get(fase)
        if not isinstance(oppgitt, dict):
            continue
        felter = []
        for felt in KONTROLLFELT:
            if felt not in oppgitt:
                continue
            verdi = oppgitt[felt]
            faktisk = beregnet.get(felt)
            if isinstance(verdi, (int, float)) and isinstance(faktisk, (int, float)):
                samsvar = abs(verdi - faktisk) < 0.05 if felt.endswith("_pct") else verdi == faktisk
            else:
                samsvar = verdi == faktisk
            felter.append({"felt": felt, "oppgitt": verdi, "beregnet": faktisk, "samsvar": samsvar})
        kontroll[fase] = {"samsvar": all(f["samsvar"] for f in felter), "felter": felter}
    return kontroll


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
    tester, varsler, metadata = _les_testdata(Path(testdata_path))
    loggrader, loggvarsler = _les_svarlogg(Path(svarlogg_path))
    alle_filer = list(Path(knowledge_dir).glob("*.txt")) if Path(knowledge_dir).is_dir() else []

    def per_fase(rader: list[dict]) -> dict:
        return {fase: _statistikk([t for t in rader if t["fase"] == fase]) for fase in ("for", "etter")}

    kategorier = []
    for kategori in KATEGORIER:
        relaterte = [t for t in tester if t["kategori"] == kategori["navn"]]
        kategorier.append({
            **kategori,
            "kilde_finnes": (Path(knowledge_dir) / kategori["dokument"]).is_file(),
            **per_fase(relaterte),
        })
    # Tester med ukjent eller manglende kategori vises for seg, uten
    # oppdiktet dokument eller konsekvens.
    for navn, filter_ in ((UKJENT_KATEGORI, UKJENT_KATEGORI), ("Ikke oppgitt", None)):
        relaterte = [t for t in tester if t["kategori"] == filter_]
        if relaterte:
            kategorier.append({
                "id": "ukjent" if filter_ else "ikke_oppgitt", "navn": navn,
                "konsekvens": None, "dokument": None, "kilde_finnes": False,
                **per_fase(relaterte),
            })

    statistikk = per_fase(tester)
    kontroll = _kontroller_oppsummering(metadata.get("oppsummering"), statistikk)
    if kontroll:
        for fase, resultat in kontroll.items():
            avvik = [f for f in resultat["felter"] if not f["samsvar"]]
            if avvik:
                detaljer = ", ".join(f"{f['felt']}: oppgitt {f['oppgitt']}, beregnet {f['beregnet']}" for f in avvik)
                varsler.append(f"Avvik mot oppsummeringen i testprotokollen ({'før' if fase == 'for' else 'etter'}): {detaljer}.")

    return {
        "tittel": "Risikoanalyse av onboarding-assistent",
        "metode": "Testresultater vurderes manuelt mot fasit. Kunnskapsstyrke vurderes separat. Confidence fra søkealgoritmen er ikke en sannsynlighet for riktig svar.",
        "forelopig_risikoklassifisering": True,
        "kunnskapskilder": len(alle_filer),
        "kategorier": kategorier,
        # Manuelle testresultater og faktisk bruk er to adskilte datakilder.
        "datakilder": {
            "manuelle_tester": {
                "type": "Manuell testprotokoll (vurdert mot fasit)",
                "fil": "data/risiko_tester.json",
                "kilde": metadata.get("kilde"),
                "testet_dato": metadata.get("testet_dato"),
                "for_versjon": metadata.get("for_versjon"),
                "etter_versjon": metadata.get("etter_versjon"),
                "antall_rader": len(tester),
                "konverteringsnotater": metadata.get("konverteringsnotater", []),
            },
            "svarlogg": {
                "type": "Faktisk bruk av chatboten (kun håndtering, ikke riktighet)",
                "fil": "logs/svarlogg.jsonl",
                "antall_rader": len(loggrader),
            },
        },
        "statistikk": statistikk,
        "kontroll": kontroll,
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
