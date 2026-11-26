/* FricheScore — interface (JavaScript sans dépendance, fonctionne hors ligne). */
"use strict";

const S = {
  meta: null,
  perimeter: { type: "tout", values: [] },
  usage: null,            // null = meilleur usage par site
  profile: "equilibre",
  weights: null,
  d5: true,
  filters: { q: "", useFilter: "", useMin: 60, minConf: 0, noBlock: true, maxCost: "" },
  sites: [],
  tab: "classement",
  compare: [],
  drawer: null,           // {id, usage}
  cache: {},
};
const DIMS = ["d1", "d2", "d3", "d4", "d5"];
const DSHORT = { d1: "Potentiel", d2: "Coût", d3: "Friction", d4: "Marché", d5: "Impact" };
const TABS = [
  ["classement", "Classement"], ["contre", "Contre-analyse"], ["couts", "Coûts cachés"],
  ["zan", "Horloge ZAN"], ["jumeau", "Jumeau"], ["portfolio", "Portfolio"], ["donnees", "Données"],
];

/* ------------------------------------------------------------------ outils */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v == null || isNaN(v) ? "—" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const eur = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return fmt(v / 1e6, 1) + " M€";
  if (a >= 1e3) return fmt(v / 1e3, 0) + " k€";
  return fmt(v, 0) + " €";
};
const r1 = (x) => Math.round(x * 10) / 10;
function colorFor(v, invert = false) {
  const g = invert ? 100 - v : v;
  return g >= 65 ? "var(--good)" : g >= 45 ? "var(--mid)" : "var(--bad)";
}
function confDot(c) {
  const col = c >= 0.8 ? "var(--good)" : c >= 0.6 ? "var(--mid)" : "var(--bad)";
  const lbl = c >= 0.8 ? "élevée" : c >= 0.6 ? "moyenne" : "faible";
  return `<span class="conf" title="Confiance des données : ${fmt(c, 2)}"><i style="background:${col}"></i>confiance ${lbl}</span>`;
}
function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined && !(body instanceof FormData)) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  else if (body instanceof FormData) opt.body = body;
  const r = await fetch(path, opt);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch (e) { /* noop */ }
    throw new Error(typeof m === "string" ? m : JSON.stringify(m));
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.blob();
}
async function download(path, body, name) {
  try {
    toast("Préparation du fichier…", 8000);
    const blob = await api(path, body);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("Fichier généré.");
  } catch (e) { toast("Erreur : " + e.message, 5000); }
}
const ctx = () => ({ perimeter: S.perimeter, usage: S.usage, weights: S.weights, d5: S.d5, profile: S.profile });

/* -------------------------------------------------- score global (miroir Python) */
// Doit rester identique à app/scoring/engine.py:global_score
function weightsEff() {
  const w = { ...S.weights };
  if (!S.d5) w.d5 = 0;
  const tot = DIMS.reduce((a, k) => a + (+w[k] || 0), 0);
  return tot > 0 ? w : { d1: 1, d2: 1, d3: 1, d4: 1, d5: S.d5 ? 1 : 0 };
}
function globalScore(u) {
  const w = weightsEff();
  const tot = DIMS.reduce((a, k) => a + (+w[k] || 0), 0);
  const v = { ...u.dims, d3: 100 - u.dims.d3 };
  const raw = DIMS.reduce((a, k) => a + w[k] * v[k], 0) / tot;
  const conf = DIMS.reduce((a, k) => a + w[k] * u.conf[k], 0) / tot;
  const pen = r1((1 - conf) * S.meta.constants.confidence_max_penalty);
  let g = raw - pen;
  const blocked = u.blockers.length > 0;
  if (blocked) g = Math.min(g, S.meta.constants.blocker_cap);
  const eff = [];
  DIMS.forEach((k) => {
    if (!w[k]) return;
    (u.contribs[k] || []).forEach((c) => eff.push({ label: c.label, dim: k, effet: r1((c.delta * w[k]) / tot), key: c.key }));
  });
  if (pen >= 1) {
    let weak = [];
    DIMS.forEach((k) => { if (w[k]) weak = weak.concat(u.weak[k] || []); });
    const seen = {}; weak = weak.filter((x) => (seen[x.key] ? false : (seen[x.key] = true))).sort((a, b) => a.confiance - b.confiance).slice(0, 1);
    const lbl = "Données fragiles : " + weak.map((x) => `${lcFirst(catLabel(x.key))} (${x.manquant ? "manquant" : "confiance " + fmt(x.confiance, 2)})`).join(", ");
    eff.push({ label: lbl, dim: "conf", effet: -pen });
  }
  eff.sort((a, b) => Math.abs(b.effet) - Math.abs(a.effet));
  return { global: r1(Math.max(0, g)), brut: r1(raw), confiance: conf, penalite: pen, bloque: blocked, drivers: eff.slice(0, 3) };
}
const lcFirst = (t) => t.charAt(0).toLowerCase() + t.slice(1);
const catLabel = (k) => (S.meta.catalog[k] ? S.meta.catalog[k].label : k);

function ranking() {
  const rows = [];
  const nonEval = [];
  for (const s of S.sites) {
    if (!s.evaluable) { nonEval.push(s); continue; }
    let best = null;
    const cands = S.usage ? [S.usage] : Object.keys(s.usages);
    for (const u of cands) {
      const g = globalScore(s.usages[u]);
      const key = [!g.bloque, g.global];
      if (!best || key[0] > best.key[0] || (key[0] === best.key[0] && key[1] > best.key[1])) best = { u, g, key };
    }
    rows.push({ site: s, usage: best.u, ur: s.usages[best.u], g: best.g });
  }
  rows.sort((a, b) => b.g.global - a.g.global);
  rows.forEach((r, i) => (r.rangAll = i + 1));
  const f = S.filters;
  const q = f.q.trim().toLowerCase();
  const filtered = rows.filter((r) => {
    if (q && !(`${r.site.nom} ${r.site.commune} ${r.site.epci} ${r.site.id}`.toLowerCase().includes(q))) return false;
    if (f.useFilter && !(r.site.usages[f.useFilter].dims.d1 > +f.useMin)) return false;
    if (f.minConf && r.g.confiance < +f.minConf) return false;
    if (f.noBlock && r.g.bloque) return false;
    if (f.maxCost !== "" && (r.ur.cout.m2_bas + r.ur.cout.m2_haut) / 2 > +f.maxCost) return false;
    return true;
  });
  filtered.forEach((r, i) => (r.rang = i + 1));
  return { rows: filtered, all: rows, nonEval };
}

/* ------------------------------------------------------------- démarrage */
async function boot() {
  S.meta = await api("/api/meta");
  S.weights = { ...S.meta.profiles.equilibre.weights };
  $("#tabs").innerHTML = TABS.map(([k, l]) => `<button data-tab="${k}">${l}</button>`).join("");
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); });
  $("#btn-csv").onclick = () => download("/api/export/csv", ctx(), "frichescore_classement.csv");
  $("#btn-report").onclick = () => download("/api/export/rapport", { ...ctx(), perimeter_label: perimeterLabel(), top_n: 10 }, "frichescore_rapport_precadrage.pdf");
  $("#drawer").addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  renderSide();
  await loadSites();
  setTab(location.hash.slice(1) || "classement");
}
async function loadSites() {
  $("#main").innerHTML = `<div class="loading">Calcul des scores du périmètre…</div>`;
  const r = await api("/api/scores", S.perimeter);
  S.sites = r.sites;
  $("#demo-banner").hidden = !S.sites.some((s) => String(s.origine || "").startsWith("demo"));
  S.cache = {};
}
function setTab(t) {
  if (!TABS.find((x) => x[0] === t)) t = "classement";
  S.tab = t; history.replaceState(null, "", "#" + t);
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  render();
}
function render() {
  const f = { classement: renderClassement, contre: renderContre, couts: renderCouts, zan: renderZan, jumeau: renderJumeau, portfolio: renderPortfolio, donnees: renderDonnees }[S.tab];
  f();
}
function perimeterLabel() {
  const p = S.perimeter;
  if (p.type === "tout" || !p.values.length) return "Tout le référentiel";
  if (p.type === "departement") return "Département(s) " + p.values.join(", ");
  if (p.type === "communes") {
    const names = S.meta.perimeters.communes.filter((c) => p.values.includes(c.code_insee)).map((c) => c.nom);
    return "Communes : " + (names.join(", ") || p.values.join(", "));
  }
  return p.values.join(", ");
}

/* --------------------------------------------------------------- sidebar */
function renderSide() {
  const m = S.meta;
  const p = S.perimeter;
  let list = "";
  if (p.type === "departement") list = m.perimeters.departements.map((d) => [d.code, `${d.code}`, d.n]);
  if (p.type === "epci") list = m.perimeters.epcis.map((d) => [d.nom, d.nom, d.n]);
  if (p.type === "communes") list = m.perimeters.communes.map((d) => [d.code_insee, `${d.nom} (${d.code_insee})`, d.n]);
  const checks = list ? `<div class="checklist">${list.map(([v, l, n]) => `<label><input type="checkbox" value="${esc(v)}" ${p.values.includes(v) ? "checked" : ""}>${esc(l)}<span class="n">${n}</span></label>`).join("")}</div>
     ${p.type === "communes" ? `<input type="text" id="insee-paste" placeholder="ou coller des codes INSEE : 59350, 59512…" style="margin-top:6px">` : ""}` : "";
  const wsum = DIMS.reduce((a, k) => a + (k === "d5" && !S.d5 ? 0 : +S.weights[k]), 0) || 1;
  $("#side").innerHTML = `
  <div class="side-block">
    <label>1 · Périmètre</label>
    <select id="per-type">
      ${[["tout", "Tout le référentiel"], ["departement", "Département"], ["epci", "EPCI"], ["communes", "Liste de communes"]].map(([v, l]) => `<option value="${v}" ${p.type === v ? "selected" : ""}>${l}</option>`).join("")}
    </select>
    ${checks}
    ${p.type !== "tout" ? `<button class="btn sm" id="per-apply" style="margin-top:6px;width:100%">Charger le périmètre</button>` : ""}
  </div>
  <div class="side-block">
    <label>2 · Usage cible</label>
    <select id="usage"><option value="">Meilleur usage par site</option>${Object.entries(m.usages).map(([k, l]) => `<option value="${k}" ${S.usage === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    <div class="hint">Chaque usage a sa propre logique de scoring. « Meilleur usage » retient, pour chaque friche, l'usage au score le plus élevé.</div>
  </div>
  <div class="side-block">
    <label>3 · Profil de pondération</label>
    <div class="seg" id="profiles">${Object.entries(m.profiles).map(([k, v]) => `<button data-p="${k}" class="${S.profile === k ? "on" : ""}">${v.label}</button>`).join("")}</div>
    <div style="margin-top:10px">
    ${DIMS.map((k) => `<div class="slider ${k === "d5" && !S.d5 ? "off" : ""}"><span>${m.dimensions[k]}${k === "d3" ? " <span class='muted'>(pénalise)</span>" : ""}</span><b>${Math.round(((k === "d5" && !S.d5 ? 0 : +S.weights[k]) / wsum) * 100)} %</b>
      <input type="range" min="0" max="60" step="1" value="${S.weights[k]}" data-w="${k}" ${k === "d5" && !S.d5 ? "disabled" : ""}></div>`).join("")}
    </div>
    <label class="toggle"><input type="checkbox" id="d5" ${S.d5 ? "checked" : ""}> Activer l'impact & les financements (projets subventionnés)</label>
    <div class="hint">Le classement se recompose instantanément. La friction entre dans le score sous la forme 100 − friction ; une pénalité de 0 à ${m.constants.confidence_max_penalty} pts est retirée selon la fiabilité des données.</div>
  </div>
  <div class="side-block">
    <label>4 · Filtres</label>
    <div class="row" style="margin-bottom:6px"><select id="f-use"><option value="">Filtre par usage…</option>${Object.entries(m.usages).map(([k, l]) => `<option value="${k}" ${S.filters.useFilter === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <input type="number" id="f-min" value="${S.filters.useMin}" min="0" max="100" style="max-width:64px" title="Potentiel minimum (D1)"></div>
    <div class="hint" style="margin:-2px 0 8px">ex. « Photovoltaïque &gt; 60 » : ne garde que les friches dont le potentiel PV dépasse 60.</div>
    <div class="row" style="margin-bottom:6px"><span class="small">Confiance min.</span><select id="f-conf">${[[0, "toutes"], [0.6, "≥ moyenne"], [0.8, "élevée"]].map(([v, l]) => `<option value="${v}" ${+S.filters.minConf === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="row" style="margin-bottom:6px"><span class="small">Coût max. (€/m²)</span><input type="number" id="f-cost" value="${S.filters.maxCost}" placeholder="—"></div>
    <label class="toggle"><input type="checkbox" id="f-block" ${S.filters.noBlock ? "checked" : ""}> Masquer les usages rédhibitoires</label>
  </div>`;
  $("#per-type").onchange = async (e) => {
    S.perimeter = { type: e.target.value, values: [] };
    renderSide();
    if (S.perimeter.type === "tout") { await loadSites(); render(); }
  };
  const apply = $("#per-apply");
  if (apply) apply.onclick = async () => {
    const vals = [...document.querySelectorAll("#side .checklist input:checked")].map((i) => i.value);
    const paste = $("#insee-paste");
    if (paste && paste.value.trim()) paste.value.split(/[\s,;]+/).filter(Boolean).forEach((c) => vals.includes(c) || vals.push(c));
    S.perimeter.values = vals;
    if (!vals.length) { toast("Sélectionnez au moins un élément."); return; }
    await loadSites(); renderSide(); render();
  };
  $("#usage").onchange = (e) => { S.usage = e.target.value || null; S.cache = {}; render(); };
  $("#profiles").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.profile = b.dataset.p; S.weights = { ...m.profiles[b.dataset.p].weights };
    if (S.profile === "impact") S.d5 = true;
    S.cache = {}; renderSide(); render();
  };
  document.querySelectorAll("#side input[data-w]").forEach((inp) => {
    inp.oninput = () => {
      S.weights[inp.dataset.w] = +inp.value; S.profile = "perso";
      document.querySelectorAll("#profiles button").forEach((b) => b.classList.remove("on"));
      const ws = DIMS.reduce((a, k) => a + (k === "d5" && !S.d5 ? 0 : +S.weights[k]), 0) || 1;
      document.querySelectorAll("#side .slider").forEach((sl) => {
        const k = sl.querySelector("input").dataset.w;
        sl.querySelector("b").textContent = Math.round(((k === "d5" && !S.d5 ? 0 : +S.weights[k]) / ws) * 100) + " %";
      });
      S.cache = {};
      if (S.tab === "classement") renderClassement();
    };
    inp.onchange = () => { if (S.tab !== "classement") render(); };
  });
  $("#d5").onchange = (e) => { S.d5 = e.target.checked; S.cache = {}; renderSide(); render(); };
  const fl = () => { if (S.tab === "classement") renderClassement(); };
  $("#f-use").onchange = (e) => { S.filters.useFilter = e.target.value; fl(); };
  $("#f-min").oninput = (e) => { S.filters.useMin = e.target.value; fl(); };
  $("#f-conf").onchange = (e) => { S.filters.minConf = e.target.value; fl(); };
  $("#f-cost").oninput = (e) => { S.filters.maxCost = e.target.value; fl(); };
  $("#f-block").onchange = (e) => { S.filters.noBlock = e.target.checked; fl(); };
}

/* ------------------------------------------------------------ fragments */
function dimsMini(dims) {
  return `<div class="dims">${DIMS.map((k) => {
    if (k === "d5" && !S.d5) return "";
    const v = dims[k], inv = k === "d3";
    return `<span>${DSHORT[k]}</span><div class="bar" title="${esc(S.meta.dimensions[k])}${inv ? " (plus c'est haut, plus c'est pénalisant)" : ""}"><i style="width:${v}%;background:${colorFor(v, inv)}"></i></div><b>${fmt(v)}</b>`;
  }).join("")}</div>`;
}
function driversHtml(drivers) {
  return `<div class="drivers">${drivers.map((d) => `<div class="drv ${d.effet >= 0 ? "pos" : "neg"}"><em>${d.effet > 0 ? "+" : ""}${fmt(d.effet, 1)}</em><span>${esc(d.label)}</span></div>`).join("")}</div>`;
}
function siteBadges(s) {
  const b = [];
  if (s.statut === "verifiee") b.push(`<span class="chip ok">Cartofriches vérifiée</span>`);
  else if (s.statut === "non_verifiee") b.push(`<span class="chip">non vérifiée</span>`);
  if (String(s.origine || "").startsWith("demo")) b.push(`<span class="chip gold">démo</span>`);
  if (s.portfolio) b.push(`<span class="chip ok">portfolio : ${esc(S.meta.stages[s.portfolio])}</span>`);
  return b.join("");
}

/* ------------------------------------------------------------ classement */
function renderClassement() {
  const { rows, all, nonEval } = ranking();
  const total = S.sites.length;
  const top = rows.slice(0, 200);
  const usageLbl = S.usage ? S.meta.usages[S.usage] : "meilleur usage par site";
  const avgConf = rows.length ? rows.reduce((a, r) => a + r.g.confiance, 0) / rows.length : 0;
  const scrollY = window.scrollY;
  $("#main").innerHTML = `
  <div class="section-head">
    <div><h2>Classement des friches — ${esc(perimeterLabel())}</h2>
    <p>Usage : <b>${esc(usageLbl)}</b>. Chaque ligne affiche le FricheScore, ses cinq notes et les trois facteurs qui ont le plus pesé. Cliquez sur une friche pour la fiche complète, la fiche de vie de la donnée et la contestation d'une hypothèse.</p></div>
    <div class="kpis">
      <div class="kpi"><b>${total}</b><span>friches dans le périmètre</span></div>
      <div class="kpi"><b>${all.length}</b><span>évaluables</span></div>
      <div class="kpi"><b>${rows.length}</b><span>après filtres</span></div>
      <div class="kpi"><b>${nonEval.length}</b><span>non évaluables</span></div>
      <div class="kpi"><b>${fmt(avgConf, 2)}</b><span>confiance moyenne</span></div>
    </div>
  </div>
  <div class="pill-filter">
    <input type="text" id="q" placeholder="Rechercher un site, une commune…" value="${esc(S.filters.q)}">
    ${S.compare.length ? `<span class="chip ok">${S.compare.length} site(s) sélectionné(s) pour comparaison</span><button class="btn sm" id="go-cmp" ${S.compare.length !== 2 ? "disabled" : ""}>Comparer</button><button class="btn sm sec" id="clr-cmp">Vider</button>` : `<span class="muted small">Cochez deux friches pour les comparer côte à côte.</span>`}
  </div>
  <div class="card" style="padding:0;overflow:visible">
  <table>
    <thead><tr><th></th><th>#</th><th>Friche</th><th>Usage retenu</th><th>FricheScore</th><th>Cinq dimensions</th><th>Ce qui a le plus pesé</th><th>Remise en état</th><th>Délai</th></tr></thead>
    <tbody>
    ${top.map((r) => `<tr class="click" data-id="${esc(r.site.id)}" data-u="${r.usage}">
      <td><input type="checkbox" class="cmp" data-id="${esc(r.site.id)}" data-u="${r.usage}" ${S.compare.find((c) => c.id === r.site.id) ? "checked" : ""} title="Sélectionner pour comparaison"></td>
      <td class="rank">${r.rang}</td>
      <td style="min-width:200px"><div class="site-name">${esc(r.site.nom)}</div><div class="site-sub">${esc(r.site.commune)} · ${fmt((r.site.surface_m2 || 0) / 10000, 1)} ha · ${esc(r.site.type_friche || "")}</div>${siteBadges(r.site)}</td>
      <td><span class="chip">${esc(S.meta.usages[r.usage])}</span>${r.ur.elig.length ? `<div class="site-sub">${r.ur.elig.length} financement(s) potentiel(s)</div>` : ""}</td>
      <td><div class="score ${r.g.bloque ? "blocked" : ""}" style="color:${r.g.bloque ? "" : colorFor(r.g.global)}">${fmt(r.g.global)}<small>${r.g.penalite >= 1 ? `brut ${fmt(r.g.brut)} − ${fmt(r.g.penalite)}` : "sans pénalité"}</small></div>${confDot(r.g.confiance)}${r.g.bloque ? `<div class="chip bad">rédhibitoire</div>` : ""}</td>
      <td>${dimsMini(r.ur.dims)}</td>
      <td>${driversHtml(r.g.drivers)}</td>
      <td class="cost">${fmt(r.ur.cout.m2_bas)}–${fmt(r.ur.cout.m2_haut)} €/m²<div class="site-sub">${eur(r.ur.cout.bas)} à ${eur(r.ur.cout.haut)}</div><span class="chip ${r.ur.cout.risque.includes("élevé") ? "warn" : ""}">surcoût ${esc(r.ur.cout.risque)}</span></td>
      <td class="num">~${r.ur.delai} mois</td>
    </tr>`).join("") || `<tr><td colspan="9" class="empty">Aucune friche ne correspond aux filtres.</td></tr>`}
    </tbody>
  </table>
  ${rows.length > top.length ? `<div class="empty small">${rows.length - top.length} friches supplémentaires — affinez le périmètre ou les filtres, ou utilisez l'export CSV.</div>` : ""}
  </div>
  ${nonEval.length ? `<div class="card"><details><summary>${nonEval.length} friche(s) non évaluable(s) — l'outil refuse de les scorer plutôt que de produire un score faux</summary>
    <table class="ne-list"><thead><tr><th>Réf.</th><th>Site</th><th>Commune</th><th>Données minimales manquantes</th></tr></thead><tbody>
    ${nonEval.map((s) => `<tr class="click" data-id="${esc(s.id)}"><td class="small">${esc(s.id)}</td><td>${esc(s.nom || "—")}</td><td>${esc(s.commune || "")}</td><td>${s.manquants.map((m) => `<span class="chip warn">${esc(m)}</span>`).join(" ")}</td></tr>`).join("")}
    </tbody></table></details></div>` : ""}`;
  window.scrollTo(0, scrollY);
  const q = $("#q");
  q.oninput = () => { S.filters.q = q.value; const pos = q.selectionStart; renderClassement(); const nq = $("#q"); nq.focus(); nq.setSelectionRange(pos, pos); };
  document.querySelectorAll("#main tr.click").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.classList.contains("cmp")) return;
    openDrawer(tr.dataset.id, tr.dataset.u || null);
  }));
  document.querySelectorAll("#main input.cmp").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) { S.compare.push({ id: cb.dataset.id, usage: cb.dataset.u }); if (S.compare.length > 2) S.compare.shift(); }
    else S.compare = S.compare.filter((c) => c.id !== cb.dataset.id);
    renderClassement();
  }));
  const go = $("#go-cmp"); if (go) go.onclick = () => { S.jumeau = { a: { ...S.compare[0] }, b: { ...S.compare[1] } }; setTab("jumeau"); runCompare(); };
  const clr = $("#clr-cmp"); if (clr) clr.onclick = () => { S.compare = []; renderClassement(); };
}

/* --------------------------------------------------------------- fiche */
async function openDrawer(id, usage, keepWhatif) {
  S.drawer = { id, usage, whatif: keepWhatif ? S.drawer?.whatif : null };
  $("#drawer").hidden = false;
  document.body.style.overflow = "hidden";
  $("#drawer-panel").innerHTML = `<div class="d-body loading">Chargement de la fiche…</div>`;
  try {
    const d = await api(`/api/sites/${encodeURIComponent(id)}`, { usage, weights: S.weights, d5: S.d5 });
    S.drawer.data = d;
    if (S.drawer.whatif) await runWhatif(true);
    renderDrawer();
  } catch (e) { $("#drawer-panel").innerHTML = `<div class="d-body">Erreur : ${esc(e.message)}</div>`; }
}
function closeDrawer() { $("#drawer").hidden = true; document.body.style.overflow = ""; S.drawer = null; }

function renderDrawer() {
  const d = S.drawer.data;
  const v = d.values;
  const head = `<div class="d-head"><button class="d-close" data-close title="Fermer">×</button>
    <h2>${esc(v.nom || v.id)}</h2>
    <div class="muted small">${esc(v.commune)} (${esc(v.code_insee)}) · ${esc(v.epci || "")} · ${fmt((v.surface_m2 || 0) / 10000, 2)} ha · ${esc(v.type_friche || "type inconnu")} · réf. ${esc(v.id)}</div>
    ${d.evaluable ? `<div class="utabs">${Object.keys(d.usages).map((u) => { const g = d.globals[u]; return `<button data-u="${u}" class="${u === d.usage ? "on" : ""} ${g.bloque ? "blocked" : ""}">${esc(S.meta.usages[u])}<b>${fmt(g.global)}</b></button>`; }).join("")}</div>` : ""}
  </div>`;
  if (!d.evaluable) {
    $("#drawer-panel").innerHTML = head + `<div class="d-body">
      <div class="alert bad"><b>Friche non évaluable.</b> Données minimales absentes : ${d.manquants.map(esc).join(" ; ")}. FricheScore refuse de produire un score qui serait faux.</div>
      <div class="card"><h3>Pour rendre ce site évaluable</h3>${recoHtml(d.recommandations)}</div>
      ${fieldForm(d)}
      <div class="card"><h3>Fiche de vie de la donnée</h3>${ficheVie(d.fiche_vie)}</div></div>`;
    bindDrawer(); return;
  }
  const u = d.usage, ur = d.usages[u], g = d.globals[u];
  const wi = S.drawer.whatif;
  const c = ur.cost, fr = ur.friction, mk = ur.market, im = ur.impact;
  $("#drawer-panel").innerHTML = head + `<div class="d-body">
    <div class="card"><div class="scorebox">
      <div><div class="big" style="color:${g.bloque ? "var(--bad)" : colorFor(g.global)}">${fmt(g.global)}<small> / 100</small></div>${confDot(g.confiance)}
        <div class="small muted" style="margin-top:4px">brut ${fmt(g.brut)} − pénalité de confiance ${fmt(g.penalite, 1)}</div></div>
      <div><h4>Les trois facteurs qui pèsent le plus</h4>${driversHtml(g.drivers)}
        ${ur.blockers.map((b) => `<div class="alert bad">Rédhibitoire pour cet usage : ${esc(b)}</div>`).join("")}
        ${ur.alerts.map((a) => `<div class="alert info">${esc(a.message)}</div>`).join("")}</div>
    </div>
    <div class="row" style="margin-top:12px;flex-wrap:wrap">
      <button class="btn" id="d-pdf">Fiche de synthèse PDF</button>
      <button class="btn sec" id="d-port">${d.portfolio ? "Dans le portfolio : " + esc(S.meta.stages[d.portfolio.stage]) : "Ajouter au portfolio"}</button>
      <button class="btn sec" id="d-cmp">Comparer avec…</button>
    </div></div>

    <div class="card"><h3 style="margin-bottom:10px">Les cinq dimensions — ${esc(S.meta.usages[u])}</h3><div class="dimcards">
    ${DIMS.map((k) => { const val = ur.dims[k]; return `<div class="dimcard"><div class="top-line"><b>${esc(S.meta.dimensions[k])}</b><span class="v" style="color:${colorFor(val, k === "d3")}">${fmt(val)}</span></div>
      <div class="bar"><i style="width:${val}%;background:${colorFor(val, k === "d3")}"></i></div>
      <p>${esc(ur.phrases[k])}</p>
      <ul>${(ur.contribs[k] || []).map((x) => `<li><em class="${x.delta >= 0 ? "delta-pos" : "delta-neg"}">${x.delta > 0 ? "+" : ""}${fmt(x.delta, 1)}</em><span>${esc(x.label)}</span></li>`).join("")}</ul>
      <div class="small muted" style="margin-top:4px">confiance ${fmt(ur.conf[k], 2)}${k === "d3" ? " · plus c'est haut, plus c'est pénalisant" : ""}${k === "d5" && !S.d5 ? " · dimension désactivée" : ""}</div></div>`; }).join("")}
    </div></div>

    <div class="card" id="whatif-card"><h3>Contester le score</h3>
      <p class="small muted" style="margin:4px 0 10px">Modifiez une donnée pour voir l'effet sur le score — sans rien enregistrer. Pour enregistrer une donnée de terrain vérifiée, utilisez le formulaire « Données terrain » plus bas.</p>
      <div class="whatif">${indicatorPicker("wi")}<button class="btn sec" id="wi-add">Ajouter l'hypothèse</button></div>
      ${wi && Object.keys(wi.overrides).length ? `<div style="margin-top:10px">${Object.entries(wi.overrides).map(([k, val]) => `<span class="chip warn">${esc(catLabel(k))} = ${esc(val)} <a href="#" data-rm="${esc(k)}">×</a></span>`).join("")}</div>
        ${wi.result ? whatifResult(d, wi.result) : ""}` : ""}
    </div>

    <div class="grid2">
      <div class="card"><h3>Remise en état</h3><p class="small">${esc(c.phrase)}</p>
        <div class="kv"><span>Fourchette totale</span><b>${eur(c.total_bas)} – ${eur(c.total_haut)}</b><span>Par m² de terrain</span><b>${fmt(c.eur_m2_bas)} – ${fmt(c.eur_m2_haut)} €/m²</b></div>
        <table style="margin-top:8px"><thead><tr><th style="position:static">Poste</th><th style="position:static">Bas</th><th style="position:static">Haut</th></tr></thead><tbody>
        ${c.postes.map((p) => `<tr><td class="small">${esc(p.label)}</td><td class="num small">${eur(p.bas)}</td><td class="num small">${eur(p.haut)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3>Friction et délais</h3><p class="small">${esc(fr.phrase)}</p>
        <div class="kv"><span>Délai indicatif jusqu'au démarrage</span><b>~${fr.delai_total_mois} mois</b><span>dont contraintes</span><b>${fr.delai_additionnel_mois} mois</b></div>
        <table style="margin-top:8px"><thead><tr><th style="position:static">Contrainte</th><th style="position:static">Pts</th><th style="position:static">Mois</th></tr></thead><tbody>
        ${fr.facteurs.map((f) => `<tr><td class="small">${esc(f.label)}</td><td class="num small">${fmt(f.points)}</td><td class="num small">${fmt(f.mois)}</td></tr>`).join("") || `<tr><td colspan="3" class="small muted">Aucune contrainte identifiée</td></tr>`}</tbody></table></div>
    </div>
    <div class="grid2" style="margin-top:14px">
      <div class="card"><h3>Équilibre économique</h3>
        ${mk.marchand ? `<div class="kv" style="margin-top:8px"><span>Charge foncière admissible</span><b>${eur(mk.valeur_residuelle_bas)} – ${eur(mk.valeur_residuelle_haut)}</b>
          <span>Acquisition estimée</span><b>${eur(mk.acquisition_bas)} – ${eur(mk.acquisition_haut)}</b>
          <span>Remise en état</span><b>${eur(c.total_bas)} – ${eur(c.total_haut)}</b>
          <span><b>Bilan net</b></span><b style="color:${mk.bilan_net_haut < 0 ? "var(--bad)" : mk.bilan_net_bas > 0 ? "var(--good)" : "var(--mid)"}">${eur(mk.bilan_net_bas)} – ${eur(mk.bilan_net_haut)}</b></div>
          <p class="small muted">${esc(mk.hypothese)}. Compte à rebours simplifié : ordre de grandeur à confirmer par un pré-bilan.</p>`
          : `<p class="small">${esc(mk.phrase)}</p><div class="kv"><span>Coût net à financer</span><b>${eur(-mk.bilan_net_haut)} – ${eur(-mk.bilan_net_bas)}</b></div>`}
      </div>
      <div class="card"><h3>Financements & horloge ZAN</h3>
        ${im.eligibilites.length ? im.eligibilites.map((e) => `<div class="alert info"><b>${esc(e.nom)}</b><br><span class="small">${esc(e.motif)} — présomption à confirmer</span></div>`).join("") : `<p class="small muted">Aucun dispositif identifié pour cet usage.</p>`}
        ${d.zan.map((z) => `<div class="alert"><b>⏱ ${esc(z.fenetre_nom)}</b><br><span class="small">Clôture ${esc(z.cloture)} · dossier prêt vers ${esc(z.maturite.date)} · ${z.statut === "coïncide" ? "fenêtre coïncidente" : "trop juste"}</span></div>`).join("")}
      </div>
    </div>

    <div class="card"><h3>Études complémentaires recommandées</h3><p class="small muted" style="margin:4px 0">L'outil ne remplace pas un diagnostic : voici ce qu'il faudrait savoir pour fiabiliser le score.</p>${recoHtml(d.recommandations)}</div>
    ${fieldForm(d)}
    <div class="card"><h3>Fiche de vie de la donnée</h3><p class="small muted" style="margin:4px 0 8px">Source, date et niveau de confiance de chaque indicateur. Les lignes surlignées ont plus de 10 ans.</p>${ficheVie(d.fiche_vie)}</div>
  </div>`;
  bindDrawer();
}
function recoHtml(recos) {
  if (!recos || !recos.length) return `<p class="small muted">Aucune étude complémentaire prioritaire.</p>`;
  return recos.map((r) => `<div class="reco"><span class="chip ${r.priorite === "haute" ? "bad" : r.priorite === "moyenne" ? "warn" : ""}">${esc(r.priorite)}</span><div><b>${esc(r.etude)}</b><div class="small muted">${esc(r.motif)}</div></div></div>`).join("");
}
function ficheVie(rows) {
  return `<table class="fv"><thead><tr><th>Indicateur</th><th>Valeur</th><th>Source</th><th>Date</th><th>Confiance</th></tr></thead><tbody>
  ${rows.map((x) => `<tr class="${x.ancienne ? "old" : ""}"><td>${esc(x.label)}</td><td>${esc(x.valeur)}</td><td>${esc(x.source)}${x.methode ? `<div class="muted">${esc(x.methode)}</div>` : ""}</td><td class="num">${esc(x.date || "n.r.")}${x.age_ans != null ? `<div class="muted">${fmt(x.age_ans, 0)} an(s)</div>` : ""}</td>
  <td><span class="chip ${x.confiance === "eleve" ? "ok" : x.confiance === "faible" ? "warn" : ""}">${esc(x.confiance)}</span></td></tr>`).join("")}</tbody></table>`;
}
function indicatorPicker(prefix, keys) {
  const cat = S.meta.catalog;
  const ks = keys || Object.keys(cat).filter((k) => !["nom", "commune", "code_insee", "departement", "epci", "lat", "lon", "statut_cartofriches"].includes(k));
  return `<div><label class="small muted">Indicateur</label><select id="${prefix}-key">${ks.map((k) => `<option value="${k}">${esc(cat[k].label)}</option>`).join("")}</select></div>
          <div id="${prefix}-valwrap"></div>`;
}
function bindValueInput(prefix) {
  const sel = $(`#${prefix}-key`); if (!sel) return;
  const upd = () => {
    const c = S.meta.catalog[sel.value];
    let inp;
    if (c.valeurs) inp = `<select id="${prefix}-val">${c.valeurs.map((v) => `<option>${esc(v)}</option>`).join("")}</select>`;
    else if (c.type === "bool") inp = `<select id="${prefix}-val"><option value="true">oui</option><option value="false">non</option></select>`;
    else inp = `<input type="${["int", "float"].includes(c.type) ? "number" : "text"}" id="${prefix}-val" step="any">`;
    $(`#${prefix}-valwrap`).innerHTML = `<label class="small muted">Nouvelle valeur</label>${inp}`;
  };
  sel.onchange = upd; upd();
}
function fieldForm(d) {
  const rows = d.field || [];
  const hist = d.history || [];
  return `<div class="card" id="field-card"><h3>Données terrain (portfolio)</h3>
    <p class="small muted" style="margin:4px 0 10px">Une donnée saisie ici (étude de sol, visite, relevé de propriété) est enregistrée avec sa source et sa date, prime sur les bases nationales et met à jour le score dans le temps.</p>
    <div class="grid3" style="align-items:end">${indicatorPicker("fd", ["pollution_constatee", "bati_etat", "bati_annee", "nb_proprietaires", "proprietaire_type", "zone_humide", "plu_zone", "plu_vocation", "surface_m2", "bati_emprise_m2", "basol", "basias_count", "icpe_count", "captage", "ppri", "servitudes_count", "acces", "enclave", "forme", "prix_terrain_friche_m2", "type_friche", "activite_passee"])}
      <div><label class="small muted">Source</label><input type="text" id="fd-src" placeholder="ex. Diagnostic sol bureau X"></div>
      <div><label class="small muted">Date</label><input type="date" id="fd-date" value="${new Date().toISOString().slice(0, 10)}"></div>
      <div><label class="small muted">Confiance</label><select id="fd-conf"><option value="eleve">élevée</option><option value="moyen">moyenne</option><option value="faible">faible</option></select></div>
      <div><label class="small muted">Auteur</label><input type="text" id="fd-auteur" placeholder="optionnel"></div>
    </div>
    <button class="btn" id="fd-save" style="margin-top:10px">Enregistrer la donnée terrain</button>
    ${rows.length ? `<table style="margin-top:12px"><thead><tr><th style="position:static">Indicateur</th><th style="position:static">Valeur</th><th style="position:static">Source</th><th style="position:static">Date</th><th style="position:static"></th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="small">${esc(catLabel(r.key))}</td><td class="small">${esc(JSON.stringify(r.value))}</td><td class="small">${esc(r.source)}${r.auteur ? " — " + esc(r.auteur) : ""}</td><td class="small">${esc(r.date)}</td><td><button class="btn sm sec" data-delfield="${r.id}">Supprimer</button></td></tr>`).join("")}</tbody></table>` : ""}
    ${hist.length ? `<h4 style="margin-top:14px">Historique du score <span class="muted small" style="text-transform:none;letter-spacing:0">(profil Équilibré, pour comparer dans le temps)</span></h4>${historyChart(hist)}
      <table style="margin-top:6px"><tbody>${hist.slice().reverse().map((h) => `<tr><td class="small muted">${esc(h.ts.replace("T", " "))}</td><td class="small"><b>${fmt(h.global)}</b> · rang ${h.rang}/${h.rang_sur} dans le département (${esc(S.meta.usages[h.usage] || h.usage)})</td><td class="small">${esc(h.motif)}</td></tr>`).join("")}</tbody></table>` : ""}
  </div>`;
}
function historyChart(hist, w = 520, hgt = 90) {
  if (hist.length < 2) return "";
  const xs = (i) => 20 + (i * (w - 40)) / (hist.length - 1);
  const ys = (v) => hgt - 15 - (v / 100) * (hgt - 30);
  const pts = hist.map((h, i) => `${xs(i)},${ys(h.global)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${hgt}" width="100%" style="max-width:${w}px"><line x1="20" x2="${w - 20}" y1="${ys(50)}" y2="${ys(50)}" stroke="#e1e4e0" stroke-dasharray="3 3"/>
    <polyline points="${pts}" fill="none" stroke="#1f6f5c" stroke-width="2"/>${hist.map((h, i) => `<circle cx="${xs(i)}" cy="${ys(h.global)}" r="3.5" fill="#1f6f5c"><title>${esc(h.motif)} : ${h.global}</title></circle><text x="${xs(i)}" y="${ys(h.global) - 7}" font-size="10" text-anchor="middle" fill="#3c4855">${fmt(h.global)}</text>`).join("")}</svg>`;
}
function whatifResult(d, res) {
  const u = d.usage;
  const g0 = d.globals[u], g1 = res.globals[u];
  const delta = g1.global - g0.global;
  const ur0 = d.usages[u], ur1 = res.usages[u];
  return `<div style="margin-top:10px" class="kv">
    <span>FricheScore (${esc(S.meta.usages[u])})</span><b>${fmt(g0.global)} → ${fmt(g1.global)} <span class="${delta >= 0 ? "delta-pos" : "delta-neg"}">(${delta >= 0 ? "+" : ""}${fmt(delta, 1)})</span></b>
    ${DIMS.map((k) => { const a = ur0.dims[k], b = ur1.dims[k]; if (Math.abs(a - b) < 0.05) return ""; const better = k === "d3" ? b < a : b > a; return `<span>${esc(S.meta.dimensions[k])}</span><b>${fmt(a)} → ${fmt(b)} <span class="${better ? "delta-pos" : "delta-neg"}">${better ? "▲" : "▼"}</span></b>`; }).join("")}
    <span>Coût de remise en état</span><b>${eur(ur1.cost.total_bas)} – ${eur(ur1.cost.total_haut)}</b>
    <span>Meilleur usage après hypothèse</span><b>${esc(S.meta.usages[res.usage])}</b>
  </div>`;
}
async function runWhatif(silent) {
  const wi = S.drawer.whatif;
  if (!wi || !Object.keys(wi.overrides).length) { if (wi) wi.result = null; return; }
  const res = await api(`/api/sites/${encodeURIComponent(S.drawer.id)}`, { weights: S.weights, d5: S.d5, overrides: wi.overrides });
  wi.result = res;
  if (!silent) renderDrawer();
}
function bindDrawer() {
  const p = $("#drawer-panel");
  p.querySelectorAll(".utabs button").forEach((b) => (b.onclick = () => { S.drawer.data.usage = b.dataset.u; S.drawer.usage = b.dataset.u; renderDrawer(); }));
  bindValueInput("wi"); bindValueInput("fd");
  const pdfb = $("#d-pdf"); if (pdfb) pdfb.onclick = () => download(`/api/sites/${encodeURIComponent(S.drawer.id)}/pdf`, { usage: S.drawer.data.usage, weights: S.weights, d5: S.d5, profile: S.profile }, `frichescore_${S.drawer.id}.pdf`);
  const port = $("#d-port"); if (port) port.onclick = async () => {
    if (S.drawer.data.portfolio) { setTab("portfolio"); closeDrawer(); return; }
    await api(`/api/portfolio/${encodeURIComponent(S.drawer.id)}`, { stage: "reperee", usage: S.drawer.data.usage, weights: S.weights, d5: S.d5 }, "PUT");
    toast("Ajouté au portfolio (étape « repérée »)."); markPortfolio(S.drawer.id, "reperee"); openDrawer(S.drawer.id, S.drawer.data.usage);
  };
  const cmp = $("#d-cmp"); if (cmp) cmp.onclick = () => { S.jumeau = { a: { id: S.drawer.id, usage: S.drawer.data.usage }, b: S.jumeau?.b || { extension: true, usage: S.drawer.data.usage } }; closeDrawer(); setTab("jumeau"); };
  const add = $("#wi-add"); if (add) add.onclick = async () => {
    const k = $("#wi-key").value, v = $("#wi-val").value;
    if (v === "") { toast("Indiquez une valeur."); return; }
    S.drawer.whatif = S.drawer.whatif || { overrides: {} };
    S.drawer.whatif.overrides[k] = v;
    await runWhatif();
    $("#whatif-card").scrollIntoView({ block: "nearest" });
  };
  p.querySelectorAll("[data-rm]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); delete S.drawer.whatif.overrides[a.dataset.rm]; await runWhatif(); }));
  const save = $("#fd-save"); if (save) save.onclick = async () => {
    const body = { key: $("#fd-key").value, value: $("#fd-val").value, source: $("#fd-src").value || "Donnée terrain", date: $("#fd-date").value,
      confiance: $("#fd-conf").value, auteur: $("#fd-auteur").value || null, weights: S.weights, d5: S.d5 };
    if (body.value === "") { toast("Indiquez une valeur."); return; }
    try { await api(`/api/sites/${encodeURIComponent(S.drawer.id)}/terrain`, body); toast("Donnée terrain enregistrée — score mis à jour."); await refreshSite(S.drawer.id); openDrawer(S.drawer.id, S.drawer.data.usage); }
    catch (e) { toast("Erreur : " + e.message, 5000); }
  };
  p.querySelectorAll("[data-delfield]").forEach((b) => (b.onclick = async () => {
    await api(`/api/sites/${encodeURIComponent(S.drawer.id)}/terrain/${b.dataset.delfield}`, undefined, "DELETE");
    await refreshSite(S.drawer.id); openDrawer(S.drawer.id, S.drawer.data.usage);
  }));
}
async function refreshSite(id) {
  const r = await api("/api/scores", { type: "ids", values: [id] });
  const i = S.sites.findIndex((s) => s.id === id);
  if (i >= 0 && r.sites[0]) S.sites[i] = r.sites[0];
  S.cache = {};
  if (S.tab === "classement") renderClassement();
}
function markPortfolio(id, stage) { const s = S.sites.find((x) => x.id === id); if (s) s.portfolio = stage; }

/* ------------------------------------------------------- contre-analyse */
function renderContre() {
  const st = S.stress || (S.stress = { preset: "defavorable", hyp: { cout_depollution_mult: 2, cout_demolition_mult: 1, delai_supplementaire_mois: 12, prix_marche_pct: -10, sans_subventions: false, pessimiste: false, usage_force: "" }, top_n: 10, runs: 100 });
  const h = st.hyp;
  const presets = { defavorable: { cout_depollution_mult: 2, delai_supplementaire_mois: 12, prix_marche_pct: -10 }, depollution_x2: { cout_depollution_mult: 2 }, delais_12: { delai_supplementaire_mois: 12 }, marche_baisse: { prix_marche_pct: -15 }, sans_subventions: { sans_subventions: true }, pessimiste: { pessimiste: true, cout_depollution_mult: 1.3 } };
  $("#main").innerHTML = `
  <div class="section-head"><div><h2>Contre-analyse — la short-list résiste-t-elle ?</h2>
    <p>Durcissez une ou plusieurs hypothèses et voyez quels sites du top ${st.top_n} restent en tête et lesquels s'effondrent. La simulation de Monte-Carlo rejoue ensuite chaque site en tirant ses incertitudes propres : un site bien documenté est plus robuste qu'un site au score égal construit sur des données fragiles.</p></div></div>
  <div class="card">
    <div class="seg" id="presets">${Object.entries(S.meta.presets).map(([k, l]) => `<button data-p="${k}" class="${st.preset === k ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
    <div class="grid3" style="margin-top:14px">
      <div class="slider"><span>Coût de dépollution</span><b>×${fmt(h.cout_depollution_mult, 1)}</b><input type="range" min="1" max="3" step="0.1" value="${h.cout_depollution_mult}" data-h="cout_depollution_mult"></div>
      <div class="slider"><span>Coût de démolition</span><b>×${fmt(h.cout_demolition_mult || 1, 1)}</b><input type="range" min="1" max="2" step="0.1" value="${h.cout_demolition_mult || 1}" data-h="cout_demolition_mult"></div>
      <div class="slider"><span>Délais supplémentaires</span><b>+${h.delai_supplementaire_mois} m</b><input type="range" min="0" max="36" step="3" value="${h.delai_supplementaire_mois}" data-h="delai_supplementaire_mois"></div>
      <div class="slider"><span>Prix de marché</span><b>${h.prix_marche_pct > 0 ? "+" : ""}${h.prix_marche_pct} %</b><input type="range" min="-30" max="10" step="5" value="${h.prix_marche_pct}" data-h="prix_marche_pct"></div>
      <div><label class="toggle"><input type="checkbox" data-hb="sans_subventions" ${h.sans_subventions ? "checked" : ""}> Aucune subvention obtenue</label>
           <label class="toggle"><input type="checkbox" data-hb="pessimiste" ${h.pessimiste ? "checked" : ""}> Lecture pessimiste des données incertaines</label></div>
      <div><label class="small muted">Changer l'usage cible de tous les sites</label><select id="h-usage"><option value="">Conserver l'usage de base</option>${Object.entries(S.meta.usages).map(([k, l]) => `<option value="${k}" ${h.usage_force === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    </div>
    <div class="row" style="margin-top:12px;flex-wrap:wrap;justify-content:flex-start">
      <label class="small" style="flex:0">Top</label><input type="number" id="h-top" value="${st.top_n}" min="3" max="30" style="width:70px;flex:0 0 70px">
      <button class="btn" id="run-scn" style="flex:0;white-space:nowrap">Lancer le scénario</button>
      <label class="small" style="flex:0;white-space:nowrap">Tirages</label><input type="number" id="h-runs" value="${st.runs}" min="20" max="400" step="20" style="width:80px;flex:0 0 80px">
      <button class="btn sec" id="run-mc" style="flex:0;white-space:nowrap">Simulation Monte-Carlo</button>
    </div>
  </div>
  <div id="scn-out">${st.scn ? scnHtml(st.scn) : ""}</div>
  <div id="mc-out">${st.mc ? mcHtml(st.mc) : ""}</div>`;
  $("#presets").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    st.preset = b.dataset.p;
    st.hyp = { cout_depollution_mult: 1, cout_demolition_mult: 1, delai_supplementaire_mois: 0, prix_marche_pct: 0, sans_subventions: false, pessimiste: false, usage_force: "", ...presets[b.dataset.p] };
    renderContre();
  };
  document.querySelectorAll("#main [data-h]").forEach((i) => (i.oninput = () => { h[i.dataset.h] = +i.value; st.preset = null; i.parentElement.querySelector("b").textContent = i.dataset.h === "delai_supplementaire_mois" ? `+${i.value} m` : i.dataset.h === "prix_marche_pct" ? `${+i.value > 0 ? "+" : ""}${i.value} %` : `×${fmt(+i.value, 1)}`; document.querySelectorAll("#presets button").forEach((b) => b.classList.remove("on")); }));
  document.querySelectorAll("#main [data-hb]").forEach((i) => (i.onchange = () => { h[i.dataset.hb] = i.checked; st.preset = null; }));
  $("#h-usage").onchange = (e) => (h.usage_force = e.target.value);
  $("#h-top").onchange = (e) => (st.top_n = +e.target.value);
  $("#h-runs").onchange = (e) => (st.runs = +e.target.value);
  $("#run-scn").onclick = async () => {
    $("#scn-out").innerHTML = `<div class="card loading">Calcul du scénario…</div>`;
    const hyp = { ...h }; if (!hyp.usage_force) delete hyp.usage_force;
    try { st.scn = await api("/api/stress", { ...ctx(), hyp, top_n: st.top_n }); $("#scn-out").innerHTML = scnHtml(st.scn); bindRows("#scn-out"); }
    catch (e) { $("#scn-out").innerHTML = `<div class="card">Erreur : ${esc(e.message)}</div>`; }
  };
  $("#run-mc").onclick = async () => {
    $("#mc-out").innerHTML = `<div class="card loading">Simulation de ${st.runs} tirages…</div>`;
    const hyp = { ...h }; if (!hyp.usage_force) delete hyp.usage_force;
    try { st.mc = await api("/api/montecarlo", { ...ctx(), hyp, top_n: st.top_n, runs: st.runs }); $("#mc-out").innerHTML = mcHtml(st.mc); bindRows("#mc-out"); }
    catch (e) { $("#mc-out").innerHTML = `<div class="card">Erreur : ${esc(e.message)}</div>`; }
  };
  bindRows("#main");
}
function bindRows(sel) { document.querySelectorAll(`${sel} tr.click`).forEach((tr) => (tr.onclick = () => openDrawer(tr.dataset.id, tr.dataset.u || null))); }
function statusCls(s) { return { stable: "stable", fragile: "fragile", "sort du top": "sort", "s'effondre": "effondre" }[s] || ""; }
function scnHtml(r) {
  return `<div class="synth">${esc(r.synthese)}</div>
  <div class="card" style="padding:0"><table><thead><tr><th>Site</th><th>Usage</th><th>Rang</th><th>Score</th><th>Statut</th><th>Pourquoi</th></tr></thead><tbody>
  ${r.lignes.map((l) => `<tr class="click" data-id="${esc(l.id)}" data-u="${l.usage_scenario}"><td><div class="site-name">${esc(l.nom)}</div><div class="site-sub">${esc(l.commune)}</div></td>
    <td class="small">${esc(S.meta.usages[l.usage_base])}${l.usage_scenario !== l.usage_base ? " → " + esc(S.meta.usages[l.usage_scenario]) : ""}</td>
    <td class="arrow">${l.rang_base} → <b>${l.rang_scenario}</b></td>
    <td class="arrow">${fmt(l.score_base)} → <b>${fmt(l.score_scenario)}</b> <span class="${l.delta >= 0 ? "delta-pos" : "delta-neg"}">(${l.delta > 0 ? "+" : ""}${fmt(l.delta, 1)})</span></td>
    <td><span class="status ${statusCls(l.statut)}">${esc(l.statut)}</span></td><td class="small">${esc(l.pourquoi)}</td></tr>`).join("")}
  </tbody></table></div>
  ${r.entrants.length ? `<div class="card"><h3>Entrent dans le top ${r.top_n} sous ce scénario</h3>${r.entrants.map((e) => `<span class="chip ok">${esc(e.nom)} (${esc(e.commune)}) : ${e.rang_base} → ${e.rang_scenario}</span>`).join(" ")}</div>` : ""}`;
}
function mcHtml(r) {
  return `<div class="synth">${esc(r.synthese)}</div>
  <div class="card" style="padding:0"><table><thead><tr><th>Site</th><th>Rang de base</th><th>P(rester dans le top ${r.top_n})</th><th>Score : 10ᵉ–90ᵉ centile</th><th>Confiance</th><th>Verdict</th></tr></thead><tbody>
  ${r.lignes.map((l) => `<tr class="click" data-id="${esc(l.id)}" data-u="${l.usage}"><td><div class="site-name">${esc(l.nom)}</div><div class="site-sub">${esc(l.commune)} · ${esc(S.meta.usages[l.usage])}</div></td>
    <td class="num">${l.rang_base}</td>
    <td><div class="prob"><div class="bar"><i style="width:${l.p_top * 100}%;background:${l.p_top >= 0.8 ? "var(--good)" : l.p_top >= 0.5 ? "var(--mid)" : "var(--bad)"}"></i></div><b class="num">${fmt(l.p_top * 100)} %</b></div></td>
    <td><div class="range" title="${fmt(l.score_p10, 1)} – ${fmt(l.score_p90, 1)}"><i style="left:${l.score_p10}%;width:${Math.max(1, l.score_p90 - l.score_p10)}%"></i><b style="left:${l.score_base}%"></b></div><div class="small muted num">${fmt(l.score_p10)} – ${fmt(l.score_p90)} (base ${fmt(l.score_base)})</div></td>
    <td>${confDot(l.confiance)}</td>
    <td><span class="status ${l.robustesse === "robuste" ? "stable" : l.robustesse === "probable" ? "fragile" : "effondre"}">${esc(l.robustesse)}</span></td></tr>`).join("")}
  </tbody></table></div>`;
}

/* ------------------------------------------------------- coûts cachés */
async function renderCouts() {
  const level = S.heatLevel || "epci";
  $("#main").innerHTML = `<div class="section-head"><div><h2>Carte de chaleur des coûts cachés</h2>
    <p>Où se concentrent les sites « piégeux » (risque de surcoût élevé, D2 &lt; ${40}) et où se trouvent les « pépites » propres (coût faible, friction faible, données fiables) ? Agrégation par territoire, pour l'usage retenu de chaque site.</p></div>
    <div class="seg" id="lvl">${[["departement", "Département"], ["epci", "EPCI"], ["commune", "Commune"]].map(([k, l]) => `<button data-l="${k}" class="${level === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="card loading">Agrégation…</div>`;
  $("#lvl").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.heatLevel = b.dataset.l; renderCouts(); } };
  const key = JSON.stringify(["heat", level, ctx()]);
  const r = S.cache[key] || (S.cache[key] = await api(`/api/heatmap?level=${level}`, ctx()));
  if (S.tab !== "couts") return;
  const heatCol = (p) => { const t = Math.min(1, p / 0.5); const stops = [[227, 239, 233], [244, 228, 184], [238, 189, 152], [215, 125, 99]]; const x = t * 3, i = Math.min(2, Math.floor(x)), f = x - i; const c = stops[i].map((v, j) => Math.round(v + (stops[i + 1][j] - v) * f)); return `rgb(${c.join(",")})`; };
  const card = $("#main .card"); card.outerHTML = `
  <div class="legend" style="margin-bottom:10px"><span>Part de sites piégeux :</span><span>0 %</span><span class="ramp"></span><span>≥ 50 %</span></div>
  <div class="heatgrid">${r.groupes.map((g) => `<div class="heat" style="background:${heatCol(g.part_piegeux)}">
    <h3>${esc(g.groupe)}</h3><div class="big2">${fmt(g.part_piegeux * 100)} %</div>
    <div class="meta">${g.n_piegeux} piégeux sur ${g.n} · ${fmt(g.surface_ha)} ha<br>Coût médian ${fmt(g.cout_m2_median)} €/m² · D2 moyen ${fmt(g.d2_moyen)}<br><b>${g.n_pepites} pépite(s) propre(s)</b></div></div>`).join("")}</div>
  <div class="grid2" style="margin-top:16px">
    <div class="card"><h3>Sites piégeux à approcher avec prudence</h3><table><tbody>
      ${r.groupes.flatMap((g) => g.piegeux).sort((a, b) => a.d2 - b.d2).slice(0, 12).map((i) => `<tr class="click" data-id="${esc(i.id)}" data-u="${i.usage}"><td><div class="site-name">${esc(i.nom)}</div><div class="site-sub">${esc(i.commune)} · ${esc(S.meta.usages[i.usage])}</div></td><td class="num">D2 ${fmt(i.d2)}</td><td class="cost">${fmt(i.cout_m2)} €/m²</td></tr>`).join("") || `<tr><td class="empty">Aucun</td></tr>`}</tbody></table></div>
    <div class="card"><h3>Pépites propres (coût faible, friction faible, données fiables)</h3><table><tbody>
      ${r.groupes.flatMap((g) => g.pepites).sort((a, b) => b.global - a.global).slice(0, 12).map((i) => `<tr class="click" data-id="${esc(i.id)}" data-u="${i.usage}"><td><div class="site-name">${esc(i.nom)}</div><div class="site-sub">${esc(i.commune)} · ${esc(S.meta.usages[i.usage])}</div></td><td class="num">score ${fmt(i.global)}</td><td class="cost">${fmt(i.cout_m2)} €/m²</td></tr>`).join("") || `<tr><td class="empty">Aucune</td></tr>`}</tbody></table></div>
  </div>
  <div class="card"><h3>Repérage</h3><p class="small muted" style="margin:4px 0 8px">La carte sert à naviguer, pas à décider : chaque point est une friche, sa couleur est son risque de surcoût, sa taille sa surface. Sélectionnez un département ou un EPCI dans le périmètre pour zoomer.</p>${scatter(r.points)}</div>`;
  bindRows("#main");
  document.querySelectorAll("#main circle[data-id]").forEach((c) => (c.onclick = () => openDrawer(c.dataset.id, c.dataset.u)));
}
function scatter(points) {
  const pts = points.filter((p) => p.lat != null && p.lon != null);
  if (!pts.length) return `<div class="empty">Pas de coordonnées.</div>`;
  const W = 900, H = 460, pad = 24;
  let minX = Math.min(...pts.map((p) => p.lon)), maxX = Math.max(...pts.map((p) => p.lon));
  let minY = Math.min(...pts.map((p) => p.lat)), maxY = Math.max(...pts.map((p) => p.lat));
  const k = Math.cos(((minY + maxY) / 2) * Math.PI / 180);
  const sx = (maxX - minX) * k || 0.1, sy = maxY - minY || 0.1;
  const scale = Math.min((W - 2 * pad) / sx, (H - 2 * pad) / sy);
  const X = (lon) => pad + (lon - minX) * k * scale + ((W - 2 * pad) - sx * scale) / 2;
  const Y = (lat) => H - pad - (lat - minY) * scale - ((H - 2 * pad) - sy * scale) / 2;
  const col = (d2) => (d2 < 40 ? "#c8553d" : d2 < 55 ? "#e39b5b" : d2 < 70 ? "#d8c06a" : "#4f9a78");
  const groups = {};
  const byDep = new Set(pts.map((p) => p.groupe)).size > 8;
  pts.forEach((p) => { const g = byDep ? "Dépt " + p.departement : p.groupe; (groups[g] = groups[g] || []).push(p); });
  const labels = Object.entries(groups).map(([g, ps]) => { const lx = ps.reduce((a, p) => a + p.lon, 0) / ps.length, ly = Math.max(...ps.map((p) => p.lat)); return `<text x="${X(lx)}" y="${Y(ly) - 12}" text-anchor="middle" font-size="11" fill="#6b7682">${esc(g.length > 34 ? g.slice(0, 32) + "…" : g)}</text>`; });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="background:#fafbf9;border-radius:8px">
    ${labels.join("")}
    ${pts.sort((a, b) => b.surface_ha - a.surface_ha).map((p) => `<circle data-id="${esc(p.id)}" data-u="${p.usage}" cx="${X(p.lon).toFixed(1)}" cy="${Y(p.lat).toFixed(1)}" r="${(3 + Math.sqrt(p.surface_ha) * 1.6).toFixed(1)}" fill="${col(p.d2)}" fill-opacity=".8" stroke="#fff" stroke-width="1" style="cursor:pointer"><title>${esc(p.nom)} — ${esc(p.commune)}\nRisque de surcoût ${esc(p.risque)} (D2 ${fmt(p.d2)}), ${fmt(p.cout_m2)} €/m²</title></circle>`).join("")}
    <rect x="${W - 268}" y="6" width="262" height="22" rx="5" fill="#fff" fill-opacity=".9"/><g transform="translate(${W - 210},17)" font-size="11" fill="#3c4855">${[["#4f9a78", "faible"], ["#d8c06a", "modéré"], ["#e39b5b", "élevé"], ["#c8553d", "piégeux"]].map(([c, l], i) => `<circle cx="${i * 64 - 50}" cy="0" r="5" fill="${c}"/><text x="${i * 64 - 42}" y="4">${l}</text>`).join("")}</g>
  </svg>`;
}

/* -------------------------------------------------------- horloge ZAN */
async function renderZan() {
  $("#main").innerHTML = `<div class="section-head"><div><h2>Horloge ZAN — fenêtres de financement</h2>
    <p>Échéances réglementaires du ZAN et fenêtres de subvention, croisées avec la maturité estimée de chaque friche (étape du portfolio + délais procéduraux). Les friches dont le dossier peut être prêt avant la clôture d'un dispositif auquel elles sont éligibles sont signalées.</p></div></div><div class="card loading">Calcul…</div>`;
  const key = JSON.stringify(["zan", ctx()]);
  const r = S.cache[key] || (S.cache[key] = await api("/api/zan", ctx()));
  if (S.tab !== "zan") return;
  $("#main .card").outerHTML = `
  <div class="alert">${esc(r.avertissement)} Fichier éditable : <code>app/data/funding_windows.json</code>.</div>
  <div class="card tl-wrap">${timeline(r)}</div>
  <div class="card" style="padding:0"><table><thead><tr><th>Friche</th><th>Usage</th><th>Score</th><th>Dossier prêt vers</th><th>Fenêtres de financement compatibles</th></tr></thead><tbody>
  ${zanGroups(r.correspondances).slice(0, 30).map((g) => `<tr class="click zan-row" data-id="${esc(g.id)}" data-u="${g.usage}"><td><div class="site-name">${esc(g.nom)}</div><div class="site-sub">${esc(g.commune)}${g.stage ? " · " + esc(S.meta.stages[g.stage]) : " · hors portfolio"}</div></td>
    <td>${esc(S.meta.usages[g.usage])}</td><td class="num">${fmt(g.score)}</td><td class="num">${esc(g.maturite.date)}<div class="site-sub">~${fmt(g.maturite.mois, 1)} mois</div></td>
    <td>${g.items.map((m) => `<div style="margin-bottom:3px"><span class="status ${m.statut === "coïncide" ? "stable" : "fragile"}">${m.statut === "coïncide" ? "coïncide" : "trop juste"}</span> ${esc(m.fenetre_nom)} <span class="muted small">— clôture ${esc(m.cloture)}, marge <b class="${m.marge_jours >= 0 ? "delta-pos" : "delta-neg"}">${m.marge_jours >= 0 ? "+" : ""}${m.marge_jours} j</b></span></div>`).join("")}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">Aucune friche du périmètre ne coïncide avec une fenêtre ouverte. Faites avancer des sites dans le portfolio pour réduire leur délai de maturité.</td></tr>`}
  </tbody></table></div>`;
  bindRows("#main");
}
function zanGroups(list) {
  const m = new Map();
  list.forEach((c) => { if (!m.has(c.id)) m.set(c.id, { ...c, items: [] }); m.get(c.id).items.push(c); });
  const out = [...m.values()];
  out.forEach((g) => g.items.sort((a, b) => a.cloture.localeCompare(b.cloture)));
  // d'abord les sites ayant une fenêtre qui coïncide, puis par score
  return out.sort((a, b) => (b.items.some((i) => i.statut === "coïncide") - a.items.some((i) => i.statut === "coïncide")) || b.score - a.score);
}
function timeline(r) {
  const t0 = new Date(r.aujourdhui), t1 = new Date("2031-12-31");
  const W = 1000, L = 210, top = 34, rowH = 22;
  const X = (d) => L + ((new Date(d) - t0) / (t1 - t0)) * (W - L - 20);
  const wins = r.fenetres;
  const H = top + (wins.length + 1) * rowH + 30;
  const years = []; for (let y = t0.getFullYear() + 1; y <= 2031; y++) years.push(y);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:760px">
    ${years.map((y) => `<line x1="${X(y + "-01-01")}" x2="${X(y + "-01-01")}" y1="20" y2="${H - 10}" stroke="#eef0ec"/><text x="${X(y + "-01-01") + 3}" y="16" font-size="11" fill="#6b7682">${y}</text>`).join("")}
    <line x1="${L}" x2="${L}" y1="20" y2="${H - 10}" stroke="#1c2733" stroke-width="2"/><text x="${L - 4}" y="16" font-size="11" text-anchor="end" fill="#1c2733" font-weight="700">aujourd'hui</text>
    ${r.jalons.filter((j) => !j.passee).map((j) => `<g><line x1="${X(j.date)}" x2="${X(j.date)}" y1="24" y2="${H - 10}" stroke="#b5541c" stroke-dasharray="4 3"/><path d="M${X(j.date)} ${top - 8} l6 6 l-6 6 l-6 -6z" fill="#b5541c"><title>${esc(j.nom)} — ${esc(j.date)}\n${esc(j.effet || "")}\n${esc(j.source)}</title></path></g>`).join("")}
    ${wins.map((w, i) => { const y = top + 10 + i * rowH; const x0 = Math.max(L, X(w.ouverture)), x1 = X(w.cloture); return `<text x="${L - 8}" y="${y + 12}" font-size="10.5" text-anchor="end" fill="#3c4855">${esc(w.nom.length > 38 ? w.nom.slice(0, 36) + "…" : w.nom)}</text><rect x="${x0}" y="${y + 2}" width="${Math.max(3, x1 - x0)}" height="13" rx="3" fill="#1f6f5c" fill-opacity=".75"><title>${esc(w.nom)}\n${esc(w.ouverture)} → ${esc(w.cloture)}\nConfiance : ${esc(w.confiance)}</title></rect>`; }).join("")}
    <text x="${L}" y="${H - 2}" font-size="10.5" fill="#b5541c">◆ échéances ZAN : ${r.jalons.filter((j) => !j.passee).map((j) => `${esc(j.nom.split("(")[0].replace("Intégration des objectifs ZAN dans les ", "").replace("Fin de la première décennie ZAN ", "fin 1re décennie"))} ${esc(j.date)}`).join(" · ")}</text>
  </svg>`;
}

/* ------------------------------------------------------------ jumeau */
function renderJumeau() {
  const J = S.jumeau || (S.jumeau = { a: { id: null, usage: "logement" }, b: { extension: true, usage: "logement" } });
  const ev = S.sites.filter((s) => s.evaluable);
  const communes = S.meta.perimeters.communes;
  const side = (k) => {
    const x = J[k];
    const ext = x.extension;
    const e = typeof ext === "object" ? ext : (J[k].extension = ext ? { code_insee: (S.sites.find((x) => x.id === (J.a.id || J.b.id)) || {}).code_insee || communes[0]?.code_insee, surface_m2: 30000, distance_centre_km: 4, plu_zone: "AU", prix_terrain_m2: 12, desserte_tc: "faible", nb_proprietaires: 2 } : null);
    return `<div class="cmp-side" id="side-${k}"><h3>Site ${k.toUpperCase()}</h3>
      <label class="small muted">Friche ou site en extension</label>
      <select data-k="${k}" class="j-site"><option value="__ext" ${ext ? "selected" : ""}>➕ Site en extension (terrain non artificialisé)</option>
        ${ev.map((s) => `<option value="${esc(s.id)}" ${x.id === s.id && !ext ? "selected" : ""}>${esc(s.nom)} — ${esc(s.commune)}</option>`).join("")}</select>
      <label class="small muted" style="margin-top:6px;display:block">Usage simulé</label>
      <select data-k="${k}" class="j-usage">${Object.entries(S.meta.usages).map(([u, l]) => `<option value="${u}" ${x.usage === u ? "selected" : ""}>${l}</option>`).join("")}</select>
      ${e ? `<div class="grid2" style="gap:8px;margin-top:8px">
        <div><label class="small muted">Commune</label><select data-e="${k}" data-f="code_insee">${communes.map((c) => `<option value="${c.code_insee}" ${e.code_insee === c.code_insee ? "selected" : ""}>${esc(c.nom)}</option>`).join("")}</select></div>
        <div><label class="small muted">Surface (m²)</label><input type="number" data-e="${k}" data-f="surface_m2" value="${e.surface_m2}"></div>
        <div><label class="small muted">Distance au centre (km)</label><input type="number" data-e="${k}" data-f="distance_centre_km" value="${e.distance_centre_km}"></div>
        <div><label class="small muted">Zone PLU</label><select data-e="${k}" data-f="plu_zone">${["AU", "A", "N", "U"].map((z) => `<option ${e.plu_zone === z ? "selected" : ""}>${z}</option>`).join("")}</select></div>
        <div><label class="small muted">Prix du terrain (€/m²)</label><input type="number" data-e="${k}" data-f="prix_terrain_m2" value="${e.prix_terrain_m2}"></div>
        <div><label class="small muted">Desserte TC</label><select data-e="${k}" data-f="desserte_tc">${["faible", "moyenne", "forte"].map((z) => `<option ${e.desserte_tc === z ? "selected" : ""}>${z}</option>`).join("")}</select></div>
      </div>` : ""}</div>`;
  };
  $("#main").innerHTML = `<div class="section-head"><div><h2>Jumeau de scénario</h2>
    <p>Comparez deux friches, ou une friche et un site en extension, en simulant un usage différent sur chacune. L'outil dit lequel est préférable et pourquoi, en trois lignes.</p></div></div>
    <div class="grid2">${side("a")}${side("b")}</div>
    <div style="margin:12px 0"><button class="btn" id="j-run">Comparer</button></div>
    <div id="j-out">${S.jumeauRes ? cmpHtml(S.jumeauRes) : ""}</div>`;
  document.querySelectorAll(".j-site").forEach((s) => (s.onchange = () => { const k = s.dataset.k; if (s.value === "__ext") J[k] = { extension: true, usage: J[k].usage }; else J[k] = { id: s.value, usage: J[k].usage }; renderJumeau(); }));
  document.querySelectorAll(".j-usage").forEach((s) => (s.onchange = () => (J[s.dataset.k].usage = s.value)));
  document.querySelectorAll("[data-e]").forEach((i) => (i.onchange = () => { const v = i.type === "number" ? +i.value : i.value; J[i.dataset.e].extension[i.dataset.f] = v; }));
  $("#j-run").onclick = runCompare;
  if (!J.a.id && !J.a.extension && ev[0]) { J.a.id = ev[0].id; renderJumeau(); }
}
async function runCompare() {
  const J = S.jumeau;
  const side = (x) => (x.extension ? { usage: x.usage, extension: typeof x.extension === "object" ? x.extension : {} } : { id: x.id, usage: x.usage });
  const out = $("#j-out"); if (out) out.innerHTML = `<div class="card loading">Comparaison…</div>`;
  try {
    S.jumeauRes = await api("/api/compare", { a: side(J.a), b: side(J.b), weights: S.weights, d5: S.d5 });
    if (S.tab === "jumeau") renderJumeau();
  } catch (e) { if ($("#j-out")) $("#j-out").innerHTML = `<div class="card">Erreur : ${esc(e.message)}</div>`; }
}
function cmpHtml(r) {
  const A = r.a, B = r.b;
  const winA = r.gagnant === A.id && A.global.global >= B.global.global;
  const col = (x, win) => `<div class="cmp-side ${win ? "win" : ""}"><div class="row" style="justify-content:space-between"><div><div class="site-name">${esc(x.nom)}</div><div class="site-sub">${esc(x.commune || "")} · ${esc(x.usage_label)}</div></div>
     <div class="score" style="flex:0;color:${x.global.bloque ? "var(--bad)" : colorFor(x.global.global)}">${fmt(x.global.global)}</div></div>${confDot(x.global.confiance)}
     <div class="kv" style="margin-top:8px"><span>Remise en état</span><b>${eur(x.cost.total_bas)} – ${eur(x.cost.total_haut)}</b><span>Délai indicatif</span><b>~${x.friction.delai_total_mois} mois</b>
     ${x.market.marchand ? `<span>Bilan net</span><b>${eur(x.market.bilan_net_bas)} – ${eur(x.market.bilan_net_haut)}</b>` : ""}
     <span>Financements</span><b>${x.impact.eligibilites.length}</b></div>
     <h4 style="margin-top:10px">Facteurs déterminants</h4>${driversHtml(x.global.drivers)}
     ${x.blockers.map((b) => `<div class="alert bad">${esc(b)}</div>`).join("")}</div>`;
  return `<div class="synth">${r.synthese.map((l) => `<div>${esc(l)}</div>`).join("")}</div>
  <div class="card"><div class="pairbar"><span></span><b class="small">${esc(A.nom)}</b><b class="small">${esc(B.nom)}</b>
    ${DIMS.map((k) => `<span>${esc(S.meta.dimensions[k])}</span>${[A, B].map((x) => `<div class="val"><div class="bar"><i style="width:${x.dims[k]}%;background:${colorFor(x.dims[k], k === "d3")}"></i></div><b>${fmt(x.dims[k])}</b></div>`).join("")}`).join("")}
  </div></div>
  <div class="grid2" style="margin-top:14px">${col(A, winA)}${col(B, !winA)}</div>`;
}

/* --------------------------------------------------------- portfolio */
async function renderPortfolio() {
  $("#main").innerHTML = `<div class="section-head"><div><h2>Portfolio — pipeline d'études</h2>
    <p>Suivez vos sites de « repérée » à « en étude ». Le score reste vivant : chaque donnée terrain saisie (étude de sol, visite, relevé foncier) le met à jour, et l'historique montre comment le site remonte ou descend dans le classement de son département. Glissez-déposez une carte pour changer d'étape.</p></div></div><div class="loading">Chargement…</div>`;
  const r = await api("/api/portfolio/list", { weights: S.weights, d5: S.d5 });
  if (S.tab !== "portfolio") return;
  const items = r.items;
  $("#main .loading").outerHTML = items.length ? `<div class="kanban">${Object.entries(r.stages).map(([k, l]) => {
    const its = items.filter((i) => i.stage === k).sort((a, b) => (b.global ?? -1) - (a.global ?? -1));
    return `<div class="col" data-stage="${k}"><h3>${esc(l)}<span class="muted">${its.length}</span></h3>${its.map((i) => {
      const h = i.history || [];
      const first = h[0], last = h[h.length - 1];
      const trend = first && last && h.length > 1 ? `${first.rang}ᵉ → ${last.rang}ᵉ / ${last.rang_sur}` : last ? `${last.rang}ᵉ / ${last.rang_sur} dans le dépt.` : "";
      return `<div class="kcard" draggable="true" data-id="${esc(i.id)}" data-u="${i.usage || ""}"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><div class="site-name">${esc(i.nom)}</div><div class="site-sub">${esc(i.commune)} · ${esc(S.meta.usages[i.usage] || "")}</div></div>
        <div class="kscore" style="flex:0;color:${i.global == null ? "var(--muted)" : colorFor(i.global)}">${i.global == null ? "n.é." : fmt(i.global)}</div></div>
        ${h.length > 1 ? historyChart(h, 220, 46) : ""}${last ? `<div class="small muted">historique (profil Équilibré) : ${fmt(last.global)}</div>` : ""}
        <div class="ktrend muted">${esc(trend)}${i.n_terrain ? ` · ${i.n_terrain} donnée(s) terrain` : ""}</div></div>`;
    }).join("") || `<div class="small muted" style="padding:6px">—</div>`}</div>`;
  }).join("")}</div>` : `<div class="card empty">Le portfolio est vide. Ouvrez une friche depuis le classement et cliquez « Ajouter au portfolio ».</div>`;
  document.querySelectorAll(".kcard").forEach((c) => {
    c.ondragstart = (e) => e.dataTransfer.setData("text/plain", c.dataset.id);
    c.onclick = () => openDrawer(c.dataset.id, c.dataset.u || null);
  });
  document.querySelectorAll(".col").forEach((col) => {
    col.ondragover = (e) => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = async (e) => {
      e.preventDefault(); col.classList.remove("over");
      const id = e.dataTransfer.getData("text/plain");
      await api(`/api/portfolio/${encodeURIComponent(id)}`, { stage: col.dataset.stage, weights: S.weights, d5: S.d5 }, "PUT");
      markPortfolio(id, col.dataset.stage); S.cache = {}; renderPortfolio();
    };
  });
}

/* ------------------------------------------------------- données */
function renderDonnees() {
  const m = S.meta;
  $("#main").innerHTML = `<div class="section-head"><div><h2>Données & méthode</h2><p>Sources chargées, import de vos propres données et règles de calcul du FricheScore.</p></div></div>
  <div class="grid2">
    <div class="card"><h3>Données chargées</h3><table style="margin-top:6px"><tbody>${m.perimeters.origines.map((o) => `<tr><td>${esc(o.origine)}</td><td class="num">${o.n} friches</td><td><button class="btn sm sec" data-delorig="${esc(o.origine)}">Retirer</button></td></tr>`).join("")}</tbody></table>
      <button class="btn sec sm" id="reset-demo" style="margin-top:10px">Réinitialiser le jeu de démonstration</button>
      <p class="hint">La réinitialisation efface aussi le portfolio et les données terrain.</p></div>
    <div class="card"><h3>Importer un fichier CSV</h3>
      <p class="small muted">Format FricheScore (une colonne par indicateur — <a href="/api/import/modele.csv">télécharger le modèle</a>) ou export Cartofriches (colonnes reconnues : site_id, site_nom, comm_insee, site_surface, site_type, bati_surface…). Pour tracer la provenance colonne par colonne, ajoutez <code>&lt;colonne&gt;__source</code>, <code>__date</code>, <code>__confiance</code>.</p>
      <input type="file" id="imp-file" accept=".csv,text/csv"><div class="row" style="margin-top:6px"><input type="text" id="imp-src" placeholder="Libellé de la source (ex. Observatoire EPF 2026)"><input type="date" id="imp-date"></div>
      <button class="btn" id="imp-go" style="margin-top:8px">Importer</button><div id="imp-out" class="small" style="margin-top:6px"></div></div>
  </div>
  <div class="card"><h3>Connecteur Cartofriches + Géorisques</h3>
    <p class="small muted">Interroge l'API Données foncières du Cerema (Cartofriches) puis Géorisques (CASIAS/ex-BASIAS, instructions SSP/ex-BASOL, SIS, ICPE) pour chaque friche. Nécessite un accès Internet depuis la machine qui exécute FricheScore ; en cas d'échec, l'erreur est affichée telle quelle et aucune donnée n'est inventée.</p>
    <div class="row"><input type="text" id="cf-codes" placeholder="Codes INSEE séparés par des virgules (ex. 59512, 59599)"><button class="btn" id="cf-go" style="flex:0;white-space:nowrap">Interroger</button></div><div id="cf-out" class="small" style="margin-top:6px"></div></div>
  <div class="card doc"><h3>Méthode</h3>
    <p>Chaque friche reçoit une note de 0 à 100 sur cinq dimensions, calculées <b>séparément pour chaque usage</b> (${Object.values(m.usages).join(", ").toLowerCase()}).</p>
    <ul>
      <li><b>Potentiel d'usage</b> — caractéristiques du site croisées avec les besoins du territoire, avec une logique propre à chaque usage. Certains motifs sont rédhibitoires (zone rouge de PPRi pour du logement, captage en protection immédiate…) : le score est alors plafonné à ${m.constants.blocker_cap}.</li>
      <li><b>Coût de remise en état</b> — indice de risque de surcoût, pas un devis : démolition et amiante, dépollution selon le signal le plus grave (diagnostic terrain &gt; BASOL/SIS &gt; ICPE &gt; BASIAS) modulée par la sensibilité de l'usage, viabilisation, géométrie, maîtrise foncière. Le score combine le niveau de coût (70 %) et la largeur de la fourchette (30 %).</li>
      <li><b>Friction réglementaire</b> — points de friction et mois de délai : zones humides, captages, PPRi/PPRT, Natura 2000, ABF, évaluation environnementale, compatibilité du PLU(i), servitudes, ICPE à cesser, multipropriété. <b>Plus elle est élevée, plus le site est pénalisé</b>.</li>
      <li><b>Attractivité économique</b> — marché (DVF, dynamique, liquidité, SITADEL, vacance) et compte à rebours aménageur simplifié (charge foncière admissible − remise en état − acquisition).</li>
      <li><b>Impact & financements</b> (optionnelle) — contribution ZAN, programmes territoriaux, éligibilité présumée aux dispositifs (Fonds Vert, ADEME, France 2030, CRE, régions).</li>
    </ul>
    <p><code>FricheScore = Σ poids × note / Σ poids − pénalité de confiance</code>, où la friction entre sous la forme <code>100 − friction</code> et la pénalité vaut <code>(1 − confiance) × ${m.constants.confidence_max_penalty}</code>. La confiance d'un indicateur = niveau de la source (élevé 1, moyen 0,75, faible 0,45) × fraîcheur (1 jusqu'à 3 ans, 0,7 à 10 ans, 0,6 au-delà) ; un indicateur manquant compte 0,3.</p>
    <p><b>Refus de scorer</b> : sans localisation, surface et au moins une information sur la pollution ou l'usage passé, la friche est déclarée « non évaluable ».</p>
    <p>Toutes les hypothèses chiffrées sont regroupées dans <code>app/scoring/config.py</code> et peuvent être calibrées sur vos propres opérations.</p></div>
  <div class="card"><details><summary>Catalogue des ${Object.keys(m.catalog).length} indicateurs (source de référence et confiance par défaut)</summary>
    <table class="fv"><thead><tr><th>Clé</th><th>Indicateur</th><th>Source de référence</th><th>Confiance par défaut</th><th>Valeurs</th></tr></thead><tbody>
    ${Object.entries(m.catalog).map(([k, c]) => `<tr><td><code>${k}</code></td><td>${esc(c.label)}</td><td>${esc(c.source)}</td><td>${esc(c.confiance)}</td><td class="small">${esc((c.valeurs || []).join(", "))}</td></tr>`).join("")}</tbody></table></details></div>`;
  $("#reset-demo").onclick = async () => { if (!confirm("Réinitialiser le jeu de démonstration ? Le portfolio et les données terrain seront effacés.")) return; await api("/api/demo/reset", {}); S.meta = await api("/api/meta"); await loadSites(); renderSide(); render(); toast("Jeu de démonstration rechargé."); };
  document.querySelectorAll("[data-delorig]").forEach((b) => (b.onclick = async () => { if (!confirm(`Retirer toutes les friches « ${b.dataset.delorig} » ?`)) return; await api(`/api/donnees/${encodeURIComponent(b.dataset.delorig)}`, undefined, "DELETE"); S.meta = await api("/api/meta"); await loadSites(); renderSide(); render(); }));
  $("#imp-go").onclick = async () => {
    const f = $("#imp-file").files[0]; if (!f) { toast("Choisissez un fichier."); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("source", $("#imp-src").value || f.name); if ($("#imp-date").value) fd.append("date", $("#imp-date").value);
    try { const r = await api("/api/import/csv", fd); $("#imp-out").innerHTML = `${r.importees} friche(s) importée(s).${r.colonnes_ignorees.length ? " Colonnes ignorées : " + esc(r.colonnes_ignorees.join(", ")) : ""}${r.avertissements.length ? "<br>" + r.avertissements.map(esc).join("<br>") : ""}`; S.meta = await api("/api/meta"); await loadSites(); renderSide(); }
    catch (e) { $("#imp-out").textContent = "Erreur : " + e.message; }
  };
  $("#cf-go").onclick = async () => {
    const codes = $("#cf-codes").value.split(/[\s,;]+/).filter(Boolean); if (!codes.length) return;
    $("#cf-out").textContent = "Interrogation en cours…";
    try { const r = await api("/api/ingest/cartofriches", { codes_insee: codes }); $("#cf-out").innerHTML = `${r.importees} friche(s) importée(s).${r.erreurs.length ? "<br>" + r.erreurs.map(esc).join("<br>") : ""}`; S.meta = await api("/api/meta"); await loadSites(); renderSide(); }
    catch (e) { $("#cf-out").textContent = "Erreur : " + e.message; }
  };
}

boot().catch((e) => { $("#main").innerHTML = `<div class="card">Erreur de démarrage : ${esc(e.message)}</div>`; });
