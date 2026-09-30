# QA Gate — Following an http:// redirect as https:// (doi.org → bioRxiv DOI links)

- **Date:** 2026-09-29
- **Range reviewed:** `origin/main..HEAD` (caller-supplied, commit d8e83d4)
- **Reviewer:** learning-qa failure-pattern sweep, run directly by the gate author
  against the materialized diff and the changed files read in full context.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1625 passed, 2 skipped**
  (76.5 s; 4 warnings, all Starlette deprecation notices). No failures.

RANGE:       origin/main..HEAD (caller-supplied; single commit d8e83d4).
COMMITS:     1 commit:
             d8e83d4 fix(fetch): follow an http:// redirect as https:// (doi.org sends DOIs to http)
APPLICABLE:  Project P8 (SSRF — this is SSRF-sensitive code, re-audited in full).
             P5 (sibling robustness — the shared hop loop's other callers).
             P19 (producer/consumer drift — the https-candidate contract).
CHECKED:     P8 (SSRF), P5, P19. Read src/safe_fetch.py in full; traced every
             caller of fetch_pdf / fetch_html / https_candidate (src/fulltext.py,
             agents/monitor.py, web/routes_references.py, web/routes_summaries.py,
             src/paper_meta.py). Ran an adversarial probe of https_candidate over
             urljoin output for userinfo/port/scheme/scheme-relative edge cases.
NOT COVERED: out-of-scope families per learning-qa.md: concurrency/races;
             auth/authz; injection & dependency/supply-chain; API-contract
             compatibility beyond P19. The live doi.org reproduction is recorded
             in docs/cycles/2026-09-29_browser-run.md (checked with curl on
             2026-09-29); not re-run here.

---

## What the change does

`src/safe_fetch.py` `_fetch_public` (the hop loop shared by `fetch_pdf` and
`fetch_html`) previously did, on a redirect:

    url = urllib.parse.urljoin(url, location)

so a redirect whose Location was `http://…` hit the next hop's `parsed.scheme !=
"https"` check and was refused with "URL must use https". doi.org 302s many DOIs to
`http://biorxiv.org/lookup/doi/…`, so open copies behind DOI links were refused.

The change is one line:

    url = https_candidate(urllib.parse.urljoin(url, location))

`https_candidate` (pre-existing, introduced in 3809778, already used by the three
PDF callers on the *initial* URL) rewrites a leading `http://` to `https://` and
returns any other URL unchanged. The next hop then goes through the unchanged
per-hop pipeline: scheme check → `resolve_public` → `pinned_get`.

---

## Each check, verified

1. **Every hop is still resolved and checked by `resolve_public`, and pinned to its
   IP — confirmed.** The loop body is unchanged except for the one URL rewrite.
   Each hop still re-parses, checks `scheme == "https"`, calls
   `resolve_public(hostname, port)`, and issues `get(url, ip=…)` over the checked
   addresses. `test_public_redirect_is_followed_and_rechecked` and
   `test_connection_is_pinned_to_a_checked_address` still pin this.

2. **No request is ever sent over plain http — confirmed.** Two independent layers:
   (a) the per-hop `parsed.scheme != "https"` guard raises `FetchRefused` before
   any I/O, and (b) `pinned_get` always constructs an
   `urllib3.HTTPSConnectionPool` and connects over TLS to the pinned IP, verifying
   the certificate against the URL's hostname. The upgrade can only turn an
   `http://` string into an `https://` string; it cannot make the pool plain.

3. **Non-http(s) schemes are still refused — confirmed.** `https_candidate` only
   rewrites a literal `http://` prefix; `ftp:`, `file:`, `gopher:`, `javascript:`,
   `data:` come through unchanged and are refused by the scheme check. Pinned by
   `test_redirect_to_another_scheme_is_refused` (parametrized ftp/file/gopher) and
   the probe below (javascript/data also confirmed).

4. **Scheme-relative `//host` is still safe — confirmed by probe.**
   `urljoin("https://doi.org/10.1/x", "//169.254.169.254/latest")` already yields
   `https://169.254.169.254/latest` before `https_candidate` runs, so the host is
   resolved by `resolve_public` and refused as link-local. No path introduces a
   plain-http or unchecked hop.

5. **Redirect loops still hit MAX_REDIRECTS — confirmed.** The hop counter
   (`for _hop in range(MAX_REDIRECTS + 1)`) and the trailing
   `raise FetchFailed("More than 5 redirects.")` are untouched.
   `test_too_many_redirects` still passes.

6. **userinfo/port tricks cannot reach internal addresses — confirmed by probe.**
   - `http://user@169.254.169.254:80/…` → `https://user@…`, host = 169.254.169.254
     (userinfo separated); `resolve_public` refuses the link-local address before
     any request. The `:80` port does not alter the host check.
   - `http://169.254.169.254:80@public.example/x` → host = `public.example`, the
     metadata address lands in the *userinfo*, which `pinned_get` never uses for
     the connection or the Host header (`parsed.hostname`, userinfo excluded —
     pinned by `test_host_header_never_carries_userinfo`). The connection goes to
     public.example's resolved address, TLS-verified against public.example.
   - An IP-literal redirect is still refused via `resolve_public` (the DNS stand-in
     resolves the literal to itself, then `is_public_address` rejects it) — pinned
     by `test_redirect_to_http_internal_address_is_refused`, which also asserts the
     internal address was **never requested** (`get.calls == [first hop only]`).

7. **`fetch_html` is affected only safely — confirmed.** It shares `_fetch_public`,
   so its redirect hops now upgrade http→https and are re-checked identically.
   Its initial URL is still https-only (the scheme check is unchanged; callers
   `scrape_abstract_from_url`/`recover_abstract` do not pre-upgrade, so an http
   initial URL is still refused, exactly as before). `test_fetch_html_applies_the_same_rules_and_decodes`
   still passes.

8. **The references PDF proxy (`web/routes_references.py`) is affected only safely —
   confirmed.** `proxy_pdf` already applies `https_candidate` to the initial URL
   (line 264, unchanged); it now also benefits from the redirect upgrade. Both
   paths terminate in the same checked `fetch_pdf`. No proxy-specific bypass.

---

## Findings

None at medium or higher severity.

- **Non-blocking · observation — the upgrade preserves an explicit port.**
  `https_candidate("http://host:80/x")` → `https://host:80/x`, so TLS is attempted
  on port 80 rather than defaulting to 443. This fails closed (connection error →
  `FetchFailed`), never degrades to plain http, and does not arise for the
  production case (doi.org's `http://biorxiv.org/lookup/doi/…` carries no explicit
  port). Recorded, not a defect.

The applicable patterns (P8 SSRF, P5, P19) are satisfied, the full suite is green,
and the adversarial probe confirms no hop is unchecked, no request is plain-http,
and no scheme/userinfo/port/loop edge reaches an internal address.

---

## Verdict: APPROVED
