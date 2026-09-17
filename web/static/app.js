/* BioRx web client.
 *
 * Vanilla JS, no build step: one deployable container. Mirrors the concepts of
 * the desktop app's Search & Browse and Configure screens and adds nothing
 * beyond them.
 */
"use strict";

const PAGE_SIZE = 25;
const POLL_MS = 1500;

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

const state = {
  me: null,
  jobId: null,
  polling: null,
  offset: 0,
  total: 0,
  fetched: 0,
  results: [],
};

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
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

/* ── Sign in ─────────────────────────────────────────────────────────────── */

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
  loadFilters();
}

/* ── Profile and LLM settings ────────────────────────────────────────────── */

function renderMe() {
  const me = state.me;
  const name = me.display_name || "unnamed";
  $("who").textContent = `${name} · ${me.provider} (${me.model || "no model"})`;

  const select = $("key-provider");
  if (!select.options.length) {
    for (const provider of me.available_providers) {
      const option = document.createElement("option");
      option.value = provider;
      option.textContent = provider;
      select.appendChild(option);
    }
  }
  select.value = me.key_source === "user" ? me.provider : select.value || me.provider;

  // Populate model field: show stored preference; hint shows the config default.
  const modelInput = $("preferred-model");
  if (document.activeElement !== modelInput) {
    modelInput.value = me.preferred_model || "";
  }
  $("model-hint").textContent = me.default_model ? `(default: ${me.default_model})` : "";

  const where = {
    user: `Using your own key (…${me.key_last4}).`,
    owner: `Using the shared key. ${me.owner_summaries_remaining} of ` +
           `${me.owner_summaries_cap} summaries left today.`,
    none: "Using a local model; no key needed.",
    missing: "No API key is available. Add yours below to summarize.",
  }[me.key_source] || "";
  $("key-state").textContent = where;

  const disabled = $("byo-disabled");
  if (me.byo_enabled) {
    disabled.classList.add("hidden");
    $("byo").classList.remove("hidden");
  } else {
    disabled.textContent =
      "Personal API keys cannot be stored on this server: " + me.byo_disabled_reason;
    disabled.classList.remove("hidden");
    $("byo").classList.add("hidden");
  }
}

async function saveKey() {
  notice("");
  const keyVal = $("api-key").value.trim();
  const modelVal = $("preferred-model").value.trim();
  try {
    if (keyVal) {
      state.me = await api("PUT", "/api/me/llm-key", {
        provider: $("key-provider").value,
        api_key: keyVal,
        model: modelVal,
      });
      $("api-key").value = "";
    } else if (modelVal !== (state.me.preferred_model || "")) {
      // Model-only change: no key entered, just update the model preference.
      state.me = await api("PUT", "/api/me/llm-model", { model: modelVal });
    }
    renderMe();
    notice("Settings saved.", "ok");
  } catch (e) { notice(e.message); }
}

async function removeKey() {
  notice("");
  try {
    state.me = await api("DELETE", "/api/me/llm-key");
    renderMe();
    notice("Key removed.", "ok");
  } catch (e) { notice(e.message); }
}

/* ── Saved filters ───────────────────────────────────────────────────────── */

async function loadFilters() {
  const list = $("filter-list");
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
    const days = document.createElement("span");
    days.className = "tag";
    days.textContent = `${filter.days_back || 7}d`;
    const run = document.createElement("button");
    run.textContent = "Run";
    run.dataset.filterId = filter.id;
    run.addEventListener("click", () => startSearch({ filter_id: filter.id }));
    li.append(name, days, run);
    list.appendChild(li);
  }
}

/* ── Search ──────────────────────────────────────────────────────────────── */

function manualFilter() {
  return {
    days_back: Number($("q-days").value) || 14,
    text_groups: [{ title: "", abstract: "", both: $("q-both").value }],
    authors: [],
  };
}

async function startSearch(payload) {
  notice("");
  stopPolling();
  state.offset = 0;
  state.results = [];
  state.fetched = 0;
  $("results-card").classList.add("hidden");
  $("sources-failed").classList.add("hidden");
  $("progress-wrap").classList.remove("hidden");
  $("progress").value = 0;
  $("phase").textContent = "Starting…";
  $("run-search").disabled = true;
  $("cancel-search").disabled = false;

  const body = Object.assign({ max_results: Number($("q-max").value) || 200 }, payload);
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

  // "0 matching" alone cannot be told apart from "the sources returned
  // nothing". Say which it was: the sources may have returned plenty that the
  // filter then excluded.
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
    title.appendChild(link);

    const authors = document.createElement("td");
    authors.className = "small";
    authors.textContent = (paper.authors || "").split(";").slice(0, 3).join("; ");

    const date = document.createElement("td");
    date.className = "small";
    date.textContent = paper.date || "";

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

    tr.append(title, authors, date, source, actions);
    body.appendChild(tr);
  }

  const from = state.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + PAGE_SIZE, state.total);
  $("page-label").textContent = `${from}–${to} of ${state.total}`;
  $("prev-page").disabled = state.offset === 0;
  $("next-page").disabled = state.offset + PAGE_SIZE >= state.total;
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

  // Ask whether someone has already summarized this paper. Summaries are shared
  // per paper, so re-running one costs a model call for an answer we have.
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
    // 404 is the normal case: nothing stored yet, so run one.
  }

  let job;
  try {
    job = await api("POST", "/api/summaries", { paper });
  } catch (e) {
    notice(e.message);
    $("summary-meta").textContent = "";
    button.disabled = false;
    button.textContent = "Summarize";
    return;
  }
  $("summary-meta").textContent = `${job.provider} · ${job.model}`;

  const timer = setInterval(async () => {
    let status;
    try { status = await api("GET", `/api/summaries/${job.job_id}`); }
    catch (e) { clearInterval(timer); notice(e.message); return; }

    $("summary-meta").textContent =
      `${job.provider} · ${job.model} — ${status.phase || status.status}`;
    if (["done", "error", "cancelled"].includes(status.status)) {
      clearInterval(timer);
      button.disabled = false;
      button.textContent = "Summarize";
      if (status.status === "done") renderSummary(job, status.result);
      else {
        notice(status.error || "The summary failed.");
        $("summary-meta").textContent = `${job.provider} · ${job.model} — failed`;
      }
      refreshMe();
    }
  }, POLL_MS);
}

function renderStoredSummary(stored) {
  let findings = [];
  try { findings = JSON.parse(stored.key_findings || "[]"); }
  catch (e) { findings = []; }
  renderSummary(null, {
    provider: "stored",
    model: stored.model_version || "",
    key_source: "none",
    key_findings: findings,
    methodology: stored.methodology,
    conclusions: stored.conclusions,
  });
  $("summary-meta").textContent =
    `Already summarized with ${stored.model_version || "an earlier model"} — not re-run.`;
}


function renderSummary(job, result) {
  const body = $("summary-body");
  body.textContent = "";
  if (!result) return;
  $("summary-meta").textContent =
    `${result.provider} · ${result.model} · ${result.key_source} key`;

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
  for (const [label, value] of [["Methodology", result.methodology],
                                ["Conclusions", result.conclusions]]) {
    if (!value) continue;
    const heading = document.createElement("strong");
    heading.textContent = label;
    const p = document.createElement("p");
    p.textContent = value;
    body.append(heading, p);
  }
}

async function refreshMe() {
  try { state.me = await api("GET", "/api/me"); renderMe(); } catch (e) { /* not fatal */ }
}

/* ── Wiring ──────────────────────────────────────────────────────────────── */

function wire() {
  $("sign-in").addEventListener("click", signIn);
  $("access-code").addEventListener("keydown", (e) => { if (e.key === "Enter") signIn(); });
  $("sign-out").addEventListener("click", signOut);
  $("toggle-settings").addEventListener("click",
    () => $("settings").classList.toggle("hidden"));
  $("save-key").addEventListener("click", saveKey);
  $("remove-key").addEventListener("click", removeKey);
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
}

async function boot() {
  wire();
  try {
    state.me = await api("GET", "/api/me");
    showApp();
    // Surface any startup degradation (missing contact email, disabled sources).
    try {
      const health = await api("GET", "/healthz");
      if (health && health.startup_warnings && health.startup_warnings.length) {
        notice("⚠ " + health.startup_warnings.join(" | "), "warn");
      }
    } catch (_) { /* healthz failure is non-fatal */ }
  } catch (e) {
    $("gate").classList.remove("hidden");
  }
}

document.addEventListener("DOMContentLoaded", boot);
