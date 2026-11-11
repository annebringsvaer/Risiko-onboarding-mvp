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
2. **Risikoreduserende tiltak** som er bygget inn i assistenten. Alle kategorier
   og terskler ligger som konstanter øverst i `rag.py`:
   - **Konsekvensbasert inndeling:** hvert spørsmål får en kategori (fra dokumentet
     til beste treff, se `KATEGORIER`) og et konsekvensnivå. Høy konsekvens (`hoy`)
     gjelder HMS, rettigheter, lønn, permisjon og fravær. Lav konsekvens (`lav`)
     gjelder IT, verktøy og praktisk informasjon. Et spørsmål regnes også som høy
     konsekvens hvis det inneholder et ord i `HOY_KONSEKVENS_NOKKELORD`. Ved tvil
     velges alltid det strengeste nivået.
   - **Konfidensterskel:** hvert svar får en confidence-score (BM25-basert) som
     klassifiseres som Sikker / Middels sikker / Usikker. Tersklene står i
     `TERSKLER`. Høy konsekvens har strengere terskler
     (`HOY_KONSEKVENS_LOW_THRESHOLD`, `HOY_KONSEKVENS_HIGH_THRESHOLD`) enn lav
     (`LOW_CONFIDENCE_THRESHOLD`, `HIGH_CONFIDENCE_THRESHOLD`).
   - **Avstå fra å svare:** under laveste terskel avstår chatboten *alltid*. Den
     sier at den ikke vet og henviser til HR, uten å vise utdrag fra
     kunnskapsbasen som svar.
   - **Dekningssjekk:** de viktigste ordene i spørsmålet må finnes i det beste
     treffet (`MIN_DEKNING`, `VIKTIG_ORD_MIN_IDF`, `GENERISKE_ORD`,
     `MIN_PREFIKS_LENGDE`). Ellers avstår chatboten *uansett score* og sier
     hvilke ord treffet ikke handler om. Dette fjerner feilene fra sprint 2, der
     f.eks. «Kan jeg ta med hunden min på kontoret?» ble besvart med avsnittet
     «Utstyr» fordi begge inneholder «kontor». «Hjemmefra» er lagt inn som
     synonym for «fjernarbeid» (`_SYNONYMS`) og treffer «VPN og fjernarbeid».
   - **Henvisning til HR:** svar på spørsmål med høy konsekvens avsluttes alltid
     med at viktig informasjon bør bekreftes hos HR eller nærmeste leder.
   - **Kildehenvisning og usikkerhet:** hvert svar, også ved høy confidence,
     oppgir kilde (dokument og avsnitt) og usikkerhetsnivå i selve svarteksten.
     Kildelisten vises i tillegg alltid i chat-UI-et.
   - **Logging:** hvert svar logges som én JSON-linje i `logs/svarlogg.jsonl`
     med feltene `tidspunkt`, `sporsmal`, `kategori`, `konsekvens` (`hoy`/`lav`),
     `confidence_pct` og `faktisk_handtering` (`svar`, `avstå` eller `henvis`).
     Formatet følger `risk_dashboard.py` på branchen `risiko_dashboard`. Spørsmål
     der chatboten avstår, logges i tillegg til `logs/usikre_sporsmal.log`.

   Faktisk håndtering:

   | Situasjon                                   | `faktisk_handtering` |
   |---------------------------------------------|----------------------|
   | Score under terskelen for konsekvensnivået  | `avstå`              |
   | Viktige ord i spørsmålet mangler i treffet  | `avstå`              |
   | Over terskelen, høy konsekvens              | `henvis`             |
   | Over terskelen, lav konsekvens              | `svar`               |

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
├── app.py                      # Flask-app: nettside, /api/chat, /api/health, logging av svar
├── rag.py                      # RAG-motor: kategorier/terskler (øverst), BM25-søk, confidence, svarbygging
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
│   └── script.js               # Frontend: svar, confidence- og konsekvens-badge, varsel og kilder
├── logs/
│   ├── svarlogg.jsonl          # Alle svar (kategori, konsekvens, håndtering); ignoreres av git
│   └── usikre_sporsmal.log     # Spørsmål chatboten avsto fra; ignoreres av git
└── tests/
    └── test_rag.py             # Tester for svar, avstå, konsekvensregel, kilder og logging
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

Kjør testene etter endringer i `rag.py`, tersklene, kategoriene eller
kunnskapsbasen. Testspørsmål legges til i `tests/test_rag.py`:

- `ANSWERABLE_CASES`: skal besvares med riktig innhold.
- `UNANSWERABLE_CASES`: utenfor tema, skal avvises.
- `HOY_KONSEKVENS_AVSTA_CASES`: høy konsekvens med svakt treff, skal gi `avstå`.
- `HANDTERING_CASES`: forventet kategori, konsekvens og håndtering.
- `DEKNING_AVSTA_CASES`: høy nok score, men viktige ord mangler i treffet. Skal gi
  `avstå`.

Spørsmålene i sidepanelet (`templates/index.html`) testes også. Ingen av dem skal
gi `avstå`, slik at demoen ikke starter med at chatboten avstår.

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
