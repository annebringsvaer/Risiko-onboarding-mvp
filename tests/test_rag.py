"""
Enkle tester for RAG-motoren. Kjøres med:  python3 -m pytest tests/  (eller
python3 tests/test_rag.py for å kjøre uten pytest).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402
import re  # noqa: E402
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
    # Skal treffe avsnittet "Ferie", ikke "Feriepenger".
    ("Hvor mange feriedager har jeg?", "30 virkedager", "medium"),
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
HOY_KONSEKVENS_AVSTA_CASES = [
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
    ("Kan jeg jobbe hjemmefra?", "IT-tilgang", "lav", "svar"),
    ("Kan jeg ta med hunden min på kontoret?", "IT-tilgang", "lav", "avstå"),
]

# Feil fra sprint 2: spørsmål der beste treff har høy nok score, men bare
# deler et generelt ord med spørsmålet. (spørsmål, ord som mangler i treffet)
DEKNING_AVSTA_CASES = [
    ("Kan jeg ta med hunden min på kontoret?", "hunden"),
    ("Hva slags bil kjører daglig leder?", "bil"),
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


# Dekningssjekken skal skille disse: de tre første har svar i kunnskapsbasen,
# de tre siste skal avstå. (spørsmål, forventet håndtering, forventet avsnitt
# eller None)
DEKNING_BALANSE_CASES = [
    ("Hva er rutinene for HMS på arbeidsplassen?", "henvis", "HMS-RUTINER - RISIKO AS"),
    ("Hvor mange dager kan jeg være syk uten legeerklæring?", "henvis", "Sykefravær"),
    ("Hvem er verneombudet?", "henvis", "Ansvar og organisering"),
    ("Kan jeg ta med hunden min på kontoret?", "avstå", None),
    ("Når får jeg lønn?", "avstå", None),
    ("Hva slags bil kjører daglig leder?", "avstå", None),
    # Test 2 fra sprint 2: "lønn" står bare i "Permisjon uten lønn".
    ("Hva er lønnen min neste år?", "avstå", None),
]


def test_coverage_check_balance():
    failures = []
    for question, handtering, heading in DEKNING_BALANSE_CASES:
        r = engine.answer(question)
        got_heading = r["sources"][0]["heading"] if r["sources"] else None
        if r["faktisk_handtering"] != handtering or (heading and got_heading != heading):
            failures.append(
                f"  - '{question}' -> {r['faktisk_handtering']} fra {got_heading!r}, "
                f"forventet {handtering} fra {heading!r}"
            )
    assert not failures, "Feil håndtering:\n" + "\n".join(failures)


def test_word_in_other_topic_phrase_is_not_coverage():
    result = engine.answer("Hva er lønnen min neste år?")
    assert result["faktisk_handtering"] == "avstå"
    assert "lønnen" in result["manglende_ord"]
    # Unntak: spørsmålet handler selv om permisjon uten lønn.
    result = engine.answer("Hvordan søker jeg permisjon uten lønn?")
    assert result["faktisk_handtering"] == "henvis"
    assert result["sources"][0]["heading"] == "Permisjon uten lønn"


def test_vacation_days_hits_ferie_section():
    result = engine.answer("Hvor mange feriedager har jeg?")
    assert result["faktisk_handtering"] == "henvis"
    assert result["sources"][0]["heading"] == "Ferie"


def test_sick_leave_answer_mentions_egenmelding():
    answer = engine.answer("Hvor mange dager kan jeg være syk uten legeerklæring?")["answer"]
    assert "8 kalenderdager" in answer and "sykmelding" in answer


def test_coverage_check_abstains_regardless_of_score():
    for question, missing_word in DEKNING_AVSTA_CASES:
        score = engine.index.search(question, 1)[0][1]
        assert score >= LOW_CONFIDENCE_THRESHOLD, (
            f"'{question}' har score {score:.2f} - testen forutsetter at "
            "scoren alene ville gitt et svar."
        )
        result = engine.answer(question)
        assert result["faktisk_handtering"] == "avstå", question
        assert missing_word in result["manglende_ord"], question
        assert f"«{missing_word}»" in result["answer"], question


def test_hjemmefra_hits_vpn_and_remote_work():
    result = engine.answer("Kan jeg jobbe hjemmefra?")
    assert result["faktisk_handtering"] == "svar"
    assert result["sources"][0]["heading"] == "VPN og fjernarbeid"
    assert "VPN" in result["answer"]


def test_coverage_check_tolerates_inflected_words():
    # "bestiller" skal matche "bestilling", og "skjer" er et generisk ord.
    for question in ["Hvordan bestiller jeg hev/senk-pult?", "Hva skjer første dag?"]:
        assert engine.answer(question)["faktisk_handtering"] != "avstå", question


def test_sidebar_suggestions_are_answered():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    suggestions_html = html.split('id="suggestions"', 1)[1].split("</ul>", 1)[0]
    suggestions = re.findall(r"<li>(.*?)</li>", suggestions_html)
    assert len(suggestions) >= 5
    for question in suggestions:
        assert engine.answer(question)["faktisk_handtering"] != "avstå", question


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
        test_coverage_check_balance,
        test_word_in_other_topic_phrase_is_not_coverage,
        test_vacation_days_hits_ferie_section,
        test_sick_leave_answer_mentions_egenmelding,
        test_coverage_check_abstains_regardless_of_score,
        test_hjemmefra_hits_vpn_and_remote_work,
        test_coverage_check_tolerates_inflected_words,
        test_sidebar_suggestions_are_answered,
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
