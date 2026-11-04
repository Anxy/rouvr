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

### (insert, unscheduled) — Moment A: self-description, recorded live
No `.webm` captured for this beat yet — capture-pending; the runs below
are saved as SSE transcripts (project `supervisor/slice-e-demo-probe`),
so screen-capture fresh from the chat page or overlay the transcript.
Admin sends `who are you and what can you do`. Turns #1–2 (messages
`c4ea844af9b4e64a`, `ac93ec0f9c5a51fa`): reply "I am an AI assistant. …"
with badge `Logged`, classification `chat` (0.99), `created: 0`. Caption
(honest — keep it): "Genuine live turns — and a genuine gap at the time:
the reply names none of the 26 manifest tools." Then turn #3
(`fr-self-001`, classification `chat` 0.98, badge `Logged`, `created: 0`,
2026-11-03, model `muse-spark-1.3-contributor`): reply "I am the Rouvr
supervisor for tenant 'supervisor', project 'slice-e-demo-probe'. …
Tools: workhub_create_tenant, workhub_create_project, … rouvr_schedule,
rouvr_schedule_ops." — all 26 live tool names. Caption: "After the
identity-block fix, the same question answers from live sources." Do not
paraphrase either reply into a capability list; the before/after is the
point.

### (insert, unscheduled) — Moment C: plan + pipeline (recorded live, offer path capture-pending)
Recorded 2026-11-03, no `.webm` — capture-pending for footage; SSE
transcripts only, so overlay the transcript or re-capture from the chat
page. Admin sends `propose a plan for simple auth` (message `fr-plan-001`,
project `supervisor/slice-e-demo-probe`). The model classified it `task`
(0.85, `feature`) — not the ask path — so per contract the plan
auto-converted: entry `plan-simple-auth-plan` (`knowledge:1202@v1`,
`verified: 1`) plus slice #153, head task #152 and step tasks #154–157
(`created: 6`, badge `Task #152`), every `chat:create` receipt naming
source `fr-plan-001` and model `muse-spark-1.3-contributor`. Caption
(honest — keep it): "The live model chose the imperative branch, so the
offer gate never appeared — the offer shape below is contract-verified
only." The offer→Convert recording is still pending: when billing
recovers, capture fresh and expect exactly these shapes
(`tests/test_chat_intake.py`, 17 passed) — do not stage them. Offer reply:
"Shall I turn this into an actionable plan? It becomes {n} tasks:
{titles}." with badge `Saved: plan-<slug>`, `offer` carrying
`plan_entry_id` / `plan_slug` / `task_titles`, task count unchanged.
Convert reply: `Converted plan '<slug>' into Slice #<id> <title>: {n}
task(s).` plus inline cards, badge `Task #N` on every created reply.

Pipeline beat (same run, terminal overlay, typed): the four step-task
workflows (`rouvr-task-supervisor-154-2eb47c28f26c1607` through
`-157-…`) all fail-closed at assign within a minute — claimed, then
`blocked` with `escalation` evidence `escalate:attempt-1:E_CONTRACT`.
Caption: "No exec spec and no hidden suite exist for chat-created tasks,
so the pipeline parks them before any code is touched — no workspace, no
coder session, no grant. Fail closed, exactly as designed." The reviewer
beat (skill `knowledge:1199@v1`) is capture-pending with reason: no diff
ever existed to review. Model-flakiness note for the edit: the run
alternated HTTP 402 with empty-200 turns across repeated sends; keep one
`retrying` overlay at most — the flakiness is context, not the story.

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
