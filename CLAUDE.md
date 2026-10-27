# CLAUDE.md

Veiledning for Claude Code (og andre bidragsytere) som jobber i dette repoet.

## Om prosjektet

En RAG-basert (Retrieval-Augmented Generation) onboarding-chatbot for den fiktive
bedriften **Risiko AS**, skrevet i Python/Flask. Prosjektet er laget som
prosjektoppgave i **IND422 Risiko- og mulighetsledelse ved Universitetet i Agder (UiA)**.

### Formål

Chatboten er ikke et mål i seg selv, men et demonstrasjonsobjekt. Formålet er å vise:

1. **Risikoen** ved KI-assistenter som gir feilinformasjon til nyansatte – f.eks.
   feil om HMS-rutiner, sykefravær, ferierettigheter eller tilgangsrutiner – og
   hvilke konsekvenser det kan få for den ansatte og virksomheten.
2. **Risikoreduserende tiltak** som kan bygges inn i en slik assistent:
   - **Konfidensterskel:** hvert svar får en confidence-score (BM25-basert) som
     klassifiseres som Sikker / Middels sikker / Usikker
     (`HIGH_CONFIDENCE_THRESHOLD` og `LOW_CONFIDENCE_THRESHOLD` i `rag.py`).
   - **Avstå fra å svare:** under laveste terskel gir chatboten ikke et svar, men
     en tydelig advarsel («Usikker – sjekk med HR»), og spørsmålet logges til
     `logs/usikre_sporsmal.log` for oppfølging.
   - **Kildehenvisning:** hvert svar viser hvilke filer/avsnitt i kunnskapsbasen
     det bygger på, med relevans-prosent, slik at brukeren kan verifisere.
   - **Henvisning til HR for høykonsekvens-spørsmål:** spørsmål om f.eks. HMS og
     ansattes rettigheter (ferie, fravær, lønn, arbeidsavtale) har høy konsekvens
     ved feil svar og skal henvise brukeren til HR/nærmeste leder. Per nå skjer
     HR-henvisning kun ved lav confidence; en egen regel for høykonsekvens-temaer
     uavhengig av confidence er et naturlig neste tiltak.

Svarene er **ekstraktive** (hentet direkte fra kunnskapsbasen), ikke generert av en
språkmodell. Dette er et bevisst risikoreduserende valg for å unngå hallusinering.
Endringer som innfører generativ tekst (LLM) må vurderes opp mot dette.

Når du gjør endringer: tenk alltid på om endringen øker eller reduserer risikoen for
at en nyansatt får feil eller misvisende informasjon. Ikke senk tersklene eller fjern
advarsler/kildehenvisninger uten en begrunnelse.

## Mappestruktur

```
.
├── CLAUDE.md                   # Denne filen
├── README.md                   # Utfyllende dokumentasjon (arkitektur, terskler, begrensninger)
├── app.py                      # Flask-app: nettside, /api/chat, /api/health, logging av usikre spørsmål
├── rag.py                      # RAG-motor: chunking, norsk lett-stemming, BM25-søk, confidence, svarbygging
├── requirements.txt            # Avhengigheter (kun Flask)
├── knowledge/                  # Kunnskapsbasen – eneste kilde til svar (fiktivt innhold)
│   ├── velkomstguide.txt
│   ├── hms-rutiner.txt
│   ├── it-tilgang-oppsett.txt
│   ├── ferie-og-fravaer.txt
│   └── interne-verktoy.txt
├── templates/
│   └── index.html              # Chat-UI
├── static/
│   ├── style.css
│   └── script.js               # Frontend: sender spørsmål, viser svar, badge, advarsel og kilder
├── logs/
│   └── usikre_sporsmal.log     # Opprettes automatisk; ignoreres av git
└── tests/
    └── test_rag.py             # Tester for besvarbare og ubesvarbare spørsmål
```

## Kjøre appen lokalt på Windows

Forutsetninger: Python 3.10 eller nyere (`py --version`) og Git.

I PowerShell, fra prosjektmappen:

```powershell
# 1. Opprett og aktiver virtuelt miljø
py -m venv .venv
.venv\Scripts\Activate.ps1
# (Kommandoprompt/cmd: .venv\Scripts\activate.bat)
# Hvis PowerShell blokkerer skriptet, kjør én gang:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Installer avhengigheter
pip install -r requirements.txt

# 3. Start appen
python app.py
```

Åpne **http://localhost:5000** i nettleseren. Stopp med `Ctrl+C`.

Valgfritt:

```powershell
$env:PORT = "8000"; python app.py      # annen port
$env:FLASK_DEBUG = "0"; python app.py  # slå av debug-modus
```

### Tester

```powershell
python tests\test_rag.py
# eller, med pytest installert:
python -m pytest tests
```

Kjør testene etter endringer i `rag.py`, tersklene eller kunnskapsbasen. Nye
spørsmål som skal besvares eller avvises, legges til i `ANSWERABLE_CASES` /
`UNANSWERABLE_CASES` i `tests/test_rag.py`.

## Arbeidsflyt: branch og pull request

- **Alle endringer gjøres på en egen branch** – aldri direkte på `main`.
  ```powershell
  git checkout main
  git pull
  git checkout -b <kort-beskrivende-navn>
  ```
- Commit med tydelige meldinger som forklarer hva og hvorfor.
- Push branchen og **lever endringen som en pull request** mot `main`:
  ```powershell
  git push -u origin <kort-beskrivende-navn>
  ```
- Beskriv i pull requesten hva som er endret, og – der det er relevant – hvordan
  endringen påvirker risikoen for feilinformasjon (f.eks. endrede terskler,
  ny kunnskapsbase-tekst, endret svarlogikk).
- Sørg for at testene passerer før pull requesten sendes til gjennomgang.

## Konvensjoner

- Kode, kommentarer, UI-tekst og dokumentasjon skrives på **norsk**.
- Hold løsningen lett: ingen tunge ML-avhengigheter eller eksterne API-er uten at
  det er et bevisst valg.
- Innholdet i `knowledge/` er fiktivt og gjelder kun Risiko AS i prosjektoppgaven.
