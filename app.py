"""
Flask-app for onboarding-chatboten til Risiko AS.

Eksponerer en enkel nettside med en chat-boks, og et API-endepunkt
(/api/chat) som bruker RAG-motoren i rag.py til å svare på spørsmål
basert på tekstfilene i /knowledge.
"""

import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from rag import RagEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
UNCERTAIN_LOG_PATH = os.path.join(LOG_DIR, "usikre_sporsmal.log")

os.makedirs(LOG_DIR, exist_ok=True)

app = Flask(__name__)
engine = RagEngine()


def log_uncertain_question(question: str, confidence_pct: int) -> None:
    """Logger spørsmål chatboten ikke fant godt nok kontekst til å svare
    sikkert på, slik at HR/administrator kan følge opp og eventuelt
    utvide kunnskapsbasen."""
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_question = question.replace("\n", " ").strip()
    line = f"{timestamp}\tconfidence={confidence_pct}%\tspørsmål: {safe_question}\n"
    with open(UNCERTAIN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("message") or "").strip()

    if not question:
        return jsonify({"error": "Spørsmålet kan ikke være tomt."}), 400

    if len(question) > 1000:
        return jsonify({"error": "Spørsmålet er for langt (maks 1000 tegn)."}), 400

    result = engine.answer(question)

    if result["is_uncertain"]:
        log_uncertain_question(question, result["confidence_pct"])

    return jsonify(result)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "chunks_indexed": len(engine.chunks)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
