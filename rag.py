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

# =======================================================================
# Risikostyring: kategorier, konsekvensnivå og terskler
# -----------------------------------------------------------------------
# Disse konstantene styrer når chatboten svarer, når den henviser til HR,
# og når den avstår fra å svare. Juster dem her.
# =======================================================================

# Kategorinavn og konsekvens ("hoy"/"lav") følger KATEGORIER i
# risk_dashboard.py (branch risiko_dashboard), slik at loggen kan brukes
# direkte i risikodashboardet. Kategorien settes ut fra hvilket dokument
# det beste søketreffet kommer fra.
KATEGORIER = {
    "hms-rutiner.txt": {"navn": "HMS", "konsekvens": "hoy"},
    "ferie-og-fravaer.txt": {"navn": "Ferie og fravær", "konsekvens": "hoy"},
    "it-tilgang-oppsett.txt": {"navn": "IT-tilgang", "konsekvens": "lav"},
    "interne-verktoy.txt": {"navn": "Interne verktøy", "konsekvens": "lav"},
    "velkomstguide.txt": {"navn": "Generell onboarding", "konsekvens": "lav"},
}
UKJENT_KATEGORI = "Ukjent"

# Spørsmål som inneholder ett av disse ordene (også som del av et ord,
# f.eks. "sykefraværet", "lønnsslipp") regnes alltid som høy konsekvens,
# uansett hvilket dokument treffet kommer fra: HMS, rettigheter, lønn,
# permisjon og fravær.
HOY_KONSEKVENS_NOKKELORD = [
    # HMS
    "hms", "brann", "brenn", "evakuer", "rømning", "skade", "ulykke",
    "avvik", "førstehjelp", "hjertestarter", "verneombud", "varsl",
    "trakasser", "mobbing", "rusmidl", "arbeidsmiljø",
    # Rettigheter og arbeidsforhold
    "rettighet", "arbeidsavtale", "prøvetid", "oppsigelse", "overtid",
    "pensjon", "forsikring", "tariff",
    # Lønn
    "lønn", "feriepenger",
    # Permisjon og fravær
    "permisjon", "fravær", "sykemeld", "egenmeld", "sykdom", "syke",
    "omsorgsdag", "ferie",
]

# Terskler for BM25-score per konsekvensnivå. Under "lav" avstår chatboten
# ALLTID fra å svare. Mellom "lav" og "hoy" vises svaret som middels sikkert.
# Høy konsekvens har strengere terskler: et feil svar om HMS eller
# rettigheter er mer alvorlig enn et feil svar om hvilket chatteverktøy vi
# bruker. Tersklene er kalibrert mot spørsmålene i tests/test_rag.py.
LOW_CONFIDENCE_THRESHOLD = 3.0
HIGH_CONFIDENCE_THRESHOLD = 6.0
HOY_KONSEKVENS_LOW_THRESHOLD = 4.0
HOY_KONSEKVENS_HIGH_THRESHOLD = 7.5

TERSKLER = {
    "lav": {"lav": LOW_CONFIDENCE_THRESHOLD, "hoy": HIGH_CONFIDENCE_THRESHOLD},
    "hoy": {"lav": HOY_KONSEKVENS_LOW_THRESHOLD, "hoy": HOY_KONSEKVENS_HIGH_THRESHOLD},
}

# Faktisk håndtering, samme verdier som i loggen og risikodashboardet.
HANDTERING_SVAR = "svar"
HANDTERING_AVSTA = "avstå"
HANDTERING_HENVIS = "henvis"

HR_KONTAKT = "HR (hr@risiko.no, tlf. 22 00 00 10) eller nærmeste leder"

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


# --- Confidence --------------------------------------------------------
# BM25-score er ubegrenset oppad (typisk 0-10 for korte avsnitt som dette),
# og avhenger av antall matchende ord og hvor sjeldne/relevante de er.
# Tersklene (se TERSKLER øverst) er satt empirisk ved å teste et sett med
# typiske og utenfor-tema-spørsmål mot kunnskapsbasen (se tests/test_rag.py).
#
# Merk (kjent begrensning): dette er nøkkelordbasert søk uten semantisk
# forståelse. Spørsmål som tilfeldigvis deler ord med kunnskapsbasen (f.eks.
# "lønn" i "Permisjon uten lønn") kan få en høyere score enn de fortjener.
# Derfor er tersklene strengere for spørsmål med høy konsekvens, og kilden
# vises alltid slik at brukeren kan vurdere relevansen selv.

CONFIDENCE_TEKST = {"high": "Sikker", "medium": "Middels sikker", "low": "Usikker"}


def score_to_confidence_label(score: float, konsekvens: str = "lav") -> str:
    terskler = TERSKLER[konsekvens]
    if score >= terskler["hoy"]:
        return "high"
    if score >= terskler["lav"]:
        return "medium"
    return "low"


def score_to_percentage(score: float) -> int:
    # Skalerer slik at HIGH_CONFIDENCE_THRESHOLD havner rundt 60 %,
    # og score >= ca. 10 nærmer seg 99 %. Rent kosmetisk for brukeren.
    pct = min(score / 10.0, 1.0) * 99
    return max(1, round(pct))


def er_hoy_konsekvens_sporsmal(question: str) -> bool:
    tekst = question.lower()
    return any(ord_ in tekst for ord_ in HOY_KONSEKVENS_NOKKELORD)


def klassifiser(question: str, toppkilde: str | None) -> tuple[str, str]:
    """Returnerer (kategori, konsekvens) for et spørsmål.

    Kategorien kommer fra dokumentet til beste treff. Konsekvensen er høy
    hvis enten kategorien eller selve spørsmålet tilsier det - ved tvil
    velges alltid det strengeste nivået."""
    definisjon = KATEGORIER.get(toppkilde or "")
    kategori = definisjon["navn"] if definisjon else UKJENT_KATEGORI
    if (definisjon and definisjon["konsekvens"] == "hoy") or er_hoy_konsekvens_sporsmal(question):
        return kategori, "hoy"
    return kategori, "lav"


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
            kategori, konsekvens = klassifiser(question, None)
            return self._avsta(kategori, konsekvens, 0, None, [])

        best_score = results[0][1]
        kategori, konsekvens = klassifiser(question, results[0][0].source)
        confidence_label = score_to_confidence_label(best_score, konsekvens)
        confidence_pct = score_to_percentage(best_score)

        # Samle kontekst fra topp-treffene, gruppert per kilde/overskrift.
        seen_sources = []
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

        # Under terskelen avstår chatboten alltid - uten å vise utdrag fra
        # kunnskapsbasen som svar.
        if confidence_label == "low" or not sources:
            return self._avsta(kategori, konsekvens, confidence_pct, sources[:1], sources[:3])

        top_source = sources[0]
        answer_text = top_source["excerpt"]
        if len(sources) > 1:
            extra = sources[1]
            answer_text += f"\n\nI tillegg, under \"{extra['heading']}\" ({extra['title']}): {extra['excerpt']}"

        answer_text += "\n\n" + _kilde_og_usikkerhet(sources[:2], confidence_label, confidence_pct)

        if konsekvens == "hoy":
            handtering = HANDTERING_HENVIS
            answer_text += (
                "\n\nViktig: Dette gjelder HMS eller dine rettigheter. Viktig "
                f"informasjon bør bekreftes hos {HR_KONTAKT}."
            )
        else:
            handtering = HANDTERING_SVAR

        return {
            "answer": answer_text,
            "confidence_label": confidence_label,
            "confidence_pct": confidence_pct,
            "sources": sources[:3],
            "is_uncertain": False,
            "kategori": kategori,
            "konsekvens": konsekvens,
            "faktisk_handtering": handtering,
        }

    def _avsta(self, kategori, konsekvens, confidence_pct, naermeste, sources) -> dict:
        answer_text = (
            "Jeg vet ikke svaret på dette. Jeg fant ikke god nok informasjon i "
            "kunnskapsbasen til å gi et pålitelig svar, så jeg avstår heller "
            f"enn å gjette. Ta kontakt med {HR_KONTAKT}."
        )
        if naermeste:
            answer_text += (
                f"\n\nKilde: ingen pålitelig kilde funnet. Nærmeste treff var "
                f"«{naermeste[0]['heading']}» i {naermeste[0]['title']}, men "
                "treffet er for svakt til at jeg vil stole på det."
            )
        else:
            answer_text += "\n\nKilde: ingen relevant kilde funnet i kunnskapsbasen."
        answer_text += f"\nUsikkerhet: Usikker ({confidence_pct} % confidence)."
        return {
            "answer": answer_text,
            "confidence_label": "low",
            "confidence_pct": confidence_pct,
            "sources": sources,
            "is_uncertain": True,
            "kategori": kategori,
            "konsekvens": konsekvens,
            "faktisk_handtering": HANDTERING_AVSTA,
        }


def _kilde_og_usikkerhet(sources: list[dict], confidence_label: str, confidence_pct: int) -> str:
    kilder = "; ".join(f"{s['title']} – «{s['heading']}»" for s in sources)
    return (
        f"Kilde: {kilder}.\n"
        f"Usikkerhet: {CONFIDENCE_TEKST[confidence_label]} ({confidence_pct} % confidence). "
        "Confidence sier hvor godt spørsmålet matcher kunnskapsbasen, ikke om svaret er riktig."
    )
