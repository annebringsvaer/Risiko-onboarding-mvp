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
  feil: document.getElementById("kpi-errors"),
  kategorier: document.getElementById("risk-categories"),
  kilderListe: document.getElementById("risk-evidence"),
  svakheter: document.getElementById("risk-insights"),
  tester: document.getElementById("risk-test-rows"),
  testAntall: document.getElementById("risk-test-count"),
};

let risikodata = null;

function element(tag, klasse = "", tekst = null) {
  const node = document.createElement(tag);
  if (klasse) node.className = klasse;
  if (tekst !== null) node.textContent = String(tekst);
  return node;
}

function prosent(tall) {
  return typeof tall === "number" ? `${tall} %` : "—";
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
  side.korrekte.textContent = prosent(s.andel_korrekt);
  side.feil.textContent = s.vurderte ? String(s.risikofylte_feil) : "—";
  side.scope.textContent = `Fase: ${fase === "for" ? "før" : "etter"} endringer · ${s.vurderte} av ${s.antall} tester vurdert`;
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
        ? `${statistikk.korrekte} av ${statistikk.vurderte} korrekt`
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
    const antallStyrker = vurderinger.sterk + vurderinger.middels + vurderinger.svak;
    const beskrivelse = antallStyrker
      ? `Manuell kunnskapsstyrke: sterk ${vurderinger.sterk}, middels ${vurderinger.middels}, svak ${vurderinger.svak}.`
      : "Kunnskapsstyrke er ikke vurdert ennå.";
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
    const alvor = kategori.konsekvens === "hoy" ? "Høy konsekvens" : "Lav konsekvens";
    topp.append(
      element("strong", "", kategori.navn),
      element("span", `risk-pill ${kategori.konsekvens === "hoy" ? "high" : "low"}`, alvor)
    );
    const kilde = kategori.kilde_finnes
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
      : `Korrekt håndtering: ${prosent(statistikk.andel_korrekt)} i dette testsettet.`;
    kort.append(overskrift, document.createTextNode(detalj));
    side.svakheter.append(kort);
  }
}

const RESULTATNAVN = {
  korrekt_svar: ["Korrekt svar", "ok"],
  korrekt_avsta: ["Korrekt avstått", "ok"],
  feil_svar: ["Feil svar", "bad"],
  feil_avsta: ["Avstod unødig", "bad"],
  uonsket_svar: ["Skulle avstått", "bad"],
  ikke_vurdert: ["Ikke vurdert", "unknown"],
};

function lesbarHandtering(verdi) {
  if (verdi === "svar") return "Gi svar";
  if (verdi === "avsta") return "Avstå";
  return "Ikke registrert";
}

function skrivTester(data, fase) {
  side.tester.replaceChildren();
  const tester = data.tester.filter(t => t.fase === fase);
  side.testAntall.textContent = `${tester.length} testspørsmål`;

  if (!tester.length) {
    const rad = element("tr");
    const celle = element("td", "risk-empty", "Ingen tester for denne fasen. B kan legge dem i data/risiko_tester.json.");
    celle.colSpan = 8;
    rad.append(celle);
    side.tester.append(rad);
    return;
  }

  for (const test of tester) {
    const rad = element("tr");
    const [navn, klasse] = RESULTATNAVN[test.resultat] || RESULTATNAVN.ikke_vurdert;
    const status = element("span", `risk-state ${klasse}`, navn);
    const statusCelle = element("td");
    statusCelle.append(status);
    const vurdertStyrke = test.kunnskapsstyrke
      ? test.kunnskapsstyrke.charAt(0).toUpperCase() + test.kunnskapsstyrke.slice(1)
      : "Ikke vurdert";
    const merknad = test.confidence_pct === null
      ? test.kommentar
      : `${test.kommentar ? test.kommentar + " · " : ""}Søkerelevans: ${test.confidence_pct} %`;
    rad.append(
      element("td", "", test.id),
      element("td", "risk-question", test.sporsmal),
      element("td", "", test.kategori),
      element("td", "", lesbarHandtering(test.forventet_handtering)),
      element("td", "", lesbarHandtering(test.faktisk_handtering)),
      statusCelle,
      element("td", "", vurdertStyrke),
      element("td", "risk-comment", merknad || "—")
    );
    side.tester.append(rad);
  }
}

function visData() {
  if (!risikodata) return;
  const fase = side.fase.value;
  skrivNokkeltall(risikodata, fase);
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
    visData();
  } catch (feil) {
    side.scope.textContent = "Kunne ikke hente risikodata";
    visVarsler(["API-et /api/risiko svarte ikke. Kontroller at Blueprint er registrert i Flask."]);
  }
}

side.fase.addEventListener("change", visData);
lastDashboard();
