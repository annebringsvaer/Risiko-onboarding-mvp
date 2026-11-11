"""
Enkle tester for RAG-motoren. Kjøres med:  python3 -m pytest tests/  (eller
python3 tests/test_rag.py for å kjøre uten pytest).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402
import tempfile  # noqa: E402

import app as app_module  # noqa: E402
from rag import (  # noqa: E402
    HOY_KONSEKVENS_LOW_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    RagEngine,
)

engine = RagEngine()

# (spørsmål, tekst som bør forekomme i svaret, forventet minimum confidence-nivå)
ANSWERABLE_CASES = [
    ("Hvordan melder jeg fra om sykefravær?", "leder", "medium"),
    ("Hvordan får jeg tilgang til VPN?", "GlobalProtect", "medium"),
    ("Hva gjør jeg hvis det brenner på kontoret?", "rømningsvei", "medium"),
    ("Hvilket system bruker vi for oppgavestyring?", "Jira", "high"),
    ("Hvem er fadderen min?", "fadder", "medium"),
    ("Hvor mange dager kan jeg bruke egenmelding?", "8 kalenderdager", "medium"),
    ("Hvordan rapporterer jeg phishing?", "Report Phishing", "high"),
]

# Spørsmål vi ikke har noe godt svar på i kunnskapsbasen - skal flagges usikre.
UNANSWERABLE_CASES = [
    "Hvor mye koster aksjen deres på børs?",
    "Hva slags bil kjører daglig leder?",
    "Hva er favorittfargen til styreleder?",
]

# Høy konsekvens-spørsmål der beste treff er for svakt for den strengere
# terskelen. Her skal chatboten avstå. "Når får jeg lønn?" ga tidligere et
# svar fra avsnittet "Permisjon uten lønn" - feilinformasjon.
# "Hvor mange feriedager har jeg?" har "Feriepenger" som beste treff.
HOY_KONSEKVENS_AVSTA_CASES = [
    "Hvor mange feriedager har jeg?",
    "Når får jeg lønn?",
    "Hvor mye lønn får jeg?",
    "Hva er rutinen for førstehjelp?",
]

# (spørsmål, forventet kategori, forventet konsekvens, forventet håndtering)
HANDTERING_CASES = [
    ("Hvordan får jeg tilgang til VPN?", "IT-tilgang", "lav", "svar"),
    ("Hvilket system bruker vi for oppgavestyring?", "Interne verktøy", "lav", "svar"),
    ("Hva gjør jeg hvis det brenner på kontoret?", "HMS", "hoy", "henvis"),
    ("Hvordan søker jeg om foreldrepermisjon?", "Ferie og fravær", "hoy", "henvis"),
    # Treffet er i velkomstguiden (lav), men prøvetid er en rettighet.
    ("Hvor lang er prøvetiden?", "Generell onboarding", "hoy", "henvis"),
    ("Hvor mye koster aksjen deres på børs?", "Ukjent", "lav", "avstå"),
]

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def test_answerable_questions_return_expected_content():
    failures = []
    for question, expected_substring, min_level in ANSWERABLE_CASES:
        result = engine.answer(question)
        ok_content = expected_substring.lower() in result["answer"].lower()
        ok_confidence = (
            CONFIDENCE_RANK[result["confidence_label"]] >= CONFIDENCE_RANK[min_level]
        )
        if not (ok_content and ok_confidence):
            failures.append(
                f"  - '{question}' -> confidence={result['confidence_label']} "
                f"({result['confidence_pct']}%), forventet inneholde "
                f"'{expected_substring}' med min. nivå '{min_level}'.\n"
                f"    Fikk: {result['answer'][:150]!r}"
            )
    assert not failures, "Feilet spørsmål:\n" + "\n".join(failures)


def test_unanswerable_questions_are_flagged_uncertain_or_low_signal():
    failures = []
    for question in UNANSWERABLE_CASES:
        result = engine.answer(question)
        # Skal enten være flagget usikker, eller ha lav confidence.
        if not (result["is_uncertain"] or result["confidence_pct"] < 45):
            failures.append(
                f"  - '{question}' -> confidence={result['confidence_label']} "
                f"({result['confidence_pct']}%), is_uncertain={result['is_uncertain']}"
            )
    assert not failures, "Feilet spørsmål (burde vært usikre):\n" + "\n".join(failures)


def test_always_abstains_below_threshold():
    """Under terskelen skal chatboten alltid avstå: si at den ikke vet,
    henvise til HR og ikke vise utdrag fra kunnskapsbasen som svar."""
    questions = (
        UNANSWERABLE_CASES
        + HOY_KONSEKVENS_AVSTA_CASES
        + ["asdkjasdkj qweqwe zxczxc", "Hvem er verneombud?"]
    )
    for question in questions:
        results = engine.index.search(question, 1)
        result = engine.answer(question)
        threshold = (
            HOY_KONSEKVENS_LOW_THRESHOLD
            if result["konsekvens"] == "hoy"
            else LOW_CONFIDENCE_THRESHOLD
        )
        if results and results[0][1] >= threshold:
            continue
        assert result["faktisk_handtering"] == "avstå", question
        assert result["is_uncertain"] is True, question
        assert result["confidence_label"] == "low", question
        assert "Jeg vet ikke" in result["answer"], question
        assert "HR" in result["answer"], question
        for source in result["sources"]:
            assert source["excerpt"] not in result["answer"], question


def test_high_consequence_abstains_with_stricter_threshold():
    for question in HOY_KONSEKVENS_AVSTA_CASES:
        score = engine.index.search(question, 1)[0][1]
        assert LOW_CONFIDENCE_THRESHOLD <= score < HOY_KONSEKVENS_LOW_THRESHOLD, (
            f"'{question}' har score {score:.2f} - testen forutsetter at den "
            "ville blitt besvart med vanlig terskel."
        )
        result = engine.answer(question)
        assert result["konsekvens"] == "hoy", question
        assert result["faktisk_handtering"] == "avstå", question


def test_category_consequence_and_handling():
    failures = []
    for question, kategori, konsekvens, handtering in HANDTERING_CASES:
        r = engine.answer(question)
        got = (r["kategori"], r["konsekvens"], r["faktisk_handtering"])
        if got != (kategori, konsekvens, handtering):
            failures.append(f"  - '{question}' -> {got}, forventet {(kategori, konsekvens, handtering)}")
    assert not failures, "Feil klassifisering:\n" + "\n".join(failures)


def test_high_consequence_answers_end_with_hr_referral():
    for question, _k, konsekvens, handtering in HANDTERING_CASES:
        if handtering != "henvis":
            continue
        answer = engine.answer(question)["answer"]
        last_line = answer.strip().splitlines()[-1]
        assert "bekreftes hos HR" in last_line and "nærmeste leder" in last_line, question


def test_source_and_uncertainty_always_in_answer():
    questions = [q for q, _, _ in ANSWERABLE_CASES] + [q for q, *_ in HANDTERING_CASES]
    for question in questions:
        r = engine.answer(question)
        assert "Kilde:" in r["answer"], question
        assert "Usikkerhet:" in r["answer"], question
        if r["faktisk_handtering"] != "avstå":
            assert r["sources"], question
            assert r["sources"][0]["heading"] in r["answer"], question


def test_every_answer_is_logged_in_dashboard_format():
    with tempfile.TemporaryDirectory() as tmp:
        original = app_module.ANSWER_LOG_PATH
        app_module.ANSWER_LOG_PATH = os.path.join(tmp, "svarlogg.jsonl")
        original_uncertain = app_module.UNCERTAIN_LOG_PATH
        app_module.UNCERTAIN_LOG_PATH = os.path.join(tmp, "usikre.log")
        try:
            client = app_module.app.test_client()
            for question, *_ in HANDTERING_CASES:
                assert client.post("/api/chat", json={"message": question}).status_code == 200
            with open(app_module.ANSWER_LOG_PATH, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f]
        finally:
            app_module.ANSWER_LOG_PATH = original
            app_module.UNCERTAIN_LOG_PATH = original_uncertain

    assert len(rows) == len(HANDTERING_CASES)
    for row, (question, kategori, konsekvens, handtering) in zip(rows, HANDTERING_CASES):
        assert row["sporsmal"] == question
        assert row["kategori"] == kategori
        assert row["konsekvens"] == konsekvens
        assert row["faktisk_handtering"] == handtering
        assert isinstance(row["confidence_pct"], int) and 0 <= row["confidence_pct"] <= 100


def test_empty_question_has_no_crash():
    result = engine.answer("asdkjasdkj qweqwe zxczxc")
    assert result["is_uncertain"] in (True, False)
    assert 0 <= result["confidence_pct"] <= 99


def test_knowledge_base_loaded():
    assert len(engine.chunks) > 10, "Forventet at kunnskapsbasen faktisk ble lastet inn."


if __name__ == "__main__":
    tests = [
        test_answerable_questions_return_expected_content,
        test_unanswerable_questions_are_flagged_uncertain_or_low_signal,
        test_always_abstains_below_threshold,
        test_high_consequence_abstains_with_stricter_threshold,
        test_category_consequence_and_handling,
        test_high_consequence_answers_end_with_hr_referral,
        test_source_and_uncertainty_always_in_answer,
        test_every_answer_is_logged_in_dashboard_format,
        test_empty_question_has_no_crash,
        test_knowledge_base_loaded,
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
