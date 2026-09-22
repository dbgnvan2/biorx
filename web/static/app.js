/* BioRx web client — full feature parity with the desktop GUI.
 *
 * Vanilla JS, no build step: one deployable container.
 * All paper/author data is set via textContent, never innerHTML (XSS guard).
 */
"use strict";

const PAGE_SIZE = 25;
const POLL_MS   = 1500;
// Consecutive failed status checks before a search is given up as lost
// (~12 s at POLL_MS). A single blip must not end a search that is still running.
const POLL_GIVE_UP = 8;

const BIORXIV_CATEGORIES = [
  "(any)","animal behavior and cognition","biochemistry","bioengineering",
  "bioinformatics","biophysics","cancer biology","cell biology","clinical trials",
  "developmental biology","ecology","epidemiology","evolutionary biology",
  "genetics","genomics","immunology","microbiology","molecular biology",
  "neuroscience","paleontology","pathology","pharmacology and toxicology",
  "physiology","plant biology","scientific communication and education",
  "synthetic biology","systems biology","zoology",
];
const PAPER_TYPES = ["(any)","new results","confirmatory results","contradictory results","review article"];
const VERSIONS    = ["(any)","1 (first submission only)","2+ (revised only)"];
const PUBLISHED   = ["(any)","preprints only (not in journal)","published in journal only"];
const LICENSES    = ["(any)","cc_by","cc_by_nc","cc_by_nd","cc_no","pd"];
const SPECIES     = ["(any)","Human studies only","Exclude animal studies","Animal studies only"];

/* A paper's URL comes from an external API. Assigning it to href without
 * checking the scheme would let a "javascript:" URL run in a colleague's
 * browser on click — the same class of problem as innerHTML, through a
 * different door. */
function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.origin);
    return (url.protocol === "https:" || url.protocol === "http:") ? url.href : "";
  } catch (e) {
    return "";
  }
}

/* ── State ───────────────────────────────────────────────────────────────── */

const state = {
  me: null,
  jobId: null,
  polling: null,
  offset: 0,
  total: 0,
  fetched: 0,
  searchRunning: false,       // a search job is in flight (any Run button)
  pollFailures: 0,            // consecutive failed status checks this run
  filterRun: null,            // {id, status} of the last saved-filter run (FR4)
  searchFilters: [],          // saved filters behind the Select Filter dropdown
  results: [],
  checkedPapers: new Set(),   // canonical_ids of checked search results
  summarizing: new Set(),     // paperKey()s with a summary in progress
  activeTab: "search",
  sources: [],                // from /healthz
  // Filters tab
  filters: [],
  activeFilterId: null,
  filterTestJobId: null,
  filterTestPolling: null,
  filterTestFailures: 0,      // consecutive failed status checks of the test
  // References tab
  refLists: [],
  activeListId: null,
  refItems: [],
  refSummaries: [],           // stored summaries for the open list (RL1)
  findByTitleDefault: true,   // server default for title search (/healthz)
  // Discover
  discoverJobId: null,
  discoverPolling: null,
};

const $ = (id) => document.getElementById(id);

/* ── HTTP helper ─────────────────────────────────────────────────────────── */

async function api(method, path, body, opts) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    const error = new Error("Could not reach the server. Check your connection and try again.");
    error.status = 0;
    throw error;
  }
  if (opts && opts.raw) {
    // Downloads read the response themselves, but a signed-out session still
    // goes back to the sign-in page with its reason (csdp review 2026-09-18).
    if (response.status === 401 && state.me && !path.startsWith("/api/session")) {
      let detail = SIGN_IN_MESSAGE;
      try { detail = (await response.clone().json()).detail || detail; } catch (e) {}
      showGate(detail);
    }
    return response;
  }
  let payload = null;
  try { payload = await response.json(); } catch (e) { payload = null; }
  if (!response.ok) {
    const detail = (payload && payload.detail) || `${response.status} ${response.statusText}`;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    // Signed out mid-session (code expired or turned off, PC5): back to the
    // sign-in page with the reason, rather than a stray error banner.
    if (response.status === 401 && state.me && !path.startsWith("/api/session")) {
      showGate(error.message);
    }
    throw error;
  }
  return payload;
}

function notice(message, kind = "error") {
  const el = $("notice");
  if (!message) { el.classList.add("hidden"); return; }
  el.textContent = message;
  el.className = `banner ${kind}`;
}

/* ── Sign in / out ───────────────────────────────────────────────────────── */

/* Sign in: personal access code + PIN
   (docs/implementation_plan_2026-09-18_invite_codes.md#PC13). The code says
   who you are and this device can remember it; the PIN proves it is you. */
const LS_MY_CODE = "biorx_my_code";
const SIGN_IN_MESSAGE = "Sign in to continue.";
let gateCode = "";       // the code the PIN step is for
let gatePinSet = true;   // false: a new code, or a PIN the owner reset
let gatePinMin = 6;      // LOGIN_PIN_MIN_LENGTH, from /healthz

function gateError(message) {
  $("gate-error").textContent = message;
  $("gate-error").classList.toggle("hidden", !message);
}

function rememberedCode() {
  try { return localStorage.getItem(LS_MY_CODE) || ""; } catch (e) { return ""; }
}

function rememberCode(code) {
  try {
    if (code) localStorage.setItem(LS_MY_CODE, code);
    else localStorage.removeItem(LS_MY_CODE);
  } catch (e) { /* private window: the code is simply not remembered */ }
}

function showGateStep(step) {
  $("code-step").classList.toggle("hidden", step !== "code-step");
  $("pin-step").classList.toggle("hidden", step !== "pin-step");
  $("legacy-step").classList.toggle("hidden", step !== "legacy-step");
}

const SS_GATE_MESSAGE = "biorx_gate_message";

async function showGate(message) {
  // Leaving a signed-in page: reload, so nothing of the last person's search,
  // summaries, ticks or running timers carries over to whoever signs in next
  // on this device (csdp review 2026-09-18). The reason survives the reload.
  if (!$("app").classList.contains("hidden")) {
    try { sessionStorage.setItem(SS_GATE_MESSAGE, message || ""); } catch (e) { /* ignore */ }
    location.reload();
    return;
  }
  state.me = null;
  $("app").classList.add("hidden");
  $("gate").classList.remove("hidden");
  gateError(message && message !== SIGN_IN_MESSAGE ? message : "");
  try {
    const health = await api("GET", "/healthz");
    // The old name sign-in only while the shared access code is still set (PC8).
    $("show-legacy").classList.toggle("hidden", !health.access_code_set);
    if (health.pin_min_length) gatePinMin = health.pin_min_length;
  } catch (e) { /* the sign-in form still works */ }
  const code = rememberedCode();
  if (code && !(message && message !== SIGN_IN_MESSAGE)) {
    $("my-code").value = code;
    await lookupCode(code);
  } else {
    $("my-code").value = code;
    showGateStep("code-step");
  }
}

async function lookupCode(code) {
  let who;
  try { who = await api("POST", "/api/session/lookup", { code }); }
  catch (e) { gateError(e.message); showGateStep("code-step"); return false; }
  gateError("");
  gateCode = code;
  gatePinSet = !!who.pin_set;
  $("welcome").textContent = gatePinSet
    ? `Welcome back, ${who.name}.`
    : `Welcome, ${who.name}. Choose a PIN you will remember.`;
  $("my-pin-label").textContent = gatePinSet ? "PIN" : `New PIN (at least ${gatePinMin} characters)`;
  $("my-pin-confirm-wrap").classList.toggle("hidden", gatePinSet);
  $("pin-help").textContent = gatePinSet
    ? "Forgot your PIN? Ask whoever runs this server to reset it." : "";
  $("my-pin").value = "";
  $("my-pin-confirm").value = "";
  showGateStep("pin-step");
  $("my-pin").focus();
  return true;
}

async function codeNext() {
  const code = $("my-code").value.trim();
  if (!code) { gateError("Enter your access code."); return; }
  await lookupCode(code);
}

async function pinSignIn() {
  gateError("");
  const pin = $("my-pin").value;
  if (!gatePinSet && pin !== $("my-pin-confirm").value) {
    gateError("The two PINs are not the same.");
    return;
  }
  try {
    state.me = await api("POST", "/api/session", { code: gateCode, pin });
  } catch (e) { gateError(e.message); return; }
  $("my-pin").value = "";
  $("my-pin-confirm").value = "";
  rememberCode($("remember-code").checked ? gateCode : "");
  showApp();
}

function useOtherCode() {
  rememberCode("");
  gateCode = "";
  $("my-code").value = "";
  gateError("");
  showGateStep("code-step");
  $("my-code").focus();
}

/* The old way: shared access code + name + PIN, for accounts made before
   personal codes (PC8). It no longer creates accounts. */
async function signIn() {
  gateError("");
  try {
    state.me = await api("POST", "/api/session", {
      access_code: $("access-code").value,
      name: $("login-name").value,
      pin: $("login-pin").value,
    });
  } catch (e) { gateError(e.message); return; }
  $("login-pin").value = "";
  showApp();
}

async function recoverAccount() {
  gateError("");
  try {
    state.me = await api("POST", "/api/session/recover", {
      access_code: $("access-code").value,
      name: $("login-name").value,
      recovery_code: $("recovery-code").value,
      new_pin: $("new-pin").value,
    });
  } catch (e) { gateError(e.message); return; }
  $("recovery-code").value = ""; $("new-pin").value = "";
  showRecoverForm(false);
  showApp();
  showRecoveryCode(state.me.recovery_code);
}

function showRecoverForm(on) {
  $("pin-wrap").classList.toggle("hidden", on);
  $("recover-wrap").classList.toggle("hidden", !on);
}

function showRecoveryCode(code) {
  $("recovery-code-text").textContent = code;
  $("recovery-modal").classList.remove("hidden");
}

async function signOut() {
  try { await api("DELETE", "/api/session"); }
  catch (e) { notice(`Could not sign out: ${e.message}`); return; }
  // The next person to sign in on this browser starts a new session and a new
  // window; leaving the old figure on screen would attribute it to them.
  $("token-meter").textContent = "";
  await showGate("");
}

function showApp() {
  $("gate").classList.add("hidden");
  $("app").classList.remove("hidden");
  renderMe();
  loadSources();
  loadSearchFilters();
  restoreActiveTab();
  // On a reload the session, and so the meter's window, carries on from before
  // — the count is not reset by opening the page again (M1.C.2).
  refreshTokenMeter();
}

/* ── Local key storage ───────────────────────────────────────────────────── */

const LS_KEY      = "biorx_local_key";
const LS_PROVIDER = "biorx_local_provider";
const LS_MODEL    = "biorx_local_model";
const LS_TAB      = "biorx_active_tab";
const LS_DEFAULT_SOURCES = "biorx_default_sources";
const LS_FIND_BY_TITLE = "biorx_find_by_title";

/* FT3: look for free copies by title as well as DOI. This browser's choice if
   it made one, else the server's default (sources_config.yaml full_text.
   find_by_title, via /healthz) — gate finding F1: the config was ignored.
   Pure apart from reading storage, for the node-run test. */
function findByTitle() {
  let choice = null;
  try { choice = localStorage.getItem(LS_FIND_BY_TITLE); } catch (e) { choice = null; }
  if (choice === "on") return true;
  if (choice === "off") return false;
  return state.findByTitleDefault !== false;
}

function localSettings() {
  try {
    return {
      key:      localStorage.getItem(LS_KEY)      || "",
      provider: localStorage.getItem(LS_PROVIDER) || "",
      model:    localStorage.getItem(LS_MODEL)    || "",
    };
  } catch (e) { return { key: "", provider: "", model: "" }; }
}

function saveLocalSettings(provider, key, model) {
  try {
    if (key)      localStorage.setItem(LS_KEY, key);
    else          localStorage.removeItem(LS_KEY);
    if (provider) localStorage.setItem(LS_PROVIDER, provider);
    if (model)    localStorage.setItem(LS_MODEL, model);
    else          localStorage.removeItem(LS_MODEL);
    return true;
  } catch (e) { return false; }
}

function clearLocalSettings() {
  try {
    localStorage.removeItem(LS_KEY);
    localStorage.removeItem(LS_PROVIDER);
    localStorage.removeItem(LS_MODEL);
    return true;
  } catch (e) { return false; }
}

/* ── Profile / LLM settings ──────────────────────────────────────────────── */

function renderMe() {
  const me = state.me;
  const local = localSettings();
  const name = me.login_name || me.display_name || "unnamed";
  $("account-state").textContent =
    `Signed in as ${name}. Your access code and PIN bring this account back on any device.`;
  const displayProvider = local.key ? (local.provider || me.provider) : me.provider;
  const displayModel    = local.key ? (local.model    || me.model)    : me.model;
  $("who").textContent = `${name} · ${displayProvider} (${displayModel || "no model"})`;

  const select = $("key-provider");
  if (!select.options.length) {
    for (const provider of me.available_providers) {
      const option = document.createElement("option");
      option.value = provider;
      option.textContent = provider;
      select.appendChild(option);
    }
  }
  select.value = local.provider || me.provider;
  const modelInput = $("preferred-model");
  if (document.activeElement !== modelInput) {
    modelInput.value = local.model || me.preferred_model || "";
  }
  $("model-hint").textContent = me.default_model ? `(default: ${me.default_model})` : "";
  const keyInput = $("api-key");
  if (document.activeElement !== keyInput && local.key) {
    keyInput.placeholder = `Local key saved (…${local.key.slice(-4)}). Enter a new one to replace.`;
  }
  const serverStatus = {
    user:    `Server also has your encrypted key (…${me.key_last4}).`,
    owner:   `Using shared key if local key absent. ${me.owner_summaries_remaining} of ` +
             `${me.owner_summaries_cap} summaries left today.`,
    none:    "Local model (no key needed) — or add your key above.",
    missing: local.key ? "" : "No key available. Enter yours above.",
  }[me.key_source] || "";
  const localStatus = local.key ? `Your key is saved in this browser (…${local.key.slice(-4)}).` : "";
  $("key-state").textContent = localStatus || serverStatus;
  $("byo").classList.remove("hidden");
  const disabled = $("byo-disabled");
  if (!me.byo_enabled && !local.key) {
    disabled.textContent = "Key saved here stays in this browser only (not on the server).";
    disabled.classList.remove("hidden");
  } else {
    disabled.classList.add("hidden");
  }
}

/* Report exactly what was and was not saved. A failed server call must not
   be followed by "Settings saved." — a key the user thinks is gone could
   still be stored and billed (P2). */
async function saveKey() {
  notice("");
  const keyVal      = $("api-key").value.trim();
  const modelVal    = $("preferred-model").value.trim();
  const providerVal = $("key-provider").value;
  const existingLocal = localSettings();
  const keyToStore = keyVal || existingLocal.key;
  let localOk = true;
  if (keyToStore) {
    localOk = saveLocalSettings(providerVal, keyToStore, modelVal);
    if (keyVal && localOk) $("api-key").value = "";
  } else if (modelVal !== existingLocal.model) {
    localOk = saveLocalSettings(providerVal, "", modelVal);
  }
  let serverError = "";
  try {
    if (keyVal && state.me.byo_enabled) {
      state.me = await api("PUT", "/api/me/llm-key", { provider: providerVal, api_key: keyVal, model: modelVal });
    } else if (!keyVal && modelVal !== (state.me.preferred_model || "")) {
      state.me = await api("PUT", "/api/me/llm-model", { model: modelVal });
    } else {
      state.me = await api("GET", "/api/me");
    }
  } catch (e) { serverError = e.message; }
  renderMe();
  const problems = [];
  if (!localOk) problems.push("this browser is blocking storage, so nothing was saved here");
  if (serverError) problems.push(`the server copy was not updated: ${serverError}`);
  if (problems.length) notice(`Settings not fully saved — ${problems.join("; ")}.`);
  else notice("Settings saved.", "ok");
}

async function removeKey() {
  notice("");
  const localOk = clearLocalSettings();
  let serverError = "";
  try { state.me = await api("DELETE", "/api/me/llm-key"); }
  catch (e) { serverError = e.message; }
  try { state.me = await api("GET", "/api/me"); } catch (e) {}
  $("api-key").placeholder = "Stored encrypted; only the last 4 are ever shown";
  renderMe();
  const problems = [];
  if (!localOk) problems.push("the key saved in this browser could not be cleared");
  if (serverError) problems.push(`the key stored on the server was not removed: ${serverError}`);
  if (problems.length) notice(`Key not fully removed — ${problems.join("; ")}.`);
  else notice("Key removed.", "ok");
}

/* M1.C.2: the meter's text. Pure, for the node-run test.

   An uncounted call is a real call whose cost the provider did not report.
   Folding it into the total as zero would make the number read as complete
   when it is not, so it is named separately (P2). */
function tokenMeterText(usage) {
  if (!usage) return "";
  const counted = usage.counted_calls || 0;
  const uncounted = usage.uncounted_calls || 0;
  if (!counted && !uncounted) return "";
  const parts = [];
  if (counted) parts.push(`${compactTokens(usage.total || 0)} tokens`);
  if (uncounted) {
    parts.push(counted ? `+ ${uncounted} uncounted`
                       : `${uncounted} call${uncounted === 1 ? "" : "s"}, cost not reported`);
  }
  return parts.join(" ") + " this session";
}

/* 947 → "947", 18432 → "18.4k", 184320 → "184k", 2300000 → "2.3M".
   One decimal below 100 of a unit, whole numbers above: at 18k the decimal
   still tells you something, at 184k it is noise. Pure, for the node-run test. */
function compactTokens(n) {
  if (n < 1000) return String(n);
  if (n < 1000000) {
    const k = n / 1000;
    return (k < 100 ? k.toFixed(1) : Math.round(k)) + "k";
  }
  const m = n / 1000000;
  return (m < 100 ? m.toFixed(1) : Math.round(m)) + "M";
}

/* Read the meter from the server. Never throws: a meter that cannot be read is
   hidden, and must not interrupt the work the user actually asked for. */
async function refreshTokenMeter() {
  let usage = null;
  try { usage = await api("GET", "/api/usage/session"); }
  catch (e) { return; }
  const el = $("token-meter");
  el.textContent = "";
  const text = tokenMeterText(usage);
  if (!text) return;
  el.appendChild(document.createTextNode(text));
}

async function refreshMe() {
  try { state.me = await api("GET", "/api/me"); renderMe(); } catch (e) {}
}

/* ── Tabs ────────────────────────────────────────────────────────────────── */

const TABS = ["search", "filters", "references", "settings"];

function switchTab(name) {
  state.activeTab = name;
  try { localStorage.setItem(LS_TAB, name); } catch (e) {}
  $("tab-search").classList.toggle("active", "search" === name);
  $("tab-filters").classList.toggle("active", "filters" === name);
  $("tab-references").classList.toggle("active", "references" === name);
  $("tab-settings").classList.toggle("active", "settings" === name);
  $("panel-search").classList.toggle("hidden", "search" !== name);
  $("panel-filters").classList.toggle("hidden", "filters" !== name);
  $("panel-references").classList.toggle("hidden", "references" !== name);
  $("panel-settings").classList.toggle("hidden", "settings" !== name);
  if (name === "filters") loadFilterTab();
  if (name === "references") loadRefTab();
  if (name === "settings") renderDefaultSourcesPicker();
}

function restoreActiveTab() {
  let saved = "search";
  try { saved = localStorage.getItem(LS_TAB) || "search"; } catch (e) {}
  if (!TABS.includes(saved)) saved = "search";
  switchTab(saved);
}

/* "Could not reach" with each source's display name and why (D2). Pure. */
/* Issue 3: an enrichment service that failed leaves results complete but
   missing PDF links or metadata — say so. "" when nothing failed. Pure, for
   the node-run test. */
function enrichProblemsText(job) {
  const problems = job.enrich_problems || {};
  const parts = Object.keys(problems).map(
    (name) => `${name} could not be reached for ${problems[name][0]} of ${problems[name][1]} papers`);
  return parts.length
    ? `${parts.join("; ")} — some PDF links or details may be missing.`
    : "";
}

function failedSourcesText(job) {
  const problems = job.source_problems || {};
  const names = Object.keys(problems);
  if (!names.length) {
    return `Could not reach: ${(job.sources_failed || []).join(", ")}. Results are incomplete.`;
  }
  return names.map(n => `${n} ${problems[n]}.`).join(" ") + " Results are incomplete.";
}

/* ── Sources ─────────────────────────────────────────────────────────────── */

async function loadSources() {
  try {
    const health = await api("GET", "/healthz");
    state.sources = (health.sources || []).filter(s => s.enabled);
    state.findByTitleDefault = health.find_by_title_default !== false;
    $("find-by-title").checked = findByTitle();
    if (health.startup_warnings && health.startup_warnings.length) {
      notice("⚠ " + health.startup_warnings.join(" | "), "warn");
    }
    renderSourcePicker($("search-sources"), state.sources, defaultSourceIds());
    renderSourcePicker($("filter-sources-picker"), state.sources, defaultSourceIds());
    renderDefaultSourcesPicker();
  } catch (e) {}
}

/* The user's default sources: which are pre-ticked in the Search tab and in
   new filters. Per user, in this browser only; never sent to the server.
   Pure, so it can be exercised in node (tests/web/test_frontend_wiring.py). */
function applyDefaultSources(enabledIds, savedRaw, serverDefaultIds) {
  // With nothing saved, start from the server's default_selected (D1), e.g.
  // bioRxiv and arXiv unticked; with no server defaults either, tick all.
  const fallback = () => {
    const server = enabledIds.filter(id => (serverDefaultIds || []).includes(id));
    return server.length ? server : enabledIds.slice();
  };
  let saved;
  try { saved = JSON.parse(savedRaw); } catch (e) { return fallback(); }
  if (!Array.isArray(saved)) return fallback();
  // A saved source the server no longer enables is dropped, not shown.
  const kept = enabledIds.filter(id => saved.includes(id));
  return kept.length ? kept : fallback();
}

function defaultSourceIds() {
  let raw = null;
  try { raw = localStorage.getItem(LS_DEFAULT_SOURCES); } catch (e) {}
  return applyDefaultSources(state.sources.map(s => s.id), raw,
                             state.sources.filter(s => s.default_selected).map(s => s.id));
}

function renderDefaultSourcesPicker() {
  renderSourcePicker($("default-sources"), state.sources, defaultSourceIds());
}

function saveDefaultSources() {
  const { selected } = getSourceSelection($("default-sources"));
  const status = $("default-sources-status");
  if (!selected.length) {
    status.textContent = "Tick at least one source.";
    return;
  }
  try {
    localStorage.setItem(LS_DEFAULT_SOURCES, JSON.stringify(selected));
  } catch (e) {
    status.textContent = "This browser is blocking storage; defaults were not saved.";
    return;
  }
  renderSourcePicker($("search-sources"), state.sources, defaultSourceIds());
  if (state.activeFilterId === null) {
    renderSourcePicker($("filter-sources-picker"), state.sources, defaultSourceIds());
  }
  status.textContent = "Saved.";
}

function renderSourcePicker(container, sources, checkedIds) {
  if (!container) return;
  container.textContent = "";
  for (const s of sources) {
    const label = document.createElement("label");
    label.className = "small source-pick";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !checkedIds || checkedIds.includes(s.id);
    cb.dataset.sourceId = s.id;
    const txt = document.createTextNode(" " + s.label);
    label.appendChild(cb);
    label.appendChild(txt);
    container.appendChild(label);
  }
}

function getSourceSelection(container) {
  if (!container) return { all: true, selected: [] };
  const all = container.querySelectorAll("input[type=checkbox]");
  const selected = Array.from(all).filter(cb => cb.checked).map(cb => cb.dataset.sourceId);
  return { all: selected.length === all.length, selected };
}

/* ── Search tab ──────────────────────────────────────────────────────────── */

function populateCategorySelect(selectId) {
  const sel = $(selectId);
  if (!sel || sel.options.length > 0) return;
  for (const cat of BIORXIV_CATEGORIES) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = cat;
    sel.appendChild(opt);
  }
}

function populateSelect(selectId, options) {
  const sel = $(selectId);
  if (!sel || sel.options.length > 0) return;
  for (const val of options) {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = val;
    sel.appendChild(opt);
  }
}

/* E2: the saved filters are a Select Filter dropdown, not a list that grows
   without end. The selection survives a rebuild of the list (a filter saved on
   the Filters tab) as long as that filter still exists. */
async function loadSearchFilters() {
  populateCategorySelect("search-category");
  const sel = $("search-filter-select");
  const previous = sel.value;
  sel.textContent = "";
  let filters = [];
  try { filters = (await api("GET", "/api/filters")).filters; }
  catch (e) { notice(e.message); return; }

  state.searchFilters = filters;
  if (!filters.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No saved filters yet";
    sel.appendChild(opt);
    sel.value = "";
  } else {
    for (const filter of filters) {
      const opt = document.createElement("option");
      opt.value = String(filter.id);
      opt.textContent = filter.name;
      sel.appendChild(opt);
    }
    const keep = filters.some((f) => String(f.id) === previous);
    sel.value = keep ? previous : String(filters[0].id);
  }
  renderFilterRunButtons();
}

/* Run the filter picked in the dropdown. */
function runSelectedFilter() {
  const id = $("search-filter-select").value;
  if (!id) return;
  const filter = (state.searchFilters || []).find((f) => String(f.id) === String(id));
  state.searchLabel = filter ? filter.name : "";
  return startSearch({ filter_id: Number(id) });
}

/* FR4: the label a saved filter's Run button shows. "Done" only for a run that
   finished; a stopped or failed run says so rather than claiming success.
   Pure, for the node-run test. */
function filterRunLabel(filterId, run) {
  if (!run || String(run.id) !== String(filterId)) return "Run";
  return { running: "Running…", done: "Done", cancelled: "Stopped", error: "Failed",
           lost: "Lost track" }[run.status]
    || "Run";
}

/* Apply state to the Run button beside the Select Filter dropdown. Called on
   each state change, after the list is rebuilt, and when the selection changes,
   so the label always describes the filter now picked (FR4.3). */
function renderFilterRunButtons() {
  const sel = $("search-filter-select");
  const btn = $("btn-run-filter");
  btn.textContent = filterRunLabel(sel.value, state.filterRun);
  btn.disabled = state.searchRunning || !sel.value;
  sel.disabled = state.searchRunning;
}

/* One failed status check is not a failed job: it is still running on the
   server (P1). Give up only on a definite answer (signed out, gone, expired)
   or after POLL_GIVE_UP failures in a row. Shared by the search and the
   Filters-tab test. Pure, for the node-run test. */
function shouldStopPolling(error, failuresInARow) {
  return [401, 404, 410].includes(error.status) || failuresInARow >= POLL_GIVE_UP;
}

/* FR3: the live line under the progress bar — found, matched, and enriched
   once enrichment has started, then the current step. Pure, for the node-run
   test. */
function progressText(job) {
  const parts = [`Found ${job.fetched || 0}`, `Matched ${job.matched || 0}`];
  if (job.enrich_total > 0) parts.push(`Enriched ${job.enriched || 0}/${job.enrich_total}`);
  const step = job.phase || job.status || "";
  return parts.join(" · ") + (step ? ` — ${step}` : "");
}

function manualFilter() {
  const useRange = $("search-date-range-toggle").checked;
  const f = {
    text_groups: [{ title: "", abstract: "", both: $("q-both").value }],
    authors: [],
  };
  const cat = $("search-category").value;
  if (cat && cat !== "(any)") f.category = cat;
  if (useRange) {
    f.start_date = $("search-start-date").value;
    f.end_date   = $("search-end-date").value;
  } else {
    f.days_back = Number($("search-days").value) || 14;
  }
  return f;
}

async function startSearch(payload) {
  notice("");
  stopPolling();
  state.offset = 0;
  state.results = [];
  state.fetched = 0;
  state.checkedPapers.clear();
  state.searchSummaries = [];
  renderSummariesPanel();
  // A1: a new search starts with nothing ticked, header box included.
  $("select-all-results").checked = false;
  $("select-all-results").indeterminate = false;
  $("results-card").classList.add("hidden");
  $("sources-failed").classList.add("hidden");
  $("enrich-problems").classList.add("hidden");
  $("progress-wrap").classList.remove("hidden");
  $("progress").value = 0;
  $("phase").textContent = "Starting…";
  // Every Run button is disabled before the request leaves (FR4.2).
  state.searchRunning = true;
  state.pollFailures = 0;
  state.filterRun = payload.filter_id != null ? { id: payload.filter_id, status: "running" } : null;
  renderFilterRunButtons();
  $("run-search").disabled = true;
  $("cancel-search").disabled = false;
  $("btn-save-as-list").disabled = true;

  // The Search panel's source boxes belong to the manual search. A saved
  // filter runs on the sources saved with it, so none are sent and the server
  // uses the filter's own (issue 1, 2026-09-18).
  const body = Object.assign(
    { max_results: Number($("q-max").value) || 200 },
    payload.filter ? { source_selection: getSourceSelection($("search-sources")) } : {},
    payload,
  );
  try {
    const job = await api("POST", "/api/searches", body);
    state.jobId = job.job_id;
    state.polling = setInterval(pollSearch, POLL_MS);
    pollSearch();
  } catch (e) {
    // Refused before it started (e.g. an empty filter): nothing ran, so the
    // button goes back to Run rather than claiming Failed.
    notice(e.message);
    state.filterRun = null;
    searchFinished();
  }
}

async function pollSearch() {
  if (!state.jobId) return;
  let job;
  try { job = await api("GET", `/api/searches/${state.jobId}`); }
  catch (e) {
    if (!state.polling) return;
    // Say "lost track", not "failed", when giving up: the search itself may
    // well have finished (shouldStopPolling).
    state.pollFailures += 1;
    if (!shouldStopPolling(e, state.pollFailures)) {
      $("phase").textContent =
        `Lost contact with the server — retrying (${state.pollFailures}/${POLL_GIVE_UP})…`;
      return;
    }
    notice(e.message);
    searchFinished("lost");
    return;
  }
  state.pollFailures = 0;

  $("phase").textContent = progressText(job);
  if (job.total > 0) {
    $("progress").max = job.total;
    $("progress").value = job.fetched;
  }
  if (job.sources_failed && job.sources_failed.length) {
    $("sources-failed").textContent = failedSourcesText(job);
    $("sources-failed").classList.remove("hidden");
  }
  const enrichNote = enrichProblemsText(job);
  if (enrichNote) {
    $("enrich-problems").textContent = enrichNote;
    $("enrich-problems").classList.remove("hidden");
  }
  if (["done", "error", "cancelled"].includes(job.status)) {
    // An earlier poll still in flight when this one finished the job.
    if (!state.polling) return;
    if (job.status === "error") notice(job.error || "The search failed.");
    state.fetched = job.fetched;
    // Stop first: the interval must not re-enter while results load, and a
    // failed load must still release the Run buttons.
    stopPolling();
    try { await loadResults(); }
    catch (e) { notice(`Could not load the results: ${e.message}`); }
    searchFinished(job.status);
  }
}

function searchFinished(status) {
  stopPolling();
  state.searchRunning = false;
  if (state.filterRun) state.filterRun.status = status || "error";
  renderFilterRunButtons();
  $("run-search").disabled = false;
  $("cancel-search").disabled = true;
  $("progress-wrap").classList.add("hidden");
  if (status === "cancelled") notice("Search stopped.", "warn");
}

function stopPolling() {
  if (state.polling) { clearInterval(state.polling); state.polling = null; }
}

async function cancelSearch() {
  if (!state.jobId) return;
  try { await api("DELETE", `/api/searches/${state.jobId}`); }
  catch (e) { notice(e.message); }
}

async function loadResults() {
  if (!state.jobId) return;
  const page = await api(
    "GET",
    `/api/searches/${state.jobId}/results?offset=${state.offset}&limit=${PAGE_SIZE}`
  );
  state.total = page.total;
  state.results = page.results;
  renderResults();
  if (page.status === "done") refreshSearchSummaries();
}

function renderResults() {
  const body = $("results-body");
  body.textContent = "";
  $("results-card").classList.remove("hidden");
  $("results-heading").textContent = `Results — ${state.total} matching`;

  const empty = $("empty-note");
  if (state.total === 0 && state.fetched > 0) {
    empty.textContent =
      `No papers matched your filter. ${state.fetched} were fetched and checked — ` +
      `try broader words or a longer date range.`;
    empty.classList.remove("hidden");
  } else if (state.total === 0) {
    empty.textContent = "The sources returned nothing for this search.";
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
  }

  for (const paper of state.results) {
    const tr = document.createElement("tr");

    const checkTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.checkedPapers.has(paper.canonical_id);
    cb.addEventListener("change", () => {
      if (cb.checked) state.checkedPapers.add(paper.canonical_id);
      else state.checkedPapers.delete(paper.canonical_id);
      updateSaveAsListBtn();
    });
    checkTd.appendChild(cb);

    const title = document.createElement("td");
    const href = safeUrl(paper.url || paper.source_url);
    let link;
    if (href) {
      link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    } else {
      link = document.createElement("span");
    }
    link.textContent = paper.title || "(untitled)";
    link.style.cursor = "pointer";
    link.addEventListener("click", (e) => {
      if (href) return;   // let the <a> navigate normally
      e.preventDefault();
      openModal(paper);
    });
    const detailBtn = document.createElement("span");
    detailBtn.className = "tag small";
    detailBtn.textContent = "detail";
    detailBtn.style.cursor = "pointer";
    detailBtn.style.marginLeft = "4px";
    detailBtn.addEventListener("click", () => openModal(paper));
    title.appendChild(link);
    title.appendChild(detailBtn);
    if (hasSummary(paper)) {
      const badge = document.createElement("span");
      badge.className = "tag small has-summary";
      badge.textContent = summaryBadgeText(summaryFor(paper));
      badge.style.marginLeft = "4px";
      badge.addEventListener("click", () => openModal(paper));
      title.appendChild(badge);
    }

    const authors = document.createElement("td");
    authors.className = "small";
    authors.textContent = (paper.authors || "").split(";").slice(0, 3).join("; ");

    const date = document.createElement("td");
    date.className = "small";
    date.textContent = paper.date || paper.pub_date || "";

    const source = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = paper.journal_or_server || paper.source || "";
    source.appendChild(tag);

    const actions = document.createElement("td");
    const pdfHref = safeUrl(paper.pdf_url);
    if (pdfHref) {
      const pdf = document.createElement("a");
      pdf.href = pdfHref;
      pdf.target = "_blank";
      pdf.rel = "noopener noreferrer";
      pdf.textContent = "PDF";
      pdf.className = "tag";
      actions.appendChild(pdf);
      actions.append(" ");
    }
    const summarize = document.createElement("button");
    // A redraw while a summary runs must not offer the button again: a second
    // click would bill the model twice (csdp review 2026-09-18).
    const busy = state.summarizing.has(paperKey(paper));
    summarize.textContent = busy ? "Summarizing…" : "Summarize";
    summarize.disabled = busy;
    summarize.dataset.canonicalId = paper.canonical_id || "";
    summarize.dataset.paperKey = paperKey(paper);
    summarize.addEventListener("click", () => startSummary(paper, summarize));
    actions.appendChild(summarize);

    tr.append(checkTd, title, authors, date, source, actions);
    body.appendChild(tr);
  }

  const from = state.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + PAGE_SIZE, state.total);
  $("page-label").textContent = `${from}–${to} of ${state.total}`;
  updateSaveAsListBtn();
  $("prev-page").disabled = state.offset === 0;
  $("next-page").disabled = state.offset + PAGE_SIZE >= state.total;
}

/* "Save selected (N)" when papers are ticked, else "Save all N results";
   enabled whenever there are results (UI4). Pure, for the node-run test. */
function saveButtonLabel(checked, total) {
  if (!total) return { text: "Save to Saved References", disabled: true };
  if (checked) return { text: `Save selected (${checked}) to Saved References`, disabled: false };
  return { text: `Save all ${total} results to Saved References`, disabled: false };
}

/* Default name for a saved list: what drove the search, and when (PF1). */
function defaultListName(label, isoDate) {
  const base = (label || "").trim() || "Search";
  return `${base.slice(0, 150)} – ${isoDate}`;
}

function updateSaveAsListBtn() {
  const { text, disabled } = saveButtonLabel(state.checkedPapers.size, state.total || 0);
  $("btn-save-as-list").textContent = text;
  $("btn-save-as-list").disabled = disabled;
  $("select-all-results").indeterminate =
    state.checkedPapers.size > 0 && state.checkedPapers.size < state.results.length;
}

function toggleSelectAll(checked) {
  for (const paper of state.results) {
    if (checked) state.checkedPapers.add(paper.canonical_id);
    else state.checkedPapers.delete(paper.canonical_id);
  }
  renderResults();
}

/* "name" -> "name (2)" -> "name (3)": a free name to suggest when the one
   asked for is taken (B2). Pure, for the node-run test. */
function nextListName(name) {
  const m = /^(.*) \((\d+)\)$/.exec(name);
  return m ? `${m[1]} (${Number(m[2]) + 1})` : `${name} (2)`;
}

/* B1: a dialog, not a toggling inline box — a second click used to hide it.
   A taken name re-opens the dialog with the reason and a free name. */
async function saveResults() {
  if (!state.jobId) { notice("No search to save."); return; }
  let name = defaultListName(state.searchLabel, new Date().toISOString().slice(0, 10));
  let message = "Save to Saved References as:";
  for (;;) {
    name = window.prompt(message, name);
    if (name === null) return;              // Cancel
    name = name.trim();
    if (!name) { message = "Enter a name for the list:"; continue; }
    try {
      await saveResultsAs(name);
      return;
    } catch (e) {
      if (e.status !== 409 || !/already have a list/i.test(e.message)) { notice(e.message); return; }
      message = `"${name}" is already used. Choose another name:`;
      name = nextListName(name);
    }
  }
}

async function saveResultsAs(name) {
  const saved = await api("POST", `/api/searches/${state.jobId}/save-as-list`, {
    name,
    // Nothing ticked means "save all results" (null), not "save nothing".
    paper_ids: state.checkedPapers.size ? Array.from(state.checkedPapers) : null,
  });
  if (saved.skipped && saved.skipped.length) {
    notice(`Saved "${name}": ${saved.saved} of ${saved.requested} papers. ` +
           `Could not store: ${saved.skipped.join("; ")}`, "warn");
  } else {
    notice(`Saved "${name}" to Saved References (${saved.saved} papers).`, "ok");
  }
  return saved;
}

/* ── Summaries ───────────────────────────────────────────────────────────── */

/* The summary is shown in the paper detail popup, which is always in view
   (UI2). A poll that finishes after the user opened another paper does not
   write into that paper's popup. */
function paperKey(paper) {
  return paper.canonical_id || paper.doi || paper.title || "";
}

function modalShows(paper) {
  return !$("paper-modal").classList.contains("hidden") && state.modalPaper === paperKey(paper);
}

function setSummarizeButtons(key, busy) {
  for (const b of document.querySelectorAll("button[data-paper-key]")) {
    if (b.dataset.paperKey !== key) continue;
    b.disabled = busy;
    b.textContent = busy ? "Summarizing…" : "Summarize";
  }
}

async function startSummary(paper, button) {
  const key = paperKey(paper);
  if (state.summarizing.has(key)) return;
  notice("");
  state.summarizing.add(key);
  button.disabled = true;
  button.textContent = "Summarizing…";
  setSummarizeButtons(key, true);
  await openModal(paper, { lookup: false });
  $("modal-summary-meta").textContent = "Starting…";

  const done = () => { state.summarizing.delete(key); setSummarizeButtons(key, false); };
  try {
    const stored = await api("POST", "/api/summaries/lookup", { paper });
    if (modalShows(paper)) renderStoredSummary(stored);
    // FT1.2: an abstract kept for lack of full text is not final — Summarize
    // searches again, in case a free copy has appeared since.
    if (stored.source_text !== "abstract") {
      done();
      return;
    }
    if (modalShows(paper)) $("modal-summary-meta").textContent = "Searching again for the full text…";
  } catch (e) {
    if (e.status !== 404) {
      if (modalShows(paper)) $("modal-summary-meta").textContent = e.message;
      notice(e.message);
      done();
      return;
    }
  }

  let job;
  try {
    const local = localSettings();
    const summaryBody = { paper, find_by_title: findByTitle() };
    if (local.key) {
      summaryBody.api_key  = local.key;
      summaryBody.provider = local.provider || state.me.provider;
      summaryBody.model    = local.model    || "";
    }
    job = await api("POST", "/api/summaries", summaryBody);
  } catch (e) {
    notice(e.message);
    if (modalShows(paper)) $("modal-summary-meta").textContent = e.message;
    done();
    return;
  }
  if (modalShows(paper)) $("modal-summary-meta").textContent = `${job.provider} · ${job.model}`;

  let inFlight = false;
  let netFailures = 0;
  const timer = setInterval(async () => {
    if (inFlight) return;             // do not pile up requests on a slow link
    let s;
    inFlight = true;
    try { s = await api("GET", `/api/summaries/${job.job_id}`); }
    catch (e) {
      // A network blip: the job is still running on the server, so keep the
      // button busy and try again next tick (a second click would bill twice).
      if (e.status === 0) {
        netFailures += 1;
        if (netFailures === 3) {
          notice("Can't reach the server — still waiting for the summary.", "warn");
        }
        return;
      }
      clearInterval(timer); notice(e.message); done(); return;
    } finally { inFlight = false; }
    // Clear only our own warning, not an unrelated banner.
    if (netFailures >= 3 && $("notice").textContent.startsWith("Can't reach the server")) notice("");
    netFailures = 0;

    if (modalShows(paper)) {
      $("modal-summary-meta").textContent = `${job.provider} · ${job.model} — ${s.phase || s.status}`;
    }
    if (["done", "error", "cancelled"].includes(s.status)) {
      clearInterval(timer);
      done();
      if (s.status === "done") {
        state.searchSummaries = mergeSummaries(state.searchSummaries || [], [{
          canonical_id: paper.canonical_id || "", doi: paper.doi || "", title: paper.title || "",
          key_findings: s.result.key_findings || [], model_version: s.result.model || "",
          source_text: s.result.source_text || "",
          abstract_only: s.result.source_text === "abstract" ? (s.result.abstract || "") : "",
          created_at: new Date().toISOString(),
        }]);
        renderSummariesPanel();
        renderResults();
        refreshSearchSummaries();
        // RL2: a summary made from a saved list shows there at once.
        if (state.activeListId &&
            state.refItems.some(i => paperKey(i.paper || i) === key)) {
          refreshRefSummaries(state.activeListId);
        }
        if (modalShows(paper)) renderSummary(job, s.result);
        else notice(`Summary ready for "${(paper.title || "").slice(0, 80)}" — click Summarize to view it.`, "ok");
      } else {
        const reason = (s.error || "The summary failed.").replace(/^\w+Error: /, "");
        notice(reason);
        // K1: the reason belongs where the user is looking, not only in the bar.
        if (modalShows(paper)) {
          $("modal-summary-meta").textContent = `${job.provider} · ${job.model} — failed: ${reason}`;
        }
      }
      refreshMe();
      // A summary is the main thing that spends tokens (M1.C.2).
      refreshTokenMeter();
    }
  }, POLL_MS);
}

/* FT1: show an abstract kept for lack of full text for what it is. */
function renderAbstractStandIn(abstract, why) {
  const body = $("modal-summary");
  body.textContent = "";
  const heading = document.createElement("h4");
  heading.textContent = "Abstract only — no full text found (not a model summary)";
  const p = document.createElement("p");
  p.textContent = abstract || "(no abstract)";
  body.append(heading, p);
  $("modal-summary-meta").textContent = why || "";
}

function renderStoredSummary(stored) {
  if (stored.source_text === "abstract") {
    renderAbstractStandIn(stored.summary_text,
      "No full text was found when this was saved. Summarize searches again.");
    return;
  }
  let findings = stored.key_findings || [];
  if (typeof findings === "string") {
    try { findings = JSON.parse(findings || "[]"); } catch (e) { findings = []; }
  }
  renderSummary(null, {
    provider: "stored", model: stored.model_version || "",
    key_source: "none", key_findings: findings,
    methodology: stored.methodology, conclusions: stored.conclusions,
  });
  $("modal-summary-meta").textContent =
    `Summary by ${stored.model_version || "an earlier model"} (already stored — not re-run).`;
}

function renderSummary(job, result) {
  const body = $("modal-summary");
  body.textContent = "";
  if (!result) return;
  if (result.source_text === "abstract") {
    renderAbstractStandIn(result.abstract,
      `No full text found, so no model was used. Looked in: ${result.full_text || "—"}`);
    return;
  }
  $("modal-summary-meta").textContent = `${result.provider} · ${result.model} · ${result.key_source} key` +
    (result.text_source ? ` · from the full text via ${result.text_source}` : "");
  const heading = document.createElement("h4");
  heading.textContent = "Summary";
  body.appendChild(heading);
  if ((result.key_findings || []).length) {
    const strong = document.createElement("strong");
    strong.textContent = "Key findings";
    const list = document.createElement("ul");
    for (const finding of result.key_findings) {
      const li = document.createElement("li");
      li.textContent = finding;
      list.appendChild(li);
    }
    body.append(strong, list);
  }
  for (const [label, value] of [["Methodology", result.methodology], ["Conclusions", result.conclusions]]) {
    if (!value) continue;
    const strong = document.createElement("strong");
    strong.textContent = label;
    const p = document.createElement("p");
    p.textContent = value;
    body.append(strong, p);
  }
}

/* ── Summaries panel (S1–S3) ─────────────────────────────────────────────── */

function summaryKey(item) {
  return item.canonical_id || item.doi || item.title || "";
}

/* Merge incoming summaries into the list: same paper replaced, every other
   kept, newest first. A new summary never removes another (S2). Pure. */
function mergeSummaries(existing, incoming) {
  const byKey = new Map();
  for (const s of existing) byKey.set(summaryKey(s), s);
  for (const s of incoming) byKey.set(summaryKey(s), s);
  return Array.from(byKey.values())
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

function summaryFor(paper, summaries = state.searchSummaries) {
  const key = summaryKey(paper);
  return (summaries || []).find(s => summaryKey(s) === key ||
    (paper.doi && s.doi === paper.doi) || (paper.canonical_id && s.canonical_id === paper.canonical_id));
}

function hasSummary(paper, summaries = state.searchSummaries) {
  return Boolean(summaryFor(paper, summaries));
}

/* FT1: an abstract kept because no full text was found is not a summary, and
   its badge must not say so. Pure, for the node-run test. */
function summaryBadgeText(entry) {
  return entry && entry.source_text === "abstract" ? "✓ Abstract only" : "✓ Summary";
}

async function refreshSearchSummaries() {
  if (!state.jobId) return;
  const jobId = state.jobId;
  let data;
  try { data = await api("GET", `/api/searches/${jobId}/summaries`); }
  catch (e) { return; }                    // not finished, or expired: keep what we have
  if (state.jobId !== jobId) return;       // a newer search started meanwhile
  state.searchSummaries = mergeSummaries(state.searchSummaries || [], data.summaries || []);
  renderSummariesPanel();
  renderResults();
}

function renderSummariesPanel() {
  const list = $("summaries-list");
  const items = state.searchSummaries || [];
  list.textContent = "";
  $("summaries-card").classList.toggle("hidden", items.length === 0);
  $("summaries-heading").textContent = `Summaries (${items.length})`;
  for (const s of items) {
    const li = document.createElement("li");
    const t = document.createElement("div");
    t.className = "title";
    t.textContent = s.title || "(untitled)";
    const f = document.createElement("div");
    f.className = "finding";
    const first = (s.key_findings || [])[0];
    f.textContent = s.source_text === "abstract"
      ? "Abstract only — no full text found"
      : (first ? first : "") + (s.model_version ? `  — ${s.model_version}` : "");
    li.append(t, f);
    const paper = (state.results || []).find(p => summaryKey(p) === summaryKey(s)) ||
      { title: s.title, doi: s.doi, canonical_id: s.canonical_id };
    li.addEventListener("click", () => openModal(paper));
    list.appendChild(li);
  }
}

async function saveSummariesPdf() {
  if (!state.jobId) { notice("Run a search first."); return; }
  const body = {
    title: (state.searchLabel || "Search results").slice(0, 200),
    paper_ids: state.checkedPapers.size ? Array.from(state.checkedPapers) : null,
  };
  let resp;
  try {
    resp = await api("POST", `/api/searches/${state.jobId}/summaries.pdf`, body, { raw: true });
  } catch (e) { notice(`Could not build the PDF: ${e.message}`); return; }
  if (resp.status === 401) return;          // api() has shown the sign-in page
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch (e) {}
    notice(`Could not build the PDF: ${detail}`);
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${body.title.replace(/[^\w\- ]+/g, "_")} - summaries.pdf`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  notice(state.checkedPapers.size
    ? `Saved summaries for the ${state.checkedPapers.size} ticked papers.`
    : "Saved summaries for all results.", "ok");
}

/* ── Paper detail modal ──────────────────────────────────────────────────── */

async function openModal(paper, { lookup = true } = {}) {
  state.modalPaper = paperKey(paper);
  $("modal-title").textContent = paper.title || "(untitled)";
  $("modal-abstract").textContent = paper.abstract || "(no abstract)";
  $("modal-summary").textContent = "";
  $("modal-summary-meta").textContent = "";
  const pdfHref = safeUrl(paper.pdf_url || paper.best_oa_url);
  const pdfLink = $("modal-pdf-link");
  if (pdfHref) {
    pdfLink.href = pdfHref;
    pdfLink.textContent = "Open PDF";
    pdfLink.style.display = "";
  } else {
    pdfLink.style.display = "none";
  }
  $("paper-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  if (!lookup) return;
  try {
    const stored = await api("POST", "/api/summaries/lookup", { paper });
    if (modalShows(paper)) renderStoredSummary(stored);
  } catch (e) {
    if (modalShows(paper)) {
      $("modal-summary-meta").textContent =
        e.status === 404 ? "Not summarized yet — use Summarize to create one." : e.message;
    }
  }
}

function closeModal() {
  $("paper-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  state.modalPaper = null;
}

/* ── Filters tab ─────────────────────────────────────────────────────────── */

async function loadFilterTab() {
  populateCategorySelect("filter-category");
  populateSelect("filter-paper-type", PAPER_TYPES);
  populateSelect("filter-version", VERSIONS);
  populateSelect("filter-published", PUBLISHED);
  populateSelect("filter-license", LICENSES);
  populateSelect("filter-species", SPECIES);
  renderSourcePicker($("filter-sources-picker"), state.sources, defaultSourceIds());
  await reloadFilterList();
}

async function reloadFilterList() {
  let filters = [];
  try { filters = (await api("GET", "/api/filters")).filters; }
  catch (e) { notice(e.message); return; }
  state.filters = filters;

  const ul = $("filter-list");
  ul.textContent = "";
  for (const f of filters) {
    const li = document.createElement("li");
    const nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = f.name;
    if (!f.enabled) nameSpan.style.opacity = "0.5";
    li.appendChild(nameSpan);
    li.dataset.filterId = f.id;
    li.style.cursor = "pointer";
    li.addEventListener("click", () => selectFilter(f.id));
    if (state.activeFilterId === f.id) li.classList.add("active");
    ul.appendChild(li);
  }
}

/* GET /api/filters returns each filter flat: its fields sit beside id, name
   and enabled, not under a `filter` key. Pure, so the contract with the real
   route output is exercised in node (tests/web/test_frontend_wiring.py). */
function filterFields(f) {
  return f || {};
}

function selectFilter(filterId) {
  state.activeFilterId = filterId;
  const f = state.filters.find(x => x.id === filterId);
  if (!f) return;

  $("filter-name").value = f.name || "";
  $("filter-enabled").checked = f.enabled !== false;

  const fd = filterFields(f);
  const cat = fd.category || "(any)";
  $("filter-category").value = cat;

  // start_date/end_date are what the query builder reads; date_from/date_to
  // were written by an earlier web build and are read here only to migrate.
  const startDate = fd.start_date || fd.date_from || "";
  const endDate   = fd.end_date   || fd.date_to   || "";
  const useRange = !!(startDate || endDate);
  $("filter-date-range-toggle").checked = useRange;
  $("filter-days-wrap").classList.toggle("hidden", useRange);
  $("filter-date-range-wrap").classList.toggle("hidden", !useRange);
  $("filter-days").value = fd.days_back || 7;
  $("filter-start-date").value = startDate;
  $("filter-end-date").value   = endDate;

  $("filter-authors").value    = (fd.authors    || []).join(", ");
  // A string in the desktop format; an earlier web build saved a list.
  $("filter-institution").value = Array.isArray(fd.institution)
    ? fd.institution.join(", ") : (fd.institution || "");
  $("filter-paper-type").value = fd.paper_type || "(any)";
  $("filter-version").value    = fd.version    || "(any)";
  $("filter-published").value  = fd.published  || "(any)";
  $("filter-license").value    = fd.license    || "(any)";
  $("filter-species").value    = fd.species    || "(any)";

  renderTextGroups(fd.text_groups || [{}]);

  // Show the filter's own saved sources, so saving it does not silently
  // replace them with whatever the picker last held.
  const sel = fd.source_selection;
  const own = sel && !sel.all && Array.isArray(sel.selected) ? sel.selected : null;
  renderSourcePicker($("filter-sources-picker"), state.sources, own);

  Array.from($("filter-list").children).forEach(li => {
    li.classList.toggle("active", Number(li.dataset.filterId) === filterId);
  });
  // The chips show which terms are in THIS filter, so they follow the editor.
  renderDiscoverChipState();
}

/* A text group has three comma-separated term fields, matching the desktop
   editor and what src/filtering.py and the query builder read: title,
   abstract, and both (title or abstract). Groups are ORed; fields within a
   group are ANDed. `keywords` was written by an earlier web build and is read
   only to migrate it into `both`. */
const TEXT_GROUP_FIELDS = [
  ["title", "Title words"],
  ["abstract", "Abstract words"],
  ["both", "Title or abstract words"],
];

function normaliseTextGroup(g) {
  g = g || {};
  return {
    title: g.title || "",
    abstract: g.abstract || "",
    both: g.both || g.keywords || "",
  };
}

function renderTextGroups(groups) {
  const container = $("filter-text-groups");
  container.textContent = "";
  if (!groups.length) groups = [{}];
  for (let gi = 0; gi < groups.length; gi++) {
    const g = normaliseTextGroup(groups[gi]);
    const div = document.createElement("div");
    div.className = "text-group";
    div.style.cssText = "border:1px solid #ccc;padding:6px;margin-bottom:6px;border-radius:4px";
    div.dataset.groupIndex = gi;
    if (gi > 0) {
      const orLabel = document.createElement("span");
      orLabel.className = "tag small";
      orLabel.textContent = "OR";
      orLabel.style.marginBottom = "4px";
      div.appendChild(orLabel);
    }
    const row = document.createElement("div");
    row.className = "row";
    for (const [field, label] of TEXT_GROUP_FIELDS) {
      const wrap = document.createElement("label");
      const span = document.createElement("span");
      span.textContent = label;
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "comma-separated";
      input.dataset.field = field;
      input.value = g[field];
      wrap.append(span, input);
      row.appendChild(wrap);
    }
    const removeBtn = document.createElement("button");
    removeBtn.className = "link small shrink";
    removeBtn.textContent = "remove";
    removeBtn.addEventListener("click", () => { div.remove(); });
    row.appendChild(removeBtn);
    div.appendChild(row);
    container.appendChild(div);
  }
}

function collectTextGroups(keepEmpty = false) {
  const groups = [];
  for (const div of $("filter-text-groups").querySelectorAll(".text-group")) {
    const g = {};
    for (const [field] of TEXT_GROUP_FIELDS) {
      const input = div.querySelector(`input[data-field="${field}"]`);
      g[field] = input ? input.value.trim() : "";
    }
    if (keepEmpty || g.title || g.abstract || g.both) groups.push(g);
  }
  return groups;
}

function buildFilterDict() {
  const useRange = $("filter-date-range-toggle").checked;
  const f = {
    text_groups: collectTextGroups(),
    authors:     $("filter-authors").value.split(",").map(s => s.trim()).filter(Boolean),
    institution: $("filter-institution").value.trim(),   // a string: filtering.py calls .strip()
    source_selection: getSourceSelection($("filter-sources-picker")),
  };
  const cat = $("filter-category").value;
  if (cat && cat !== "(any)") f.category = cat;
  if (useRange) {
    f.start_date = $("filter-start-date").value;
    f.end_date   = $("filter-end-date").value;
  } else {
    f.days_back = Number($("filter-days").value) || 7;
  }
  for (const [key, elId, defVal] of [
    ["paper_type", "filter-paper-type", "(any)"],
    ["version",    "filter-version",    "(any)"],
    ["published",  "filter-published",  "(any)"],
    ["license",    "filter-license",    "(any)"],
    ["species",    "filter-species",    "(any)"],
  ]) {
    const val = $(elId).value;
    if (val && val !== defVal) f[key] = val;
  }
  return f;
}

async function newFilter() {
  state.activeFilterId = null;
  $("filter-name").value = "New filter";
  $("filter-enabled").checked = true;
  $("filter-days").value = 7;
  $("filter-date-range-toggle").checked = false;
  $("filter-days-wrap").classList.remove("hidden");
  $("filter-date-range-wrap").classList.add("hidden");
  $("filter-authors").value = "";
  $("filter-institution").value = "";
  renderTextGroups([{}]);
  renderSourcePicker($("filter-sources-picker"), state.sources, defaultSourceIds());
  $("filter-name").focus();
  renderDiscoverChipState();
}

async function saveFilter() {
  const name = $("filter-name").value.trim();
  if (!name) { notice("Enter a filter name."); return; }
  const body = { name, enabled: $("filter-enabled").checked, filter: buildFilterDict() };
  try {
    if (state.activeFilterId) {
      await api("PUT", `/api/filters/${state.activeFilterId}`, body);
    } else {
      const created = await api("POST", "/api/filters", body);
      state.activeFilterId = created.id;
    }
    notice("Filter saved.", "ok");
    await reloadFilterList();
    await loadSearchFilters();
  } catch (e) { notice(e.message); }
}

async function saveFilterAs() {
  const name = prompt("Save as name:");
  if (!name) return;
  const body = { name, enabled: $("filter-enabled").checked, filter: buildFilterDict() };
  try {
    const created = await api("POST", "/api/filters", body);
    state.activeFilterId = created.id;
    notice("Saved.", "ok");
    await reloadFilterList();
    await loadSearchFilters();
  } catch (e) { notice(e.message); }
}

async function deleteFilter() {
  if (!state.activeFilterId) return;
  if (!confirm("Delete this filter?")) return;
  try {
    await api("DELETE", `/api/filters/${state.activeFilterId}`);
    state.activeFilterId = null;
    $("filter-name").value = "";
    notice("Deleted.", "ok");
    await reloadFilterList();
    await loadSearchFilters();
  } catch (e) { notice(e.message); }
}

async function testFilter() {
  if (!state.activeFilterId) { notice("Save the filter first."); return; }
  $("filter-test-results").classList.remove("hidden");
  $("filter-test-status").textContent = "Starting test…";
  $("filter-test-body").textContent = "";
  if (state.filterTestPolling) clearInterval(state.filterTestPolling);
  try {
    const job = await api("POST", `/api/filters/${state.activeFilterId}/test`);
    state.filterTestJobId = job.job_id;
    state.filterTestFailures = 0;
    state.filterTestPolling = setInterval(pollFilterTest, POLL_MS);
    pollFilterTest();
  } catch (e) { $("filter-test-status").textContent = e.message; }
}

async function pollFilterTest() {
  if (!state.filterTestJobId) return;
  let job;
  try { job = await api("GET", `/api/searches/${state.filterTestJobId}`); }
  catch (e) {
    if (!state.filterTestPolling) return;
    state.filterTestFailures += 1;
    if (!shouldStopPolling(e, state.filterTestFailures)) {
      $("filter-test-status").textContent =
        `Lost contact with the server — retrying (${state.filterTestFailures}/${POLL_GIVE_UP})…`;
      return;
    }
    clearInterval(state.filterTestPolling);
    state.filterTestPolling = null;
    $("filter-test-status").textContent = `Lost track of the test: ${e.message}`;
    return;
  }
  state.filterTestFailures = 0;
  $("filter-test-status").textContent = progressText(job);
  if (["done", "error", "cancelled"].includes(job.status)) {
    clearInterval(state.filterTestPolling);
    state.filterTestPolling = null;
    // Same rule as the main search: an unreachable source makes "0 matched"
    // mean "incomplete", not "this filter finds nothing".
    const failed = (job.sources_failed || []).length ? " " + failedSourcesText(job) : "";
    if (job.status === "done") {
      let page;
      try {
        page = await api("GET", `/api/searches/${state.filterTestJobId}/results?limit=50`);
      } catch (e) {
        $("filter-test-status").textContent = `Could not load the results: ${e.message}`;
        return;
      }
      renderFilterTestResults(page.results || []);
      const enrichNote = enrichProblemsText(job);
      $("filter-test-status").textContent =
        `${page.total} papers matched.${failed}${enrichNote ? " " + enrichNote : ""}`;
    } else {
      $("filter-test-status").textContent = (job.error || job.status) + failed;
    }
  }
}

function renderFilterTestResults(papers) {
  const body = $("filter-test-body");
  body.textContent = "";
  for (const p of papers) {
    const tr = document.createElement("tr");
    const tdTitle = document.createElement("td");
    const href = safeUrl(p.url || p.source_url);
    let link;
    if (href) {
      link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    } else {
      link = document.createElement("span");
    }
    link.textContent = p.title || "(untitled)";
    tdTitle.appendChild(link);
    const tdAuth = document.createElement("td");
    tdAuth.className = "small";
    tdAuth.textContent = (p.authors || "").split(";")[0] || "";
    const tdDate = document.createElement("td");
    tdDate.className = "small";
    tdDate.textContent = p.date || p.pub_date || "";
    const tdSrc = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = p.journal_or_server || p.source || "";
    tdSrc.appendChild(tag);
    tr.append(tdTitle, tdAuth, tdDate, tdSrc);
    body.appendChild(tr);
  }
}

/* ── Discover terms ──────────────────────────────────────────────────────── */

async function discoverTerms() {
  const desc = $("discover-desc").value.trim();
  if (!desc) { notice("Enter a research description."); return; }
  $("btn-discover").disabled = true;
  $("discover-terms-chips").textContent = "Searching…";
  $("discover-terms-chips").classList.remove("hidden");
  if (state.discoverPolling) clearInterval(state.discoverPolling);
  try {
    const local = localSettings();
    const body = { description: desc };
    if (local.key) { body.api_key = local.key; body.provider = local.provider; body.model = local.model; }
    const job = await api("POST", "/api/discover-terms", body);
    state.discoverJobId = job.job_id;
    state.discoverPolling = setInterval(pollDiscover, POLL_MS);
    pollDiscover();
  } catch (e) {
    $("discover-terms-chips").textContent = e.message;
    $("btn-discover").disabled = false;
  }
}

async function pollDiscover() {
  if (!state.discoverJobId) return;
  let job;
  try { job = await api("GET", `/api/discover-terms/${state.discoverJobId}`); }
  catch (e) {
    clearInterval(state.discoverPolling);
    $("discover-terms-chips").textContent = e.message;
    $("btn-discover").disabled = false;
    return;
  }
  if (["done", "error", "cancelled"].includes(job.status)) {
    clearInterval(state.discoverPolling);
    state.discoverPolling = null;
    $("btn-discover").disabled = false;
    // Discover calls the model too — on the failure paths as well, which is
    // where a billed call is easiest to forget (M1.C.2).
    refreshTokenMeter();
    const failed = (job.sources_failed || []).length ? " " + failedSourcesText(job) : "";
    if (job.status === "done" && job.result && job.result.papers_found === 0) {
      $("discover-terms-chips").textContent =
        `No papers found for "${job.result.keywords}" in the date range, ` +
        `so there was nothing to suggest terms from.${failed}`;
    } else if (job.status === "done" && job.result) {
      renderDiscoverChips(job.result.terms || []);
      if (failed) notice(`Discover Terms: results may be incomplete.${failed}`, "warn");
    } else {
      $("discover-terms-chips").textContent = (job.error || `Discover ${job.status}.`) + failed;
    }
  } else {
    $("discover-terms-chips").textContent = job.phase || "Working…";
  }
}

function renderDiscoverChips(terms) {
  const container = $("discover-terms-chips");
  container.textContent = "";
  if (!terms.length) { container.textContent = "No terms suggested."; return; }

  const hint = document.createElement("p");
  hint.className = "muted small";
  hint.style.margin = "0 0 4px";
  hint.textContent = "Click a term to make a new filter named after it. " +
                     "Right-click a term to add it as another group in the " +
                     "filter you have open.";
  container.appendChild(hint);

  for (const term of terms) {
    const chip = document.createElement("button");
    chip.className = "tag discover-chip";
    chip.style.cursor = "pointer";
    chip.style.margin = "2px";
    chip.dataset.term = term;
    chip.textContent = term;
    chip.title = "Click: new filter · Right-click: add as a group";
    chip.addEventListener("click", () => createFilterFromTerm(term));
    chip.addEventListener("contextmenu", (e) => {
      e.preventDefault();                 // the browser's menu, not ours
      addTermAsGroup(term);
    });
    container.appendChild(chip);
  }
  renderDiscoverChipState();
}

/* Is this term already a search term anywhere in these groups? Case-
   insensitive, and it looks inside comma lists too, so a term typed into a
   group by hand is recognised. Pure, for the node-run test. */
function groupsHaveTerm(groups, term) {
  const key = term.trim().toLowerCase();
  return groups.some(g => String(g.both || "").split(",")
    .some(t => t.trim().toLowerCase() === key));
}

/* The groups with this term added as a group of its own — OR with the rest.
   Empty groups are dropped: a group with no conditions matches every paper,
   so one left beside the others would make the filter match everything.
   Pure, for the node-run test. */
function groupsWithTerm(groups, term) {
  const kept = groups.filter(g => (g.title || g.abstract || g.both || "").trim());
  return [...kept, { title: "", abstract: "", both: term.trim() }];
}

/* A name no existing filter already has.

   POST /api/filters is an upsert on the name, so a new filter given a taken
   name silently REPLACES that filter. Clicking "inflammaging" when a filter
   called "inflammaging" exists would otherwise overwrite it; instead the new
   one is "inflammaging (2)". Case-insensitive. Pure, for the node-run test. */
function freeFilterName(base, takenNames) {
  const taken = new Set((takenNames || []).map(n => String(n).trim().toLowerCase()));
  let name = (base || "").trim().slice(0, 80) || "Discovered term";
  while (taken.has(name.toLowerCase())) name = nextListName(name);
  return name;
}

/* Click: a new filter, named after the term, searching for it. */
async function createFilterFromTerm(term) {
  if (state.discoverSaving) return;       // one save at a time
  await newFilter();
  const name = freeFilterName(term, (state.filters || []).map(f => f.name));
  $("filter-name").value = name;
  renderTextGroups(groupsWithTerm([], term));
  await saveTermFilter(`Created filter "${name}".`);
}

/* Right-click: the term becomes another group in the filter that is open.
   With no saved filter open there is nothing to add to, so it starts one —
   doing nothing at all was the old behaviour, and it looked broken. */
async function addTermAsGroup(term) {
  if (state.discoverSaving) return;
  if (!state.activeFilterId) { await createFilterFromTerm(term); return; }

  const groups = collectTextGroups();
  if (groupsHaveTerm(groups, term)) {
    notice(`"${term}" is already in "${$("filter-name").value}".`, "warn");
    return;
  }
  renderTextGroups(groupsWithTerm(groups, term));
  await saveTermFilter(`Added "${term}" to "${$("filter-name").value}" as a new group.`);
}

/* Save the filter in the editor: create it if new, update it if not. */
async function saveTermFilter(message) {
  const name = $("filter-name").value.trim();
  const body = { name, enabled: $("filter-enabled").checked, filter: buildFilterDict() };
  state.discoverSaving = true;
  try {
    if (state.activeFilterId) {
      await api("PUT", `/api/filters/${state.activeFilterId}`, body);
    } else {
      const created = await api("POST", "/api/filters", body);
      state.activeFilterId = created.id;
    }
  } catch (e) {
    notice(`Could not save the filter: ${e.message}`);
    return;
  } finally {
    state.discoverSaving = false;
  }
  notice(message, "ok");
  await reloadFilterList();
  await loadSearchFilters();              // runnable from Search straight away
  renderDiscoverChipState();
}

/* Mark each chip that is already a term in the open filter. */
function renderDiscoverChipState() {
  const groups = state.activeFilterId ? collectTextGroups() : [];
  for (const chip of $("discover-terms-chips").querySelectorAll(".discover-chip")) {
    const on = groupsHaveTerm(groups, chip.dataset.term);
    chip.classList.toggle("chip-on", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

/* ── References tab ──────────────────────────────────────────────────────── */

async function loadRefTab() {
  let lists = [];
  try { lists = (await api("GET", "/api/references")).lists; }
  catch (e) { notice(e.message); return; }
  state.refLists = lists;
  renderRefLists();
}

function renderRefLists() {
  const ul = $("ref-lists");
  ul.textContent = "";
  for (const lst of state.refLists) {
    const li = document.createElement("li");
    const nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = lst.name;
    const countSpan = document.createElement("span");
    countSpan.className = "tag small";
    countSpan.textContent = lst.item_count || 0;
    li.appendChild(nameSpan);
    li.appendChild(countSpan);
    li.dataset.listId = lst.id;
    li.style.cursor = "pointer";
    li.addEventListener("click", () => selectRefList(lst.id));
    if (state.activeListId === lst.id) li.classList.add("active");
    ul.appendChild(li);
  }
}

async function selectRefList(listId) {
  state.activeListId = listId;
  const lst = state.refLists.find(x => x.id === listId);
  if (!lst) return;
  $("ref-list-title").textContent = lst.name;
  Array.from($("ref-lists").children).forEach(li => {
    li.classList.toggle("active", Number(li.dataset.listId) === listId);
  });
  try {
    const data = await api("GET", `/api/references/${listId}/items`);
    state.refItems = data.items || [];
    state.refSummaries = [];
    renderRefItems();
  } catch (e) { notice(e.message); return; }
  // A review made earlier survives a reload and a change of list (M4.A.3).
  $("ref-review").classList.add("hidden");
  await refreshRefSummaries(listId);
  await loadStoredReview(listId);
}

/* RL1: which papers in the open list already have a stored summary. */
async function refreshRefSummaries(listId) {
  let data;
  try { data = await api("GET", `/api/references/${listId}/summaries`); }
  catch (e) { return; }                 // the list still shows; badges just stay off
  if (state.activeListId !== listId) return;    // another list was opened meanwhile
  state.refSummaries = data.summaries || [];
  renderRefItems();
}

function renderRefItems() {
  const body = $("ref-papers-body");
  body.textContent = "";
  for (const item of state.refItems) {
    const p = item.paper || item;
    const tr = document.createElement("tr");

    const checkTd = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.itemId = item.item_id;
    cb.addEventListener("change", updateRefCheckedCount);
    checkTd.appendChild(cb);

    const titleTd = document.createElement("td");
    const href = safeUrl(p.url || p.source_url);
    let link;
    if (href) {
      link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    } else {
      link = document.createElement("span");
    }
    link.textContent = p.title || "(untitled)";
    titleTd.appendChild(link);
    // RL2: the same details view as the Search results — abstract, PDF link
    // and the stored summary.
    const detailBtn = document.createElement("span");
    detailBtn.className = "tag small";
    detailBtn.textContent = "detail";
    detailBtn.style.cursor = "pointer";
    detailBtn.style.marginLeft = "4px";
    detailBtn.addEventListener("click", () => openModal(p));
    titleTd.appendChild(detailBtn);
    if (hasSummary(p, state.refSummaries)) {
      const badge = document.createElement("span");
      badge.className = "tag small has-summary";
      badge.textContent = summaryBadgeText(summaryFor(p, state.refSummaries));
      badge.style.marginLeft = "4px";
      badge.style.cursor = "pointer";
      badge.addEventListener("click", () => openModal(p));
      titleTd.appendChild(badge);
    }

    const authTd = document.createElement("td");
    authTd.className = "small";
    authTd.textContent = (p.authors || "").split(";")[0] || "";

    const dateTd = document.createElement("td");
    dateTd.className = "small";
    dateTd.textContent = p.pub_date || "";

    const srcTd = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = p.source || p.server || "";
    srcTd.appendChild(tag);

    // RL2: generate a summary from the list, as from the Search results.
    const actTd = document.createElement("td");
    const summarize = document.createElement("button");
    const busy = state.summarizing.has(paperKey(p));
    summarize.textContent = busy ? "Summarizing…" : "Summarize";
    summarize.disabled = busy;
    summarize.dataset.paperKey = paperKey(p);
    summarize.addEventListener("click", () => startSummary(p, summarize));
    actTd.appendChild(summarize);

    tr.append(checkTd, titleTd, authTd, dateTd, srcTd, actTd);
    body.appendChild(tr);
  }
  updateRefCheckedCount();
}

function updateRefCheckedCount() {
  const boxes = $("ref-papers-body")
    ? Array.from($("ref-papers-body").querySelectorAll("input[data-item-id]")) : [];
  const checked = boxes.filter(cb => cb.checked).length;
  $("ref-checked-count").textContent = `${checked} selected`;
  // M2.A.1: the header box mirrors the rows — ticked when all are, cleared when
  // none are, and indeterminate in between, so it never claims a whole-list
  // selection that is not there.
  const all = $("select-all-refs");
  if (all) {
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  }
}

/* M2.A.1: tick or clear every paper in the list. The References table renders
   the whole list — it is not paginated — so "all" here really is all of it,
   which is what the count then reports. */
function toggleSelectAllRefs(checked) {
  const body = $("ref-papers-body");
  if (!body) return;
  for (const cb of body.querySelectorAll("input[data-item-id]")) cb.checked = checked;
  updateRefCheckedCount();
}

async function newRefList() {
  const name = prompt("New list name:");
  if (!name) return;
  try {
    await api("POST", "/api/references", { name });
    await loadRefTab();
  } catch (e) { notice(e.message); }
}

async function deleteRefList() {
  if (!state.activeListId) { notice("Select a list first."); return; }
  if (!confirm("Delete this list?")) return;
  try {
    await api("DELETE", `/api/references/${state.activeListId}`);
    state.activeListId = null;
    state.refItems = [];
    $("ref-list-title").textContent = "Select a list";
    $("ref-papers-body").textContent = "";
    await loadRefTab();
  } catch (e) { notice(e.message); }
}

async function removeRefSelected() {
  if (!state.activeListId) return;
  const checked = Array.from($("ref-papers-body").querySelectorAll("input:checked"));
  const failures = [];
  for (const cb of checked) {
    try {
      await api("DELETE", `/api/references/${state.activeListId}/items/${cb.dataset.itemId}`);
    } catch (e) { failures.push(e.message); }
  }
  await selectRefList(state.activeListId);
  await loadRefTab();
  if (failures.length) {
    notice(`Removed ${checked.length - failures.length} of ${checked.length}; ` +
           `${failures.length} failed: ${[...new Set(failures)].join("; ")}`);
  }
}

/* The name a downloaded PDF lands under in the browser's downloads folder.
   Mirrors the desktop app's convention (src/pdf_handler._get_safe_filename) so
   the same paper is called the same thing whichever front end fetched it:
   <doi>_<title>.pdf, with "/" in the DOI replaced. ASCII-only on purpose —
   a downloads folder is not the place to discover an encoding problem. Falls
   back to the paper id only when there is neither a DOI nor a title.
   Pure, for the node-run test. */
function pdfFileName(title, doi, paperId) {
  const safeDoi = doi ? String(doi).replace(/\//g, "_").slice(0, 50) : "";
  const safeTitle = String(title || "")
    .slice(0, 40)
    .replace(/[^\w\s-]/g, "")
    .replace(/[-\s]+/g, "_")
    .replace(/^_+|_+$/g, "");
  const stem = [safeDoi, safeTitle].filter(Boolean).join("_");
  return `${stem || `paper-${paperId}`}.pdf`;
}

async function downloadRefPdfs(selectedOnly) {
  if (!state.activeListId) return;
  const itemIds = selectedOnly
    ? Array.from($("ref-papers-body").querySelectorAll("input:checked")).map(cb => cb.dataset.itemId)
    : state.refItems.map(i => i.item_id);
  if (!itemIds.length) { notice("Nothing selected."); return; }

  $("ref-dl-status").textContent = `Downloading ${itemIds.length} PDF(s)…`;
  $("ref-dl-status").classList.remove("hidden");
  let done = 0;
  const failures = [];
  for (const itemId of itemIds) {
    const item = state.refItems.find(i => String(i.item_id) === String(itemId));
    if (!item) { failures.push(`item ${itemId}: no longer in the list`); continue; }
    const paper = item.paper || item;
    const title = (paper.title || `paper ${itemId}`).slice(0, 60);
    const paperId = paper.paper_id;
    try {
      const resp = await api("GET", `/api/references/${state.activeListId}/pdf/${paperId}`, undefined, { raw: true });
      if (resp.status === 401) return;      // api() has shown the sign-in page
      if (resp.ok) {
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = pdfFileName(paper.title, paper.doi, paperId);
        a.click();
        // Revoking in the same tick can cancel the download in some browsers.
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        done++;
      } else {
        let detail = `${resp.status}`;
        try { detail = (await resp.json()).detail || detail; } catch (e) {}
        failures.push(`${title}: ${detail}`);
      }
    } catch (e) { failures.push(`${title}: ${e.message}`); }
    $("ref-dl-status").textContent = `Downloaded ${done} / ${itemIds.length}`;
  }
  $("ref-dl-status").textContent =
    `Done — ${done} of ${itemIds.length} PDF(s) downloaded.` +
    (failures.length ? ` Not downloaded: ${failures.join(" · ")}` : "");
}

async function exportRefSummariesPdf() {
  if (!state.activeListId) { notice("Select a list first."); return; }
  const lst = (state.refLists || []).find(x => x.id === state.activeListId) || {};
  $("ref-dl-status").textContent = "Building the summaries PDF…";
  $("ref-dl-status").classList.remove("hidden");
  // RL3: the ticked papers, or the whole list when none are ticked.
  const ticked = Array.from($("ref-papers-body").querySelectorAll("input:checked"))
    .map(cb => cb.dataset.itemId);
  let resp;
  try {
    // Each call written out in full so the route-inventory test can read it.
    resp = ticked.length
      ? await api("GET", `/api/references/${state.activeListId}/summaries.pdf?item_ids=${ticked.join(",")}`,
                  undefined, { raw: true })
      : await api("GET", `/api/references/${state.activeListId}/summaries.pdf`,
                  undefined, { raw: true });
  } catch (e) { $("ref-dl-status").textContent = `Export failed: ${e.message}`; return; }
  if (resp.status === 401) return;          // api() has shown the sign-in page
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch (e) {}
    $("ref-dl-status").textContent = `Export failed: ${detail}`;
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(lst.name || "references").replace(/[^\w\- ]+/g, "_")} - summaries.pdf`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  $("ref-dl-status").textContent = "Summaries PDF downloaded.";
}

/* M6.A.5: the saved file is named after the list, not "references". The server
   sends the name in Content-Disposition; this is the fallback for when the
   browser does not use it. Pure, for the node-run test. */
function savedListFileName(listName, format) {
  const stem = String(listName || "")
    .replace(/[^\w\s-]/g, "")
    .replace(/[-\s]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
  return `${stem || "references"}.${format}`;
}

/* M6: save the open list as CSV, RTF or PDF. Ticked papers only when any are
   ticked, the whole list otherwise — the same rule the Search tab's save uses,
   so "save" never quietly means something different on the two screens. */
async function saveRefList() {
  if (!state.activeListId) return;
  const format = $("ref-save-format").value || "csv";
  const ticked = Array.from(
    $("ref-papers-body").querySelectorAll("input[data-item-id]:checked"))
    .map(cb => cb.dataset.itemId);

  const idsParam = ticked.length
    ? `&item_ids=${encodeURIComponent(ticked.join(","))}` : "";

  const status = $("ref-dl-status");
  status.classList.remove("hidden");
  status.textContent = ticked.length
    ? `Saving ${ticked.length} selected as ${format.toUpperCase()}…`
    : `Saving the whole list as ${format.toUpperCase()}…`;

  let resp;
  try {
    resp = await api("GET",
      `/api/references/${state.activeListId}/save?format=${encodeURIComponent(format)}${idsParam}`,
      undefined, { raw: true });
  }
  catch (e) { status.textContent = `Save failed: ${e.message}`; return; }
  if (resp.status === 401) return;          // api() has shown the sign-in page
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch (e) {}
    status.textContent = `Save failed: ${detail}`;
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = savedListFileName(refListName(), format);
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  status.textContent = ticked.length
    ? `Saved ${ticked.length} of ${state.refItems.length} as ${format.toUpperCase()}.`
    : `Saved ${state.refItems.length} paper(s) as ${format.toUpperCase()}.`;
}

/* The estimate request, carrying whatever credential the run itself would
   use. Built in one place so the estimate and the run cannot disagree. */
function estimateBody(papers) {
  const local = localSettings();
  const body = { papers };
  if (local.key) {
    body.api_key = local.key;
    body.provider = local.provider || (state.me && state.me.provider) || "";
    body.model = local.model || "";
  }
  return body;
}

/* M5.A.3/M5.A.4: what the confirm dialog says about cost. An estimate is
   always labelled as one, and a model with no configured rate shows token
   counts with no dollar figure rather than an invented price. Pure, for the
   node-run test. */
function spendEstimateText(est) {
  if (!est || !est.papers) return "";
  // A review's prompt is built from stored text, so its size is measured and
  // both ends are the same — "2.1k–2.1k" would present a figure as a range.
  // Same collapse as the money line below.
  const low = compactTokens(est.low), high = compactTokens(est.high);
  const tokens = `${low === high ? low : `${low}–${high}`} tokens`;
  let money = ", cost depends on your provider's rates";
  if (est.dollars_low != null && est.dollars_high != null) {
    const low = formatMoney(est.dollars_low);
    const high = formatMoney(est.dollars_high);
    // Both ends under a cent format the same, and "less than $0.01–less than
    // $0.01" is nonsense. One figure when they agree.
    money = low === high ? `, roughly ${low}` : `, roughly ${low}–${high}`;
  }
  return `Estimated ${tokens}${money}. This is an estimate, not a quote.`;
}

/* $0.0272 → "$0.03"; $0.0004 → "less than $0.01". Pure, for the node-run test. */
function formatMoney(amount) {
  if (amount < 0.01) return "less than $0.01";
  return "$" + amount.toFixed(2);
}

/* Who pays, and what is left of a shared allowance. Pure, for the node-run
   test — the user must know whose money this is before pressing Go ahead. */
function spendKeyText(est) {
  if (!est) return "";
  if (est.billed_to_owner) {
    return `Billed to the shared key (${est.provider}). ` +
           `${est.cap_remaining} of today's allowance left.`;
  }
  if (est.key_source === "user") return `Billed to your own ${est.provider} key.`;
  return `Running on ${est.provider}, which costs nothing.`;
}

/* FR: the cap will stop the run part-way. Said before it starts, not after.
   Pure, for the node-run test. */
function capWarningText(est) {
  if (!est || !est.billed_to_owner || est.cap_remaining == null) return "";
  // What the allowance is counted in is model CALLS, not papers. A summary
  // batch makes one call per paper; a review makes one call however many
  // papers it reads. Comparing against papers told a user with 3 calls left
  // that only 3 of 10 papers could be reviewed, when the one call would read
  // all ten (gate 2026-09-21 finding 3).
  const needed = est.calls != null ? est.calls : est.papers;
  if (est.cap_remaining >= needed) return "";
  if (est.cap_remaining === 0) {
    return "Today's allowance on the shared key is used up. Add your own API " +
           "key in Settings, or try again tomorrow.";
  }
  return `Only ${est.cap_remaining} of these ${est.papers} can run today on ` +
         `the shared key. The rest will be left for tomorrow.`;
}

/* Show the spend dialog and resolve true only if the user agrees. Nothing is
   sent before that (M5.A.3). */
function confirmSpend(title, est) {
  $("spend-title").textContent = title;
  $("spend-count").textContent = `${est.papers} paper(s) selected.`;
  $("spend-estimate").textContent = spendEstimateText(est);
  $("spend-key").textContent = spendKeyText(est);
  const warning = capWarningText(est);
  $("spend-cap").textContent = warning;
  $("spend-cap").classList.toggle("hidden", !warning);
  $("spend-go").disabled = est.billed_to_owner && est.cap_remaining === 0;

  $("spend-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  return new Promise((resolve) => {
    const close = (answer) => {
      $("spend-modal").classList.add("hidden");
      document.body.classList.remove("modal-open");
      $("spend-go").onclick = null;
      $("spend-cancel").onclick = null;
      resolve(answer);
    };
    $("spend-go").onclick = () => close(true);
    $("spend-cancel").onclick = () => close(false);
  });
}

/* M3: summarize every ticked paper, one at a time through the same per-paper
   job the single Summarize button uses. No batch endpoint: the owner-key cap
   is reserved per paper by that route, and a second copy of the cap logic is
   how caps get bypassed.

   Papers that already have a summary are skipped, and the skip is reported.
   One failure does not abandon the rest (P1), and the run says what it did:
   "N of M", never a bare "done". */
async function summarizeChecked() {
  if (!state.activeListId) return;
  // A second click would start a concurrent batch; on a user's own uncapped
  // key the papers still in flight would run and bill twice. startSummary has
  // its own dedup set for the same reason (gate 2026-09-21 finding 4).
  if (state.batchRunning) return;
  const ticked = Array.from(
    $("ref-papers-body").querySelectorAll("input[data-item-id]:checked"))
    .map(cb => String(cb.dataset.itemId));
  if (!ticked.length) { notice("Tick some papers first."); return; }

  const papers = ticked
    .map(id => state.refItems.find(i => String(i.item_id) === id))
    .filter(Boolean)
    .map(i => i.paper || i);

  // The estimate must resolve the SAME credentials the run will use, or the
  // dialog names the wrong payer — a key held only in localStorage looked like
  // the shared key (gate 2026-09-21 finding 1). Sent as a POST body so the key
  // never reaches a URL.
  let est;
  try { est = await api("POST", "/api/usage/estimate", estimateBody(papers.length)); }
  catch (e) { notice(`Could not work out the cost: ${e.message}`); return; }
  if (!await confirmSpend("Summarize checked papers", est)) return;

  const status = $("ref-dl-status");
  status.classList.remove("hidden");
  setBatchRunning(true);
  let done = 0, abstractOnly = 0, skipped = 0, stoppedByCap = false;
  const failures = [];
  try {

  for (const [index, paper] of papers.entries()) {
    status.textContent = `Summarizing ${index + 1} of ${papers.length}…`;
    try {
      const stored = await api("POST", "/api/summaries/lookup", { paper });
      if (stored && stored.source_text !== "abstract") { skipped++; continue; }
    } catch (e) {
      if (e.status !== 404) { failures.push(`${shortTitle(paper)}: ${e.message}`); continue; }
    }
    const outcome = await summarizeOnePaper(paper);
    if (outcome.cap) { stoppedByCap = true; break; }
    if (outcome.ok && outcome.abstractOnly) abstractOnly++;
    else if (outcome.ok) done++;
    else failures.push(`${shortTitle(paper)}: ${outcome.error}`);
  }

  } finally {
    // Released even if a paper threw, or the button stays dead for the session.
    setBatchRunning(false);
  }

  refreshTokenMeter();
  await selectRefList(state.activeListId);
  status.textContent = batchSummaryReport(
    { total: papers.length, done, abstractOnly, skipped, stoppedByCap, failures });
  if (failures.length) notice(status.textContent, "warn");
}

/* One batch at a time, and the buttons show it. */
function setBatchRunning(running) {
  state.batchRunning = running;
  for (const id of ["btn-ref-summarize-checked", "btn-ref-review-checked"]) {
    const button = $(id);
    if (button) button.disabled = running;
  }
}

function shortTitle(paper) {
  return (paper.title || "untitled").slice(0, 60);
}

/* P2: the run reports what it did and what it did not, never a bare "done".
   Pure, for the node-run test. */
function batchSummaryReport({ total, done, abstractOnly = 0, skipped,
                             stoppedByCap, failures }) {
  const parts = [`Summarized ${done} of ${total}`];
  if (abstractOnly) {
    // Named, not folded into the total: the model never read these papers.
    parts.push(`${abstractOnly} kept as abstract only (no full text found)`);
  }
  if (skipped) parts.push(`${skipped} already had a summary`);
  if (failures.length) parts.push(`${failures.length} failed`);
  let text = parts.join("; ") + ".";
  if (stoppedByCap) {
    text += " Today's allowance on the shared key ran out, so the rest were " +
            "not run.";
  }
  if (failures.length) text += " " + failures.slice(0, 3).join("; ");
  return text;
}

/* One paper, start to finish. Resolves {ok} / {error} / {cap} — the cap is its
   own outcome because it means "stop", not "this one failed". */
async function summarizeOnePaper(paper) {
  const local = localSettings();
  const body = { paper, find_by_title: findByTitle() };
  if (local.key) {
    body.api_key = local.key;
    body.provider = local.provider || state.me.provider;
    body.model = local.model || "";
  }
  let job;
  try { job = await api("POST", "/api/summaries", body); }
  catch (e) {
    if (e.status === 429) return { cap: true };
    return { error: e.message };
  }
  const settled = await pollJobUntilSettled(`/api/summaries/${job.job_id}`);
  if (settled.error) return { error: settled.error };
  // FT1: with no full text found the abstract is kept and no model runs.
  // Counting that as "summarized" would tell the user three papers were
  // summarized when the model only read one.
  const result = settled.status.result || {};
  return { ok: true, abstractOnly: result.source_text === "abstract" };
}

/* Poll a background job until it settles. ONE implementation for every batch
   poller, because each hand-written copy drifted: the summary batch was fixed
   to give up on an unreachable server, and the review poller added in the
   same commit was not, so a stuck server became a silent forever-loop that
   also left both batch buttons disabled until a reload (gate 2026-09-21,
   twice). A third copy would drift the same way.

   The rule, matching startSummary: only status 0 — no answer at all — is a
   blip worth retrying (P1). It is announced after three, and given up on at
   POLL_GIVE_UP. Any other error ends the job.

   Resolves { done, status } or { error }. Never rejects, so a caller's
   finally always runs. */
async function pollJobUntilSettled(path, onPhase) {
  let blips = 0;
  for (;;) {
    await new Promise(r => setTimeout(r, POLL_MS));
    let status;
    try { status = await api("GET", path); }
    catch (e) {
      if (e.status !== 0) return { error: e.message };
      blips += 1;
      if (blips === 3) notice("Can't reach the server — still waiting.", "warn");
      if (blips >= POLL_GIVE_UP) {
        return { error: "lost track of this — the server stopped answering" };
      }
      continue;
    }
    blips = 0;
    if (onPhase && status.phase) onPhase(status.phase);
    if (status.status === "done") return { done: true, status };
    if (["error", "cancelled"].includes(status.status)) {
      return { error: status.error || status.status };
    }
  }
}

/* M4: one synthesis across the ticked papers. The preview says what would be
   read and what it would cost before anything is sent — near-exact, because
   the prompt is built from text already stored (M5.A.2). */
async function reviewChecked() {
  if (!state.activeListId) return;
  if (state.batchRunning) return;
  const ticked = Array.from(
    $("ref-papers-body").querySelectorAll("input[data-item-id]:checked"))
    .map(cb => String(cb.dataset.itemId));

  // The "?" stays in the literal so the path is still readable as
  // /api/references/{id}/review-preview by anything parsing this source.
  const query = ticked.length ? `item_ids=${encodeURIComponent(ticked.join(","))}` : "";
  let preview;
  try {
    preview = await api("GET",
      `/api/references/${state.activeListId}/review-preview?${query}`);
  } catch (e) { notice(`Could not work out what to review: ${e.message}`); return; }

  if (!preview.papers) {
    notice("None of these papers have a summary or an abstract stored, so " +
           "there is nothing to review yet.", "warn");
    return;
  }
  // Who pays comes from the same resolution the run uses, for the same reason.
  let payer = {};
  try { payer = await api("POST", "/api/usage/estimate", estimateBody(0)); }
  catch (e) { /* the preview still shows what would be read */ }
  if (!await confirmSpend("Review checked papers",
                          reviewEstimate(preview, payer))) return;

  const status = $("ref-dl-status");
  status.classList.remove("hidden");
  status.textContent = `Reviewing ${preview.papers} paper(s)…`;

  const local = localSettings();
  const body = { list_id: state.activeListId };
  if (ticked.length) body.item_ids = ticked.join(",");
  if (local.key) {
    body.api_key = local.key;
    body.provider = local.provider || state.me.provider;
    body.model = local.model || "";
  }

  // Released in the finally on every path. The guard used to be released by
  // hand at each exit, and a forever-looping poll never reached any of them,
  // leaving both batch buttons disabled until a reload (gate finding 2).
  setBatchRunning(true);
  try {
    let job;
    try { job = await api("POST", "/api/reviews", body); }
    catch (e) { status.textContent = `Review failed: ${e.message}`; return; }

    const settled = await pollJobUntilSettled(
      `/api/reviews/${job.job_id}`, (phase) => { status.textContent = phase; });
    if (settled.error) {
      status.textContent = `Review failed: ${settled.error}`;
      return;
    }
    renderReview(settled.status.result);
    status.textContent = `Reviewed ${preview.papers} paper(s).`;
  } finally {
    setBatchRunning(false);
    refreshTokenMeter();
  }
}

/* The preview in the shape confirmSpend expects. The token count is measured,
   not estimated, so both ends of the "range" are the same figure — which
   spendEstimateText already collapses. Pure, for the node-run test. */
function reviewEstimate(preview, payer = {}) {
  return {
    papers: preview.papers,
    calls: 1,                  // one synthesis, one call, one slot
    low: preview.prompt_tokens,
    high: preview.prompt_tokens,
    exact: true,
    dollars_low: null,
    dollars_high: null,
    provider: payer.provider || "",
    key_source: payer.key_source || "",
    billed_to_owner: !!payer.billed_to_owner,
    cap_remaining: payer.cap_remaining,
  };
}

/* Show a review, with what it was able to read. The basis note is not
   decoration: a reader who cannot see that four of six papers were abstracts
   will take the synthesis as better grounded than it is. */
function renderReview(review) {
  if (!review || !review.review_text) {
    $("ref-review").classList.add("hidden");
    return;
  }
  $("ref-review").classList.remove("hidden");
  $("ref-review-text").textContent = review.review_text;
  $("ref-review-basis").textContent = review.basis_note || "";
  $("ref-review-when").textContent = review.created_at
    ? `Made ${review.created_at.slice(0, 10)} · ${review.model_version || ""}`
    : "";

  const text = leftOutText(review.left_out || []);
  $("ref-review-left-out").textContent = text;
  $("ref-review-left-out").classList.toggle("hidden", !text);
}

/* P2: papers the review could not cover are named, not quietly absent.
   Pure, for the node-run test. */
function leftOutText(leftOut) {
  if (!leftOut.length) return "";
  const names = leftOut.slice(0, 3).map(p => p.title).join("; ");
  const more = leftOut.length > 3 ? `, and ${leftOut.length - 3} more` : "";
  return `Not covered by this review: ${names}${more}. ` +
         `(${leftOut[0].reason})`;
}

/* Load any stored review when a list is opened, so it survives a reload. */
async function loadStoredReview(listId) {
  try {
    const stored = await api("GET", `/api/references/${listId}/review`);
    if (state.activeListId === listId) renderReview(stored);
  } catch (e) {
    $("ref-review").classList.add("hidden");   // 404 = never reviewed
  }
}

/* The open list's name, for the download filename. */
function refListName() {
  const list = (state.refLists || []).find(
    l => String(l.id) === String(state.activeListId));
  return list ? list.name : "";
}

/* ── Wiring ──────────────────────────────────────────────────────────────── */

function wire() {
  // Auth
  $("code-next").addEventListener("click", codeNext);
  $("my-code").addEventListener("keydown", (e) => { if (e.key === "Enter") codeNext(); });
  $("pin-sign-in").addEventListener("click", pinSignIn);
  $("my-pin").addEventListener("keydown", (e) => { if (e.key === "Enter") pinSignIn(); });
  $("my-pin-confirm").addEventListener("keydown", (e) => { if (e.key === "Enter") pinSignIn(); });
  $("use-other-code").addEventListener("click", useOtherCode);
  $("show-legacy").addEventListener("click", () => { gateError(""); showGateStep("legacy-step"); });
  $("hide-legacy").addEventListener("click", () => { gateError(""); showGateStep("code-step"); });
  $("sign-in").addEventListener("click", signIn);
  $("login-pin").addEventListener("keydown", (e) => { if (e.key === "Enter") signIn(); });
  $("show-recover").addEventListener("click", () => showRecoverForm(true));
  $("hide-recover").addEventListener("click", () => showRecoverForm(false));
  $("recover").addEventListener("click", recoverAccount);
  $("recovery-done").addEventListener("click", () => {
    $("recovery-modal").classList.add("hidden");
    $("recovery-code-text").textContent = "";
  });
  $("help-deepseek-toggle").addEventListener("click", () =>
    $("help-deepseek").classList.toggle("hidden"));
  $("sign-out").addEventListener("click", signOut);

  // LLM settings panel
  $("save-key").addEventListener("click", saveKey);
  $("remove-key").addEventListener("click", removeKey);

  // Tabs
  $("tab-search").addEventListener("click", () => switchTab("search"));
  $("tab-filters").addEventListener("click", () => switchTab("filters"));
  $("tab-references").addEventListener("click", () => switchTab("references"));
  $("tab-settings").addEventListener("click", () => switchTab("settings"));

  // Search tab
  $("search-date-range-toggle").addEventListener("change", () => {
    const on = $("search-date-range-toggle").checked;
    $("search-days-wrap").classList.toggle("hidden", on);
    $("search-date-range-wrap").classList.toggle("hidden", !on);
  });
  $("search-filter-select").addEventListener("change", renderFilterRunButtons);
  $("btn-run-filter").addEventListener("click", runSelectedFilter);
  $("run-search").addEventListener("click", () => {
    state.searchLabel = $("q-both").value;
    startSearch({ filter: manualFilter() });
  });
  $("cancel-search").addEventListener("click", cancelSearch);
  $("prev-page").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - PAGE_SIZE);
    loadResults();
  });
  $("next-page").addEventListener("click", () => {
    state.offset += PAGE_SIZE;
    loadResults();
  });
  $("select-all-results").addEventListener("change", (e) => toggleSelectAll(e.target.checked));
  $("btn-save-as-list").addEventListener("click", saveResults);
  $("btn-save-summaries-pdf").addEventListener("click", saveSummariesPdf);

  // Modal
  $("btn-modal-close").addEventListener("click", closeModal);
  $("paper-modal").addEventListener("click", (e) => {
    if (e.target === $("paper-modal")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("paper-modal").classList.contains("hidden")) closeModal();
  });

  // Filters tab
  $("btn-filter-new").addEventListener("click", newFilter);
  $("btn-filter-save").addEventListener("click", saveFilter);
  $("btn-filter-saveas").addEventListener("click", saveFilterAs);
  $("btn-filter-delete").addEventListener("click", deleteFilter);
  $("btn-filter-test").addEventListener("click", testFilter);
  $("filter-date-range-toggle").addEventListener("change", () => {
    const on = $("filter-date-range-toggle").checked;
    $("filter-days-wrap").classList.toggle("hidden", on);
    $("filter-date-range-wrap").classList.toggle("hidden", !on);
  });
  $("btn-add-group").addEventListener("click", () => {
    const groups = collectTextGroups(true);
    groups.push({});
    renderTextGroups(groups);
  });
  $("btn-discover").addEventListener("click", discoverTerms);

  // References tab
  $("btn-ref-new-list").addEventListener("click", newRefList);
  $("btn-ref-delete-list").addEventListener("click", deleteRefList);
  $("btn-ref-remove-selected").addEventListener("click", removeRefSelected);
  $("btn-ref-dl-selected").addEventListener("click", () => downloadRefPdfs(true));
  $("btn-ref-dl-all").addEventListener("click", () => downloadRefPdfs(false));
  $("btn-ref-save-list").addEventListener("click", saveRefList);
  $("btn-ref-summarize-checked").addEventListener("click", summarizeChecked);
  $("btn-ref-review-checked").addEventListener("click", reviewChecked);
  $("select-all-refs").addEventListener("change",
    (e) => toggleSelectAllRefs(e.target.checked));
  $("btn-ref-export-summaries").addEventListener("click", exportRefSummariesPdf);

  // Settings tab
  $("btn-save-default-sources").addEventListener("click", saveDefaultSources);
  $("find-by-title").checked = findByTitle();
  $("find-by-title").addEventListener("change", (e) => {
    try { localStorage.setItem(LS_FIND_BY_TITLE, e.target.checked ? "on" : "off"); } catch (err) {}
  });
}

async function boot() {
  wire();
  // A reason carried across the sign-out reload is used once, then dropped,
  // so it cannot reappear at a later sign-in page in this tab.
  let carried = "";
  try {
    carried = sessionStorage.getItem(SS_GATE_MESSAGE) || "";
    sessionStorage.removeItem(SS_GATE_MESSAGE);
  } catch (err) { /* ignore */ }
  try {
    state.me = await api("GET", "/api/me");
    showApp();
  } catch (e) {
    await showGate(carried || (e.status === 401 ? e.message : ""));
  }
}

document.addEventListener("DOMContentLoaded", boot);
