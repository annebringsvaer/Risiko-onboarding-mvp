"""
Enkle tester for RAG-motoren. Kjøres med:  python3 -m pytest tests/  (eller
python3 tests/test_rag.py for å kjøre uten pytest).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import RagEngine  # noqa: E402

engine = RagEngine()

# (spørsmål, tekst som bør forekomme i svaret, forventet minimum confidence-nivå)
ANSWERABLE_CASES = [
    ("Hvor mange feriedager har jeg?", "25 virkedager", "medium"),
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
