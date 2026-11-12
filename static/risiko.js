"use strict";

/*
 * Risikodashboard. Leser KUN /api/risiko.
 * Bevisst uavhengig av /api/chat og av hvordan C implementerer søket.
 * All tekst fra API-et legges inn med textContent (unngår HTML-injeksjon).
 */

const side = {
  fase: document.getElementById("risk-phase"),
  scope: document.getElementById("risk-scope"),
  notice: document.getElementById("risk-notice"),
  kilder: document.getElementById("kpi-sources"),
  vurderte: document.getElementById("kpi-assessed"),
  vurderteTekst: document.getElementById("kpi-assessed-note"),
  korrekte: document.getElementById("kpi-success"),
  korrekteTekst: document.getElementById("kpi-success-note"),
  feil: document.getElementById("kpi-errors"),
  feilTekst: document.getElementById("kpi-errors-note"),
  henvis: document.getElementById("kpi-henvis"),
  feilAvsta: document.getElementById("kpi-feil-avsta"),
  usikker: document.getElementById("kpi-usikker"),
  uklassifisert: document.getElementById("kpi-uklassifisert"),
  kildeDetalj: document.getElementById("risk-source-detail"),
  kontrollStatus: document.getElementById("risk-control-status"),
  kontrollRader: document.getElementById("risk-control-rows"),
  kategorier: document.getElementById("risk-categories"),
  kilderListe: document.getElementById("risk-evidence"),
  svakheter: document.getElementById("risk-insights"),
  tester: document.getElementById("risk-test-rows"),
  testAntall: document.getElementById("risk-test-count"),
  loggAntall: document.getElementById("log-count"),
  loggVarsel: document.getElementById("log-notice"),
  loggSvar: document.getElementById("log-svar"),
  loggHenvis: document.getElementById("log-henvis"),
  loggAvsta: document.getElementById("log-avsta"),
  loggAvstaTekst: document.getElementById("log-avsta-note"),
  loggHoyUten: document.getElementById("log-hoy-uten"),
  loggRader: document.getElementById("log-rows"),
};

let risikodata = null;

function element(tag, klasse = "", tekst = null) {
  const node = document.createElement(tag);
  if (klasse) node.className = klasse;
  if (tekst !== null) node.textContent = String(tekst);
  return node;
}

function prosent(tall) {
  return typeof tall === "number" ? `${String(tall).replace(".", ",")} %` : "—";
}

function konsekvensTekst(verdi) {
  if (verdi === "hoy") return ["Høy konsekvens", "high"];
  if (verdi === "lav") return ["Lav konsekvens", "low"];
  return ["Ikke klassifisert", ""];
}

function visVarsler(varsler) {
  const meldinger = Array.isArray(varsler) ? varsler : [];
  side.notice.hidden = meldinger.length === 0;
  side.notice.textContent = meldinger.join(" ");
}

function skrivNokkeltall(data, fase) {
  const s = data.statistikk[fase];
  side.kilder.textContent = data.kunnskapskilder;
  side.vurderte.textContent = s.vurderte;
  side.vurderteTekst.textContent = `${s.antall} testspørsmål registrert i fasen`;
  side.korrekte.textContent = prosent(s.andel_riktig_pct);
  side.korrekteTekst.textContent = s.vurderte
    ? `${s.riktig} riktig · ${s.delvis} delvis · ${s.feil} feil (manuell vurdering)`
    : "Manuell vurdering (Ja / Delvis / Nei)";

  // Feil uten konsekvensklassifisering regnes verken som høy eller lav risiko.
  if (!s.vurderte) {
    side.feil.textContent = "—";
    side.feilTekst.textContent = "Feilaktige svar med klassifisert høy konsekvens";
  } else if (s.feil_uten_konsekvens > 0 && s.risikofylte_feil === 0) {
    side.feil.textContent = "Ikke klassifisert";
    side.feilTekst.textContent = `${s.feil_uten_konsekvens} feilaktige svar mangler konsekvensklassifisering`;
  } else {
    side.feil.textContent = String(s.risikofylte_feil);
    side.feilTekst.textContent = s.feil_uten_konsekvens
      ? `Klassifisert høy konsekvens. ${s.feil_uten_konsekvens} feil til er ikke klassifisert`
      : "Feilaktige svar med klassifisert høy konsekvens";
  }

  const harTester = s.antall > 0;
  side.henvis.textContent = harTester ? s.henvis : "—";
  side.feilAvsta.textContent = harTester ? s.feil_avsta : "—";
  side.usikker.textContent = harTester ? s.usikker : "—";
  side.uklassifisert.textContent = harTester ? s.konsekvens.ikke_klassifisert : "—";
  side.scope.textContent = `Fase: ${fase === "for" ? "før" : "etter"} endringer · ${s.vurderte} av ${s.antall} tester vurdert`;
}

function skrivDatakilde(data) {
  const m = data.datakilder && data.datakilder.manuelle_tester;
  if (!m || !m.antall_rader) {
    side.kildeDetalj.textContent = "Ingen testprotokoll lastet inn. Tallene under kommer ikke fra svarloggen.";
    return;
  }
  const deler = [
    m.kilde ? `Kilde: ${m.kilde}` : null,
    m.testet_dato ? `testet ${m.testet_dato}` : null,
    m.for_versjon ? `før-versjon ${m.for_versjon}` : null,
    m.etter_versjon ? `etter-versjon ${m.etter_versjon}` : null,
    `${m.antall_rader} rader`,
  ].filter(Boolean);
  side.kildeDetalj.textContent = `${deler.join(" · ")}. Adskilt fra faktisk bruk (svarloggen).`;
}

const KONTROLLNAVN = {
  antall: "Antall spørsmål",
  riktig: "Riktig håndtert (Ja)",
  delvis: "Delvis riktig",
  feil: "Feil håndtert (Nei)",
  andel_riktig_pct: "Andel riktig",
  feilsvar_uten_kilde: "Feilsvar uten kilde",
  feil_kilde: "Feil kilde",
  feil_avsta: "Feilaktige avståelser",
  henvis: "HR-henvisninger",
};

function skrivKontroll(data, fase) {
  side.kontrollRader.replaceChildren();
  const kontroll = data.kontroll && data.kontroll[fase];
  if (!kontroll) {
    side.kontrollStatus.textContent = "Ingen oppsummering";
    const rad = element("tr");
    const celle = element("td", "risk-empty", "Testprotokollen har ingen oppsummering for denne fasen.");
    celle.colSpan = 4;
    rad.append(celle);
    side.kontrollRader.append(rad);
    return;
  }
  side.kontrollStatus.textContent = kontroll.samsvar ? "Samsvarer" : "Avvik funnet";
  for (const felt of kontroll.felter) {
    const rad = element("tr");
    const visning = v => felt.felt.endsWith("_pct") ? prosent(v) : (v === null || v === undefined ? "—" : v);
    const status = element("span", `risk-state ${felt.samsvar ? "ok" : "bad"}`, felt.samsvar ? "Samsvar" : "Avvik");
    const statusCelle = element("td");
    statusCelle.append(status);
    rad.append(
      element("td", "", KONTROLLNAVN[felt.felt] || felt.felt),
      element("td", "", visning(felt.oppgitt)),
      element("td", "", visning(felt.beregnet)),
      statusCelle
    );
    side.kontrollRader.append(rad);
  }
}

function skrivKategorier(data, fase) {
  side.kategorier.replaceChildren();
  for (const kategori of data.kategorier) {
    const statistikk = kategori[fase];
    const wrapper = element("div", "risk-category");
    const header = element("div", "risk-category-head");
    header.append(
      element("span", "risk-category-name", kategori.navn),
      element("span", "risk-category-note", statistikk.vurderte
        ? `${statistikk.riktig} av ${statistikk.vurderte} riktig${statistikk.delvis ? ` · ${statistikk.delvis} delvis` : ""}`
        : "Ingen vurderte tester")
    );

    const meter = element("div", "risk-meter");
    meter.setAttribute("role", "img");
    meter.setAttribute("aria-label", statistikk.vurderte
      ? `${kategori.navn}: ${statistikk.andel_korrekt} prosent korrekt håndtert i testene`
      : `${kategori.navn}: ingen vurderte tester`);
    const fyll = element("div", "risk-meter-fill");
    if (statistikk.andel_korrekt !== null) {
      fyll.style.width = `${statistikk.andel_korrekt}%`;
      if (statistikk.andel_korrekt < 70) fyll.classList.add("low");
      if (statistikk.andel_korrekt >= 90) fyll.classList.add("high");
    }
    meter.append(fyll);

    const vurderinger = statistikk.kunnskapsstyrke;
    const beskrivelse = statistikk.kunnskapsstyrke_vurdert
      ? `Manuell kunnskapsstyrke: sterk ${vurderinger.sterk}, middels ${vurderinger.middels}, svak ${vurderinger.svak}, ikke vurdert ${vurderinger.ikke_vurdert}.`
      : "Kunnskapsstyrke: ikke vurdert (mangler datagrunnlag).";
    const merknad = element("div", "risk-strength-detail", beskrivelse);
    wrapper.append(header, meter, merknad);
    side.kategorier.append(wrapper);
  }
}

function skrivKildegrunnlag(data) {
  side.kilderListe.replaceChildren();
  for (const kategori of data.kategorier) {
    const rad = element("div", "risk-evidence");
    const topp = element("div", "risk-evidence-top");
    const [alvor, klasse] = konsekvensTekst(kategori.konsekvens);
    topp.append(
      element("strong", "", kategori.navn),
      element("span", `risk-pill ${klasse}`, alvor)
    );
    const kilde = !kategori.dokument
      ? "Ingen kildedokument: kategorien er ikke fastslått i testprotokollen."
      : kategori.kilde_finnes
        ? `Kilde: ${kategori.dokument}`
        : `Kilde mangler: ${kategori.dokument}`;
    rad.append(topp, element("small", "", kilde));
    side.kilderListe.append(rad);
  }
}

function skrivSvakheter(data, fase) {
  side.svakheter.replaceChildren();
  const kandidater = data.kategorier
    .map(kategori => ({ kategori, statistikk: kategori[fase] }))
    .filter(item => item.statistikk.feil > 0)
    .sort((a, b) => b.statistikk.feil - a.statistikk.feil);

  if (kandidater.length === 0) {
    const s = data.statistikk[fase];
    const melding = !s.vurderte
      ? "Ingen svakheter kan påvises før spørsmålene er gjennomført og vurdert mot fasit."
      : "Ingen feil er registrert i dette testsettet. Det betyr ikke at systemet er feilfritt.";
    side.svakheter.append(element("div", "risk-insight", melding));
    return;
  }

  for (const { kategori, statistikk } of kandidater) {
    const kort = element("div", "risk-insight");
    const overskrift = element("strong", "", `${kategori.navn}: ${statistikk.feil} feil av ${statistikk.vurderte} vurderte tester`);
    const detalj = statistikk.vurderte < 5
      ? "Foreløpig indikasjon: under fem vurderte spørsmål i denne kategorien."
      : `Riktig håndtering: ${prosent(statistikk.andel_riktig_pct)} i dette testsettet.`;
    kort.append(overskrift, document.createTextNode(detalj));
    side.svakheter.append(kort);
  }
}

const RESULTATNAVN = {
  korrekt_svar: ["Korrekt svar", "ok"],
  korrekt_avsta: ["Korrekt avstått", "ok"],
  feil_svar: ["Feil svar", "bad"],
  delvis_svar: ["Delvis riktig svar", "partial"],
  feil_avsta: ["Avstod unødig", "bad"],
  uonsket_svar: ["Skulle avstått", "bad"],
  usikker_historisk: ["Usikker (historisk)", "unknown"],
  ikke_vurdert: ["Ikke vurdert", "unknown"],
};

// B sin manuelle vurdering er autoritativ for riktig/delvis/feil.
const VURDERINGSNAVN = {
  riktig: ["Ja", "ok"],
  delvis: ["Delvis", "partial"],
  feil: ["Nei", "bad"],
  ikke_vurdert: ["Ikke vurdert", "unknown"],
};

function lesbarHandtering(verdi) {
  if (verdi === "svar") return "Gi svar";
  if (verdi === "henvis") return "Svar + henvisning til HR";
  if (verdi === "avsta") return "Avstå";
  if (verdi === "usikker") return "Usikker (historisk før-versjon)";
  return "Ikke registrert";
}

function merkelapp(tekst, klasse) {
  const celle = element("td");
  celle.append(element("span", `risk-state ${klasse}`, tekst));
  return celle;
}

function skrivTester(data, fase) {
  side.tester.replaceChildren();
  const tester = data.tester.filter(t => t.fase === fase);
  side.testAntall.textContent = `${tester.length} testspørsmål`;

  if (!tester.length) {
    const rad = element("tr");
    const celle = element("td", "risk-empty", "Ingen tester for denne fasen. B kan legge dem i data/risiko_tester.json.");
    celle.colSpan = 10;
    rad.append(celle);
    side.tester.append(rad);
    return;
  }

  for (const test of tester) {
    const rad = element("tr");
    const [utfall, utfallKlasse] = RESULTATNAVN[test.resultat] || RESULTATNAVN.ikke_vurdert;
    const [vurdering, vurderingKlasse] = VURDERINGSNAVN[test.vurdering] || VURDERINGSNAVN.ikke_vurdert;
    const [konsekvens, konsekvensKlasse] = konsekvensTekst(test.konsekvens);
    const vurdertStyrke = test.kunnskapsstyrke
      ? test.kunnskapsstyrke.charAt(0).toUpperCase() + test.kunnskapsstyrke.slice(1)
      : "Ikke vurdert";
    const kategori = test.kategori === null || test.kategori === undefined
      ? "Ikke oppgitt"
      : `${test.kategori}${test.kategori_grunnlag ? " *" : ""}`;
    const merknader = [
      test.kommentar || null,
      test.kategori_merknad ? `Kategori: ${test.kategori_merknad}` : null,
      test.beste_treff ? `Beste treff: ${test.beste_treff}` : null,
      test.confidence_pct === null ? null : `Søkerelevans: ${test.confidence_pct} %`,
    ].filter(Boolean);
    const kategoriCelle = element("td", "", kategori);
    if (test.kategori_grunnlag) kategoriCelle.title = test.kategori_grunnlag;
    rad.append(
      element("td", "", test.id),
      element("td", "risk-question", test.sporsmal),
      kategoriCelle,
      element("td", "", lesbarHandtering(test.forventet_handtering)),
      element("td", "", lesbarHandtering(test.faktisk_handtering)),
      merkelapp(vurdering, vurderingKlasse),
      merkelapp(utfall, utfallKlasse),
      merkelapp(konsekvens, konsekvensKlasse === "high" ? "bad" : konsekvensKlasse === "low" ? "ok" : "unknown"),
      element("td", "", vurdertStyrke),
      element("td", "risk-comment", merknader.join(" · ") || "—")
    );
    side.tester.append(rad);
  }
}

// Svarloggen er uavhengig av testfase; den viser bare aggregerte tall.
function skrivSvarlogg(data) {
  const logg = data.svarlogg;
  if (!logg) return;
  const meldinger = Array.isArray(logg.varsler) ? logg.varsler : [];
  side.loggVarsel.hidden = meldinger.length === 0;
  side.loggVarsel.textContent = meldinger.join(" ");

  const harData = logg.antall > 0;
  side.loggAntall.textContent = `${logg.antall} logget`;
  side.loggSvar.textContent = harData ? logg.svar : "—";
  side.loggHenvis.textContent = harData ? logg.henvis : "—";
  side.loggAvsta.textContent = harData ? logg.avsta : "—";
  side.loggAvstaTekst.textContent = harData
    ? `${prosent(logg.andel_avsta)} av ${logg.antall} spørsmål`
    : "Ingen spørsmål logget ennå";
  side.loggHoyUten.textContent = harData ? logg.hoy_uten_henvisning : "—";

  side.loggRader.replaceChildren();
  if (!harData) {
    const rad = element("tr");
    const celle = element("td", "risk-empty", "Ingen spørsmål er logget ennå. Tallene fylles inn når chatboten brukes.");
    celle.colSpan = 7;
    rad.append(celle);
    side.loggRader.append(rad);
    return;
  }

  for (const kategori of logg.kategorier) {
    if (kategori.antall === 0) continue;
    const rad = element("tr");
    rad.append(
      element("td", "", kategori.navn),
      element("td", "", kategori.antall),
      element("td", "", kategori.svar),
      element("td", "", kategori.henvis),
      element("td", "", kategori.avsta),
      element("td", "", prosent(kategori.andel_avsta)),
      element("td", "", kategori.hoy_konsekvens)
    );
    side.loggRader.append(rad);
  }
}

function visData() {
  if (!risikodata) return;
  const fase = side.fase.value;
  skrivNokkeltall(risikodata, fase);
  skrivKontroll(risikodata, fase);
  skrivKategorier(risikodata, fase);
  skrivKildegrunnlag(risikodata);
  skrivSvakheter(risikodata, fase);
  skrivTester(risikodata, fase);
}

async function lastDashboard() {
  try {
    const svar = await fetch("/api/risiko", { cache: "no-store" });
    if (!svar.ok) throw new Error(`HTTP ${svar.status}`);
    risikodata = await svar.json();
    const finnesEtter = risikodata.statistikk.etter.antall > 0;
    side.fase.value = finnesEtter ? "etter" : "for";
    visVarsler(risikodata.varsler);
    skrivDatakilde(risikodata);
    skrivSvarlogg(risikodata);
    visData();
  } catch (feil) {
    side.scope.textContent = "Kunne ikke hente risikodata";
    visVarsler(["API-et /api/risiko svarte ikke. Kontroller at Blueprint er registrert i Flask."]);
  }
}

side.fase.addEventListener("change", visData);
lastDashboard();
