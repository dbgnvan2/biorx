# QA gate — chunk 5 (single-page client, container image, deployment docs), 2026-09-15

RANGE: origin/main..HEAD
COMMITS: 1 — ae2ade6 feat(web): single-page client, container image, and deployment docs
METHOD: adversarial security + deploy-ability review of the materialized diff plus the
  current tree. Standards at ~/.claude/standards/{learnings,security}.md. Backend was
  approved in docs/cycles/2026-09-15_chunk4-qa-gate.md; this gate reviews the SPA,
  the Dockerfile/railway.json, .env.example, the README deploy docs, and the two new
  test files, per the five review focuses.
TESTS: /opt/homebrew/bin/pytest tests/ -q -> 398 passed, 8 skipped, 2 warnings, exit code 0.
VERDICT: REJECTED

APPLICABLE (of P1-P35): P19, P25, P26, P27, P29, P32
  (most handled — see findings; the two blockers are not pattern-class defects but
  deploy/operator failures this chunk's own files introduce).

CHECKED:
  - Session cookie: httponly=True, samesite="lax", secure default True (auth.py:47-53),
    no domain (host-only). Not readable by page scripts, not sent on cross-site POSTs.
    The access code and display name travel in a POST body, never a query string, so
    they never reach a URL, referrer, or access log.
  - API key: sent PUT /api/me/llm-key as a JSON body over HTTPS, cleared from the
    input on success (app.js:757), never in localStorage/URL/referrer; server returns
    only last4 (routes_session.py _me) and stores Fernet-ciphertext (crypto.py). No
    log line or traceback carries the raw key (covered in chunk 4, re-confirmed).
  - Referrer: rel="noopener noreferrer" on the title and PDF links (app.js:923, 946).
  - Client rendering: textContent for every paper field (title, authors, date, source,
    summary findings/method/conclusions). No innerHTML/outerHTML/insertAdjacentHTML/
    document.write. display_name and byo_disabled_reason also textContent-only.
  - Dockerfile COPY paths all exist (requirements-web.txt, src/, web/, agents/,
    llm_config.yaml, sources_config.yaml, filters.json — verified on disk).
  - requirements-web.txt is separate from requirements.txt (no PyQt6 in the image),
    pins are versioned (S7). llm_config.yaml carries no key literals (api_key_env only).
  - .env is gitignored (.gitignore:40) before any accidental commit.
  - Frontend contract tests are bidirectional and exact (see F-notes): element-id check
    runs both directions (missing id AND orphan id), and the API-path check is pinned by
    an exact 9-path set that doubles as a self-check of the parser.
  - Test-provability scan of the two new files: every test body has an assert and can
    fail by mutation; none returns a value. (Gaps noted in F6/F7.)

NOT COVERED:
  - Actual docker build/run — no daemon on this machine; the README itself records
    "the first build is the first real test of it". F1 and F2 are the specific risks
    that first real build/run would surface.
  - Live provider calls remain mocked (unchanged from chunk 4).
  - Browser behaviour (click-through, keyboard entry) was driven manually per the
    README checklist, not in CI.

RANKED FINDINGS
===============

F1 (MEDIUM, deploy blocker risk) — non-root user + runtime volume = unwritable /data.
  File: Dockerfile:14-15 (useradd/chown then USER biorx) with no runtime chown.
  The image chowns /data to uid 10001 at BUILD time. A volume mounted at /data at
  RUNTIME (Railway's documented step) shadows that directory; the mount's ownership is
  whatever the platform gives it, and image-time chown does not transfer. Only Docker
  named-volume copy-up (empty volume copies image content + ownership) rescues this, and
  that is platform-specific — Railway does not guarantee it. If the mounted volume is
  root-owned, `Database.__init__` (src/db.py:97) `mkdir(parents=True, exist_ok=True)`
  is a silent no-op but the first write to /data/biorxiv.db raises PermissionError, and
  the app fails on startup. The README's "confirm filters/summaries survive redeploy"
  checklist would catch this only AFTER a failed start.
  Fix: a root entrypoint that `chown biorx /data` then `exec`s the app as biorx (gosu or
  su), OR document that the volume must be pre-owned by uid 10001. Running as root is not
  the answer — that discards the non-root hardening this chunk deliberately added.
  Confidence: medium-high that it is a genuine risk; the deciding variable is Railway's
  mount behaviour, which no one has verified.

F2 (MEDIUM, operator mislead) — .env.example ships DATA_DIR=/data active, breaking the
  documented local run.
  File: .env.example:62 (`DATA_DIR=/data`, uncommented) vs README.md "Run it locally"
  (`cp .env.example .env` then `source .env`).
  The local default is ~/preprints/biorxiv.db (src/db.py:25). But sourcing the copied
  .env overrides DATA_DIR to /data, and on macOS `mkdir(/data)` raises PermissionError,
  so a developer following the README literally gets a startup crash. The deploy-only
  value is mixed into the file the README tells developers to source as-is ("fill in
  ACCESS_CODE at minimum").
  Fix: comment out DATA_DIR (and the other deploy-only vars) so the local default
  applies, moving them to a clearly-marked deploy section; or have the local-run
  instructions explicitly unset DATA_DIR. KEY_ENC_SECRET and SESSION_SECRET are correctly
  documented as "required/empty, never auto-generated" — those are fine.

F3 (LOW) — no .dockerignore: a filled .env is sent to the build context.
  `docker build .` transmits the whole context, so a developer's filled .env (ACCESS_CODE,
  SESSION_SECRET, KEY_ENC_SECRET, provider keys) goes to the builder/daemon. It is NOT
  copied into a layer (no `COPY .env`), so the image is clean — but the secret rides the
  build context. Add a .dockerignore listing .env, .env.*, .git, etc.

F4 (LOW, defense-in-depth) — href assigned from API data without scheme validation, no CSP.
  File: web/static/app.js:921 (`link.href = paper.url || paper.source_url`) and :944
  (`pdf.href = paper.pdf_url`). These are the only external-data sinks that are not
  textContent; a `javascript:`/`data:` URL would execute on click. Today's adapters build
  https URLs and psyarxiv/unpaywall pull from reputable services, so there is no
  demonstrated exploit — but the guard test `test_the_client_never_builds_markup_from_paper_data`
  (`"innerHTML" not in source`) is blind to it and its docstring overstates ("uses
  textContent throughout"). Fix: allowlist http/https at href assignment (or validate the
  scheme server-side per S2) and add a CSP header.

F5 (LOW) — the placeholder access code is accepted as a live credential.
  web/deps.py:65-70 warns only when ACCESS_CODE is EMPTY; `ACCESS_CODE=change-me`
  (the shipped value) is a valid gate. An operator who copies and deploys without editing
  ships a guessable shared secret. Fix: refuse the literal placeholder (or any value the
  app knows is a default) at startup.

F6 (LOW, test quality) — the "derive env vars from code" test silently misses the vars
  read through an indirection.
  tests/web/test_deploy_files.py `_env_vars_read_by_the_code()` regexes only
  `os.environ.get("LITERAL")`, but the security-relevant reads are indirect:
  KEY_ENC_SECRET via crypto.ENV_VAR, ANTHROPIC_API_KEY/DEEPSEEK_API_KEY via
  self.api_key_env, ANTHROPIC_MODEL/DEEPSEEK_MODEL via model_env (llm_config.py:53,112).
  The test's stated promise ("read from the call sites, not a hand-kept list") is false
  for exactly the five vars that matter most; the guarantee currently rests on
  test_the_required_variables_are_all_present's hand-kept tuple. Not a current hole (all
  are documented), but removing KEY_ENC_SECRET from .env.example would go undetected by
  the "derived" check (P19 corollary).

F7 (LOW, test quality) — two weak assertions.
  tests/web/test_frontend_wiring.py:625 asserts `count('rel = "noopener noreferrer"') >= 2`
  — a floor (P29); a third link added without noreferrer would not fail it. Should be `== 2`.
  tests/web/test_deploy_files.py test_data_lives_on_a_volume_path_not_in_the_image asserts
  only `"DATA_DIR=/data" in text`, which proves the env var is set, not that data is
  excluded from the image (its name overclaims).

VERDICT RATIONALE
  Tests are green (398 passed, exit 0), the cookie/key/referrer posture is sound, and the
  frontend contract tests are genuinely bidirectional and exact. But this chunk's headline
  deliverable is "a container that actually runs and docs that don't mislead," and both
  medium findings sit exactly there: F1 is an unverified, concrete write-permission failure
  on the documented volume mount, and F2 makes the documented local-run path crash on the
  primary development platform. Per the loop's stop condition ("stop when a pass produces
  nothing of medium or higher"), this chunk is not approved. F3-F7 go to the backlog.

---

## Fix-loop 1 — re-gate on origin/main..HEAD (2 commits)

RANGE: origin/main..HEAD
COMMITS: 2 — ae2ade6 feat(web): single-page client, container image, and deployment docs;
          5bcab3f fix(deploy): runtime volume ownership, local-run env, and URL scheme checking
TESTS: /opt/homebrew/bin/pytest tests/ -q -> 419 passed, 8 skipped, 2 warnings, exit code 0.
VERDICT: REJECTED

F2/F3/F4/F5 and the test rewrites land; the one that fails is the headline fix for F1.

(a) ENTRYPOINT — BROKEN (blocker). docker-entrypoint.sh:15 guards the chown with

    if [ ! -O "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]; then chown -R "$APP_USER" "$DATA_DIR" …

  -O and -w test the CURRENT effective user (root — this branch only runs as root), not
  the app user the directory is being prepared for. In the failure mode the entrypoint's
  own comment names ("usually owned by root"), root both owns (-O true) and can write (-w
  true) the volume, so `! -O || ! -w` is `false || false` = false and the chown is
  skipped. The build-time chown was removed as useless, so nothing makes /data writable by
  uid 10001, and `exec gosu biorx` then starts an app that dies on its first write with
  PermissionError. The chown only fires when root does NOT own the directory — i.e. when
  the mount already belongs to some other uid and the chown is a no-op or a stale-uid
  repair. Dead code exactly where it matters.

  Verified by construction: dash (the container's /bin/sh) supports -O as a real owner
  test, and the exact guard expression against a directory owned by the runner returns
  "chown branch SKIPPED" — the structural analogue of root running against a root-owned
  mount:

    dash -c '[ -O / ]; echo $?'            -> 1  (runner uid 501, / is root-owned)
    dash -c '[ -O "$1" ]' _ <self-owned>  -> 0
    guard on <self-owned>:                 "chown branch SKIPPED"

  Forced non-root uid: `id -u != 0` skips the whole block and `exec "$@"` directly. The
  comment claims "let the app's own startup check report clearly", but no such check
  exists — web/app.py /healthz reports config only, and src/db.py:96-97 (mkdir then
  sqlite3.connect) surfaces a raw OperationalError traceback, not a clear message.

  chown-fails path: `chown … 2>/dev/null || { echo WARNING }` swallows the failure and
  falls through to gosu, so a read-only volume also ends in a later write crash.

  Fix: test the TARGET user, not root — `if ! gosu "$APP_USER" test -w "$DATA_DIR"; then
  chown -R …; fi` — or chown unconditionally in the root branch (idempotent, root-only).
  No test covers this (see (e)).

(b) safeUrl — PASS. Returns url.href (the canonical serialization) after allowlisting
  http/https, so the DOM never receives the raw input and parser-differential tricks are
  closed. Exercised in node v26 against the hostile battery:
    javascript: / JavaScript: / "  javascript:" / data: / vbscript: / file: -> "" (rejected)
    //evil.com/x -> https://evil.com/x            (inherits origin scheme; legit https, not a vector)
    java<TAB>script: -> "" ; java<VT>/<FF>script: -> same-origin https path (dead link,
      percent-encoded, not executable)
    %6a%61…:alert(1), javascr%69pt:, javascript%3A -> same-origin https path (scheme scan is
      on raw bytes; "%" is not a scheme char, so no encoded scheme becomes javascript:)
    <NUL>/<U+2028> prefixes -> "" or same-origin path, never an executable scheme
    https://user:pass@evil.com -> allowed (https). Embedded credentials do not change the
      scheme; still a user-initiated https navigation, not code execution. Noted, not a
      blocker for the stated threat model.

(c) .dockerignore — PASS. Excludes .env / .env.* (with !.env.example re-include), *.db,
  *.log, .git, tests/, docs/, gui.py, requirements.txt, *.xlsx, preprints/. None of the
  Dockerfile COPY sources (requirements-web.txt, src/, web/, agents/, llm_config.yaml,
  sources_config.yaml, filters.json, docker-entrypoint.sh) match an ignore pattern, so the
  context is pruned without breaking the build; the stray untracked *.xlsx is also excluded.

(d) README vs .env.example — PASS. DATA_DIR is commented out (.env.example:64), so the
  README's `cp .env.example .env && source .env` resolves to ~/preprints and no longer
  crashes on mkdir(/data). Remaining live values are safe to source: SESSION_SECRET= and
  KEY_ENC_SECRET= are empty (per-process secret / disabled personal-key storage, both
  documented), LLM_PROVIDER=anthropic has empty owner keys, and ACCESS_CODE=change-me is
  refused until edited — matching "fill in ACCESS_CODE at minimum". Deploy path stays
  consistent: the Dockerfile ENV sets DATA_DIR=/data and README step 2 mounts the volume
  there.

(e) tests — PARTIAL. The rewrites are genuine: test_safeurl_behaviour runs the client's
  own safeUrl in node, test_the_placeholder_access_code_is_not_a_live_credential builds a
  real context and asserts 401, and the comment/echo strippers are guard-the-guarded. But
  the two entrypoint tests only prove the chown TEXT exists —
  test_the_entrypoint_makes_the_mounted_volume_writable regexes `chown -R "$APP_USER"
  "$DATA_DIR"` and test_the_app_process_does_not_run_as_root checks "gosu". Neither runs
  the guard, so the suite is green while the entrypoint is broken. This is the P26
  fix-commit pattern: the F1 fix moved the defect from "no chown" to "a chown that never
  runs", and no test distinguishes them.

VERDICT RATIONALE
  safeUrl, .dockerignore, and the local-run docs check out by construction, and the test
  rewrites are real. But the one medium finding whose fix was supposed to make the
  container actually start against a mounted volume does not work: the chown is guarded by
  a condition that is false precisely when it is needed, and no test covers it. A Railway
  deploy with the documented root-owned volume still fails on first write. Reject; re-fix
  the entrypoint (test the target user's writability, or chown unconditionally) and add a
  test that runs the guard logic rather than grepping for the word "chown".

---

## Fix-loop 2 — re-gate on origin/main..HEAD (3 commits)

RANGE: origin/main..HEAD
COMMITS: 3 — ae2ade6 feat(web): single-page client, container image, and deployment docs;
          5bcab3f fix(deploy): runtime volume ownership, local-run env, and URL scheme checking;
          28364b3 fix(deploy): the volume chown now runs in the case it exists for
TESTS: /opt/homebrew/bin/pytest tests/ -q -> 424 passed, 8 skipped, 2 warnings, exit code 0.
VERDICT: REJECTED

(a) guard no longer inverted — PASS, verified by construction. Sourced the real entrypoint
  (ENTRYPOINT_SOURCE_ONLY=1) and called needs_chown against a live directory under both cases,
  with gosu stubbed as identity (cannot drop uid on this Mac) so the REAL `test -w` reads the
  REAL permission bits:

    chmod 0755 (writable)  -> needs_chown -> SKIP
    chmod 0500 (unwritable)-> needs_chown -> CHOWN

  The chown now fires exactly when the app user cannot write, and is skipped when the volume is
  already right. The raw `test -w` exit codes (0 on 0755, 1 on 0500) prove the test itself tracks
  the bits, so the stub is faithful. This is the inverse of the old `[ ! -O ] || [ ! -w ]`, which
  asked about root and returned SKIP precisely on the root-owned mount it existed for.

(b) failure paths exit non-zero and do not start the app — PASS, with one blocker on the success
  path. Driven the actual entrypoint:

    non-root branch (real, uid 501):
      unwritable dir (chmod 0500) -> FATAL "not writable by uid 501", rc=1, no "started"
      writable   dir (chmod 0700) -> "started", rc=0
    root branch (PATH-stubbed id/gosu/chown, ENTRYPOINT_SOURCE_ONLY=1):
      chown fails              -> FATAL "could not give biorx ownership", names uid 10001, rc=1
      chown ok, still unwritable -> FATAL "still not writable by biorx", rc=1
      chown ok, now writable   -> *** "exec: run_as_app: not found", rc=127 ***

  BLOCKER (P26, fix-commit regression): docker-entrypoint.sh:46 is `exec run_as_app "$@"`, and
  run_as_app is a shell FUNCTION. `exec` cannot run a shell function — confirmed on this machine
  in both dash (the container's /bin/sh, Debian slim) and bash --posix: `exec f ...` -> "f: not
  found", exit 127. The previous version correctly had `exec gosu "$APP_USER" "$@"`; the
  restructure moved the gosu call into a function wrapper that is only safe at the non-exec call
  site (needs_chown). The result: the container starts as root (no USER instruction), chowns
  correctly, then crashes at the privilege drop instead of launching uvicorn — the same
  "container cannot start" failure this whole fix sequence exists to close, reintroduced one line
  later. The failure paths are right; the happy path is the one that breaks.

  Why the suite is green despite this: no test exercises the root branch's drop. The three new
  guard tests source the entrypoint and call needs_chown (never main), and the two "starts /
  refuses to start" tests run the non-root branch (uid 501). And test_the_app_process_does_not_run
  _as_root (test_deploy_files.py:92) asserts `'exec run_as_app "$@"' in body` — a test that greps
  for the broken line and blesses it. This is the exact defect the previous gate (e) flagged about
  the OLD guard: a test that proves a string is present, not that the behaviour works.

(c) the new tests catch the old inversion — PASS. Restored the old guard
  `[ ! -O "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]` into needs_chown and ran the guard tests:
    2 failed, 1 passed. test_the_entrypoint_chowns_when_the_app_user_cannot_write gets SKIP
    instead of CHOWN, and test_the_entrypoint_tests_writability_as_the_target_user_not_as_root
    fails on the missing "run_as_app test -w". Both fail for the right reason; the tests run the
    decision rather than grepping for the word "chown". Reverted the edit afterwards.

(d) no other build-time ownership assumption — PASS. The only remaining build-time chown is
  Dockerfile:33 `chown -R biorx /app /data`. /app is the code directory baked into the image (not
  a volume), so that ownership is real and correct; the /data half is a harmless no-volume
  fallback that the entrypoint re-verifies at runtime rather than relying on. There is no `USER`
  instruction (the entrypoint must start as root to chown, then drop). README deploy steps (155,
  170) and railway.json name the volume and the persistence check but never instruct the operator
  to pre-chown or rely on image ownership. Consistent.

(e) no earlier approved behaviour regressed — PASS (suite-level). Full run: 424 passed, 8
  skipped, 2 warnings, exit 0. The 419 green tests from fix-loop 1 still pass; the +5 are the new
  guard/db tests. No functional regression surfaced by the suite — but the suite is blind to the
  (b) blocker, so "green" does not mean "the container starts".

VERDICT RATIONALE
  The guard is genuinely fixed and genuinely tested — (a), (c) are airtight, and the failure paths
  in (b) are correct. But the restructure that fixed the guard broke the privilege drop one line
  later: `exec run_as_app` cannot run a shell function in dash, so the root branch — the path a
  Railway deploy actually takes — exits 127 instead of starting uvicorn. That is the same
  container-cannot-start failure as the original F1, now caused by the fix itself, and a test
  greps for the broken line so the suite stays green. Fix: change line 46 to `exec gosu
  "$APP_USER" "$@"` (drop the function at the exec site; keep run_as_app for the needs_chown call
  site), and add a test that runs the root branch's main() end-to-end — with PATH-stubbed id/gosu
  /chown — asserting it execs the app (rc 0, "started"), not merely that the text exists. Then
  re-sweep the fix commit, since that is where the last three regressions have all landed.

---

## Fix-loop 3 — re-gate on origin/main..HEAD (4 commits)

RANGE: origin/main..HEAD
COMMITS: 4 — ae2ade6 feat(web): single-page client, container image, and deployment docs;
          5bcab3f fix(deploy): runtime volume ownership, local-run env, and URL scheme checking;
          28364b3 fix(deploy): the volume chown now runs in the case it exists for;
          a236f1f fix(deploy): simplify the entrypoint; test the path a deployment takes
TESTS: /opt/homebrew/bin/pytest tests/ -q -> 427 passed, 8 skipped, 2 warnings, exit code 0.
VERDICT: APPROVED

All three historical defects are fixed and — the thing missing in every prior pass — actually
run end to end. Verified by construction under /bin/dash (the container's real shell), not
macOS /bin/sh, so the results are faithful to what a Debian-slim deploy executes.

(a) root branch under /bin/dash, all four cases — PASS. Drove the real entrypoint with
  id/gosu/chown stubbed (re-implemented from scratch, not the test helper) and APP command
  `echo APP-STARTED`:
    1. volume already writable        -> rc 0, "APP-STARTED", no "taking ownership"
    2. root-owned, chown fixes        -> "taking ownership" then "APP-STARTED", rc 0 (deploy path)
    3. chown fails                    -> rc 1, "could not chown", no start
    4. chown ok but still unwritable  -> rc 1, "still not writable", no start
  The two success paths start the app; the two failure paths refuse and name the cause. The
  non-root branch also run for real against a chmod 0500 directory: rc 1, "not writable by uid
  501", no start; and a writable directory starts rc 0. (First harness run flagged case 3 as a
  failure — that was a harness bug, a marker file leaking between cases; the pytest suite uses a
  fresh tmp_path per test. Re-isolated each case and all four passed.)

(b) `exec gosu` really replaces the process — PASS, by PID. Ran the entrypoint as a dash
  subprocess and gave gosu a stub that records its own $$ and $PPID, then execs the app:
    dash entrypoint PID = 34502; stub gosu reported GOSU_PID=34502, GOSU_PPID=<harness>.
  gosu's PID equals the dash PID (image replaced) and its PPID is the harness, not the dash
  (not a child). Confirmed replacement, not a fork. The app still started (rc 0) after the drop.

(c) fourth-defect hunt — one LOW finding, nothing higher.
  - set -e interactions: probed in dash. A standalone `needs_chown && fatal` that short-circuits
    (volume now writable) does NOT exit the shell under set -e — it falls through to the exec.
    `! gosu test -w` negation is safe; `chown || fatal` runs fatal rather than being pre-empted;
    the unguarded `mkdir -p` fails hard rather than silently starting. All four behaviours are
    what the script relies on.
  - `$@` survives: ran `main "$@"` -> `exec gosu "$APP_USER" "$@"` -> gosu `shift` -> `exec "$@"`
    with `sh -c 'printf "<%s>\n" "$@"' _ "two words" third`; printed `<two words>` and `<third>`
    exactly. Quoting: every expansion is double-quoted; no `$*`, no unquoted parameter; POSIX-only
    (no `local`), so dash-safe.
  - signal handling: the chain entrypoint(PID1) -> exec gosu -> gosu execve -> sh -c "exec
    uvicorn" -> exec uvicorn preserves PID 1 end to end, so uvicorn receives SIGTERM/SIGINT
    directly. The only theoretical gap is a signal landing in the ~millisecond pre-exec chown
    window, where a PID-1 shell ignores it by kernel convention — standard for shell entrypoints,
    covered by the stop grace period, not a defect.
  - FINDING F8 (LOW, diagnostic mislead): fatal()'s second line hardcodes "mount the volume
    writable by $APP_USER" (= biorx), but the non-root branch fails on a DIFFERENT uid — the one
    the platform enforces. Confirmed: run as uid 501 against a chmod 0500 dir, stderr reads
    "FATAL /data is not writable by uid 501" then "mount the volume writable by biorx". The first
    line names the true cause; the hint tells an operator to chown to 10001 when the platform
    wants 501, so a hint-only reader is actively misled. Functional behaviour (refuse vs start) is
    correct. LOW — goes to backlog with the earlier F3-F7. Fix: parameterise the hint by the
    target (uid $(id -u) in the non-root branch, "$APP_USER" in the root branch).

(d) the new tests fail if any of the three historical defects is restored — PASS, by mutation.
  Restored each defect into the entrypoint, ran tests/web/test_deploy_files.py, confirmed red,
  then restored byte-identical (git diff empty afterwards):
    defect 1 (no runtime chown):        5 failed (2 guard + 3 root-branch)
    defect 2 (root-access inversion):   5 failed (same set)
    defect 3 (exec of a shell function):3 failed (process_does_not_run_as_root + 2 start-path)
  Each failure is for the right reason, and the "refuses to start" tests stay green under defect 3
  because those paths exit before the broken exec — so the discriminating power is exactly where
  it belongs.

(e) no earlier approved behaviour regressed — PASS. Full suite green (427 passed, exit 0), up from
  424 in fix-loop 2 by the net +3 (four root-branch tests added, the brittle
  test_the_entrypoint_fails_loudly_rather_than_starting_unwritable removed). The removed test
  counted "FATAL" occurrences — a brittle structural assertion beside a real behavioural test; the
  four end-to-end tests prove what it approximated. git diff on docker-entrypoint.sh is empty
  after the mutation exercise.

VERDICT RATIONALE
  The three defects this loop exists to close are closed, and this time the suite proves it:
  the root branch — the path a Railway deploy takes — is executed with stubbed id/gosu/chown
  across all four cases, and each of the three historical defects turns those tests red when
  reintroduced. The privilege drop is a real exec, not a fork, and the whole exec chain keeps
  uvicorn at PID 1 for correct signal delivery. Per the loop's stop condition, a pass that
  produces nothing of medium or higher is approved; the only finding is F8, a LOW diagnostic hint
  that misleads in the non-root branch while the first line of the same message states the truth.
  Approved; F8 to the backlog.


