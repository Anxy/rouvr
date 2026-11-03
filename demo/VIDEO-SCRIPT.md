# Demo video script — Rouvr end-to-end (3 minutes)

Real runs, real wall-clock. Footage: `e2e/recordings/demo-chat1.webm`,
`demo-approve1.webm`, `demo-chat2.webm`, `demo-approve2.webm` (local files,
not committed). No footage is staged or re-enacted; the approve clips show
the actual clicks that wrote the cited receipts. Timestamps below are mm:ss
in the final cut.

## Cast (honest captions — use these exact lines on screen)

- Supervisor chat: live Meta Model API turn (non-streaming, one real block).
- Coder: `muse exec` headless run on the Muse Code subscription (OAuth).
- Reviewer: a Muse Code sub-agent — NOT the Rouvr reviewer persona; it read
  the diff and spot-ran the tests.
- Hidden acceptance + probe: written by the orchestrating agent BEFORE the
  coder ran; the coder never saw them.
- Approver: the human owner clicking Approve in the web UI.

## Beats

### 0:00–0:10 — Built during the hackathon
Montage of the workspace (login → board). Caption: "Built during the
hackathon: cookie auth, supervisor chat, web workspace, approve guard —
test suite green (594 passed), secret scan clean." No recording needed;
screen-capture fresh or reuse the first seconds of `demo-chat1.webm`.

### 0:10–0:35 — Chat creates a real slice
Roll `demo-chat1.webm` (17.4 s, trim pauses). Caption: "Live model turn.
Slice #1: add GET /v1/version with alias + test. Receipts on every task
name the chat message as source." Freeze on the inline task cards.

### 0:35–1:05 — Coder builds, reviewer approves, suite verifies
No footage exists of the headless coder — say so. Terminal overlay, typed
(never screen-scraped live):
`muse exec --prompt-file e2e/demo_coder_brief.md` → "34 passed",
"review: APPROVE", "contracts attached". Caption: "Headless coder, then an
independent review pass, then the repo's own contract tests. Nothing here
is a mock." Keep this beat to 30 s; the work is proven by the next beat.

### 1:05–1:30 — Owner approves under the guard
Roll `demo-approve1.webm`. Caption: "Owner clicks Approve → done. The guard
only allows this from needs_review with a passing verify on the current
head — and every task now carries its own acceptance contract."
Approve receipts 17, 18, 19 (all `web-`-keyed: written by the clicks).

### 1:30–2:00 — Second slice, and a real failure
Roll `demo-chat2.webm` (17.8 s, trim pauses). Caption: "Slice #2: add a
build field. The hidden acceptance suite was frozen before the coder
started." Then terminal overlay: `demo2_probe_nosubprocess.py FAILED —
git rev-parse spawned per request`. Caption: "A genuine miss: the first
implementation shelled out to git on every request. The coder never saw
this probe coming."

### 2:00–2:35 — Fix, re-verify, approve
Terminal overlay: `BUILD resolved once at import` → "4 passed" (acceptance
+ probe) → "review: APPROVE". Caption: "Fix round, same task, no new
tickets." Roll `demo-approve2.webm`. Caption: "Owner approves tasks 6–8.
Receipts 39, 40, 41 — each key starts with web-, written by the click you
just watched."

### 2:35–3:00 — Close
Board shot: slices #1 and #2 all done. Caption: "Two slices, two headless
coders, two independent reviews, one real failure caught and fixed, every
approval behind the verify guard. Branches merged to rouvr-development;
main untouched." End card: repo URL + "suite green".

## Editor notes

- Total plotted runtime: ~3:00 with trims; raw footage is ~42 s of UI plus
  overlays. Do not loop or slow down footage to fill time.
- If a beat runs long, cut from 0:35–1:05 (narration), never from the
  failure beat — the failure is the proof.
- Receipt ids are in `demo-approve1.json` / `demo-approve2.json` next to
  the footage; verify each cited receipt against the ledger before final
  render.
