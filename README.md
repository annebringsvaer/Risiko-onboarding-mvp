# Risiko AS – Onboarding-chatbot (MVP)

En enkel RAG-basert (Retrieval-Augmented Generation) chatbot som hjelper nyansatte
hos den fiktive bedriften **Risiko AS** med spørsmål om HMS, IT-tilgang, ferie/fravær,
interne verktøy og generell onboarding. Bygget med Python/Flask og uten tunge
ML-avhengigheter.

## Innhold

- [Hva appen gjør](#hva-appen-gjør)
- [Arkitektur](#arkitektur)
- [Kom i gang lokalt](#kom-i-gang-lokalt)
- [Bruk](#bruk)
- [Demo](#demo)
- [Confidence-score og usikkerhetsvarsel](#confidence-score-og-usikkerhetsvarsel)
- [Loggføring av usikre spørsmål](#loggføring-av-usikre-spørsmål)
- [Prosjektstruktur](#prosjektstruktur)
- [Kjøre tester](#kjøre-tester)
- [Kjente begrensninger](#kjente-begrensninger)
- [Mulige neste steg](#mulige-neste-steg)

## Hva appen gjør

1. **Kunnskapsbase** (`/knowledge`): 5 tekstfiler med fiktiv, men realistisk
   onboarding-informasjon for Risiko AS: HMS-rutiner, IT-tilgang/oppsett,
   ferie- og fraværsregler, en velkomstguide, og en oversikt over interne verktøy.
2. **Nettside med chat-boks**: En ryddig, responsiv nettside (`/templates/index.html`
   + `/static`) hvor brukeren kan stille spørsmål i en chat-boks og få svar tilbake
   sammen med kildehenvisninger.
3. **RAG-logikk** (`rag.py`): Når du stiller et spørsmål, søker systemet gjennom
   kunnskapsbasen etter de mest relevante utdragene (se [Arkitektur](#arkitektur)),
   og bruker disse som kontekst til å sette sammen et svar.
4. **Usikkerhetsindikator**: Hvis systemet ikke finner god nok kontekst til å svare
   sikkert, vises en tydelig advarsel ("⚠️ Usikker – sjekk med HR") i stedet for et
   (potensielt feilaktig) svar, og spørsmålet logges til `logs/usikre_sporsmal.log`.
5. **Confidence-score**: Hvert svar vises med en badge som "Sikker · 82% confidence",
   "Middels sikker · 45% confidence" eller "Usikker · 12% confidence", basert på hvor
   godt spørsmålet matcher kunnskapsbasen.

## Arkitektur

Ingen eksterne AI-API-er eller tunge ML-biblioteker er nødvendig - alt kjører lokalt
med ren Python:

```
Bruker skriver spørsmål
        │
        ▼
Flask API  (/api/chat)
        │
        ▼
rag.py: RagEngine.answer()
        │
        ├─ 1. Last kunnskapsbase-filer, del i avsnitt ("chunks") med overskrift
        ├─ 2. Tokeniser spørsmål og chunks (norsk lett-stemming + stoppord)
        ├─ 3. Ranger chunks med BM25 (nøkkelordbasert relevanssøk)
        ├─ 4. Bygg svar fra de mest relevante chunkene (kontekst)
        ├─ 5. Beregn confidence-score fra topp-treffets BM25-score
        └─ 6. Hvis lav confidence → usikkerhetsvarsel + logg spørsmålet
        │
        ▼
JSON-svar med: answer, confidence_label, confidence_pct, sources, is_uncertain
        │
        ▼
Chat-UI viser svar + confidence-badge + (ev.) usikkerhetsvarsel + kildeliste
```

**Hvorfor BM25 og ikke embeddings/vector-database?** For en kunnskapsbase i denne
størrelsen (5 filer, ~50 avsnitt) gir et velprøvd nøkkelordbasert søk (samme prinsipp
som Elasticsearch/Lucene bruker) god nok presisjon uten å kreve GPU, API-nøkler eller
tunge Python-pakker (`sentence-transformers`, `faiss` osv.). Det gjør MVP-en lett å
kjøre offline og lett å forstå/vedlikeholde. Se
[Kjente begrensninger](#kjente-begrensninger) og
[Mulige neste steg](#mulige-neste-steg) for tanker om å bytte til embeddings i en
mer moden versjon.

Svaret som gis til brukeren er hentet direkte (ekstraktivt) fra de mest relevante
utdragene i kunnskapsbasen, eventuelt satt sammen fra to relaterte avsnitt. Det
brukes altså ingen generativ språkmodell til å "finne på" tekst - dette gjør at
appen aldri kan hallusinere fakta som ikke finnes i kildene, på bekostning av at
svarene noen ganger er litt mindre "flytende" enn en LLM-generert tekst ville vært.

## Kom i gang lokalt

### Forutsetninger

- Python 3.10 eller nyere
- pip

### Installasjon

```bash
# 1. Klon/gå til prosjektmappen
cd Risiko-onboarding-mvp

# 2. (Anbefalt) Opprett et virtuelt miljø
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 3. Installer avhengigheter (kun Flask)
pip install -r requirements.txt
```

### Kjør appen

```bash
python3 app.py
```

Appen starter på **http://localhost:5000**. Åpne denne adressen i nettleseren din.

Du kan endre port med miljøvariabelen `PORT`, f.eks. `PORT=8000 python3 app.py`.

## Bruk

Åpne nettsiden, skriv et spørsmål i chat-boksen nederst (eller klikk på et av
forslagene i sidepanelet), og trykk «Send». Du får:

- Et svar basert på kunnskapsbasen
- En confidence-badge (Sikker / Middels sikker / Usikker)
- En liste over hvilke kilder (fil + avsnitt) svaret er basert på, med
  relevans-prosent for hver

Eksempler å prøve:

- «Hvor mange feriedager har jeg?» → svar fra «Ferie» (30 virkedager) + henvisning til HR
- «Hva er lønnen min neste år?» → avstår: «lønn» står bare i «Permisjon uten lønn»
- «Hvordan får jeg tilgang til VPN?»
- «Hva gjør jeg hvis det brenner på kontoret?» → svar + henvisning til HR (høy konsekvens)
- «Hvem er fadderen min?»
- «Hvilket system bruker vi for oppgavestyring?»
- «Kan jeg jobbe hjemmefra?» → svar fra «VPN og fjernarbeid»
- «Kan jeg ta med hunden min på kontoret?» → avstår: treffet («Utstyr») handler ikke om «hunden»
- Noe helt urelatert, f.eks. «Hvor mye koster aksjen deres?» → skal gi usikkerhetsvarsel

## Demo
Nedenfor vises en kort demonstrasjon av hvordan applikasjonen fungerer:

https://github.com/user-attachments/assets/de3e497d-8fd8-41f0-832b-6e78efc89520

## Confidence-score og usikkerhetsvarsel

Hvert svar får en BM25-relevans-score for det best matchende utdraget i kunnskaps-
basen. Hvert spørsmål får også et konsekvensnivå: høy (HMS, rettigheter, lønn,
permisjon, fravær) eller lav (IT, verktøy, praktisk). Spørsmål med høy konsekvens
har strengere terskler. Alle terskler og kategorier står som konstanter øverst i
`rag.py`.

| Nivå            | Lav konsekvens | Høy konsekvens | Håndtering                                          |
|-----------------|----------------|----------------|-----------------------------------------------------|
| Sikker (high)   | ≥ 6.0          | ≥ 7.5          | Svar (høy konsekvens: svar + henvisning til HR)     |
| Middels (medium)| 3.0 – 6.0      | 4.0 – 7.5      | Svar (høy konsekvens: svar + henvisning til HR)     |
| Usikker (low)   | < 3.0          | < 4.0          | Avstår alltid: «Jeg vet ikke», henviser til HR, logges |

I tillegg gjøres en **dekningssjekk**: minst 60 % (`MIN_DEKNING = 0.6`) av de
viktigste ordene i spørsmålet (sjeldne eller ukjente ord, f.eks. «hund») må finnes i
de to beste treffene samlet. Ellers avstår chatboten uansett score. Vanlige ord som
«arbeidsplassen», «kontoret» og «dager» teller ikke, og et ord som bare står i et
uttrykk om et annet tema (`ANNET_TEMA_UTTRYKK`, f.eks. «permisjon uten lønn») regnes
ikke som dekning. Grensene står øverst i `rag.py`.

Kilde og usikkerhetsnivå står alltid i selve svarteksten, også ved høy confidence.
Tersklene er satt empirisk (se `tests/test_rag.py`). Den strengere terskelen for
høy konsekvens fanger blant annet at «Når får jeg lønn?» tidligere ble besvart
med avsnittet «Permisjon uten lønn».

## Loggføring av usikre spørsmål

Alle spørsmål som får «Usikker»-status logges til `logs/usikre_sporsmal.log`
(én linje per spørsmål, med tidsstempel og confidence-score), f.eks.:

```
2026-09-11T09:31:38+00:00	confidence=0%	spørsmål: Hvor mye koster aksjen deres på børs?
```

I tillegg logges *hvert* svar som én JSON-linje i `logs/svarlogg.jsonl`, med
feltene `kategori`, `konsekvens`, `confidence_pct` og `faktisk_handtering`
(`svar`, `avstå` eller `henvis`), i samme format som risikodashboardet
(`risk_dashboard.py`).

Tanken er at HR/administrator periodisk kan gå gjennom denne loggen for å se hvilke
spørsmål ansatte faktisk stiller som chatboten ikke kan svare godt på, og bruke det
til å utvide kunnskapsbasen.

## Risikodashboard

Dashboardet er registrert i Flask-appen (`risk_bp` i `app.py`) og ligger på
**http://localhost:5000/risiko**. Rådata finnes som JSON på `/api/risiko`.
Det har to datakilder:

1. **Svarloggen** (`logs/svarlogg.jsonl`) viser hvordan chatboten faktisk håndterte
   spørsmålene: antall `svar`, `henvis` (svar med henvisning til HR, regnes som svar)
   og `avstå`, fordelt per kategori og konsekvensnivå. Konsekvensen hentes fra loggen,
   ikke fra kategorien. Dashboardet viser **bare aggregerte tall**. Spørsmålstekst
   og tidspunkt per henvendelse vises ikke. Indikatoren «Høy konsekvens besvart uten
   HR-henvisning» er et oppfølgingspunkt som bør undersøkes, ikke et automatisk regelbrudd.
2. **Manuelle testresultater** (`data/risiko_tester.json`, valgfri) viser om svarene var
   *riktige*, vurdert mot fasit. `henvis` regnes som et svar, men aldri automatisk som
   korrekt. Mangler filen, viser dashboardet at testdata ikke er lagt inn, og ingen
   resultater fylles inn. Filen inneholder testprotokollen fra testingen av v1.0-frys
   (27 spørsmål, før og etter forbedringene):
   - `manuell_vurdering` (`ja`/`delvis`/`nei`) er autoritativ. `svar_korrekt` brukes
     bare når den mangler.
   - `usikker` er et historisk utfall fra før-versjonen. Det telles for seg og regnes
     ikke som avståelse.
   - Konsekvens hentes bare fra `konsekvens_excel`. Mangler den, vises
     «Ikke klassifisert», og testen tas ikke med i tall som krever kjent konsekvens.
     Kategori `Ukjent` eller manglende kategori beholdes uten gjetning.
   - Kunnskapsstyrke uten data vises som «Ikke vurdert».
   - `oppsummering_excel` kontrolleres mot tallene beregnet fra radene, og avvik
     rapporteres i dashboardet uten at vurderingene endres.

Loggdata sier altså noe om *håndtering*, ikke om *riktighet*. Confidence (BM25) er
heller ikke en sannsynlighet for at svaret er riktig.

## Prosjektstruktur

```
.
├── app.py                      # Flask-app: nettside + /api/chat + /api/health, registrerer dashboardet
├── rag.py                      # RAG-motor: chunking, BM25-søk, svargenerering
├── risk_dashboard.py           # Risikodashboard: /risiko + /api/risiko (aggregerer svarloggen og testdata)
├── requirements.txt
├── knowledge/                  # Kunnskapsbasen (kilden til alle svar)
│   ├── velkomstguide.txt
│   ├── hms-rutiner.txt
│   ├── it-tilgang-oppsett.txt
│   ├── ferie-og-fravaer.txt
│   └── interne-verktoy.txt
├── templates/
│   ├── index.html              # Chat-UI
│   └── risiko.html             # Risikodashboard
├── static/
│   ├── style.css
│   ├── script.js
│   └── risiko.js               # Frontend for risikodashboardet
├── logs/
│   ├── svarlogg.jsonl          # Alle svar (opprettes automatisk)
│   └── usikre_sporsmal.log     # Logg over usikre spørsmål (opprettes automatisk)
└── tests/
    ├── test_rag.py             # Tester for RAG-motoren
    └── test_risk_dashboard.py  # Tester for risikodashboardet og integrasjonen
```

## Kjøre tester

```bash
python3 tests/test_rag.py
python3 tests/test_risk_dashboard.py
# eller, hvis pytest er installert:
python3 -m pytest tests/
```

Testene sjekker at typiske onboarding-spørsmål gir svar med riktig innhold og
tilstrekkelig confidence, og at åpenbart urelaterte spørsmål blir flagget som usikre.

## Kjente begrensninger

Dette er en MVP, og noen forenklinger er bevisste for å holde løsningen enkel,
rask å kjøre og lett å forstå:

- **Nøkkelordbasert søk, ikke semantisk søk.** Systemet bruker BM25 (som
  Elasticsearch/Lucene), ikke embeddings/vektorsøk. Det betyr at det kan streve
  med spørsmål som bruker helt andre ord enn kunnskapsbasen selv om betydningen
  er den samme, og at det (sjelden) kan gi en for høy confidence-score på
  spørsmål som tilfeldigvis deler enkeltord med kunnskapsbasen uten å være
  reelt relevante. Kildevisningen i UI-et er ment som en ekstra sjekk for
  brukeren i slike tilfeller.
- **Ekstraktive, ikke genererte, svar.** Svarene er utdrag direkte fra
  kunnskapsbasen (eventuelt satt sammen fra 1-2 avsnitt), ikke fritt formulert
  tekst fra en språkmodell. Dette unngår hallusinering, men gir noen ganger
  litt "brå" svar sammenlignet med en ordentlig LLM-generert respons.
- **Enkel norsk lett-stemming**, ikke en fullverdig lingvistisk stemmer -
  fanger de vanligste bøyningsformene (bestemt/ubestemt form, entall/flertall),
  men ikke alle uregelrette former eller sammensatte ord.
- **Ingen brukerautentisering eller flerbrukerstøtte** - dette er en MVP for
  demonstrasjon, ikke en produksjonsklar løsning.

## Mulige neste steg

- Bytte ut/supplere BM25-søket med embeddings (f.eks. via en lokal
  sentence-transformer eller et API) for bedre semantisk gjenfinning.
- Koble på en språkmodell (LLM) for å generere mer naturlige, sammenhengende
  svar basert på det gjenfunnede konteksten, i stedet for rene utdrag.
- Enkelt admin-grensesnitt for å lese `logs/usikre_sporsmal.log` og redigere
  kunnskapsbasen direkte fra nettleseren.
- Persistent lagring (database) for chat-historikk og statistikk over ofte
  stilte spørsmål.
