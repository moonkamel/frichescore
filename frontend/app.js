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
  ["zan", "Horloge ZAN"], ["jumeau", "Jumeau"], ["portfolio", "Portfolio"], ["signaux", "Signaux"], ["donnees", "Données"], ["parametres", "Paramètres"],
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
  if (r.status === 401 && !path.startsWith("/api/auth/")) { showLogin("Votre session a expiré : reconnectez-vous."); throw new Error("connexion requise"); }
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
function saveCSV(name, header, rows) {
  const q = (v) => { const t = v == null ? "" : String(v); return /[";\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
  const txt = "\ufeff" + [header, ...rows].map((r) => r.map(q).join(";")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([txt], { type: "text/csv;charset=utf-8" })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
const numFR = (v, d = 1) => (v == null || isNaN(v) ? "" : Number(v).toFixed(d).replace(".", ","));
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
function role() { return S.user ? S.user.role : "admin_plateforme"; }  // mode local : tous les droits
const ROLE_RANG = { lecteur: 0, analyste: 1, admin: 2, admin_plateforme: 3 };
function peut(r) { return ROLE_RANG[role()] >= ROLE_RANG[r]; }
function showLogin(msg) {
  if (showLogin._on) return; showLogin._on = true;
  document.body.innerHTML = `<div class="login-wrap"><form class="login-card" id="login-form" autocomplete="on">
    <div class="brand" style="margin-bottom:14px"><span class="logo">F</span><div><strong style="color:var(--ink)">FricheScore</strong><small style="display:block;color:var(--muted)">Quelles friches méritent une étude en premier, et pourquoi</small></div></div>
    ${msg ? `<div class="alert info small">${esc(msg)}</div>` : ""}
    <label class="small muted">Adresse électronique</label><input type="email" id="lg-email" autocomplete="username" required autofocus>
    <label class="small muted" style="margin-top:8px;display:block">Mot de passe</label><input type="password" id="lg-pw" autocomplete="current-password" required>
    <div id="lg-err" class="small delta-neg" style="min-height:18px;margin-top:6px"></div>
    <button class="btn" type="submit" style="width:100%;margin-top:4px">Se connecter</button>
    <p class="small muted" style="margin-top:12px">Mot de passe oublié ou nouveau collaborateur : contactez l'administrateur de votre organisme.</p></form></div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/connexion", { email: $("#lg-email").value, mot_de_passe: $("#lg-pw").value }); location.reload(); }
    catch (er) { $("#lg-err").textContent = er.message; }
  };
}
function userMenu() {
  const old = $("#user-pop"); if (old) { old.remove(); return; }
  const u = S.user;
  const d = document.createElement("div"); d.id = "user-pop"; d.className = "user-pop";
  d.innerHTML = `<b>${esc(u.nom || u.email)}</b><div class="small muted">${esc(u.email)}</div>
    <div class="small" style="margin:6px 0">${esc(u.organisme ? u.organisme.nom : "Plateforme")} · ${esc(u.role_libelle)}</div>
    <details style="margin:6px 0"><summary class="small">Changer de mot de passe</summary>
      <input type="password" id="pw-old" placeholder="Mot de passe actuel" autocomplete="current-password" style="margin-top:6px">
      <input type="password" id="pw-new" placeholder="Nouveau (12 caractères, majuscules, minuscules, chiffres)" autocomplete="new-password" style="margin-top:6px">
      <button class="btn sm" id="pw-go" style="margin-top:6px">Modifier</button><div id="pw-out" class="small" style="margin-top:4px"></div></details>
    <button class="btn sm sec" id="logout" style="width:100%">Se déconnecter</button>`;
  document.body.appendChild(d);
  $("#logout").onclick = async () => { await api("/api/auth/deconnexion", {}); location.reload(); };
  $("#pw-go").onclick = async () => {
    try { const r = await api("/api/auth/mot-de-passe", { actuel: $("#pw-old").value, nouveau: $("#pw-new").value }); $("#pw-out").textContent = r.message; setTimeout(() => location.reload(), 1500); }
    catch (e) { $("#pw-out").innerHTML = `<span class="delta-neg">${esc(e.message)}</span>`; }
  };
}

async function boot() {
  let moi;
  try { moi = await api("/api/auth/moi"); } catch (e) { showLogin(); return; }
  S.user = moi.utilisateur;
  if (S.user) {
    const b = $("#btn-user"); b.hidden = false;
    b.textContent = `👤 ${S.user.nom || S.user.email.split("@")[0]}`;
    b.title = `${S.user.organisme ? S.user.organisme.nom : "Plateforme"} · ${S.user.role_libelle}`;
    b.onclick = userMenu;
    if (peut("admin")) TABS.push(["comptes", S.user.role === "admin_plateforme" ? "Organismes" : "Comptes"]);
  }
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
  $("#btn-jobs").onclick = () => setTab("donnees");
  watchJobs();
  refreshSignalBadge();
}

/* --------------------------------------------- tâches de fond (suivi global) */
async function watchJobs() {
  clearTimeout(watchJobs._t);
  let running = 0;
  try {
    const r = await api("/api/ingest/jobs");
    S.jobs = r.taches;
    running = r.taches.filter((j) => j.etat === "en_cours").length;
    const b = $("#btn-jobs");
    b.hidden = running === 0;
    b.textContent = `⏳ ${running} tâche${running > 1 ? "s" : ""} en cours`;
    const box = $("#jobs-box");
    if (box) box.innerHTML = jobsHtml(r.taches);
    if (watchJobs._wasRunning && running < watchJobs._wasRunning) { S.cache = {}; await loadSites(true); if (S.tab === "classement") renderClassement(); }
    watchJobs._wasRunning = running;
  } catch (e) { /* serveur indisponible : on réessaie plus tard */ }
  watchJobs._t = setTimeout(watchJobs, running ? 2000 : 15000);
}
function jobsHtml(jobs) {
  const recent = jobs.filter((j) => j.etat === "en_cours" || Date.now() / 1000 - j.debut < 6 * 3600);
  if (!recent.length) return `<div class="small muted">Aucune tâche récente.</div>`;
  return recent.map((j) => {
    const pct = j.total ? Math.round((100 * j.fait) / j.total) : 0;
    const when = new Date(j.debut * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    const st = j.etat === "en_cours" ? `<span class="status fragile">en cours</span>` : j.etat === "termine" ? `<span class="status stable">terminée</span>` : `<span class="status effondre">erreur</span>`;
    return `<div style="padding:6px 0;border-bottom:1px solid var(--line-2)"><div class="row" style="justify-content:space-between"><b style="flex:1">${esc(j.libelle || "Tâche")}</b><span class="small muted" style="flex:0;white-space:nowrap">lancée à ${when}${j.duree_s != null ? ` · ${j.duree_s} s` : ""}</span> ${st}</div>
      ${j.etat === "en_cours" ? `<div class="small">${esc(j.etape || "")} ${j.total ? `— ${j.fait} / ${j.total}` : ""}</div><div class="bar" style="height:8px;max-width:520px;margin-top:4px"><i style="width:${pct}%;background:var(--accent)"></i></div>` : ""}
      ${j.etat === "erreur" ? `<div class="small delta-neg">${esc(j.erreur || "")}</div>` : ""}</div>`;
  }).join("");
}
async function loadSites(silent) {
  // silent : recharge les données sans effacer la page affichée (ex. compte rendu d'import)
  if (!silent) $("#main").innerHTML = `<div class="loading">Calcul des scores du périmètre…</div>`;
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
  const f = { classement: renderClassement, contre: renderContre, couts: renderCouts, zan: renderZan, jumeau: renderJumeau, portfolio: renderPortfolio, signaux: renderSignaux, comptes: renderComptes, donnees: renderDonnees, parametres: renderParametres }[S.tab];
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
  if (s.signaux && s.signaux.opportunites) b.push(`<span class="chip gold" title="Signal d'opportunité non lu">◆ ${s.signaux.opportunites} signal${s.signaux.opportunites > 1 ? "s" : ""}</span>`);
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
    const [d, docs] = await Promise.all([
      api(`/api/sites/${encodeURIComponent(id)}`, { usage, weights: S.weights, d5: S.d5 }),
      api(`/api/sites/${encodeURIComponent(id)}/documents`).catch(() => null),
    ]);
    S.drawer.data = d;
    S.drawer.docs = docs;
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
      ${docsCard()}
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
    ${signauxCard(d)}

    <div class="card"><h3 style="margin-bottom:10px">Les cinq dimensions — ${esc(S.meta.usages[u])}</h3><div class="dimcards">
    ${DIMS.map((k) => { const val = ur.dims[k]; return `<div class="dimcard"><div class="top-line"><b>${esc(S.meta.dimensions[k])}</b><span class="v" style="color:${colorFor(val, k === "d3")}">${fmt(val)}</span></div>
      <div class="bar"><i style="width:${val}%;background:${colorFor(val, k === "d3")}"></i></div>
      <p>${esc(ur.phrases[k])}</p>
      <ul>${(ur.contribs[k] || []).map((x) => `<li><em class="${x.delta >= 0 ? "delta-pos" : "delta-neg"}">${x.delta > 0 ? "+" : ""}${fmt(x.delta, 1)}</em><span>${esc(x.label)}</span></li>`).join("")}</ul>
      <div class="small muted" style="margin-top:4px">confiance ${fmt(ur.conf[k], 2)}${k === "d3" ? " · plus c'est haut, plus c'est pénalisant" : ""}${k === "d5" && !S.d5 ? " · dimension désactivée" : ""}</div></div>`; }).join("")}
    </div></div>

    ${noteCard(d)}
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
    ${docsCard()}
    ${fieldForm(d)}
    <div class="card"><h3>Fiche de vie de la donnée</h3><p class="small muted" style="margin:4px 0 8px">Source, date et niveau de confiance de chaque indicateur. Les lignes surlignées ont plus de 10 ans.</p>${ficheVie(d.fiche_vie)}</div>
  </div>`;
  bindDrawer();
}
function noteCard(d) {
  const st = S.drawer.docs && S.drawer.docs.statut;
  if (!st) return "";
  const n = d.note_ia;
  const head = `<div class="row" style="justify-content:space-between;align-items:center"><h3 style="flex:1">Note d'analyse (IA)</h3>
    ${st.active ? `<button class="btn sm ${n ? "sec" : ""}" id="note-go" style="flex:0;white-space:nowrap">${n ? "Régénérer" : "Rédiger la note"}</button>` : ""}</div>`;
  if (!st.active) return `<div class="card">${head}<p class="small muted">Activez l'IA (clé ANTHROPIC_API_KEY) pour obtenir une note d'analyste rédigée à partir de ces données.</p></div>`;
  if (!n) return `<div class="card" id="note-card">${head}<p class="small muted">Une note de pré-cadrage (synthèse, points forts, vigilance, prochaine étape) rédigée à partir des seules données de cette fiche, pour l'usage ${esc(S.meta.usages[d.usage])}.</p><div id="note-out"></div></div>`;
  const x = n.note;
  return `<div class="card" id="note-card">${head}
    <div class="small muted">Rédigée par IA le ${esc(n.created_at.replace("T", " "))} pour l'usage ${esc(S.meta.usages[n.usage] || n.usage)} · ${fmt(n.cout_usd, 3)} $ · à relire avant diffusion</div>
    <h4 style="margin-top:8px;text-transform:none;letter-spacing:0">${esc(x.titre)}</h4>
    ${x.synthese.map((p) => `<p style="margin:6px 0;line-height:1.5">${esc(p)}</p>`).join("")}
    <div class="grid2" style="margin-top:6px"><div><h4>Points forts</h4><ul class="small">${x.points_forts.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>
      <div><h4>Points de vigilance</h4><ul class="small">${x.points_vigilance.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div>
    <div class="alert info"><b>Prochaine étape :</b> ${esc(x.prochaine_etape)}</div>
    <p class="small muted"><b>Fiabilité :</b> ${esc(x.fiabilite)}</p>
    ${x.chiffres_a_verifier && x.chiffres_a_verifier.length ? `<div class="alert bad small">Chiffres non retrouvés dans les données de la fiche, à vérifier : ${x.chiffres_a_verifier.map(esc).join(", ")}</div>` : ""}
    <div id="note-out"></div></div>`;
}
function bindNote() {
  const b = $("#note-go"); if (!b) return;
  b.onclick = async () => {
    b.disabled = true; $("#note-out").innerHTML = `<div class="loading small">Rédaction en cours (quelques secondes)…</div>`;
    try { await api(`/api/sites/${encodeURIComponent(S.drawer.id)}/note`, { usage: S.drawer.data.usage, weights: S.weights, d5: S.d5 }); openDrawer(S.drawer.id, S.drawer.data.usage, true); }
    catch (e) { $("#note-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; b.disabled = false; }
  };
}
function docsCard() {
  const D = S.drawer.docs;
  if (!D) return "";
  const st = D.statut;
  const head = `<h3>Documents — analyse par IA</h3>
    <p class="small muted" style="margin:4px 0 10px">Déposez une étude de sol, un acte, un courrier de la DREAL… L'IA <b>propose</b> des valeurs, chacune avec sa page et sa citation ; seules celles que vous cochez deviennent des données terrain et font évoluer le score.</p>`;
  if (!st.active) return `<div class="card" id="docs-card">${head}<div class="alert">Extraction IA non activée : renseignez la clé <code>ANTHROPIC_API_KEY</code> dans le fichier <code>.env</code> puis redémarrez (voir README).</div></div>`;
  const ex = D.extractions || [];
  return `<div class="card" id="docs-card">${head}
    <div class="row" style="flex-wrap:wrap;justify-content:flex-start;align-items:center">
      <input type="file" id="doc-file" accept="application/pdf,.pdf" style="flex:0 1 320px">
      <button class="btn" id="doc-go" style="flex:0;white-space:nowrap">Analyser le document</button></div>
    <label class="toggle" style="margin-top:6px"><input type="checkbox" id="doc-consent"> Je suis autorisé·e à transmettre ce document à l'API Anthropic (Claude) pour analyse. S'il contient des données personnelles (acte de propriété…), leur traitement est couvert par un accord avec le client ou elles ont été masquées.</label>
    <div class="small muted">${st.documents} / ${st.quota_mois} documents analysés ce mois-ci · coût estimé ${fmt(st.cout_usd, 2)} $ · modèle ${esc(st.modele)}</div>
    <div id="doc-out" class="small" style="margin-top:6px"></div>
    ${ex.map(extractionHtml).join("")}</div>`;
}
function extractionHtml(e) {
  const r = e.resultat;
  const headline = `<div class="row" style="justify-content:space-between;align-items:baseline"><b style="flex:1">${esc(e.fichier)}</b>
    <span class="small muted" style="flex:0;white-space:nowrap">${esc(e.created_at.replace("T", " "))}${e.cout_usd != null ? ` · ${fmt(e.cout_usd, 3)} $` : ""}</span></div>`;
  if (e.statut === "en_cours") return `<div class="card" style="background:#fafbf9;margin-top:10px">${headline}<div class="small muted">Analyse en cours…</div></div>`;
  if (e.statut === "erreur") return `<div class="card" style="background:#fafbf9;margin-top:10px">${headline}<div class="alert bad">${esc(e.erreur)}</div></div>`;
  const pdf = (p) => `<a href="/api/documents/${e.id}.pdf#page=${p}" target="_blank" rel="noopener">p. ${p}</a>`;
  const done = !!e.valide_le;
  return `<div class="card" style="background:#fafbf9;margin-top:10px" data-ex="${e.id}">${headline}
    <div class="small"><span class="chip">${esc(r.type_document.replaceAll("_", " "))}</span>${r.date_document ? ` daté du ${esc(r.date_document)}` : ""}${r.auteur_organisme ? ` · ${esc(r.auteur_organisme)}` : ""}</div>
    <p class="small" style="margin:6px 0">${esc(r.resume)}</p>
    ${done ? `<div class="alert info">Valeurs enregistrées le ${esc(e.valide_le.replace("T", " "))}${e.valide_par ? " par " + esc(e.valide_par) : ""}.</div>` : ""}
    ${r.valeurs.length ? `<table class="fv"><thead><tr><th></th><th>Indicateur</th><th>Valeur proposée</th><th>Confiance</th><th>Preuve</th></tr></thead><tbody>
      ${r.valeurs.map((v, i) => `<tr data-i="${i}" style="${v.utilisable ? "" : "opacity:.6"}">
        <td><input type="checkbox" class="v-keep" ${v.utilisable && v.confiance !== "faible" && !done ? "checked" : ""} ${done || !String(v.citation || "").trim() ? "disabled" : ""} title="${v.utilisable ? "" : "Corrigez la valeur avant de cocher"}"></td>
        <td><b>${esc(v.libelle)}</b>${v.probleme ? `<div class="${v.utilisable ? "muted" : "delta-neg"}">${esc(v.probleme)}</div>` : ""}</td>
        <td><input type="text" class="v-val" value="${esc(v.valeur)}" style="width:120px" ${done ? "disabled" : ""}><div class="muted">${esc(v.justification || "")}</div></td>
        <td><select class="v-conf" ${done ? "disabled" : ""}>${["eleve", "moyen", "faible"].map((c) => `<option ${v.confiance === c ? "selected" : ""}>${c}</option>`).join("")}</select></td>
        <td>${pdf(v.page)} — <i>« ${esc(v.citation)} »</i></td></tr>`).join("")}</tbody></table>`
      : `<div class="small muted">Aucune valeur d'indicateur n'a pu être extraite de ce document.</div>`}
    ${r.mesures.length ? `<h4 style="margin-top:10px">Mesures relevées</h4><table class="fv"><thead><tr><th>Substance</th><th>Milieu</th><th>Valeur</th><th>Référence</th><th>Dépassement</th><th>Preuve</th></tr></thead><tbody>
      ${r.mesures.map((m) => `<tr><td>${esc(m.substance)}</td><td>${esc(m.milieu.replaceAll("_", " "))}</td><td class="num">${fmt(m.valeur, 2)} ${esc(m.unite)}</td><td>${esc(m.valeur_reference)}</td>
        <td><span class="chip ${m.depassement === "oui" ? "bad" : m.depassement === "non" ? "ok" : ""}">${esc(m.depassement)}</span></td><td>${pdf(m.page)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${r.points_vigilance.length ? `<h4 style="margin-top:10px">Points de vigilance</h4>${r.points_vigilance.map((p) => `<div class="alert">${esc(p.texte)} <span class="small">(${pdf(p.page)})</span></div>`).join("")}` : ""}
    ${r.limites ? `<p class="small muted" style="margin-top:6px"><b>Limites du document :</b> ${esc(r.limites)}</p>` : ""}
    ${!done && r.valeurs.some((v) => String(v.citation || "").trim()) ? `<div class="row" style="margin-top:8px;justify-content:flex-start"><input type="text" class="v-auteur" placeholder="Validé par (optionnel)" style="max-width:220px">
      <button class="btn v-save" style="flex:0;white-space:nowrap">Enregistrer les valeurs cochées comme données terrain</button></div>` : ""}
  </div>`;
}
function bindDocs() {
  const go = $("#doc-go");
  if (go) go.onclick = async () => {
    const f = $("#doc-file").files[0];
    if (!f) { toast("Choisissez un fichier PDF."); return; }
    if (!$("#doc-consent").checked) { toast("Cochez la case de consentement avant l'envoi."); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("consentement", "true");
    go.disabled = true;
    const out = $("#doc-out");
    try {
      const job = await api(`/api/sites/${encodeURIComponent(S.drawer.id)}/documents`, fd);
      const st = await pollJob(job, out);
      if (st.etat === "erreur") toast("Analyse impossible : " + st.erreur, 6000);
      else toast(`Analyse terminée : ${st.rapport.valeurs} valeur(s) proposée(s), ${fmt(st.rapport.cout_usd, 3)} $.`, 5000);
      openDrawer(S.drawer.id, S.drawer.data.usage, true);
    } catch (e) { out.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; go.disabled = false; }
  };
  document.querySelectorAll("#docs-card [data-ex] .v-save").forEach((b) => (b.onclick = async () => {
    const card = b.closest("[data-ex]");
    const items = [...card.querySelectorAll("tr[data-i]")].map((tr) => ({ index: +tr.dataset.i, retenir: tr.querySelector(".v-keep").checked,
      valeur: tr.querySelector(".v-val").value, confiance: tr.querySelector(".v-conf").value }));
    if (!items.some((i) => i.retenir)) { toast("Aucune valeur cochée."); return; }
    try {
      const r = await api(`/api/sites/${encodeURIComponent(S.drawer.id)}/documents/${card.dataset.ex}/valider`,
        { items, auteur: card.querySelector(".v-auteur").value || null, weights: S.weights, d5: S.d5 });
      toast(`${r.enregistrees.length} valeur(s) enregistrée(s)${r.erreurs.length ? " — " + r.erreurs.join(" ; ") : ""} : score mis à jour.`, 6000);
      await refreshSite(S.drawer.id); openDrawer(S.drawer.id, S.drawer.data.usage);
    } catch (e) { toast("Erreur : " + e.message, 6000); }
  }));
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
  bindDocs();
  bindNote();
  bindSignalRows(() => { const id = S.drawer.id; refreshSite(id); openDrawer(id, S.drawer.usage, true); });
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
  <div class="card"><h3>Poser une question</h3>
    <p class="small muted" style="margin:4px 0 8px">Ex. : « Et si la mairie exigeait une zone humide compensatoire ? », « Quel impact d'une subvention de 500 k€ du Fonds Vert ? », « Que devient le top 5 si les prix baissent de 20 % et que les délais doublent ? ». L'IA traduit la question en hypothèses ; c'est le moteur qui calcule.</p>
    <textarea id="q-text" placeholder="Votre question…" style="min-height:54px">${esc(st.question || "")}</textarea>
    <div class="row" style="margin-top:6px;justify-content:flex-start"><button class="btn" id="q-go" style="flex:0;white-space:nowrap">Interpréter et lancer</button><span class="small muted" id="q-status" style="flex:1"></span></div>
    <div id="q-out">${st.qres ? questionHtml(st.qres) : ""}</div></div>
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
  <div id="mc-out">${st.mc ? mcHtml(st.mc) : ""}</div>
  <div class="card"><h3>Quelle étude lancer en premier ?</h3>
    <p class="small muted" style="margin:4px 0 8px">Pour chaque site proche de la limite du top ${st.top_n} et chaque étude préalable possible (diagnostic de sol, visite du bâti, zones humides, foncier, PLU, PPRi), l'outil simule les résultats plausibles de l'étude et calcule la <b>probabilité qu'elle change la sélection</b> : faire entrer un site dans le top ou l'en faire sortir. Une étude qui ne peut rien changer au classement n'est pas prioritaire pour décider. Les probabilités a priori sont affichées ; les coûts d'études se règlent dans Paramètres.</p>
    <div class="row" style="justify-content:flex-start;align-items:center"><label class="small" style="flex:0;white-space:nowrap">Budget d'études (k€)</label><input type="number" id="et-budget" value="${st.budget ?? 50}" min="0" step="5" style="width:90px;flex:0 0 90px">
      <button class="btn" id="et-go" style="flex:0;white-space:nowrap">Classer les études</button></div>
    <div id="et-out">${st.etudes ? etudesHtml(st.etudes) : ""}</div></div>`;
  $("#et-go").onclick = async () => {
    st.budget = +$("#et-budget").value || 0;
    $("#et-out").innerHTML = `<div class="loading" style="margin-top:8px">Simulation des études…</div>`;
    try { st.etudes = await api("/api/etudes", { ...ctx(), top_n: st.top_n, budget_keur: st.budget }); $("#et-out").innerHTML = etudesHtml(st.etudes); bindEtudes(); }
    catch (e) { $("#et-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
  };
  bindEtudes();
  $("#q-go").onclick = async () => {
    const q = $("#q-text").value.trim(); if (q.length < 5) { toast("Saisissez une question."); return; }
    st.question = q; $("#q-go").disabled = true; $("#q-status").textContent = "Interprétation par l'IA puis calcul par le moteur…";
    try { st.qres = await api("/api/ia/question", { ...ctx(), question: q, perimeter_label: perimeterLabel() }); renderContre(); }
    catch (e) { $("#q-status").textContent = ""; $("#q-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; $("#q-go").disabled = false; }
  };
  const qa = $("#q-apply"); if (qa) qa.onclick = (e) => {
    e.preventDefault();
    const hh = st.qres.interpretation.hypotheses;
    st.hyp = { cout_depollution_mult: 1, cout_demolition_mult: 1, delai_supplementaire_mois: 0, prix_marche_pct: 0, sans_subventions: false, pessimiste: false, usage_force: "", ...hh };
    st.preset = null; st.top_n = st.qres.interpretation.top_n; renderContre(); toast("Hypothèses reprises dans les curseurs.");
  };
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
    try { st.scn = await api("/api/stress", { ...ctx(), hyp, top_n: st.top_n }); $("#scn-out").innerHTML = scnHtml(st.scn); bindRows("#scn-out"); bindContreExports(); }
    catch (e) { $("#scn-out").innerHTML = `<div class="card">Erreur : ${esc(e.message)}</div>`; }
  };
  $("#run-mc").onclick = async () => {
    $("#mc-out").innerHTML = `<div class="card loading">Simulation de ${st.runs} tirages…</div>`;
    const hyp = { ...h }; if (!hyp.usage_force) delete hyp.usage_force;
    try { st.mc = await api("/api/montecarlo", { ...ctx(), hyp, top_n: st.top_n, runs: st.runs }); $("#mc-out").innerHTML = mcHtml(st.mc); bindRows("#mc-out"); bindContreExports(); }
    catch (e) { $("#mc-out").innerHTML = `<div class="card">Erreur : ${esc(e.message)}</div>`; }
  };
  bindRows("#main");
  bindContreExports();
}
function keur(r) { return `${fmt(r[0], r[0] < 10 ? 1 : 0)}–${fmt(r[1], r[1] < 10 ? 1 : 0)} k€`; }
function etudesHtml(r) {
  if (r.message) return `<div class="alert info" style="margin-top:8px">${esc(r.message)}</div>`;
  const utiles = r.etudes.filter((e) => e.p_changement > 0), inutiles = r.etudes.length - utiles.length;
  const row = (e, i) => `<tr><td><a href="#" data-et-site="${esc(e.site)}" data-u="${esc(e.usage)}">${esc(e.nom || e.site)}</a><div class="small muted">${esc(e.commune || "")} · rang ${e.rang} · ${fmt(e.score)}</div></td>
      <td>${esc(e.etude)}<div class="small muted">${esc(e.hypothese)}</div></td><td class="num" style="white-space:nowrap">${keur(e.cout_keur)}</td>
      <td class="num"><b>${Math.round(e.p_changement * 100)} %</b><div class="small muted">${e.dans_top ? "sortie du top" : "entrée dans le top"}</div></td>
      <td class="num">${fmt(e.fourchette_score[0])} à ${fmt(e.fourchette_score[1])}</td>
      <td><button class="btn sm sec" data-et-detail="${i}">Résultats</button></td></tr>`;
  return `${r.selection.length ? `<div class="alert info" style="margin-top:10px"><b>Programme conseillé avec ${fmt(r.budget_keur)} k€</b> (coût moyen estimé ${fmt(r.cout_selection_keur, 1)} k€) :
      <ol style="margin:6px 0 0 18px;padding:0">${r.selection.map((s) => `<li>${esc(s.etude)} — <b>${esc(s.nom)}</b> <span class="small muted">(${keur(s.cout_keur)}, ${Math.round(s.p_changement * 100)} % de chances de faire basculer la sélection)</span></li>`).join("")}</ol>
      <div class="small" style="margin-top:6px">Les études sont choisies par probabilité de bascule rapportée à leur coût. Cumulées, elles peuvent modifier environ <b>${fmt(r.decisions_changees_attendues, 1)}</b> place(s) du top ${r.top_n} : autant de sites mal classés repérés avant d'engager les études lourdes.</div></div>` : ""}
    <p class="small muted" style="margin-top:8px">Seuil du top ${r.top_n} : ${fmt(r.seuil, 1)} points · ${r.sites_examines} site(s) proches du seuil examinés · ${utiles.length} étude(s) pouvant changer la sélection${inutiles ? `, ${inutiles} sans effet sur la sélection` : ""}.</p>
    ${utiles.length ? `<table class="fv"><thead><tr><th>Site</th><th>Étude</th><th>Coût</th><th>Proba. de changer la décision</th><th>Score après étude</th><th></th></tr></thead><tbody>${utiles.slice(0, 15).map((e) => row(e, r.etudes.indexOf(e))).join("")}</tbody></table>${utiles.length > 15 ? `<details><summary class="small">${utiles.length - 15} autre(s) étude(s)</summary><table class="fv"><tbody>${utiles.slice(15).map((e) => row(e, r.etudes.indexOf(e))).join("")}</tbody></table></details>` : ""}` : `<div class="alert info">Aucune étude ne peut changer la sélection : le top ${r.top_n} est robuste aux incertitudes que ces études lèveraient.</div>`}
    <div id="et-detail"></div>`;
}
function bindEtudes() {
  document.querySelectorAll("[data-et-site]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openDrawer(a.dataset.etSite, a.dataset.u); }));
  document.querySelectorAll("[data-et-detail]").forEach((b) => (b.onclick = () => {
    const e = S.stress.etudes.etudes[+b.dataset.etDetail];
    $("#et-detail").innerHTML = `<div class="card" style="margin-top:8px"><h4>${esc(e.etude)} — ${esc(e.nom)}</h4><p class="small muted">Score actuel ${fmt(e.score, 1)} (rang ${e.rang}), seuil du top ${S.stress.etudes.top_n} : ${fmt(S.stress.etudes.seuil, 1)}. Une étude lève aussi l'incertitude de l'indicateur : la pénalité de confiance diminue, ce qui peut suffire à faire entrer un site.</p>
      <table class="fv"><thead><tr><th>Résultat possible</th><th>Probabilité a priori</th><th>Score</th><th>Usage</th><th>Décision</th></tr></thead><tbody>
      ${e.resultats.map((x) => `<tr><td>${esc(x.resultat)}</td><td class="num">${Math.round(x.probabilite * 100)} %</td><td class="num"><b>${fmt(x.score, 1)}</b></td><td>${esc(S.meta.usages[x.usage] || x.usage)}</td><td>${x.change_decision ? `<span class="chip warn">${e.dans_top ? "sort du top" : "entre dans le top"}</span>` : `<span class="chip ok">inchangée</span>`}</td></tr>`).join("")}</tbody></table></div>`;
  }));
}
const HYP_LBL = { cout_depollution_mult: (v) => `dépollution ×${fmt(v, 1)}`, cout_demolition_mult: (v) => `démolition ×${fmt(v, 1)}`,
  delai_supplementaire_mois: (v) => `délais +${fmt(v)} mois`, prix_marche_pct: (v) => `marché ${v > 0 ? "+" : ""}${fmt(v)} %`,
  sans_subventions: () => "aucune subvention", pessimiste: () => "lecture pessimiste des données incertaines",
  usage_force: (v) => `usage imposé : ${S.meta.usages[v] || v}`, subvention_eur: (v) => `subvention de ${eur(v)}`,
  subvention_cible: (v) => (v === "tous" ? "pour tous les sites" : "pour les sites éligibles au Fonds Vert"),
  compensation_zone_humide: (v) => (v === "tous" ? "zone humide compensatoire exigée partout" : "zone humide compensatoire exigée sur les sites concernés") };
function questionHtml(r) {
  const it = r.interpretation;
  const chips = Object.entries(it.hypotheses).map(([k, v]) => `<span class="chip warn">${esc(HYP_LBL[k] ? HYP_LBL[k](v) : `${k} = ${v}`)}</span>`).join(" ");
  return `<div class="alert info" style="margin-top:10px"><b>Interprétation :</b> ${esc(it.interpretation)}<div style="margin-top:6px">${chips || "<i>aucune hypothèse modélisable</i>"}</div>
      ${it.hypotheses_supposees.length ? `<div class="small" style="margin-top:6px"><b>Valeurs supposées :</b> ${it.hypotheses_supposees.map(esc).join(" ; ")}</div>` : ""}
      ${it.non_modelisable.length ? `<div class="small delta-neg" style="margin-top:4px"><b>Non modélisable par l'outil :</b> ${it.non_modelisable.map(esc).join(" ; ")}</div>` : ""}
      <div class="small muted" style="margin-top:4px">Coût de l'interprétation : ${fmt(r.cout_usd, 3)} $${r.scenario ? ` · <a href="#" id="q-apply">reprendre ces hypothèses dans les curseurs</a>` : ""}</div></div>
    ${r.scenario ? scnHtml(r.scenario) : ""}`;
}
function hypText(h) {
  const parts = [];
  if (h.cout_depollution_mult && h.cout_depollution_mult !== 1) parts.push(`dépollution ×${h.cout_depollution_mult}`);
  if (h.cout_demolition_mult && h.cout_demolition_mult !== 1) parts.push(`démolition ×${h.cout_demolition_mult}`);
  if (h.delai_supplementaire_mois) parts.push(`délais +${h.delai_supplementaire_mois} mois`);
  if (h.prix_marche_pct) parts.push(`marché ${h.prix_marche_pct} %`);
  if (h.sans_subventions) parts.push("sans subventions");
  if (h.pessimiste) parts.push("lecture pessimiste");
  if (h.usage_force) parts.push(`usage forcé : ${S.meta.usages[h.usage_force]}`);
  if (h.subvention_eur) parts.push(`subvention ${h.subvention_eur} € (${h.subvention_cible || "eligibles_fonds_vert"})`);
  if (h.compensation_zone_humide && h.compensation_zone_humide !== "non") parts.push(`compensation zone humide : ${h.compensation_zone_humide}`);
  return parts.join(", ") || "aucune";
}
function bindContreExports() {
  const st = S.stress;
  const e1 = $("#exp-scn");
  if (e1 && st.scn) e1.onclick = () => saveCSV("frichescore_contre_analyse_scenario.csv",
    ["site", "commune", "id", "usage_base", "usage_scenario", "rang_base", "rang_scenario", "score_base", "score_scenario", "ecart", "statut", "pourquoi", "hypotheses", "perimetre"],
    st.scn.lignes.map((l) => [l.nom, l.commune, l.id, S.meta.usages[l.usage_base], S.meta.usages[l.usage_scenario], l.rang_base, l.rang_scenario,
      numFR(l.score_base), numFR(l.score_scenario), numFR(l.delta), l.statut, l.pourquoi, hypText(st.scn.hypotheses), perimeterLabel()]));
  const e2 = $("#exp-mc");
  if (e2 && st.mc) e2.onclick = () => saveCSV("frichescore_contre_analyse_montecarlo.csv",
    ["site", "commune", "id", "usage", "rang_base", "score_base", `probabilite_top_${st.mc.top_n}`, "score_p10", "score_p90", "confiance", "verdict", "tirages", "perimetre"],
    st.mc.lignes.map((l) => [l.nom, l.commune, l.id, S.meta.usages[l.usage], l.rang_base, numFR(l.score_base), numFR(l.p_top, 2),
      numFR(l.score_p10), numFR(l.score_p90), numFR(l.confiance, 2), l.robustesse, st.mc.runs, perimeterLabel()]));
}
function bindRows(sel) { document.querySelectorAll(`${sel} tr.click`).forEach((tr) => (tr.onclick = () => openDrawer(tr.dataset.id, tr.dataset.u || null))); }
function statusCls(s) { return { stable: "stable", fragile: "fragile", "sort du top": "sort", "s'effondre": "effondre" }[s] || ""; }
function scnHtml(r) {
  return `<div class="row" style="justify-content:space-between;align-items:center;margin-top:12px"><h3 style="flex:1">Scénario</h3><button class="btn sm sec" id="exp-scn" style="flex:0;white-space:nowrap">Exporter ce tableau en CSV</button></div>
  <div class="synth">${esc(r.synthese)}</div>
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
  return `<div class="row" style="justify-content:space-between;align-items:center;margin-top:12px"><h3 style="flex:1">Simulation de Monte-Carlo (${r.runs} tirages)</h3><button class="btn sm sec" id="exp-mc" style="flex:0;white-space:nowrap">Exporter ce tableau en CSV</button></div>
  <div class="synth">${esc(r.synthese)}</div>
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
    <p>Où se concentrent les sites « piégeux » (risque de surcoût élevé, D2 &lt; ${S.meta.constants.piegeux_d2_max}) et où se trouvent les « pépites » propres (coût faible, friction faible, données fiables) ? Agrégation par territoire, pour l'usage retenu de chaque site.</p></div>
    <div class="seg" id="lvl">${[["departement", "Département"], ["epci", "EPCI"], ["commune", "Commune"]].map(([k, l]) => `<button data-l="${k}" class="${level === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="card loading">Agrégation…</div>`;
  $("#lvl").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.heatLevel = b.dataset.l; renderCouts(); } };
  const key = JSON.stringify(["heat", level, ctx()]);
  const r = S.cache[key] || (S.cache[key] = await api(`/api/heatmap?level=${level}`, ctx()));
  await loadDepts();
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
  <div class="card"><h3>Repérage</h3><p class="small muted" style="margin:4px 0 8px">La carte sert à naviguer, pas à décider : chaque point est une friche, sa couleur est son risque de surcoût, sa taille sa surface. Sélectionnez un département ou un EPCI dans le périmètre pour zoomer. Cliquez sur un point pour ouvrir la fiche.</p>${scatter(r.points)}</div>`;
  bindRows("#main");
  document.querySelectorAll("#main circle[data-id]").forEach((c) => (c.onclick = () => openDrawer(c.dataset.id, c.dataset.u)));
}
let DEPTS = null;
async function loadDepts() {
  if (DEPTS) return DEPTS;
  try { DEPTS = await api("/static/departements.json"); } catch (e) { DEPTS = { departements: [] }; }
  return DEPTS;
}
function scatter(points) {
  const pts = points.filter((p) => p.lat != null && p.lon != null);
  if (!pts.length) return `<div class="empty">Pas de coordonnées.</div>`;
  const W = 900, H = 520, pad = 24;
  let minX = Math.min(...pts.map((p) => p.lon)), maxX = Math.max(...pts.map((p) => p.lon));
  let minY = Math.min(...pts.map((p) => p.lat)), maxY = Math.max(...pts.map((p) => p.lat));
  // marge minimale pour voir les contours autour d'un périmètre resserré
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const spanY = Math.max(0.5, (maxY - minY) * 1.15), spanX = Math.max(0.7, (maxX - minX) * 1.15);
  minX = cx - spanX / 2; maxX = cx + spanX / 2; minY = cy - spanY / 2; maxY = cy + spanY / 2;
  const k = Math.cos((cy * Math.PI) / 180);
  const sx = (maxX - minX) * k, sy = maxY - minY;
  const scale = Math.min((W - 2 * pad) / sx, (H - 2 * pad) / sy);
  const ox = ((W - 2 * pad) - sx * scale) / 2, oy = ((H - 2 * pad) - sy * scale) / 2;
  const X = (lon) => pad + (lon - minX) * k * scale + ox;
  const Y = (lat) => H - pad - (lat - minY) * scale - oy;
  const col = (d2) => (d2 < S.meta.constants.piegeux_d2_max ? "#c8553d" : d2 < 55 ? "#e39b5b" : d2 < 70 ? "#d8c06a" : "#4f9a78");
  const deps = new Set(pts.map((p) => p.departement));
  const base = (DEPTS?.departements || []).map((d) => {
    const path = d.rings.map((r) => "M" + r.map(([lo, la]) => `${X(lo).toFixed(1)},${Y(la).toFixed(1)}`).join("L") + "Z").join("");
    const on = deps.has(d.code);
    let lbl = "";
    if (on) {
      const ring = d.rings.reduce((a, r) => (r.length > a.length ? r : a), []);
      const lx = ring.reduce((a, p) => a + p[0], 0) / ring.length, ly = ring.reduce((a, p) => a + p[1], 0) / ring.length;
      lbl = `<text x="${X(lx).toFixed(0)}" y="${Y(ly).toFixed(0)}" text-anchor="middle" font-size="12" font-weight="650" fill="#6b7682" stroke="#fff" stroke-width="3.5" paint-order="stroke" stroke-linejoin="round" pointer-events="none">${esc(d.nom)} (${esc(d.code)})</text>`;
    }
    return { svg: `<path d="${path}" fill="${on ? "#eef1ec" : "#f6f7f4"}" stroke="${on ? "#b9c2bb" : "#dde1dc"}" stroke-width="${on ? 1.1 : 0.7}"><title>${esc(d.nom)}</title></path>`, lbl };
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="background:#fbfcfa;border-radius:8px">
    <defs><clipPath id="clipmap"><rect x="0" y="0" width="${W}" height="${H}"/></clipPath></defs>
    <g clip-path="url(#clipmap)">${base.map((b) => b.svg).join("")}
    ${pts.sort((a, b) => b.surface_ha - a.surface_ha).map((p) => `<circle data-id="${esc(p.id)}" data-u="${p.usage}" cx="${X(p.lon).toFixed(1)}" cy="${Y(p.lat).toFixed(1)}" r="${(3 + Math.sqrt(p.surface_ha) * 1.6).toFixed(1)}" fill="${col(p.d2)}" fill-opacity=".85" stroke="#fff" stroke-width="1" style="cursor:pointer"><title>${esc(p.nom)} — ${esc(p.commune)}\nRisque de surcoût ${esc(p.risque)} (D2 ${fmt(p.d2)}), ${fmt(p.cout_m2)} €/m²</title></circle>`).join("")}${base.map((b) => b.lbl).join("")}</g>
    <rect x="${W - 268}" y="6" width="262" height="22" rx="5" fill="#fff" fill-opacity=".92"/><g transform="translate(${W - 210},17)" font-size="11" fill="#3c4855">${[["#4f9a78", "faible"], ["#d8c06a", "modéré"], ["#e39b5b", "élevé"], ["#c8553d", "piégeux"]].map(([c, l], i) => `<circle cx="${i * 64 - 50}" cy="0" r="5" fill="${c}"/><text x="${i * 64 - 42}" y="4">${l}</text>`).join("")}</g>
    <text x="8" y="${H - 6}" font-size="9.5" fill="#9aa3ab">Fond : ${esc(DEPTS?.source || "")}</text>
  </svg>`;
}

/* -------------------------------------------------------- horloge ZAN */
async function renderZan() {
  $("#main").innerHTML = `<div class="section-head"><div><h2>Horloge ZAN — fenêtres de financement</h2>
    <p>Échéances réglementaires du ZAN et fenêtres de subvention, croisées avec la maturité estimée de chaque friche (étape du portfolio + délais procéduraux). Les friches dont le dossier peut être prêt avant la clôture d'un dispositif auquel elles sont éligibles sont signalées.</p></div>
    <button class="btn" id="cal-edit">${S.calEdit ? "Fermer l'éditeur" : "Modifier le calendrier"}</button></div>
    <div id="cal-editor"></div><div id="zan-body" class="card loading">Calcul…</div>`;
  $("#cal-edit").onclick = () => { S.calEdit = !S.calEdit; renderZan(); };
  if (S.calEdit) renderCalendarEditor();
  const key = JSON.stringify(["zan", ctx()]);
  const r = S.cache[key] || (S.cache[key] = await api("/api/zan", ctx()));
  if (S.tab !== "zan") return;
  const F = S.zanF || (S.zanF = { types: Object.keys(r.types || {}), verifiedOnly: false });
  const typeOk = (t) => F.types.includes(t || "subvention_etat");
  const wins = r.fenetres.filter((w) => typeOk(w.type) && (!F.verifiedOnly || w.statut_verification === "verifie"));
  const winIds = new Set(wins.map((w) => w.id));
  const corr = r.correspondances.filter((m) => winIds.has(m.fenetre));
  const nUnverified = r.fenetres.filter((w) => w.statut_verification !== "verifie").length;
  $("#zan-body").outerHTML = `
  ${nUnverified ? `<div class="alert"><b>${nUnverified} fenêtre(s) de financement sur ${r.fenetres.length} ne sont pas vérifiées</b> (dates d'exemple). Utilisez « Modifier le calendrier » pour saisir les dates officielles et les marquer comme vérifiées. Le fichier est stocké avec vos données : <code>funding_windows.json</code>.</div>` : `<div class="alert info">Toutes les fenêtres de financement du calendrier sont marquées comme vérifiées.</div>`}
  <div class="card"><div class="row" style="flex-wrap:wrap;justify-content:flex-start;gap:6px;align-items:center">
    <span class="small muted" style="flex:0;white-space:nowrap">Types de financement :</span>
    <div class="seg" id="zan-types" style="flex:0 1 auto">${Object.entries(r.types || {}).map(([k, l]) => `<button data-t="${k}" class="${F.types.includes(k) ? "on" : ""}">${esc(l)} <span class="muted">${r.fenetres.filter((w) => (w.type || "subvention_etat") === k).length}</span></button>`).join("")}</div>
    <label class="toggle" style="flex:0 1 auto;margin-left:12px"><input type="checkbox" id="zan-verif" ${F.verifiedOnly ? "checked" : ""}> Fenêtres vérifiées uniquement</label>
  </div>
  <div class="tl-wrap" style="margin-top:10px">${timeline({ ...r, fenetres: wins })}</div></div>
  <div class="card" style="padding:0"><table><thead><tr><th>Friche</th><th>Usage</th><th>Score</th><th>Dossier prêt vers</th><th>Fenêtres de financement compatibles</th></tr></thead><tbody>
  ${zanGroups(corr).slice(0, 30).map((g) => `<tr class="click zan-row" data-id="${esc(g.id)}" data-u="${g.usage}"><td><div class="site-name">${esc(g.nom)}</div><div class="site-sub">${esc(g.commune)}${g.stage ? " · " + esc(S.meta.stages[g.stage]) : " · hors portfolio"}</div></td>
    <td>${esc(S.meta.usages[g.usage])}</td><td class="num">${fmt(g.score)}</td><td class="num">${esc(g.maturite.date)}<div class="site-sub">~${fmt(g.maturite.mois, 1)} mois</div></td>
    <td>${g.items.map((m) => `<div style="margin-bottom:3px"><span class="status ${m.statut === "coïncide" ? "stable" : "fragile"}">${m.statut === "coïncide" ? "coïncide" : "trop juste"}</span> ${esc(m.fenetre_nom)}${m.fenetre_statut !== "verifie" ? ` <span class="chip warn">à vérifier</span>` : ` <span class="chip ok">vérifiée</span>`} <span class="muted small">— clôture ${esc(m.cloture)}, marge <b class="${m.marge_jours >= 0 ? "delta-pos" : "delta-neg"}">${m.marge_jours >= 0 ? "+" : ""}${m.marge_jours} j</b></span></div>`).join("")}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">Aucune friche du périmètre ne coïncide avec une fenêtre ${F.verifiedOnly ? "vérifiée " : ""}des types sélectionnés. Faites avancer des sites dans le portfolio pour réduire leur délai de maturité.</td></tr>`}
  </tbody></table></div>`;
  $("#zan-types").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const t = b.dataset.t;
    F.types = F.types.includes(t) ? F.types.filter((x) => x !== t) : [...F.types, t];
    renderZan();
  };
  $("#zan-verif").onchange = (e) => { F.verifiedOnly = e.target.checked; renderZan(); };
  bindRows("#main");
}

/* ------------------------------------------------- éditeur du calendrier */
async function renderCalendarEditor(editId) {
  const box = $("#cal-editor"); if (!box) return;
  if (!S.cal || editId === "__reload") S.cal = await api("/api/calendrier");
  const C = S.cal.calendrier;
  const types = C.types || {};
  const edit = S.calForm;
  box.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="flex:1">Calendrier des financements</h3>
    <div style="flex:0;white-space:nowrap"><button class="btn sm" id="cal-add">+ Ajouter un dispositif</button> <button class="btn sm sec" id="cal-reset">Restaurer le calendrier d'exemple</button></div></div>
    <p class="small muted" style="margin:6px 0 10px">Fichier : <code>${esc(S.cal.fichier)}</code>. Une fenêtre « vérifiée » doit avoir une date de vérification et, idéalement, l'adresse de la page officielle.</p>
    <div id="cal-errors"></div>
    ${edit ? calFormHtml(edit, types) : ""}
    <table><thead><tr><th style="position:static">Dispositif</th><th style="position:static">Type</th><th style="position:static">Ouverture</th><th style="position:static">Clôture</th><th style="position:static">Usages</th><th style="position:static">Statut</th><th style="position:static"></th></tr></thead><tbody>
    ${C.fenetres.map((w, i) => `<tr><td><b>${esc(w.nom)}</b><div class="site-sub">${esc(w.financeur || "")}${w.source_url ? ` · <a href="${esc(w.source_url)}" target="_blank" rel="noopener">source</a>` : ""}</div></td>
      <td class="small">${esc(types[w.type] || w.type)}</td><td class="num small">${esc(w.ouverture)}</td><td class="num small">${esc(w.cloture)}</td>
      <td class="small">${(w.usages || []).map((u) => esc(S.meta.usages[u] || u)).join(", ")}</td>
      <td>${w.statut_verification === "verifie" ? `<span class="chip ok">vérifiée${w.date_verification ? " le " + esc(w.date_verification) : ""}</span>` : `<span class="chip warn">à vérifier</span>`}</td>
      <td style="white-space:nowrap"><button class="btn sm sec" data-cal-edit="${i}">Modifier</button> <button class="btn sm sec" data-cal-del="${i}">Supprimer</button></td></tr>`).join("")}
    </tbody></table>
    <h4 style="margin-top:14px">Échéances réglementaires</h4>
    <table><tbody>${(C.echeances_reglementaires || []).map((e) => `<tr><td class="num small" style="width:100px">${esc(e.date)}</td><td class="small"><b>${esc(e.nom)}</b><div class="muted">${esc(e.source || "")}</div></td></tr>`).join("")}</tbody></table>
    <details style="margin-top:10px"><summary>Éditeur JSON avancé (échéances, types, tous les champs)</summary>
      <textarea id="cal-json" style="min-height:320px;font-family:var(--mono);font-size:12px">${esc(JSON.stringify(C, null, 2))}</textarea>
      <button class="btn sm" id="cal-json-save" style="margin-top:6px">Valider et enregistrer le JSON</button></details>
  </div>`;
  const reload = async () => { S.cache = {}; S.calForm = null; renderZan(); };
  const save = async (data) => {
    try { S.cal = await api("/api/calendrier", data, "PUT"); toast("Calendrier enregistré."); await reload(); }
    catch (e) {
      let errs = [e.message];
      try { const d = JSON.parse(e.message); if (d.erreurs) errs = d.erreurs; } catch (x) { /* message brut */ }
      $("#cal-errors").innerHTML = `<div class="alert bad"><b>Le calendrier n'a pas été enregistré :</b><ul style="margin:4px 0 0 16px;padding:0">${errs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
      $("#cal-errors").scrollIntoView({ block: "nearest" });
    }
  };
  $("#cal-add").onclick = () => { S.calForm = { index: -1, w: { id: "", nom: "", financeur: "", type: Object.keys(types)[0], dispositif: "autre", ouverture: "", cloture: "", usages: [], conditions: {}, statut_verification: "a_verifier" } }; renderCalendarEditor(); };
  $("#cal-reset").onclick = async () => { if (!confirm("Remplacer le calendrier par le calendrier d'exemple livré avec FricheScore ? Vos modifications seront perdues.")) return; S.cal = await api("/api/calendrier/reset", {}); await reload(); };
  box.querySelectorAll("[data-cal-edit]").forEach((b) => (b.onclick = () => { const i = +b.dataset.calEdit; S.calForm = { index: i, w: JSON.parse(JSON.stringify(C.fenetres[i])) }; renderCalendarEditor(); box.scrollIntoView({ block: "start" }); }));
  box.querySelectorAll("[data-cal-del]").forEach((b) => (b.onclick = () => {
    const i = +b.dataset.calDel;
    if (!confirm(`Supprimer « ${C.fenetres[i].nom} » ?`)) return;
    const data = JSON.parse(JSON.stringify(C)); data.fenetres.splice(i, 1); save(data);
  }));
  $("#cal-json-save").onclick = () => {
    let data;
    try { data = JSON.parse($("#cal-json").value); } catch (e) { $("#cal-errors").innerHTML = `<div class="alert bad">JSON invalide : ${esc(e.message)}</div>`; return; }
    save(data);
  };
  const fs = $("#cal-form-save");
  if (fs) {
    $("#cal-form-cancel").onclick = () => { S.calForm = null; renderCalendarEditor(); };
    fs.onclick = () => {
      const g = (id) => $("#" + id).value.trim();
      const w = { ...S.calForm.w };
      w.nom = g("cf-nom"); w.financeur = g("cf-fin"); w.type = g("cf-type"); w.dispositif = g("cf-disp");
      w.ouverture = g("cf-ouv"); w.cloture = g("cf-clo"); w.maturite_requise = g("cf-mat") || null;
      w.statut_verification = g("cf-statut"); w.date_verification = g("cf-dv") || null; w.source_url = g("cf-url") || null;
      w.usages = [...document.querySelectorAll("#cal-form input[data-us]:checked")].map((i) => i.dataset.us);
      const cond = {};
      if ($("#cf-bilan").checked) cond.bilan_deficitaire = true;
      if ($("#cf-pollue").checked) cond.pollue = true;
      if (g("cf-surf")) cond.surface_min_ha = parseFloat(g("cf-surf").replace(",", "."));
      w.conditions = cond;
      if (w.statut_verification === "verifie" && !w.date_verification) w.date_verification = new Date().toISOString().slice(0, 10);
      if (!w.id) w.id = (w.nom || "dispositif").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) + "_" + Date.now().toString(36).slice(-4);
      const data = JSON.parse(JSON.stringify(C));
      if (S.calForm.index >= 0) data.fenetres[S.calForm.index] = w; else data.fenetres.push(w);
      save(data);
    };
  }
}
function calFormHtml(f, types) {
  const w = f.w, c = w.conditions || {};
  const disp = (S.cal.dispositifs || []);
  return `<div class="card" id="cal-form" style="background:#fafbf9;margin-bottom:12px"><h3>${f.index >= 0 ? "Modifier" : "Ajouter"} un dispositif</h3>
    <div class="grid3" style="margin-top:8px">
      <div style="grid-column:1 / 3"><label class="small muted">Nom</label><input type="text" id="cf-nom" value="${esc(w.nom)}"></div>
      <div><label class="small muted">Financeur</label><input type="text" id="cf-fin" value="${esc(w.financeur || "")}"></div>
      <div><label class="small muted">Type de financement</label><select id="cf-type">${Object.entries(types).map(([k, l]) => `<option value="${k}" ${w.type === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
      <div><label class="small muted">Dispositif (pour l'éligibilité)</label><select id="cf-disp">${disp.map((d) => `<option ${w.dispositif === d ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></div>
      <div><label class="small muted">Maturité requise du dossier</label><input type="text" id="cf-mat" value="${esc(w.maturite_requise || "")}"></div>
      <div><label class="small muted">Ouverture</label><input type="date" id="cf-ouv" value="${esc(w.ouverture)}"></div>
      <div><label class="small muted">Clôture</label><input type="date" id="cf-clo" value="${esc(w.cloture)}"></div>
      <div><label class="small muted">Surface minimale (ha)</label><input type="number" id="cf-surf" value="${c.surface_min_ha ?? ""}"></div>
      <div style="grid-column:1 / 3"><label class="small muted">Usages éligibles</label><div class="seg">${Object.entries(S.meta.usages).map(([k, l]) => `<label class="toggle" style="margin-right:10px"><input type="checkbox" data-us="${k}" ${(w.usages || []).includes(k) ? "checked" : ""}>${esc(l)}</label>`).join("")}</div></div>
      <div><label class="small muted">Conditions</label><label class="toggle"><input type="checkbox" id="cf-bilan" ${c.bilan_deficitaire ? "checked" : ""}> bilan d'opération déficitaire</label><label class="toggle"><input type="checkbox" id="cf-pollue" ${c.pollue ? "checked" : ""}> site pollué</label></div>
      <div><label class="small muted">Statut</label><select id="cf-statut"><option value="a_verifier" ${w.statut_verification !== "verifie" ? "selected" : ""}>à vérifier</option><option value="verifie" ${w.statut_verification === "verifie" ? "selected" : ""}>vérifié</option></select></div>
      <div><label class="small muted">Date de vérification</label><input type="date" id="cf-dv" value="${esc(w.date_verification || "")}"></div>
      <div><label class="small muted">Page officielle (URL)</label><input type="text" id="cf-url" value="${esc(w.source_url || "")}" placeholder="https://…"></div>
    </div>
    <div style="margin-top:10px"><button class="btn" id="cal-form-save">Enregistrer</button> <button class="btn sec" id="cal-form-cancel">Annuler</button></div></div>`;
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
    ${wins.map((w, i) => { const y = top + 10 + i * rowH; const x0 = Math.max(L, X(w.ouverture)), x1 = X(w.cloture); return `<text x="${L - 8}" y="${y + 12}" font-size="10.5" text-anchor="end" fill="#3c4855">${esc(w.nom.length > 38 ? w.nom.slice(0, 36) + "…" : w.nom)}</text><rect x="${x0}" y="${y + 2}" width="${Math.max(3, x1 - x0)}" height="13" rx="3" fill="${w.statut_verification === "verifie" ? "#1f6f5c" : "#fff"}" fill-opacity="${w.statut_verification === "verifie" ? ".8" : "1"}" stroke="${w.statut_verification === "verifie" ? "none" : "#c4901c"}" stroke-dasharray="3 2"><title>${esc(w.nom)}\n${esc(w.ouverture)} → ${esc(w.cloture)}\n${w.statut_verification === "verifie" ? "Vérifiée" : "À vérifier (date d'exemple)"}</title></rect>`; }).join("")}
    <g transform="translate(${W - 300},${H - 12})" font-size="10.5" fill="#3c4855"><rect width="16" height="9" rx="2" fill="#1f6f5c" fill-opacity=".8"/><text x="20" y="8">vérifiée</text><rect x="80" width="16" height="9" rx="2" fill="#fff" stroke="#c4901c" stroke-dasharray="3 2"/><text x="100" y="8">à vérifier</text></g>
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
  const jia = $("#jia-go");
  if (jia) jia.onclick = async () => {
    jia.disabled = true; $("#jia-out").innerHTML = `<div class="loading small">Rédaction en cours…</div>`;
    const side = (x) => (x.extension ? { usage: x.usage, extension: typeof x.extension === "object" ? x.extension : {} } : { id: x.id, usage: x.usage });
    try { S.jumeauIA = await api("/api/ia/jumeau", { a: side(S.jumeau.a), b: side(S.jumeau.b), weights: S.weights, d5: S.d5 }); renderJumeau(); }
    catch (e) { $("#jia-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; jia.disabled = false; }
  };
  if (!J.a.id && !J.a.extension && ev[0]) { J.a.id = ev[0].id; renderJumeau(); }
}
async function runCompare() {
  const J = S.jumeau;
  const side = (x) => (x.extension ? { usage: x.usage, extension: typeof x.extension === "object" ? x.extension : {} } : { id: x.id, usage: x.usage });
  const out = $("#j-out"); if (out) out.innerHTML = `<div class="card loading">Comparaison…</div>`;
  try {
    S.jumeauRes = await api("/api/compare", { a: side(J.a), b: side(J.b), weights: S.weights, d5: S.d5 });
    S.jumeauIA = null;
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
  const cj = S.jumeauIA;
  const iaBlock = `<div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="flex:1">Conclusion argumentée (IA)</h3>
      <button class="btn sm ${cj ? "sec" : ""}" id="jia-go" style="flex:0;white-space:nowrap">${cj ? "Régénérer" : "Rédiger la conclusion"}</button></div>
    <div id="jia-out">${cj ? `${cj.conclusion.map((l) => `<p style="margin:6px 0">${esc(l)}</p>`).join("")}
      ${cj.nuances.length ? `<h4>Nuances</h4><ul class="small">${cj.nuances.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
      ${cj.conditions_de_bascule.length ? `<h4>Ce qui inverserait la recommandation</h4><ul class="small">${cj.conditions_de_bascule.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
      ${cj.chiffres_a_verifier.length ? `<div class="alert bad small">Chiffres non retrouvés dans les données, à vérifier : ${cj.chiffres_a_verifier.map(esc).join(", ")}</div>` : ""}
      <div class="small muted">Rédigée par IA à partir des deux dossiers FricheScore · ${fmt(cj.cout_usd, 3)} $</div>` : `<p class="small muted">Rédige une conclusion nuancée à partir des deux dossiers : lequel est préférable, les nuances qui comptent et les conditions qui inverseraient la recommandation.</p>`}</div></div>`;
  return `<div class="synth">${r.synthese.map((l) => `<div>${esc(l)}</div>`).join("")}</div>${iaBlock}
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
async function renderDonnees() {
  const m = S.meta;
  $("#main").innerHTML = `<div class="section-head"><div><h2>Données & méthode</h2><p>Sources chargées, import de vos propres données et règles de calcul du FricheScore.</p></div></div>
  <div class="card"><h3>Tâches de fond</h3><p class="small muted" style="margin:4px 0 6px">Imports et mises à jour tournent sur le serveur : vous pouvez quitter la page ou fermer le navigateur, leur avancement s'affiche ici à votre retour.</p><div id="jobs-box">${jobsHtml(S.jobs || [])}</div></div>
  <div class="grid2">
    <div class="card"><h3>Données chargées</h3><table style="margin-top:6px"><tbody>${m.perimeters.origines.map((o) => `<tr><td>${esc(o.origine)}</td><td class="num">${o.n} friches</td><td><button class="btn sm sec" data-delorig="${esc(o.origine)}">Retirer</button></td></tr>`).join("")}</tbody></table>
      ${peut("admin_plateforme") ? `<button class="btn sec sm" id="reset-demo" style="margin-top:10px">Réinitialiser le jeu de démonstration</button>` : ""}
      <p class="hint">La réinitialisation efface aussi le portfolio et les données terrain.</p></div>
    <div class="card"><h3>Importer un fichier CSV</h3>
      <p class="small muted">Séparateur (point-virgule, virgule, tabulation) et encodage (UTF-8 ou Windows/Excel) détectés automatiquement. Les colonnes peuvent porter la clé (<code>surface_m2</code>), le libellé (« Surface du site (m²) ») ou un nom Cartofriches (<code>site_surface</code>). Les cellules invalides sont signalées et ignorées, sans bloquer le reste.</p>
      <div class="row" style="margin:6px 0"><a class="btn sm sec" href="/api/import/modele.csv" style="flex:0;white-space:nowrap;text-decoration:none">Télécharger le modèle CSV</a><a class="btn sm sec" href="/api/import/dictionnaire.csv" style="flex:0;white-space:nowrap;text-decoration:none">Dictionnaire des colonnes</a></div>
      <input type="file" id="imp-file" accept=".csv,.txt,text/csv"><div class="row" style="margin-top:6px"><input type="text" id="imp-src" placeholder="Libellé de la source (ex. Observatoire EPF 2026)"><input type="date" id="imp-date" title="Date des données (par défaut : aujourd'hui)"></div>
      <div class="row" style="margin-top:8px;justify-content:flex-start"><button class="btn sec" id="imp-check" style="flex:0;white-space:nowrap">Vérifier sans importer</button><button class="btn" id="imp-go" style="flex:0">Importer</button></div></div>
  </div>
  <div id="imp-out"></div>
  <div class="card"><h3>Connecteurs Cartofriches · Géorisques · DVF</h3>
    <p class="small muted">Les connecteurs interrogent les API publiques depuis la machine qui exécute FricheScore. Commencez par <b>tester la connexion</b> : l'appel est réel et le résultat brut (ou l'erreur exacte) s'affiche. En cas d'échec, l'outil n'invente aucune donnée.</p>
    <div class="grid3" style="align-items:end">
      <div><label class="small muted">Source</label><select id="ct-src"><option value="cartofriches">Cartofriches (Cerema)</option><option value="georisques">Géorisques (BASIAS, BASOL, SIS, ICPE)</option><option value="dvf">DVF géolocalisé</option><option value="communes">Indicateurs communaux (API Géo, zonage ABC, LOVAC, SITADEL, ENAF)</option><option value="sites">Indicateurs de site (gares SNCF, transports OSM, PVGIS, risques Géorisques)</option><option value="bodacc">Annonces légales BODACC (SIREN à tester)</option></select></div>
      <div><label class="small muted" id="ct-insee-lbl">Code INSEE de test</label><input type="text" id="ct-insee" value="59350"></div>
      <div class="row"><div><label class="small muted">Latitude</label><input type="number" id="ct-lat" value="50.6329" step="any"></div><div><label class="small muted">Longitude</label><input type="number" id="ct-lon" value="3.0586" step="any"></div></div>
      <div style="grid-column:1 / 3"><label class="small muted">Adresse de l'API (modifiable si elle a changé)</label><input type="text" id="ct-url"></div>
      <div class="row" style="align-items:end"><div style="max-width:110px"><label class="small muted">Délai max (s)</label><input type="number" id="ct-timeout" min="5" max="180" value="60"></div><button class="btn" id="ct-go" style="white-space:nowrap">Tester la connexion</button></div>
    </div>
    <div id="ct-out" style="margin-top:10px"></div>
    <h4 style="margin-top:16px">Importer un périmètre</h4>
    <div class="row" style="margin-top:6px"><input type="text" id="cf-codes" placeholder="Codes INSEE séparés par des virgules (ex. 59512, 59599)">
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="cf-geo" checked> Géorisques</label>
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="cf-dvf" checked> DVF</label>
      <label class="toggle" style="flex:0;white-space:nowrap" title="Population, zonage ABC, vacance, permis de construire, consommation d'espaces"><input type="checkbox" id="cf-com" checked> Indicateurs communaux</label>
      <label class="toggle" style="flex:0;white-space:nowrap" title="Gare, transports collectifs, ensoleillement, risques naturels (≈ 1 s par friche)"><input type="checkbox" id="cf-site" checked> Indicateurs de site</label>
      <button class="btn" id="cf-go" style="flex:0;white-space:nowrap">Importer</button></div>
    <div id="cf-out" class="small" style="margin-top:6px"></div>
    <h4 style="margin-top:16px">Indicateurs communaux des friches réelles</h4>
    <p class="small muted" style="margin:4px 0 6px">Population et mairie (API Géo), zonage ABC, vacance des logements (LOVAC), logements autorisés (SITADEL, interrogé commune par commune) et consommation d'espaces (Cerema). Les fichiers nationaux sont téléchargés une fois puis gardés en cache 30 jours. À relancer après un import CSV ou pour rafraîchir les données.</p>
    <div class="row" style="justify-content:flex-start"><button class="btn sec" id="com-go" style="flex:0;white-space:nowrap">Mettre à jour les indicateurs communaux</button>
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="com-refresh"> retélécharger les fichiers nationaux</label></div>
    <div id="com-out" class="small" style="margin-top:6px"></div>
    <h4 style="margin-top:16px">Indicateurs de site des friches réelles</h4>
    <p class="small muted" style="margin:4px 0 6px">Gare la plus proche (SNCF), desserte en transports collectifs (arrêts OpenStreetMap à moins de 400 m, modes lourds à moins de 600 m), ensoleillement (PVGIS), risque d'inondation et aléa argiles (Géorisques). Le rapport de risques à l'adresse est limité à 1 appel par seconde : comptez environ 1 s par friche.</p>
    <div class="row" style="justify-content:flex-start"><button class="btn sec" id="site-go" style="flex:0;white-space:nowrap">Mettre à jour les indicateurs de site</button>
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="site-adr" checked> risques à l'adresse (sinon : risques de la commune, plus rapide)</label></div>
    <div id="site-out" class="small" style="margin-top:6px"></div>
    <p class="small muted" style="margin-top:6px">Arrêts de transport : © contributeurs OpenStreetMap, licence ODbL.</p></div>
  <div class="card" id="maj-card"><h3>Mise à jour automatique et packs de données</h3><p class="small muted">Chargement…</p></div>
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
  if ($("#reset-demo")) $("#reset-demo").onclick = async () => { if (!confirm("Réinitialiser le jeu de démonstration ? Le portfolio et les données terrain seront effacés.")) return; await api("/api/demo/reset", {}); S.meta = await api("/api/meta"); await loadSites(); renderSide(); render(); toast("Jeu de démonstration rechargé."); };
  document.querySelectorAll("[data-delorig]").forEach((b) => (b.onclick = async () => { if (!confirm(`Retirer toutes les friches « ${b.dataset.delorig} » ?`)) return; await api(`/api/donnees/${encodeURIComponent(b.dataset.delorig)}`, undefined, "DELETE"); S.meta = await api("/api/meta"); await loadSites(); renderSide(); render(); }));
  const doImport = async (dry) => {
    const f = $("#imp-file").files[0]; if (!f) { toast("Choisissez un fichier."); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("source", $("#imp-src").value || f.name);
    if ($("#imp-date").value) fd.append("date", $("#imp-date").value);
    fd.append("dry_run", dry ? "true" : "false");
    $("#imp-out").innerHTML = `<div class="card loading">${dry ? "Vérification" : "Import"} en cours…</div>`;
    try {
      const r = await api("/api/import/csv", fd);
      $("#imp-out").innerHTML = importReport(r, f.name);
      const ex = $("#imp-exp"); if (ex) ex.onclick = () => saveCSV("rapport_import.csv", ["ligne", "colonne", "valeur", "niveau", "probleme"], r.problemes.map((p) => [p.ligne, p.colonne, p.valeur, p.niveau, p.message]));
      const go = $("#imp-go2"); if (go) go.onclick = () => doImport(false);
      if (!dry) { S.meta = await api("/api/meta"); await loadSites(true); renderSide(); }
    } catch (e) { $("#imp-out").innerHTML = `<div class="card alert bad">Erreur : ${esc(e.message)}</div>`; }
  };
  $("#imp-check").onclick = () => doImport(true);
  bindCommunes();
  bindSites();
  renderMaj();
  $("#imp-go").onclick = () => doImport(false);
  const urls = await api("/api/connecteurs").catch(() => ({}));
  urls.communes = "API Géo · data.gouv.fr · Cerema (adresses résolues automatiquement)";
  urls.sites = "SNCF open data · Overpass (OpenStreetMap) · PVGIS · Géorisques — point testé : latitude / longitude ci-contre";
  urls.bodacc = "BODACC (DILA, open data) — indiquez un SIREN dans le champ ci-contre";
  const setUrl = () => {
    const bo = $("#ct-src").value === "bodacc";
    $("#ct-url").value = urls[$("#ct-src").value] || "";
    $("#ct-insee-lbl").textContent = bo ? "SIREN à tester (9 chiffres)" : "Code INSEE de test";
    if (bo && $("#ct-insee").value.length === 5) $("#ct-insee").value = "";
    if (!bo && !$("#ct-insee").value) $("#ct-insee").value = "59350";
  };
  $("#ct-src").onchange = setUrl; setUrl();
  $("#ct-go").onclick = async () => {
    $("#ct-out").innerHTML = $("#ct-src").value === "communes"
      ? `<div class="loading">Interrogation des 5 sources… Le premier test télécharge deux fichiers nationaux (zonage ABC, LOVAC, quelques Mo) ; SITADEL est interrogé pour la seule commune testée. Comptez moins d'une minute.</div>`
      : `<div class="loading">Appel en cours (jusqu'à ${+$("#ct-timeout").value || urls.timeout_s || 60} s par tentative, nouvelle tentative en cas de délai dépassé)…</div>`;
    try {
      const r = await api("/api/connecteurs/test", { source: $("#ct-src").value, code_insee: $("#ct-insee").value, lat: +$("#ct-lat").value, lon: +$("#ct-lon").value, base_url: $("#ct-src").value === "bodacc" ? null : $("#ct-url").value || null, timeout: +$("#ct-timeout").value || null, siren: $("#ct-insee").value });
      S.lastConnTest = r;
      $("#ct-out").innerHTML = `<div class="row" style="justify-content:flex-end;gap:6px;margin-bottom:6px"><button class="btn sm" id="ct-copy" style="flex:0;white-space:nowrap">Copier le rapport complet</button><button class="btn sm sec" id="ct-dl" style="flex:0;white-space:nowrap">Télécharger (JSON)</button></div>
        <div class="alert ${r.ok ? "info" : "bad"}"><b>${r.ok ? "✓ Connexion réussie" : "✗ Échec"}</b> — ${esc(r.source)} · ${r.http_status ? "HTTP " + r.http_status + " · " : ""}${r.duree_ms} ms${r.erreur ? `<br>${esc(r.erreur)}` : ""}${r.url ? `<br><span class="small">URL appelée : <code>${esc(r.url)}</code></span>` : ""}</div>
        ${r.diagnostic_reseau ? netDiag(r.diagnostic_reseau) : ""}
        ${r.resultat ? `<h4>Résultat interprété</h4><pre style="background:#f6f7f4;padding:8px;border-radius:6px;overflow:auto;max-height:260px;font-size:11.5px">${esc(JSON.stringify(r.resultat, null, 2))}</pre>` : ""}
        ${r.champs ? champsHtml(r.champs) : ""}
        ${r.detail ? `<details open style="margin-top:8px"><summary>Détail par source</summary>${Object.entries(r.detail).map(([k, d]) => `<div class="alert ${d.ok ? "info" : "bad"}" style="margin:6px 0"><b>${d.ok ? "✓" : "✗"} ${esc(k)}</b>${d.valeur !== undefined && d.valeur !== null ? ` — valeur : <b>${esc(d.valeur)}</b>` : ""}${d.valeur_ha != null ? ` — ${esc(d.valeur_ha)} ha` : ""}${d.valeurs ? ` — ${esc(JSON.stringify(d.valeurs))}` : ""}${d.erreur ? `<br>${esc(d.erreur)}` : ""}${d.ressource ? `<br><span class="small">Fichier : « ${esc(d.ressource.titre || "")} » (${esc(d.ressource.date || "")})</span>` : ""}${d.detection ? `<br><span class="small">Détection : ${esc(JSON.stringify(d.detection))}</span>` : ""}</div>`).join("")}</details>` : ""}
        ${r.apercu_brut ? `<details><summary>Réponse brute (extrait)</summary><pre style="background:#f6f7f4;padding:8px;border-radius:6px;overflow:auto;max-height:260px;font-size:11.5px;white-space:pre-wrap">${esc(r.apercu_brut)}</pre></details>` : ""}`;
      const txt = JSON.stringify(r, null, 1);
      $("#ct-copy").onclick = async () => {
        try { await navigator.clipboard.writeText(txt); toast("Rapport copié : collez-le (Ctrl + V) dans la conversation."); }
        catch (e) { const ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Rapport copié."); }
      };
      $("#ct-dl").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "application/json" })); a.download = `diagnostic_${r.source}.json`; a.click(); };
    } catch (e) { $("#ct-out").innerHTML = `<div class="alert bad">Erreur : ${esc(e.message)}</div>`; }
  };
  $("#cf-go").onclick = async () => {
    const codes = $("#cf-codes").value.split(/[\s,;]+/).filter(Boolean); if (!codes.length) return;
    $("#cf-go").disabled = true;
    const out = $("#cf-out");
    try {
      const job = await api("/api/ingest/cartofriches", { codes_insee: codes, georisques: $("#cf-geo").checked, dvf: $("#cf-dvf").checked, communes: $("#cf-com").checked, sites: $("#cf-site").checked });
      let st = job;
      while (st.etat === "en_cours") {
        const pct = st.total ? Math.round((100 * st.fait) / st.total) : 0;
        out.innerHTML = `<div style="margin:6px 0"><b>${esc(st.etape || "Démarrage…")}</b> ${st.total ? `— ${st.fait} / ${st.total}` : ""}
          <div class="bar" style="height:10px;margin-top:6px;max-width:520px"><i style="width:${pct}%;background:var(--accent)"></i></div>
          <span class="small muted">Vous pouvez changer d'onglet : l'import continue en arrière-plan.</span></div>`;
        await new Promise((res) => setTimeout(res, 1000));
        st = await api(`/api/ingest/jobs/${job.id}`);
        if (!document.body.contains(out)) break;
      }
      if (st.etat === "termine") {
        const r = st.rapport;
        S.meta = await api("/api/meta"); await loadSites(true); renderSide();
        const o = $("#cf-out") || out;
        o.innerHTML = `<div class="alert info"><b>${r.importees} friche(s) importée(s)</b> en ${st.duree_s} s, ${r.communes_dvf} commune(s) enrichie(s) avec DVF.${r.indicateurs_communaux ? " Indicateurs communaux : " + esc(Object.entries(r.indicateurs_communaux).map(([k, n]) => `${catLabel(k)} (${n})`).join(", ")) + "." : ""}${r.indicateurs_sites ? " Indicateurs de site : " + esc(Object.entries(r.indicateurs_sites).map(([k, n]) => `${catLabel(k)} (${n})`).join(", ")) + "." : ""} Elles sont visibles dans l'onglet Classement.</div>${r.erreurs.length ? `<div class="alert bad" style="margin-top:6px">${r.erreurs.slice(0, 30).map(esc).join("<br>")}${r.erreurs.length > 30 ? `<br>… et ${r.erreurs.length - 30} autre(s)` : ""}</div>` : ""}${r.avertissements.length ? `<div class="alert">${r.avertissements.slice(0, 10).map(esc).join("<br>")}</div>` : ""}`;
        toast(`Import terminé : ${r.importees} friche(s).`, 5000);
      } else if (st.etat === "erreur") {
        out.innerHTML = `<div class="alert bad">L'import a échoué : ${esc(st.erreur)}</div>`;
      }
    } catch (e) { out.textContent = "Erreur : " + e.message; }
    finally { const b = $("#cf-go"); if (b) b.disabled = false; }
  };
}
async function pollJob(job, out) {
  watchJobs();
  let st = job;
  while (st.etat === "en_cours") {
    const pct = st.total ? Math.round((100 * st.fait) / st.total) : 0;
    out.innerHTML = `<div style="margin:6px 0"><b>${esc(st.etape || "Démarrage…")}</b> ${st.total ? `— ${st.fait} / ${st.total}` : ""}
      <div class="bar" style="height:10px;margin-top:6px;max-width:520px"><i style="width:${pct}%;background:var(--accent)"></i></div></div>`;
    await new Promise((res) => setTimeout(res, 1000));
    st = await api(`/api/ingest/jobs/${job.id}`);
  }
  return st;
}
function bindSites() {
  const b = $("#site-go"); if (!b) return;
  b.onclick = async () => {
    b.disabled = true;
    const out = $("#site-out");
    try {
      const st = await pollJob(await api("/api/enrichir/sites", { adresse: $("#site-adr").checked }), out);
      if (st.etat === "termine") {
        const r = st.rapport;
        S.meta = await api("/api/meta"); await loadSites(true); renderSide();
        out.innerHTML = `<div class="alert info"><b>${r.sites} friche(s) mise(s) à jour</b> en ${st.duree_s} s. ${Object.entries(r.indicateurs).map(([k, n]) => `${esc(catLabel(k))} : ${n}`).join(" · ") || "Aucun indicateur obtenu."}</div>
          ${r.erreurs.length ? `<details><summary class="small">${r.erreurs.length} message(s)</summary><div class="alert bad">${r.erreurs.slice(0, 40).map(esc).join("<br>")}</div></details>` : ""}`;
      } else out.innerHTML = `<div class="alert bad">Échec : ${esc(st.erreur)}</div>`;
    } catch (e) { out.innerHTML = `<div class="alert bad">Erreur : ${esc(e.message)}</div>`; }
    finally { b.disabled = false; }
  };
}
function bindCommunes() {
  const b = $("#com-go"); if (!b) return;
  b.onclick = async () => {
    b.disabled = true;
    const out = $("#com-out");
    try {
      const st = await pollJob(await api("/api/communes/enrichir", { rafraichir: $("#com-refresh").checked }), out);
      if (st.etat === "termine") {
        const r = st.rapport;
        S.meta = await api("/api/meta"); await loadSites(true); renderSide();
        out.innerHTML = `<div class="alert info"><b>${r.communes} commune(s) mise(s) à jour</b> en ${st.duree_s} s. ${Object.entries(r.indicateurs).map(([k, n]) => `${esc(catLabel(k))} : ${n}`).join(" · ") || "Aucun indicateur obtenu."}</div>
          ${Object.entries(r.fichiers || {}).map(([k, f]) => `<div class="small muted">${esc(k)} : « ${esc(f.titre || "")} » (${esc(f.date || "")}) — ${f.communes_trouvees} commune(s) trouvée(s)</div>`).join("")}
          ${r.erreurs.length ? `<div class="alert bad" style="margin-top:6px">${r.erreurs.slice(0, 20).map(esc).join("<br>")}</div>` : ""}`;
      } else out.innerHTML = `<div class="alert bad">Échec : ${esc(st.erreur)}</div>`;
    } catch (e) { out.innerHTML = `<div class="alert bad">Erreur : ${esc(e.message)}</div>`; }
    finally { b.disabled = false; }
  };
}
const MAJ_ETAT = { en_cours: ["en cours", "warn"], termine: ["terminée", "ok"], termine_avec_erreurs: ["terminée avec erreurs", "warn"], erreur: ["échec", "bad"] };
function fmtScore(v) { return String(Math.round(v)); }
function fmtOctets(n) { return n > 1048576 ? `${(n / 1048576).toFixed(1).replace(".", ",")} Mo` : `${Math.max(1, Math.round(n / 1024))} ko`; }
function majResume(r) {
  if (!r) return "";
  const parts = [];
  if (r.message) parts.push(esc(r.message));
  if (r.mode === "pack") parts.push(`${Object.keys(r.packs || {}).length} pack(s) importé(s)`);
  else if (r.communes !== undefined) parts.push(`${r.communes} commune(s), ${r.importees ?? 0} friche(s) Cartofriches`);
  if (r.nb_nouvelles) parts.push(`<b>${r.nb_nouvelles} nouvelle(s)</b>`);
  if (r.nb_scores_modifies) parts.push(`<b>${r.nb_scores_modifies} score(s) modifié(s)</b>`);
  if (r.nb_absentes) parts.push(`${r.nb_absentes} absente(s) de Cartofriches`);
  if (r.signaux_nouveaux) parts.push(`<b>${r.signaux_nouveaux} signal(aux)</b>`);
  if (r.packs_publies) parts.push(`${r.packs_publies.length} pack(s) publié(s)`);
  if (r.erreurs && r.erreurs.length) parts.push(`${r.erreurs.length} erreur(s)`);
  return parts.join(" · ");
}
function majDetail(r) {
  if (!r) return "";
  const row = (x) => `<tr class="click" data-id="${esc(x.id)}"><td>${esc(x.nom || x.id)}</td><td>${esc(x.commune || "")}</td><td class="num">${x.avant == null ? "—" : fmtScore(x.avant)}</td><td class="num"><b>${(x.apres ?? x.global) == null ? "—" : fmtScore(x.apres ?? x.global)}</b></td><td class="num">${x.ecart == null ? "" : `<span style="color:${x.ecart < 0 ? "var(--bad)" : "var(--good)"}">${x.ecart > 0 ? "+" : ""}${String(x.ecart).replace(".", ",")}</span>`}</td></tr>`;
  return `${(r.scores_modifies || []).length ? `<h4 style="margin-top:8px">Scores modifiés (écart d'au moins 2 points)</h4><table class="fv"><thead><tr><th>Site</th><th>Commune</th><th>Avant</th><th>Après</th><th>Écart</th></tr></thead><tbody>${r.scores_modifies.map(row).join("")}</tbody></table>` : ""}
    ${(r.nouvelles || []).length ? `<h4 style="margin-top:8px">Nouvelles friches</h4><table class="fv"><thead><tr><th>Site</th><th>Commune</th><th></th><th>Score</th><th></th></tr></thead><tbody>${r.nouvelles.map(row).join("")}</tbody></table>` : ""}
    ${(r.absentes_de_cartofriches || []).length ? `<p class="small" style="margin-top:8px"><b>Absentes de Cartofriches</b> (conservées dans la base, à vérifier : requalifiées ou retirées de l'inventaire) : ${r.absentes_de_cartofriches.map((x) => esc(typeof x === "string" ? x : `${x.nom || x.id} (${x.id})`)).join(", ")}</p>` : ""}
    ${(r.erreurs || []).length ? `<div class="alert bad" style="margin-top:6px">${r.erreurs.slice(0, 20).map(esc).join("<br>")}</div>` : ""}`;
}
async function renderMaj() {
  const box = $("#maj-card"); if (!box) return;
  if (!peut("admin_plateforme")) { box.remove(); return; }
  let st;
  try { st = await api("/api/maj"); } catch (e) { box.innerHTML = `<h3>Mise à jour automatique et packs de données</h3><div class="alert bad">${esc(e.message)}</div>`; return; }
  const r = st.reglages;
  box.innerHTML = `<h3>Mise à jour automatique et packs de données</h3>
    <p class="small muted" style="margin:4px 0 8px">Chaque nuit, le serveur remet la base à jour pour que personne n'attende les API publiques. Deux modes : interroger <b>directement les API</b> (Cartofriches, Géorisques, DVF, indicateurs communaux et de site) ou télécharger les <b>packs départementaux</b> préparés par un serveur FricheScore central. Vos saisies terrain, votre portfolio et vos documents ne sont jamais modifiés ni inclus dans un pack.</p>
    <div class="grid3" style="align-items:end">
      <label class="toggle"><input type="checkbox" id="maj-actif" ${r.actif ? "checked" : ""}> Mise à jour automatique chaque nuit</label>
      <div><label class="small muted">Heure (${esc(st.fuseau)})</label><input type="time" id="maj-heure" value="${esc(r.heure)}"></div>
      <div><label class="small muted">Mode</label><select id="maj-mode"><option value="apis" ${r.mode === "apis" ? "selected" : ""}>API publiques (directement)</option><option value="pack" ${r.mode === "pack" ? "selected" : ""}>Packs d'un serveur FricheScore central</option></select></div>
      <div style="grid-column:1 / 3"><label class="small muted">Départements couverts entièrement (toutes leurs communes), séparés par des virgules</label><input type="text" id="maj-deps" value="${esc(r.departements.join(", "))}" placeholder="ex. 59, 62"></div>
      <div id="maj-url-box"><label class="small muted">Adresse du serveur central</label><input type="text" id="maj-url" value="${esc(r.pack_url)}" placeholder="https://frichescore.mon-organisme.fr"></div>
    </div>
    <div class="row" style="margin-top:8px;justify-content:flex-start;flex-wrap:wrap;gap:12px" id="maj-apis-opts">
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="maj-refresh" ${r.rafraichir_fichiers ? "checked" : ""}> retélécharger les fichiers nationaux (LOVAC, zonage, BPE…)</label>
      <label class="toggle" style="flex:0;white-space:nowrap" title="Annonces légales des entreprises propriétaires ou exploitantes (procédures collectives, ventes, radiations)"><input type="checkbox" id="maj-bodacc" ${r.signaux_bodacc !== false ? "checked" : ""}> rechercher les signaux BODACC</label>
      <label class="toggle" style="flex:0;white-space:nowrap" title="Ce serveur devient serveur central : un pack par département est écrit après chaque mise à jour et servi à l'adresse /api/packs/&lt;département&gt;.json.gz"><input type="checkbox" id="maj-pub" ${r.publier_packs ? "checked" : ""}> publier les packs pour d'autres postes</label>
    </div>
    <p class="hint">${r.mode === "apis" ? "Sont mises à jour : les communes portant déjà des friches Cartofriches, toutes les communes des départements listés, et les indicateurs des friches importées par CSV. Comptez plusieurs heures pour un département entier la première nuit." : "Le pack de chaque département listé est téléchargé puis importé en quelques secondes."}</p>
    <div class="row" style="margin-top:8px;justify-content:flex-start"><button class="btn" id="maj-save" style="flex:0;white-space:nowrap">Enregistrer</button>
      <button class="btn sec" id="maj-run" style="flex:0;white-space:nowrap" ${st.en_cours ? "disabled" : ""}>${st.en_cours ? "Mise à jour en cours…" : "Lancer maintenant"}</button>
      <span class="small muted" style="align-self:center">${st.prochaine ? `Prochaine exécution : ${esc(st.prochaine.slice(0, 16).replace("T", " à "))}` : "Mise à jour automatique désactivée"}</span></div>
    <div id="maj-out" class="small" style="margin-top:6px"></div>
    <h4 style="margin-top:14px">Journal des mises à jour</h4>
    ${st.journal.length ? `<table class="fv" style="margin-top:4px"><thead><tr><th>Début</th><th>Déclencheur</th><th>État</th><th>Durée</th><th>Résumé</th><th></th></tr></thead><tbody>
      ${st.journal.map((j, i) => { const [lbl, cls] = MAJ_ETAT[j.etat] || [j.etat, ""]; return `<tr><td>${esc((j.debut || "").replace("T", " "))}</td><td>${esc(j.declencheur)}</td><td><span class="chip ${cls}">${esc(lbl)}</span></td><td class="num">${j.resume && j.resume.duree_s != null ? (j.resume.duree_s < 60 ? "< 1 min" : `${Math.round(j.resume.duree_s / 60)} min`) : ""}</td><td class="small">${majResume(j.resume)}</td><td>${j.resume ? `<button class="btn sm sec" data-majd="${i}">Détail</button>` : ""}</td></tr>`; }).join("")}</tbody></table><div id="maj-detail"></div>` : `<p class="small muted">Aucune mise à jour pour l'instant.</p>`}
    <h4 style="margin-top:14px">Packs de données par département</h4>
    <div class="grid2" style="margin-top:4px">
      <div><p class="small muted" style="margin:0 0 6px">Exporter les friches réelles et les indicateurs communaux d'un ou plusieurs départements dans un fichier unique, pour l'installer sur un autre poste sans rien réinterroger.</p>
        <div class="row"><input type="text" id="pk-deps" placeholder="ex. 59, 62"><button class="btn sec" id="pk-exp" style="flex:0;white-space:nowrap">Exporter le pack</button></div></div>
      <div><p class="small muted" style="margin:0 0 6px">Importer un pack (.json.gz). Une friche de même identifiant venant d'une autre source (votre CSV, par exemple) n'est jamais écrasée.</p>
        <input type="file" id="pk-file" accept=".gz,.json,application/gzip,application/json">
        <div class="row" style="margin-top:6px;justify-content:flex-start"><button class="btn sec" id="pk-check" style="flex:0;white-space:nowrap">Vérifier sans importer</button><button class="btn" id="pk-imp" style="flex:0">Importer</button></div></div>
    </div>
    <div id="pk-out" class="small" style="margin-top:6px"></div>
    ${st.packs_publies.length ? `<p class="small muted" style="margin-top:8px">Packs publiés par ce serveur : ${st.packs_publies.map((p) => `<a href="/api/packs/${esc(p.departement)}.json.gz">${esc(p.departement)}</a> (${fmtOctets(p.octets)}, ${esc(p.date.replace("T", " "))})`).join(" · ")}</p>` : ""}`;
  const modeUi = () => { const pk = $("#maj-mode").value === "pack"; $("#maj-url-box").style.visibility = pk ? "visible" : "hidden"; $("#maj-apis-opts").style.display = pk ? "none" : ""; };
  $("#maj-mode").onchange = modeUi; modeUi();
  const deps = (v) => v.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
  $("#maj-save").onclick = async () => {
    try {
      await api("/api/maj", { actif: $("#maj-actif").checked, heure: $("#maj-heure").value, mode: $("#maj-mode").value, departements: deps($("#maj-deps").value),
        pack_url: $("#maj-url").value, rafraichir_fichiers: $("#maj-refresh").checked, publier_packs: $("#maj-pub").checked, signaux_bodacc: $("#maj-bodacc").checked }, "PUT");
      toast("Réglages de mise à jour enregistrés."); renderMaj();
    } catch (e) { $("#maj-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
  };
  $("#maj-run").onclick = async () => {
    const b = $("#maj-run"); b.disabled = true;
    try {
      const stj = await pollJob(await api("/api/maj/lancer", {}), $("#maj-out"));
      $("#maj-out").innerHTML = stj.etat === "erreur" ? `<div class="alert bad">Échec : ${esc(stj.erreur)}</div>` : `<div class="alert info">Mise à jour terminée en ${stj.duree_s} s. ${majResume(stj.rapport)}</div>`;
      S.meta = await api("/api/meta"); await loadSites(true); renderSide();
      setTimeout(renderMaj, 2500);
    } catch (e) { $("#maj-out").innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; b.disabled = false; }
  };
  document.querySelectorAll("[data-majd]").forEach((b) => (b.onclick = () => { $("#maj-detail").innerHTML = majDetail(st.journal[+b.dataset.majd].resume) || `<p class="small muted">Rien à signaler.</p>`; bindRows("#maj-detail"); }));
  $("#pk-exp").onclick = () => {
    const d = deps($("#pk-deps").value); if (!d.length) { toast("Indiquez au moins un département."); return; }
    window.location.href = `/api/packs/export?departements=${encodeURIComponent(d.join(","))}`;
  };
  const pkImport = async (dry) => {
    const f = $("#pk-file").files[0]; if (!f) { toast("Choisissez un pack."); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("dry_run", dry ? "true" : "false");
    const out = $("#pk-out"); out.innerHTML = "Lecture du pack…";
    try {
      const res = await fetch("/api/packs/import", { method: "POST", body: fd });
      const r = await res.json(); if (!res.ok) throw new Error(r.detail || res.statusText);
      out.innerHTML = `<div class="alert info"><b>${dry ? "Vérification" : "Import"} du pack ${esc((r.departements || []).join(", "))}</b> (créé le ${esc((r.cree_le || "").replace("T", " "))}${r.derniere_mise_a_jour ? `, données mises à jour le ${esc(r.derniere_mise_a_jour.replace("T", " "))}` : ""}) :
        ${r.friches_nouvelles} friche(s) nouvelle(s), ${r.friches_mises_a_jour} mise(s) à jour, ${r.communes} commune(s)${r.friches_ignorees.length ? `, ${r.friches_ignorees.length} ignorée(s)` : ""}.</div>
        ${r.friches_ignorees.length ? `<div class="small muted">${r.friches_ignorees.map(esc).join("<br>")}</div>` : ""}`;
      if (!dry) { S.meta = await api("/api/meta"); await loadSites(true); renderSide(); }
    } catch (e) { out.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
  };
  $("#pk-check").onclick = () => pkImport(true);
  $("#pk-imp").onclick = () => pkImport(false);
}
const SIG_NIV = { opportunite: ["opportunité", "gold"], alerte: ["alerte", "bad"], info: ["info", ""] };
async function refreshSignalBadge() {
  try {
    const v = await api("/api/signaux?non_vus=true");
    S.signauxNonVus = v.non_vus;
    const b = document.querySelector('#tabs button[data-tab="signaux"]');
    if (b) b.innerHTML = `Signaux${v.non_vus ? ` <span class="tab-badge">${v.non_vus}</span>` : ""}`;
  } catch (e) { /* noop */ }
}
function signalRow(x, withSite) {
  const [lbl, cls] = SIG_NIV[x.niveau] || [x.niveau, ""];
  return `<tr class="${x.vu ? "muted" : ""}">
    <td class="small" style="white-space:nowrap">${esc((x.date_evenement || x.detecte_le || "").slice(0, 10))}</td>
    ${withSite ? `<td>${x.supprimee ? esc(x.friche_id) : `<a href="#" data-sig-site="${esc(x.friche_id)}">${esc(x.nom || x.friche_id)}</a>`}<div class="small muted">${esc(x.commune || "")}</div></td>` : ""}
    <td><span class="chip ${cls}">${esc(lbl)}</span></td>
    <td>${x.vu ? "" : "<b>"}${esc(x.titre)}${x.vu ? "" : "</b>"}<div class="small muted">${esc(x.detail || "")}</div></td>
    <td class="small">${esc(x.source || "")}${x.url ? ` · <a href="${esc(x.url)}" target="_blank" rel="noopener">annonce</a>` : ""}</td>
    <td>${x.vu ? `<button class="btn sm sec" data-sig-vu="${x.id}" data-v="0">Non lu</button>` : `<button class="btn sm sec" data-sig-vu="${x.id}" data-v="1">Lu</button>`}</td></tr>`;
}
function signauxCard(d) {
  const l = d.signaux || [];
  if (!l.length) return "";
  return `<div class="card"><h3>Signaux</h3><table class="fv" style="margin-top:6px"><tbody>${l.slice(0, 8).map((x) => signalRow(x, false)).join("")}</tbody></table></div>`;
}
async function renderSignaux() {
  $("#main").innerHTML = `<div class="section-head"><div><h2>Signaux d'opportunité</h2><p>Ce qui rend un site actionnable ou change la donne, détecté à chaque mise à jour : procédure collective, vente ou radiation de l'entreprise propriétaire ou exploitante (BODACC), cessation d'activité d'une installation classée, zonage du PLU modifié, projet apparu, zone humide ou zone rouge nouvellement recensée, friche sortie de Cartofriches.</p></div></div>
    <div class="card loading">Chargement…</div>`;
  const st = S.sig || (S.sig = { niveau: "", nonLus: false });
  let v;
  try { v = await api("/api/signaux"); } catch (e) { $("#main").lastElementChild.outerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
  const rows = v.signaux.filter((x) => (!st.niveau || x.niveau === st.niveau) && (!st.nonLus || !x.vu));
  $("#main").lastElementChild.outerHTML = `<div class="card">
    <div class="row" style="justify-content:flex-start;align-items:center;flex-wrap:wrap;gap:8px">
      <div class="seg" id="sig-niv">${[["", "Tous"], ["opportunite", "Opportunités"], ["alerte", "Alertes"], ["info", "Infos"]].map(([k, l]) => `<button data-n="${k}" class="${st.niveau === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <label class="toggle" style="flex:0;white-space:nowrap"><input type="checkbox" id="sig-nl" ${st.nonLus ? "checked" : ""}> non lus seulement</label>
      <span style="flex:1"></span>
      <button class="btn sm sec" id="sig-all" style="flex:0;white-space:nowrap" ${v.non_vus ? "" : "disabled"}>Tout marquer comme lu</button>
      <button class="btn sm" id="sig-scan" style="flex:0;white-space:nowrap">Interroger le BODACC maintenant</button></div>
    <div id="sig-out" class="small" style="margin-top:6px"></div>
    ${rows.length ? `<table class="fv" style="margin-top:8px"><thead><tr><th>Date</th><th>Site</th><th>Type</th><th>Signal</th><th>Source</th><th></th></tr></thead><tbody>${rows.map((x) => signalRow(x, true)).join("")}</tbody></table>`
      : `<div class="alert info" style="margin-top:8px">${v.signaux.length ? "Aucun signal pour ce filtre." : "Aucun signal pour l'instant. Les signaux apparaissent après une mise à jour (automatique ou manuelle, onglet Données) ou une interrogation du BODACC."}</div>`}
    <p class="hint">Le BODACC est interrogé par SIREN : celui des exploitants d'installations classées présentes sur le site (Géorisques) et celui de la société propriétaire, que vous pouvez renseigner dans la fiche (donnée terrain « SIREN de la société propriétaire ») ou dans votre CSV (colonne <code>siren_proprietaire</code>). Aucun nom n'est repris des annonces.</p></div>`;
  $("#sig-niv").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; st.niveau = b.dataset.n; renderSignaux(); };
  $("#sig-nl").onchange = (e) => { st.nonLus = e.target.checked; renderSignaux(); };
  $("#sig-all").onclick = async () => { await api("/api/signaux/vu", { ids: null, vu: true }); refreshSignalBadge(); await loadSites(true); renderSignaux(); };
  $("#sig-scan").onclick = async () => {
    $("#sig-scan").disabled = true;
    const stj = await pollJob(await api("/api/signaux/rechercher", {}), $("#sig-out"));
    if (stj.etat === "erreur") { $("#sig-out").innerHTML = `<div class="alert bad">${esc(stj.erreur)}</div>`; $("#sig-scan").disabled = false; return; }
    const r = stj.rapport;
    toast(`${r.sirens} SIREN interrogé(s), ${r.nouveaux} nouveau(x) signal(aux).`);
    refreshSignalBadge(); await loadSites(true); await renderSignaux();
    if (r.erreurs.length) $("#sig-out").innerHTML = `<div class="alert bad">${r.erreurs.slice(0, 10).map(esc).join("<br>")}</div>`;
  };
  bindSignalRows(renderSignaux);
}
function bindSignalRows(after) {
  document.querySelectorAll("[data-sig-site]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openDrawer(a.dataset.sigSite); }));
  document.querySelectorAll("[data-sig-vu]").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    await api("/api/signaux/vu", { ids: [+b.dataset.sigVu], vu: b.dataset.v === "1" });
    refreshSignalBadge(); loadSites(true); after();
  }));
}
async function renderComptes() {
  const pf = role() === "admin_plateforme";
  $("#main").innerHTML = `<div class="section-head"><div><h2>${pf ? "Organismes et comptes" : "Comptes de l'organisme"}</h2><p>${pf ? "Chaque organisme client dispose de son propre espace : friches importées, saisies terrain, portfolio, documents, notes, paramètres et calendrier. La base commune (Cartofriches, indicateurs communaux, signaux publics) est partagée et mise à jour la nuit." : "Les comptes de votre organisme partagent ses données. Lecteur : consultation et exports. Analyste : saisies, imports, portfolio, documents et IA. Administrateur : en plus, les comptes, les paramètres et le calendrier."}</p></div></div><div class="card loading">Chargement…</div>`;
  let orgs = [], comptes;
  try {
    if (pf) orgs = (await api("/api/organismes")).organismes;
    comptes = await api("/api/comptes");
  } catch (e) { $("#main").lastElementChild.outerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
  const roles = Object.entries(comptes.roles).filter(([k]) => pf || k !== "admin_plateforme");
  const orgNom = Object.fromEntries(orgs.map((o) => [o.id, o.nom]));
  $("#main").lastElementChild.outerHTML = `
    ${pf ? `<div class="card"><h3>Organismes</h3>
      <table class="fv" style="margin-top:6px"><thead><tr><th>Identifiant</th><th>Nom</th><th>Comptes</th><th>Quota IA / mois</th><th>Créé le</th><th>État</th><th></th></tr></thead><tbody>
      ${orgs.map((o) => `<tr><td><code>${esc(o.id)}</code></td><td>${esc(o.nom)}</td><td class="num">${o.utilisateurs}</td><td class="num"><input type="number" min="0" value="${o.quota_ia ?? ""}" placeholder="défaut" data-quota="${esc(o.id)}" style="width:90px"></td><td class="small">${esc((o.cree_le || "").slice(0, 10))}</td>
        <td>${o.actif ? `<span class="chip ok">actif</span>` : `<span class="chip bad">suspendu</span>`}</td><td><button class="btn sm sec" data-org-actif="${esc(o.id)}" data-v="${o.actif ? 0 : 1}">${o.actif ? "Suspendre" : "Réactiver"}</button></td></tr>`).join("") || `<tr><td colspan="7" class="muted">Aucun organisme.</td></tr>`}</tbody></table>
      <div class="row" style="margin-top:10px;flex-wrap:wrap"><input type="text" id="org-id" placeholder="identifiant (ex. epf-hdf)" style="max-width:200px"><input type="text" id="org-nom" placeholder="Nom (ex. EPF Hauts-de-France)"><input type="number" id="org-quota" placeholder="Quota IA (documents / mois)" style="max-width:220px"><button class="btn" id="org-add" style="flex:0;white-space:nowrap">Créer l'organisme</button></div>
      <div id="org-out" class="small" style="margin-top:6px"></div></div>` : ""}
    <div class="card"><h3>Comptes</h3>
      <table class="fv" style="margin-top:6px"><thead><tr><th>Adresse</th><th>Nom</th>${pf ? "<th>Organisme</th>" : ""}<th>Rôle</th><th>Dernière connexion</th><th>État</th><th></th></tr></thead><tbody>
      ${comptes.comptes.map((u) => `<tr><td>${esc(u.email)}</td><td>${esc(u.nom || "")}</td>${pf ? `<td>${esc(orgNom[u.organisme_id] || (u.organisme_id ? u.organisme_id : "Plateforme"))}</td>` : ""}
        <td><select data-role="${u.id}" ${S.user && u.id === S.user.id ? "disabled" : ""}>${roles.map(([k, l]) => `<option value="${k}" ${u.role === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></td>
        <td class="small">${esc((u.derniere_connexion || "jamais").replace("T", " ").slice(0, 16))}</td>
        <td>${u.actif ? `<span class="chip ok">actif</span>` : `<span class="chip bad">désactivé</span>`}</td>
        <td style="white-space:nowrap">${S.user && u.id === S.user.id ? "" : `<button class="btn sm sec" data-u-actif="${u.id}" data-v="${u.actif ? 0 : 1}">${u.actif ? "Désactiver" : "Réactiver"}</button> <button class="btn sm sec" data-u-pw="${u.id}">Nouveau mot de passe</button>`}</td></tr>`).join("")}</tbody></table>
      <h4 style="margin-top:14px">Ajouter un compte</h4>
      <div class="row" style="margin-top:6px;flex-wrap:wrap"><input type="email" id="cpt-email" placeholder="adresse électronique"><input type="text" id="cpt-nom" placeholder="Nom (facultatif)">
        ${pf ? `<select id="cpt-org">${orgs.map((o) => `<option value="${esc(o.id)}">${esc(o.nom)}</option>`).join("")}</select>` : ""}
        <select id="cpt-role">${roles.map(([k, l]) => `<option value="${k}" ${k === "analyste" ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
        <input type="text" id="cpt-pw" placeholder="Mot de passe provisoire (12 caractères min.)" autocomplete="off">
        <button class="btn sm sec" id="cpt-gen" style="flex:0;white-space:nowrap">Générer</button><button class="btn" id="cpt-add" style="flex:0;white-space:nowrap">Créer le compte</button></div>
      <p class="hint">Transmettez le mot de passe provisoire par un autre canal que l'adresse ; l'utilisateur le change depuis le menu en haut à droite.</p>
      <div id="cpt-out" class="small" style="margin-top:6px"></div></div>`;
  const err = (sel, e) => ($(sel).innerHTML = `<div class="alert bad">${esc(e.message)}</div>`);
  if (pf) {
    $("#org-add").onclick = async () => {
      try { await api("/api/organismes", { id: $("#org-id").value, nom: $("#org-nom").value, quota_ia: $("#org-quota").value ? +$("#org-quota").value : null }); toast("Organisme créé."); renderComptes(); }
      catch (e) { err("#org-out", e); }
    };
    document.querySelectorAll("[data-org-actif]").forEach((b) => (b.onclick = async () => {
      if (b.dataset.v === "0" && !confirm("Suspendre cet organisme ? Ses utilisateurs seront déconnectés.")) return;
      await api(`/api/organismes/${b.dataset.orgActif}`, { actif: b.dataset.v === "1" }, "PATCH"); renderComptes();
    }));
    document.querySelectorAll("[data-quota]").forEach((i) => (i.onchange = async () => { await api(`/api/organismes/${i.dataset.quota}`, { quota_ia: i.value === "" ? null : +i.value }, "PATCH"); toast("Quota enregistré."); }));
  }
  const gen = () => { const a = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; let p = ""; const r = crypto.getRandomValues(new Uint32Array(14)); r.forEach((x) => (p += a[x % a.length])); return p + "7Aa"; };
  $("#cpt-gen").onclick = () => ($("#cpt-pw").value = gen());
  $("#cpt-add").onclick = async () => {
    try {
      await api("/api/comptes", { email: $("#cpt-email").value, nom: $("#cpt-nom").value || null, role: $("#cpt-role").value, mot_de_passe: $("#cpt-pw").value, organisme_id: pf ? $("#cpt-org").value : null });
      toast("Compte créé."); renderComptes();
    } catch (e) { err("#cpt-out", e); }
  };
  document.querySelectorAll("[data-role]").forEach((s_) => (s_.onchange = async () => { try { await api(`/api/comptes/${s_.dataset.role}`, { role: s_.value }, "PATCH"); toast("Rôle modifié."); } catch (e) { toast(e.message); renderComptes(); } }));
  document.querySelectorAll("[data-u-actif]").forEach((b) => (b.onclick = async () => { await api(`/api/comptes/${b.dataset.uActif}`, { actif: b.dataset.v === "1" }, "PATCH"); renderComptes(); }));
  document.querySelectorAll("[data-u-pw]").forEach((b) => (b.onclick = async () => {
    const pw = gen();
    if (!confirm(`Attribuer le mot de passe provisoire suivant ?\n\n${pw}\n\nNotez-le avant de valider : il ne sera plus affiché.`)) return;
    try { await api(`/api/comptes/${b.dataset.uPw}`, { mot_de_passe: pw }, "PATCH"); toast("Mot de passe modifié ; les sessions de ce compte sont fermées."); } catch (e) { toast(e.message); }
  }));
}
function importReport(r, fname) {
  const dry = r.mode === "vérification";
  const n = dry ? r.importables : r.importees;
  const errs = r.problemes.filter((p) => p.niveau === "erreur").length;
  const warns = r.problemes.length - errs;
  return `<div class="card"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="flex:1">${dry ? "Vérification" : "Import"} de « ${esc(fname)} »</h3>
    ${r.problemes.length ? `<button class="btn sm sec" id="imp-exp" style="flex:0;white-space:nowrap">Exporter le rapport</button>` : ""}
    ${dry && n ? `<button class="btn sm" id="imp-go2" style="flex:0;white-space:nowrap;margin-left:6px">Importer maintenant</button>` : ""}</div>
    <div class="kpis" style="margin:10px 0">
      <div class="kpi"><b>${r.lignes_lues}</b><span>lignes lues</span></div>
      <div class="kpi"><b>${n}</b><span>${dry ? "importables" : "importées"}</span></div>
      <div class="kpi"><b>${r.rejetees}</b><span>rejetées</span></div>
      <div class="kpi"><b>${r.evaluables}</b><span>évaluables</span></div>
      <div class="kpi"><b>${errs}</b><span>erreurs</span></div>
      <div class="kpi"><b>${warns}</b><span>avertissements</span></div>
    </div>
    <p class="small muted">Séparateur : ${esc(r.separateur || "?")} · encodage : ${esc(r.encodage)} · ${Object.keys(r.colonnes_reconnues).length} colonne(s) reconnue(s)${r.colonnes_ignorees.length ? ` · ignorées : ${r.colonnes_ignorees.map((c) => `<code>${esc(c)}</code>`).join(", ")}` : ""}</p>
    ${r.erreurs_fichier.map((e) => `<div class="alert bad">${esc(e)}</div>`).join("")}
    ${r.problemes.length ? `<table class="fv" style="margin-top:8px"><thead><tr><th>Ligne</th><th>Colonne</th><th>Valeur</th><th>Niveau</th><th>Problème</th></tr></thead><tbody>
      ${r.problemes.slice(0, 200).map((p) => `<tr><td class="num">${p.ligne}</td><td><code>${esc(p.colonne)}</code></td><td>${esc(p.valeur)}</td><td><span class="chip ${p.niveau === "erreur" ? "bad" : "warn"}">${esc(p.niveau)}</span></td><td>${esc(p.message)}</td></tr>`).join("")}</tbody></table>
      ${r.problemes.length > 200 || r.problemes_tronques ? `<p class="small muted">${r.problemes.length - 200 + r.problemes_tronques > 0 ? (r.problemes.length - Math.min(200, r.problemes.length) + r.problemes_tronques) + " problème(s) supplémentaire(s) dans le rapport exporté." : ""}</p>` : ""}` : `<div class="alert info">Aucun problème détecté.</div>`}
  </div>`;
}

/* ----------------------------------------------------------- paramètres */
async function renderParametres() {
  $("#main").innerHTML = `<div class="loading">Chargement des paramètres…</div>`;
  const r = await api("/api/parametres");
  if (S.tab !== "parametres") return;
  S.params = r;
  const groups = [];
  r.parametres.forEach((p) => { let g = groups.find((x) => x.nom === p.groupe); if (!g) groups.push((g = { nom: p.groupe, items: [] })); g.items.push(p); });
  const open = S.paramOpen || (S.paramOpen = new Set([groups[0]?.nom]));
  const fv = (v) => (Array.isArray(v) ? v : [v]);
  const showV = (v) => fv(v).map((x) => fmt(x, Math.abs(x) < 10 && !Number.isInteger(x) ? 2 : 0)).join(" – ");
  $("#main").innerHTML = `<div class="section-head"><div><h2>Paramètres de calcul</h2>
    <p>Calibrez les coûts unitaires, les signaux de pollution, les pénalités de friction, la confiance et les profils sur vos propres opérations. Chaque modification s'applique immédiatement à tous les scores ; une justification (source, retour d'expérience) peut être notée, et la liste des paramètres modifiés figure en annexe du rapport de pré-cadrage.</p></div>
    <div class="row" style="flex:0;white-space:nowrap;gap:6px"><a class="btn sec" href="/api/parametres/export" style="text-decoration:none">Exporter (JSON)</a>
      <label class="btn sec" style="cursor:pointer">Importer (JSON)<input type="file" id="pa-imp" accept=".json,application/json" hidden></label>
      <button class="btn warn" id="pa-reset" ${r.n_modifies ? "" : "disabled"}>Tout réinitialiser</button></div></div>
  <div class="alert ${r.n_modifies ? "info" : ""}">${r.n_modifies ? `<b>${r.n_modifies} paramètre(s) calibré(s)</b> par rapport aux valeurs par défaut.` : "Tous les paramètres sont à leur valeur par défaut : ce sont des ordres de grandeur génériques, à calibrer avant de présenter des chiffres en comité."}</div>
  <div id="pa-err"></div>
  ${groups.map((g) => { const nmod = g.items.filter((i) => i.modifie).length; return `<div class="card"><details ${open.has(g.nom) ? "open" : ""} data-g="${esc(g.nom)}"><summary>${esc(g.nom)} <span class="muted small">(${g.items.length} paramètres${nmod ? `, <b>${nmod} modifié(s)</b>` : ""})</span></summary>
    ${g.items[0].aide ? `<p class="small muted" style="margin:4px 0 8px">${esc(g.items[0].aide)}</p>` : ""}
    <table class="fv"><thead><tr><th>Paramètre</th>${(g.items[0].composantes || ["valeur"]).map((c) => `<th>${esc(c)}</th>`).join("")}<th>Unité</th><th>Défaut</th><th>Justification</th><th></th></tr></thead><tbody>
    ${g.items.map((p) => `<tr data-path="${esc(p.path)}" style="${p.modifie ? "background:#f1f7f4" : ""}"><td><b>${esc(p.label)}</b>${p.modifie ? ` <span class="chip ok">modifié</span>` : ""}${p.aide && p.aide !== g.items[0].aide ? `<div class="muted">${esc(p.aide)}</div>` : ""}</td>
      ${fv(p.valeur).map((v, i) => `<td><input type="number" step="any" data-i="${i}" value="${v}" style="width:90px"></td>`).join("")}
      <td class="muted">${esc(p.unite || "")}</td><td class="muted num" title="valeur par défaut">${showV(p.defaut)}</td>
      <td><input type="text" data-note placeholder="source, retour d'expérience…" value="${esc(p.note || "")}" style="min-width:180px"></td>
      <td>${p.modifie ? `<button class="btn sm sec" data-reset="${esc(p.path)}" title="Revenir à la valeur par défaut">↺</button>` : ""}</td></tr>`).join("")}
    </tbody></table>
    <button class="btn sm" data-save="${esc(g.nom)}" style="margin-top:8px">Enregistrer ce groupe</button></details></div>`; }).join("")}`;
  document.querySelectorAll("#main details[data-g]").forEach((d) => (d.ontoggle = () => { if (d.open) open.add(d.dataset.g); else open.delete(d.dataset.g); }));
  const after = async (res) => {
    S.meta = await api("/api/meta");
    if (S.weights && S.profile && S.meta.profiles[S.profile]) S.weights = { ...S.meta.profiles[S.profile].weights };
    S.cache = {}; await loadSites(); renderSide();
    await renderParametres();
    if (res && res.erreurs && res.erreurs.length) $("#pa-err").innerHTML = `<div class="alert bad"><b>Valeurs refusées (l'ancienne valeur est conservée) :</b><ul style="margin:4px 0 0 16px;padding:0">${res.erreurs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`;
    else toast("Paramètres enregistrés — scores recalculés.");
  };
  document.querySelectorAll("[data-save]").forEach((b) => (b.onclick = async () => {
    const rows = b.closest("details").querySelectorAll("tr[data-path]");
    const changes = [];
    rows.forEach((tr) => {
      const p = r.parametres.find((x) => x.path === tr.dataset.path);
      const vals = [...tr.querySelectorAll("input[data-i]")].map((i) => i.value.replace(",", "."));
      const value = p.composantes ? vals.map(Number) : Number(vals[0]);
      const note = tr.querySelector("input[data-note]").value.trim() || null;
      const same = JSON.stringify(fv(value)) === JSON.stringify(fv(p.valeur)) && (note || null) === (p.note || null);
      if (!same) changes.push({ path: p.path, value, note });
    });
    if (!changes.length) { toast("Aucune modification dans ce groupe."); return; }
    try { await after(await api("/api/parametres", { changes }, "PUT")); } catch (e) { toast("Erreur : " + e.message, 5000); }
  }));
  document.querySelectorAll("[data-reset]").forEach((b) => (b.onclick = async () => { await api(`/api/parametres/reset?path=${encodeURIComponent(b.dataset.reset)}`, {}); await after(); }));
  $("#pa-reset").onclick = async () => { if (!confirm("Remettre tous les paramètres à leur valeur par défaut ?")) return; await api("/api/parametres/reset", {}); await after(); };
  $("#pa-imp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const data = JSON.parse(await f.text()); await after(await api("/api/parametres/import", data)); }
    catch (err) { toast("Import impossible : " + err.message, 5000); }
  };
}

boot().catch((e) => { $("#main").innerHTML = `<div class="card">Erreur de démarrage : ${esc(e.message)}</div>`; });
