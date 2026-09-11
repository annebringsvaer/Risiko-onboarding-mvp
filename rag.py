"""
Enkel RAG-motor (Retrieval-Augmented Generation) for onboarding-chatboten.

Ingen tunge ML-avhengigheter: dokumentene deles i mindre biter ("chunks"),
og vi bruker en TF-IDF-vektet cosine similarity (implementert med ren
Python) for å finne de bitene som er mest relevante for et spørsmål.
De mest relevante bitene brukes deretter som kontekst til å sette sammen
et svar, sammen med en enkel confidence-score.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")

# Norske + engelske stoppord vi ikke ønsker skal dominere relevans-scoringen.
STOPWORDS = {
    "og", "i", "jeg", "det", "at", "en", "et", "den", "til", "er", "som",
    "på", "de", "med", "han", "av", "ikke", "der", "så", "var", "meg",
    "seg", "men", "ett", "har", "om", "vi", "min", "mitt", "ha", "hadde",
    "hun", "nå", "over", "da", "ved", "fra", "du", "ut", "sin", "dem",
    "oss", "opp", "man", "kan", "hans", "hvor", "eller", "hva", "skal",
    "selv", "her", "alle", "vil", "bli", "ble", "blir", "for", "deg",
    "din", "dine", "mine", "noe", "denne", "dette", "disse", "hvordan",
    "hvorfor", "hvem", "the", "a", "an", "of", "to", "in", "is", "are",
    # Generiske ord som sjelden er nyttige for å skille relevante treff fra
    # irrelevante i denne kunnskapsbasen (de forekommer i mange sammenhenger
    # uten å være tema-spesifikke).
    "daglig", "daglige", "gjøre", "gjør", "få", "ta", "ha", "være", "lage",
}

TOKEN_RE = re.compile(r"[a-zA-ZæøåÆØÅ0-9]+")

# Enkel (ikke fullstendig akademisk korrekt) suffiksstripping for norsk, slik
# at f.eks. "feriedager"/"feriedagene", "kontoret"/"kontorer" og
# "ansatte"/"ansatt" matcher hverandre i søket. Lengst suffiks sjekkes først.
# Vi krever at minst 4 tegn står igjen av stammen for å unngå å over-stemme
# korte ord.
_STEM_SUFFIXES = [
    "endes", "heten", "hetene", "hetenes",
    "ende", "ane", "ene", "edes", "eren", "erne",
    "en", "et", "er", "ar", "as", "es",
    "a", "e", "s",
]


def stem(word: str) -> str:
    if len(word) <= 4 or word.isdigit():
        return word
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


# Små synonymgrupper for domenespesifikke ord/uregelrette former som
# suffiksstripping alene ikke fanger opp (f.eks. "brenner" -> "brann").
# Anvendes symmetrisk på både dokumenter og spørsmål via tokenize(),
# slik at det holder å definere hvert forhold én gang.
_SYNONYMS: dict[str, list[str]] = {
    "brenner": ["brann"],
    "brenne": ["brann"],
    "ild": ["brann"],
    "syk": ["sykefravær"],
    "sykt": ["sykefravær"],
    "ulykke": ["skade", "avvik"],
}


def tokenize(text: str) -> list[str]:
    raw_tokens = [t.lower() for t in TOKEN_RE.findall(text)]
    tokens = []
    for raw in raw_tokens:
        if raw in STOPWORDS or len(raw) <= 1:
            continue
        tokens.append(stem(raw))
        for synonym in _SYNONYMS.get(raw, []):
            tokens.append(stem(synonym))
    return tokens


@dataclass
class Chunk:
    id: int
    source: str          # filnavn, f.eks. "hms-rutiner.txt"
    title: str           # pen tittel avledet fra filnavn
    heading: str         # nærmeste overskrift/avsnitt-tittel i teksten
    text: str            # selve avsnittsteksten
    tokens: list[str] = field(default_factory=list)


FRIENDLY_TITLES = {
    "hms-rutiner.txt": "HMS-rutiner",
    "it-tilgang-oppsett.txt": "IT-tilgang og oppsett",
    "ferie-og-fravaer.txt": "Ferie- og fraværsregler",
    "velkomstguide.txt": "Velkomstguide",
    "interne-verktoy.txt": "Interne verktøy",
}


def _split_into_chunks(source: str, raw_text: str) -> list[Chunk]:
    """Deler en fil i avsnitt (separert av blanke linjer). Den første
    linjen i filen brukes som overordnet tittel; linjer i store bokstaver
    inni teksten tolkes som underoverskrifter og festes til påfølgende
    avsnitt slik at konteksten blir tydeligere for brukeren."""
    lines = raw_text.strip().split("\n")
    doc_title = lines[0].strip() if lines else source

    chunks: list[Chunk] = []
    current_heading = doc_title
    buffer: list[str] = []

    def flush():
        text = "\n".join(buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    id=-1,
                    source=source,
                    title=FRIENDLY_TITLES.get(source, doc_title),
                    heading=current_heading,
                    text=text,
                )
            )
        buffer.clear()

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        # En linje uten punktum, som stort sett er store bokstaver eller
        # kort og "tittel-aktig", regnes som en ny overskrift.
        looks_like_heading = (
            len(stripped) < 60
            and not stripped.endswith((".", ":", ","))
            and stripped[0].isupper()
            and not buffer
        )
        if looks_like_heading:
            flush()
            current_heading = stripped
            continue
        buffer.append(stripped)
    flush()

    return chunks


def load_knowledge_base(directory: str = KNOWLEDGE_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not os.path.isdir(directory):
        return chunks
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".txt"):
            continue
        path = os.path.join(directory, filename)
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        chunks.extend(_split_into_chunks(filename, raw))

    for idx, chunk in enumerate(chunks):
        chunk.id = idx
        chunk.tokens = tokenize(chunk.heading + " " + chunk.text)

    return chunks


class BM25Index:
    """BM25-indeks (Okapi BM25), uten eksterne avhengigheter.

    BM25 er en velprøvd, nøkkelordbasert rangeringsmodell som brukes i bl.a.
    Elasticsearch/Lucene. Vi bruker den fremfor "ren" TF-IDF cosine similarity
    fordi BM25 metter term-frekvens (gjentagelser av et ord gir avtagende
    ekstra score) og har en justerbar lengdenormalisering (parameteren ``b``),
    noe som gir mer robuste treff på korte, ujevnt lange avsnitt slik vi har
    i denne kunnskapsbasen.
    """

    K1 = 1.5   # kontrollerer metning av term-frekvens
    B = 0.5    # kontrollerer hvor mye dokumentlengde straffes (0=ingen, 1=full)

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.df: Counter = Counter()
        self.doc_term_freqs: list[Counter] = []
        self.doc_lengths: list[int] = []

        for chunk in chunks:
            tf = Counter(chunk.tokens)
            self.doc_term_freqs.append(tf)
            self.doc_lengths.append(len(chunk.tokens))
            for term in tf:
                self.df[term] += 1

        self.n_docs = max(len(chunks), 1)
        self.avgdl = (sum(self.doc_lengths) / self.n_docs) if self.n_docs else 1.0
        self.avgdl = self.avgdl or 1.0

        # Robertson/Sparck-Jones IDF med +1 for å unngå negative vekter.
        self.idf = {
            term: math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)
            for term, df in self.df.items()
        }

    # Et matchende ord regnes som "distinkt" (tema-spesifikt) hvis IDF-en er
    # over denne grensen. Se merknad i search() for hvorfor dette brukes.
    DISTINCTIVE_IDF = 1.8

    def _score_doc(self, idx: int, q_tf: Counter) -> tuple[float, float]:
        """Returnerer (score, høyeste IDF blant matchende ord i denne chunken)."""
        tf_doc = self.doc_term_freqs[idx]
        doc_len = self.doc_lengths[idx] or 1
        score = 0.0
        max_idf = 0.0
        for term, _q_count in q_tf.items():
            f = tf_doc.get(term)
            if not f:
                continue
            idf = self.idf.get(term, 0.0)
            max_idf = max(max_idf, idf)
            numerator = f * (self.K1 + 1)
            denominator = f + self.K1 * (1 - self.B + self.B * doc_len / self.avgdl)
            score += idf * (numerator / denominator)
        return score, max_idf

    def search(self, query: str, top_k: int = 4) -> list[tuple[Chunk, float]]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        q_tf = Counter(q_tokens)

        # Hvis en chunk kun matcher på generiske ord (lav IDF, f.eks. "leder"
        # som forekommer i mange avsnitt), er det treffet ofte en tilfeldighet
        # fremfor reell relevans. Vi krever derfor at minst ett av de
        # matchende ordene er noenlunde distinkt for at scoren skal telle
        # fullt ut - ellers nedjusteres den kraftig. Dette reduserer falske
        # positive på spørsmål utenfor kunnskapsbasen uten å svekke treff på
        # spørsmål med ett spesifikt nøkkelord (f.eks. "VPN", "feriedager").
        scores = []
        for idx, chunk in enumerate(self.chunks):
            score, max_idf = self._score_doc(idx, q_tf)
            if score <= 0:
                continue
            if max_idf < self.DISTINCTIVE_IDF:
                score *= 0.25
            scores.append((chunk, score))

        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores[:top_k]


# --- Confidence-terskler -----------------------------------------------
# BM25-score er ubegrenset oppad (typisk 0-10 for korte avsnitt som dette),
# og avhenger av antall matchende ord og hvor sjeldne/relevante de er.
# Tersklene under er satt empirisk ved å teste et sett med typiske og
# utenfor-tema-spørsmål mot kunnskapsbasen (se tests/test_rag.py).
#
# Merk (kjent begrensning): dette er nøkkelordbasert søk uten semantisk
# forståelse. Spørsmål som tilfeldigvis deler ord med kunnskapsbasen (f.eks.
# "leder") kan noen ganger få en høyere score enn de fortjener. Vi velger
# bevisst en lav terskel for "usikker" fremfor en streng en, fordi det er
# viktigere å ikke overse gode svar enn å være perfekt presis for
# spørsmål utenfor kunnskapsbasen - kildene vises alltid til brukeren slik
# at de kan vurdere relevansen selv.
HIGH_CONFIDENCE_THRESHOLD = 6.0
LOW_CONFIDENCE_THRESHOLD = 3.0


def score_to_confidence_label(score: float) -> str:
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if score >= LOW_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def score_to_percentage(score: float) -> int:
    # Skalerer slik at HIGH_CONFIDENCE_THRESHOLD havner rundt 60 %,
    # og score >= ca. 10 nærmer seg 99 %. Rent kosmetisk for brukeren.
    pct = min(score / 10.0, 1.0) * 99
    return max(1, round(pct))


class RagEngine:
    def __init__(self, knowledge_dir: str = KNOWLEDGE_DIR):
        self.knowledge_dir = knowledge_dir
        self.chunks = load_knowledge_base(knowledge_dir)
        self.index = BM25Index(self.chunks)

    def reload(self):
        self.chunks = load_knowledge_base(self.knowledge_dir)
        self.index = BM25Index(self.chunks)

    def answer(self, question: str, top_k: int = 4) -> dict:
        results = self.index.search(question, top_k=top_k)

        if not results:
            return {
                "answer": (
                    "Jeg fant ingen relevant informasjon om dette i "
                    "kunnskapsbasen. Usikker - sjekk med HR."
                ),
                "confidence_label": "low",
                "confidence_pct": 0,
                "sources": [],
                "is_uncertain": True,
            }

        best_score = results[0][1]
        confidence_label = score_to_confidence_label(best_score)
        confidence_pct = score_to_percentage(best_score)
        is_uncertain = confidence_label == "low"

        # Slå sammen kontekst fra topp-treffene, gruppert per kilde/overskrift,
        # og bygg et sammensatt svar av de mest relevante utdragene.
        seen_sources = []
        answer_parts = []
        sources = []
        min_score = max(best_score * 0.25, 1.0)
        for chunk, score in results:
            if score < min_score:
                continue
            key = (chunk.source, chunk.heading)
            if key in seen_sources:
                continue
            seen_sources.append(key)
            sources.append(
                {
                    "title": chunk.title,
                    "heading": chunk.heading,
                    "source": chunk.source,
                    "score": round(score, 3),
                    "relevance_pct": score_to_percentage(score),
                    "excerpt": chunk.text,
                }
            )
            answer_parts.append(chunk.text)

        if is_uncertain or not answer_parts:
            answer_text = (
                "Jeg er usikker på svaret basert på det jeg har tilgang til. "
                "Usikker - sjekk med HR eller din nærmeste leder for å være "
                "sikker på at informasjonen er korrekt og oppdatert."
            )
            if sources:
                answer_text += f"\n\nDet nærmeste jeg fant var under \"{sources[0]['heading']}\" i {sources[0]['title']}, men treffet er for svakt til at jeg vil stole på det."
        else:
            top_source = sources[0]
            answer_text = f"{top_source['excerpt']}"
            if len(sources) > 1:
                extra = sources[1]
                answer_text += f"\n\nI tillegg, under \"{extra['heading']}\" ({extra['title']}): {extra['excerpt']}"

        return {
            "answer": answer_text,
            "confidence_label": confidence_label,
            "confidence_pct": confidence_pct,
            "sources": sources[:3],
            "is_uncertain": is_uncertain,
        }
