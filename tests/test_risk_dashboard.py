"""
Tester for risikodashboardet (risk_dashboard.py) og integrasjonen med appen.
Kjøres med:  python -m pytest tests/  (eller python tests/test_risk_dashboard.py).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import app as app_module  # noqa: E402
import rag  # noqa: E402
import risk_dashboard as rd  # noqa: E402

# Spørsmål som gir hver av de tre håndteringene (se HANDTERING_CASES i test_rag.py).
ENDE_TIL_ENDE_CASES = [
    ("Hvordan får jeg tilgang til VPN?", "svar"),
    ("Hva gjør jeg hvis det brenner på kontoret?", "henvis"),
    ("Hvor mye koster aksjen deres på børs?", "avstå"),
]


def _skriv_testdata(tmp: str, tester: list) -> Path:
    path = Path(tmp) / "risiko_tester.json"
    path.write_text(json.dumps({"tester": tester}, ensure_ascii=False), encoding="utf-8")
    return path


def _test(**felt) -> dict:
    rad = {
        "fase": "for",
        "sporsmal": "Hva gjør jeg hvis det brenner?",
        "kategori": "HMS",
        "forventet_handtering": "svar",
    }
    rad.update(felt)
    return rad


def test_kategorier_stemmer_med_rag():
    dashboard = {k["dokument"]: (k["navn"], k["konsekvens"]) for k in rd.KATEGORIER}
    motor = {dok: (d["navn"], d["konsekvens"]) for dok, d in rag.KATEGORIER.items()}
    assert dashboard == motor
    assert rd.UKJENT_KATEGORI == rag.UKJENT_KATEGORI


def test_henvis_godtas_og_regnes_som_svar_men_ikke_automatisk_korrekt():
    with tempfile.TemporaryDirectory() as tmp:
        path = _skriv_testdata(tmp, [
            _test(id="A", faktisk_handtering="henvis", svar_korrekt=True),
            _test(id="B", faktisk_handtering="henvis", svar_korrekt=False),
            _test(id="C", faktisk_handtering="henvis"),  # ikke manuelt vurdert
            _test(id="D", forventet_handtering="avstå", faktisk_handtering="henvis"),
            _test(id="E", forventet_handtering="henvis", faktisk_handtering="avstå"),
            _test(id="F", forventet_handtering="avsta", faktisk_handtering="avstå"),
        ])
        tester, varsler = rd._les_testgrunnlag(path)

    assert varsler == []
    resultat = {t["id"]: t["resultat"] for t in tester}
    assert resultat == {
        "A": "korrekt_svar",
        "B": "feil_svar",
        "C": "ikke_vurdert",   # henvis er ikke automatisk korrekt
        "D": "uonsket_svar",   # henvis er et svar, ikke avståelse
        "E": "feil_avsta",
        "F": "korrekt_avsta",
    }
    assert {t["id"]: t["faktisk_handtering"] for t in tester}["A"] == "henvis"


def test_svarlogg_aggregeres_med_svar_henvis_og_avsta():
    linjer = [
        {"tidspunkt": "2026-11-12T10:00:00+00:00", "sporsmal": "Hemmelig spørsmål 1",
         "kategori": "IT-tilgang", "konsekvens": "lav", "confidence_pct": 80, "faktisk_handtering": "svar"},
        {"tidspunkt": "2026-11-12T10:01:00+00:00", "sporsmal": "Hemmelig spørsmål 2",
         "kategori": "HMS", "konsekvens": "hoy", "confidence_pct": 60, "faktisk_handtering": "henvis"},
        {"tidspunkt": "2026-11-12T10:02:00+00:00", "sporsmal": "Hemmelig spørsmål 3",
         "kategori": "Ukjent", "konsekvens": "lav", "confidence_pct": 0, "faktisk_handtering": "avstå"},
        {"tidspunkt": "2026-11-12T10:03:00+00:00", "sporsmal": "Hemmelig spørsmål 4",
         "kategori": "Generell onboarding", "konsekvens": "hoy", "confidence_pct": 70, "faktisk_handtering": "svar"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        logg = Path(tmp) / "svarlogg.jsonl"
        tekst = "\n".join(json.dumps(r, ensure_ascii=False) for r in linjer)
        tekst += "\nikke json\n" + json.dumps({"kategori": "HMS", "konsekvens": "hoy", "faktisk_handtering": "kanskje"}) + "\n"
        logg.write_text(tekst, encoding="utf-8")
        data = rd.bygg_risikodata(testdata_path=Path(tmp) / "finnes_ikke.json", svarlogg_path=logg)

    s = data["svarlogg"]
    assert (s["antall"], s["svar"], s["henvis"], s["avsta"], s["besvart"]) == (4, 2, 1, 1, 3)
    assert s["andel_avsta"] == 25
    assert s["konsekvens"]["hoy"]["henvis"] == 1
    assert s["konsekvens"]["hoy"]["svar"] == 1
    # Konsekvens hentes fra loggen, ikke fra kategorien (Generell onboarding er lav).
    onboarding = next(k for k in s["kategorier"] if k["navn"] == "Generell onboarding")
    assert onboarding["hoy_konsekvens"] == 1 and onboarding["kategori_konsekvens"] == "lav"
    ukjent = next(k for k in s["kategorier"] if k["navn"] == "Ukjent")
    assert ukjent["avsta"] == 1
    assert s["hoy_uten_henvisning"] == 1
    assert s["snitt_confidence"] == {"svar": 75, "henvis": 60, "avsta": 0}
    assert any("2 linje(r)" in v for v in s["varsler"])
    # Bare aggregerte data: ingen spørsmålstekst eller tidspunkt i resultatet.
    serialisert = json.dumps(data, ensure_ascii=False)
    assert "Hemmelig" not in serialisert
    assert "2026-11-12T" not in serialisert


def test_manglende_data_gir_tomme_tall_og_varsler():
    with tempfile.TemporaryDirectory() as tmp:
        data = rd.bygg_risikodata(
            testdata_path=Path(tmp) / "finnes_ikke.json",
            svarlogg_path=Path(tmp) / "finnes_ikke.jsonl",
        )
    assert data["tester"] == []
    assert data["varsler"], "Manglende testdata skal gi et varsel."
    for fase in ("for", "etter"):
        st = data["statistikk"][fase]
        assert st["antall"] == 0 and st["vurderte"] == 0
        assert st["andel_korrekt"] is None
    s = data["svarlogg"]
    assert s["antall"] == 0 and s["andel_avsta"] is None
    assert s["snitt_confidence"] == {"svar": None, "henvis": None, "avsta": None}
    assert s["varsler"], "Manglende svarlogg skal gi et varsel."


def test_ugyldig_testdata_krasjer_ikke():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "risiko_tester.json"
        path.write_text("{ikke gyldig", encoding="utf-8")
        tester, varsler = rd._les_testgrunnlag(path)
    assert tester == [] and varsler


def test_ende_til_ende_svar_henvis_og_avsta_i_dashboardet():
    with tempfile.TemporaryDirectory() as tmp:
        originaler = (app_module.ANSWER_LOG_PATH, app_module.UNCERTAIN_LOG_PATH, dict(app_module.app.config))
        app_module.ANSWER_LOG_PATH = os.path.join(tmp, "svarlogg.jsonl")
        app_module.UNCERTAIN_LOG_PATH = os.path.join(tmp, "usikre.log")
        app_module.app.config["SVARLOGG_PATH"] = app_module.ANSWER_LOG_PATH
        app_module.app.config["RISIKO_TESTDATA_PATH"] = os.path.join(tmp, "finnes_ikke.json")
        try:
            client = app_module.app.test_client()
            for question, handtering in ENDE_TIL_ENDE_CASES:
                r = client.post("/api/chat", json={"message": question})
                assert r.status_code == 200
                assert r.get_json()["faktisk_handtering"] == handtering, question

            api = client.get("/api/risiko")
            assert api.status_code == 200
            data = api.get_json()
            assert client.get("/risiko").status_code == 200
            assert client.get("/").status_code == 200
            assert client.get("/api/health").status_code == 200
        finally:
            app_module.ANSWER_LOG_PATH, app_module.UNCERTAIN_LOG_PATH, config = originaler
            app_module.app.config.clear()
            app_module.app.config.update(config)

    s = data["svarlogg"]
    assert (s["antall"], s["svar"], s["henvis"], s["avsta"], s["besvart"]) == (3, 1, 1, 1, 2)
    assert s["hoy_uten_henvisning"] == 0
    hms = next(k for k in s["kategorier"] if k["navn"] == "HMS")
    assert hms["henvis"] == 1 and hms["hoy_konsekvens"] == 1
    serialisert = json.dumps(data, ensure_ascii=False)
    for question, _ in ENDE_TIL_ENDE_CASES:
        assert question not in serialisert, "Rå spørsmål skal ikke eksponeres i dashboardet."
    # Ingen testdata fra B: ingen korrekthetstall.
    assert data["statistikk"]["for"]["andel_korrekt"] is None


def test_dashboard_bruker_appens_svarlogg_som_standard():
    assert app_module.app.config["SVARLOGG_PATH"] == app_module.ANSWER_LOG_PATH
    assert "risk" in app_module.app.blueprints


if __name__ == "__main__":
    tests = [
        test_kategorier_stemmer_med_rag,
        test_henvis_godtas_og_regnes_som_svar_men_ikke_automatisk_korrekt,
        test_svarlogg_aggregeres_med_svar_henvis_og_avsta,
        test_manglende_data_gir_tomme_tall_og_varsler,
        test_ugyldig_testdata_krasjer_ikke,
        test_ende_til_ende_svar_henvis_og_avsta_i_dashboardet,
        test_dashboard_bruker_appens_svarlogg_som_standard,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}\n{e}")
    if failed:
        print(f"\n{failed} test(er) feilet.")
        sys.exit(1)
    print("\nAlle tester bestått.")
