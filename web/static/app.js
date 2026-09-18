/* BioRx web client — full feature parity with the desktop GUI.
 *
 * Vanilla JS, no build step: one deployable container.
 * All paper/author data is set via textContent, never innerHTML (XSS guard).
 */
"use strict";

const PAGE_SIZE = 25;
const POLL_MS   = 1500;

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
  results: [],
  checkedPapers: new Set(),   // canonical_ids of checked search results
  activeTab: "search",
  sources: [],                // from /healthz
  // Filters tab
  filters: [],
  activeFilterId: null,
  filterTestJobId: null,
  filterTestPolling: null,
  // References tab
  refLists: [],
  activeListId: null,
  refItems: [],
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
  const response = await fetch(path, options);
  if (opts && opts.raw) return response;
  let payload = null;
  try { payload = await response.json(); } catch (e) { payload = null; }
  if (!response.ok) {
    const detail = (payload && payload.detail) || `${response.status} ${response.statusText}`;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
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

async function signIn() {
  $("gate-error").classList.add("hidden");
  try {
    state.me = await api("POST", "/api/session", {
      access_code: $("access-code").value,
      display_name: $("display-name").value,
    });
  } catch (e) {
    $("gate-error").textContent = e.message;
    $("gate-error").classList.remove("hidden");
    return;
  }
  showApp();
}

async function signOut() {
  await api("DELETE", "/api/session");
  state.me = null;
  $("app").classList.add("hidden");
  $("gate").classList.remove("hidden");
}

function showApp() {
  $("gate").classList.add("hidden");
  $("app").classList.remove("hidden");
  renderMe();
  loadSources();
  loadSearchFilters();
  restoreActiveTab();
}

/* ── Local key storage ───────────────────────────────────────────────────── */

const LS_KEY      = "biorx_local_key";
const LS_PROVIDER = "biorx_local_provider";
const LS_MODEL    = "biorx_local_model";
const LS_TAB      = "biorx_active_tab";
const LS_DEFAULT_SOURCES = "biorx_default_sources";

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
  const name = me.display_name || "unnamed";
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

/* ── Sources ─────────────────────────────────────────────────────────────── */

async function loadSources() {
  try {
    const health = await api("GET", "/healthz");
    state.sources = (health.sources || []).filter(s => s.enabled);
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
function applyDefaultSources(enabledIds, savedRaw) {
  let saved;
  try { saved = JSON.parse(savedRaw); } catch (e) { return enabledIds.slice(); }
  if (!Array.isArray(saved)) return enabledIds.slice();
  // A saved source the server no longer enables is dropped, not shown.
  const kept = enabledIds.filter(id => saved.includes(id));
  return kept.length ? kept : enabledIds.slice();
}

function defaultSourceIds() {
  let raw = null;
  try { raw = localStorage.getItem(LS_DEFAULT_SOURCES); } catch (e) {}
  return applyDefaultSources(state.sources.map(s => s.id), raw);
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

async function loadSearchFilters() {
  populateCategorySelect("search-category");
  const list = $("search-filter-list");
  list.textContent = "";
  let filters = [];
  try { filters = (await api("GET", "/api/filters")).filters; }
  catch (e) { notice(e.message); return; }

  if (!filters.length) {
    const li = document.createElement("li");
    li.className = "muted small";
    li.textContent = "No saved searches yet.";
    list.appendChild(li);
    return;
  }
  for (const filter of filters) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = filter.name;
    const run = document.createElement("button");
    run.textContent = "Run";
    run.dataset.filterId = filter.id;
    run.addEventListener("click", () => startSearch({ filter_id: filter.id }));
    li.append(name, run);
    list.appendChild(li);
  }
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
  $("results-card").classList.add("hidden");
  $("sources-failed").classList.add("hidden");
  $("progress-wrap").classList.remove("hidden");
  $("progress").value = 0;
  $("phase").textContent = "Starting…";
  $("run-search").disabled = true;
  $("cancel-search").disabled = false;
  $("btn-save-as-list").disabled = true;

  const body = Object.assign(
    { max_results: Number($("q-max").value) || 200,
      source_selection: getSourceSelection($("search-sources")) },
    payload,
  );
  try {
    const job = await api("POST", "/api/searches", body);
    state.jobId = job.job_id;
    state.polling = setInterval(pollSearch, POLL_MS);
    pollSearch();
  } catch (e) {
    notice(e.message);
    searchFinished();
  }
}

async function pollSearch() {
  if (!state.jobId) return;
  let job;
  try { job = await api("GET", `/api/searches/${state.jobId}`); }
  catch (e) { notice(e.message); searchFinished(); return; }

  $("phase").textContent = job.phase || job.status;
  if (job.total > 0) {
    $("progress").max = job.total;
    $("progress").value = job.fetched;
  }
  if (job.sources_failed && job.sources_failed.length) {
    $("sources-failed").textContent =
      `Could not reach: ${job.sources_failed.join(", ")}. Results are incomplete.`;
    $("sources-failed").classList.remove("hidden");
  }
  if (["done", "error", "cancelled"].includes(job.status)) {
    if (job.status === "error") notice(job.error || "The search failed.");
    state.fetched = job.fetched;
    await loadResults();
    searchFinished(job.status);
  }
}

function searchFinished(status) {
  stopPolling();
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
    summarize.textContent = "Summarize";
    summarize.dataset.canonicalId = paper.canonical_id || "";
    summarize.addEventListener("click", () => startSummary(paper, summarize));
    actions.appendChild(summarize);

    tr.append(checkTd, title, authors, date, source, actions);
    body.appendChild(tr);
  }

  const from = state.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + PAGE_SIZE, state.total);
  $("page-label").textContent = `${from}–${to} of ${state.total}`;
  $("prev-page").disabled = state.offset === 0;
  $("next-page").disabled = state.offset + PAGE_SIZE >= state.total;
}

function updateSaveAsListBtn() {
  $("btn-save-as-list").disabled = state.checkedPapers.size === 0;
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

function showSaveListForm() {
  $("save-list-name-wrap").classList.toggle("hidden");
}

async function confirmSaveList() {
  const name = $("save-list-name-input").value.trim();
  if (!name) { notice("Enter a list name."); return; }
  if (!state.jobId) { notice("No search to save."); return; }
  try {
    const saved = await api("POST", `/api/searches/${state.jobId}/save-as-list`, {
      name,
      paper_ids: Array.from(state.checkedPapers),
    });
    if (saved.skipped && saved.skipped.length) {
      notice(`Saved "${name}": ${saved.saved} of ${saved.requested} papers. ` +
             `Could not store: ${saved.skipped.join("; ")}`, "warn");
    } else {
      notice(`Saved "${name}" to References (${saved.saved} papers).`, "ok");
    }
    $("save-list-name-wrap").classList.add("hidden");
    $("save-list-name-input").value = "";
  } catch (e) { notice(e.message); }
}

/* ── Summaries ───────────────────────────────────────────────────────────── */

async function startSummary(paper, button) {
  notice("");
  button.disabled = true;
  button.textContent = "Summarizing…";
  $("summary-card").classList.remove("hidden");
  $("summary-title").textContent = paper.title || "Summary";
  $("summary-meta").textContent = "Starting…";
  $("summary-body").textContent = "";

  try {
    const stored = await api("POST", "/api/summaries/lookup", { paper });
    renderStoredSummary(stored);
    button.disabled = false;
    button.textContent = "Summarize";
    return;
  } catch (e) {
    if (e.status !== 404) {
      notice(e.message);
      button.disabled = false;
      button.textContent = "Summarize";
      return;
    }
  }

  let job;
  try {
    const local = localSettings();
    const summaryBody = { paper };
    if (local.key) {
      summaryBody.api_key  = local.key;
      summaryBody.provider = local.provider || state.me.provider;
      summaryBody.model    = local.model    || "";
    }
    job = await api("POST", "/api/summaries", summaryBody);
  } catch (e) {
    notice(e.message);
    $("summary-meta").textContent = "";
    button.disabled = false;
    button.textContent = "Summarize";
    return;
  }
  $("summary-meta").textContent = `${job.provider} · ${job.model}`;

  const timer = setInterval(async () => {
    let s;
    try { s = await api("GET", `/api/summaries/${job.job_id}`); }
    catch (e) { clearInterval(timer); notice(e.message); return; }

    $("summary-meta").textContent = `${job.provider} · ${job.model} — ${s.phase || s.status}`;
    if (["done", "error", "cancelled"].includes(s.status)) {
      clearInterval(timer);
      button.disabled = false;
      button.textContent = "Summarize";
      if (s.status === "done") renderSummary(job, s.result);
      else {
        notice(s.error || "The summary failed.");
        $("summary-meta").textContent = `${job.provider} · ${job.model} — failed`;
      }
      refreshMe();
    }
  }, POLL_MS);
}

function renderStoredSummary(stored) {
  let findings = [];
  try { findings = JSON.parse(stored.key_findings || "[]"); } catch (e) { findings = []; }
  renderSummary(null, {
    provider: "stored", model: stored.model_version || "",
    key_source: "none", key_findings: findings,
    methodology: stored.methodology, conclusions: stored.conclusions,
  });
  $("summary-meta").textContent =
    `Already summarized with ${stored.model_version || "an earlier model"} — not re-run.`;
}

function renderSummary(job, result) {
  const body = $("summary-body");
  body.textContent = "";
  if (!result) return;
  $("summary-meta").textContent = `${result.provider} · ${result.model} · ${result.key_source} key` +
    (result.full_text && result.full_text !== "used"
      ? ` · from the abstract only (full text ${result.full_text})` : "");
  if ((result.key_findings || []).length) {
    const heading = document.createElement("strong");
    heading.textContent = "Key findings";
    const list = document.createElement("ul");
    for (const finding of result.key_findings) {
      const li = document.createElement("li");
      li.textContent = finding;
      list.appendChild(li);
    }
    body.append(heading, list);
  }
  for (const [label, value] of [["Methodology", result.methodology], ["Conclusions", result.conclusions]]) {
    if (!value) continue;
    const heading = document.createElement("strong");
    heading.textContent = label;
    const p = document.createElement("p");
    p.textContent = value;
    body.append(heading, p);
  }
}

/* ── Paper detail modal ──────────────────────────────────────────────────── */

async function openModal(paper) {
  $("modal-title").textContent = paper.title || "(untitled)";
  $("modal-abstract").textContent = paper.abstract || "(no abstract)";
  $("modal-summary").textContent = "";
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
  // Try to load a stored summary
  try {
    const stored = await api("POST", "/api/summaries/lookup", { paper });
    const div = $("modal-summary");
    div.textContent = "";
    if (stored.key_findings) {
      let findings = [];
      try { findings = JSON.parse(stored.key_findings || "[]"); } catch (e) { findings = []; }
      if (findings.length) {
        const h = document.createElement("strong");
        h.textContent = "Key findings";
        const ul = document.createElement("ul");
        for (const f of findings) {
          const li = document.createElement("li");
          li.textContent = f;
          ul.appendChild(li);
        }
        div.append(h, ul);
      }
    }
  } catch (e) { /* no summary — that's fine */ }
}

function closeModal() {
  $("paper-modal").classList.add("hidden");
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
    state.filterTestPolling = setInterval(pollFilterTest, POLL_MS);
    pollFilterTest();
  } catch (e) { $("filter-test-status").textContent = e.message; }
}

async function pollFilterTest() {
  if (!state.filterTestJobId) return;
  let job;
  try { job = await api("GET", `/api/searches/${state.filterTestJobId}`); }
  catch (e) {
    clearInterval(state.filterTestPolling);
    $("filter-test-status").textContent = e.message;
    return;
  }
  $("filter-test-status").textContent = job.phase || job.status;
  if (["done", "error", "cancelled"].includes(job.status)) {
    clearInterval(state.filterTestPolling);
    state.filterTestPolling = null;
    // Same rule as the main search: an unreachable source makes "0 matched"
    // mean "incomplete", not "this filter finds nothing".
    const failed = (job.sources_failed || []).length
      ? ` Could not reach: ${job.sources_failed.join(", ")}. Results are incomplete.` : "";
    if (job.status === "done") {
      let page;
      try {
        page = await api("GET", `/api/searches/${state.filterTestJobId}/results?limit=50`);
      } catch (e) {
        $("filter-test-status").textContent = `Could not load the results: ${e.message}`;
        return;
      }
      renderFilterTestResults(page.results || []);
      $("filter-test-status").textContent = `${page.total} papers matched.${failed}`;
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
    const failed = (job.sources_failed || []).length
      ? ` Could not reach: ${job.sources_failed.join(", ")}.` : "";
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
  for (const term of terms) {
    const chip = document.createElement("button");
    chip.className = "tag";
    chip.style.cursor = "pointer";
    chip.style.margin = "2px";
    chip.textContent = term;
    chip.addEventListener("click", () => insertDiscoverTerm(term));
    container.appendChild(chip);
  }
}

function insertDiscoverTerm(term) {
  const groups = $("filter-text-groups").querySelectorAll('.text-group input[data-field="both"]');
  if (groups.length) {
    const last = groups[groups.length - 1];
    last.value = last.value ? last.value + ", " + term : term;
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
    renderRefItems();
  } catch (e) { notice(e.message); }
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

    tr.append(checkTd, titleTd, authTd, dateTd, srcTd);
    body.appendChild(tr);
  }
  updateRefCheckedCount();
}

function updateRefCheckedCount() {
  const checked = $("ref-papers-body")
    ? $("ref-papers-body").querySelectorAll("input:checked").length : 0;
  $("ref-checked-count").textContent = `${checked} selected`;
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
    const title = ((item.paper || item).title || `paper ${itemId}`).slice(0, 60);
    const paperId = (item.paper || item).paper_id;
    try {
      const resp = await api("GET", `/api/references/${state.activeListId}/pdf/${paperId}`, undefined, { raw: true });
      if (resp.ok) {
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `paper-${paperId}.pdf`;
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

async function exportRefCsv() {
  if (!state.activeListId) return;
  const resp = await api("GET", `/api/references/${state.activeListId}/export.csv`, undefined, { raw: true });
  if (!resp.ok) { notice(`Export failed: ${resp.status}`); return; }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "references.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/* ── Wiring ──────────────────────────────────────────────────────────────── */

function wire() {
  // Auth
  $("sign-in").addEventListener("click", signIn);
  $("access-code").addEventListener("keydown", (e) => { if (e.key === "Enter") signIn(); });
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
  $("run-search").addEventListener("click", () => startSearch({ filter: manualFilter() }));
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
  $("btn-save-as-list").addEventListener("click", showSaveListForm);
  $("btn-confirm-save-list").addEventListener("click", confirmSaveList);

  // Modal
  $("btn-modal-close").addEventListener("click", closeModal);
  $("paper-modal").addEventListener("click", (e) => {
    if (e.target === $("paper-modal")) closeModal();
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
  $("btn-ref-export-csv").addEventListener("click", exportRefCsv);

  // Settings tab
  $("btn-save-default-sources").addEventListener("click", saveDefaultSources);
}

async function boot() {
  wire();
  try {
    state.me = await api("GET", "/api/me");
    showApp();
  } catch (e) {
    $("gate").classList.remove("hidden");
  }
}

document.addEventListener("DOMContentLoaded", boot);
