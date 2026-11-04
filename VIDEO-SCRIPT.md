# Rouvr — demo video script

Live stack: ledger `http://127.0.0.1:8221`, web `http://127.0.0.1:3000`.
Model: Meta `muse-spark-1.3-contributor`. Recordings: `e2e/recordings/`.

## Hook (15 s)

Login at `/workspace/login`: the rouvr.ai mark with **rouvr.** and
**Make it happen.** Board shows real chain state, never mock data.

## Ask (60 s)

Chat, project `Test-1`, message: **"Prepare a plan for simple auth"**.
The assistant asks one clarifying question
("What stack, user types, and login methods should the simple auth plan
cover?"). Answer it in one line. The turn saves the plan entry and shows
`Saved: plan-<slug>` plus the **Convert / Edit / Keep** offer —
nothing is created yet.

## Plan + approve (60 s)

Planner → open the plan thread: **Status: draft**, slice, tasks, criteria.
Click **Approve plan** → **"Approved — slice #N with K tasks created."**
Re-clicking the same plan reports **"Already materialized — same slice,
no duplicate tasks."** Flip to the board: the slice and its kids are there.

## Execute (60 s)

Execute view for the plan: honest **"Waiting for a worker"** banner, then
**Dispatch** with a worker name → **"dispatched 2 task(s) · assigned 2."**
Groups update live: **Working now / Up next / Completed**.

## Verify (60 s)

Drive one kid: attempt → verdict **fail** → state `failed` → reopen →
reclaim → attempt → verdict **pass** → review → **done**. The Completed
group shows it; the results hold both verdicts.

## Knowledge + Meta (45 s)

Knowledge page lists the plan entry (`verified` pill). The entry shows the
raw JSON body, Trust block, Archive / Pin / New version. Sealing: approve
authors hidden suites through the Meta model (~30 s each); every task gets
a `seal:authored` receipt with `duration_s`, `suite_sealed` or
`seal_pending` evidence, and the sealed pill on its detail page. A 402 or
an unparseable suite fails closed to pending — retried with
`POST /v1/tasks/{id}/seal`.

## Close (15 s)

Every leg is a receipt in the ledger — no separate reviewer persona, no
mock data. **Rouvr. Make it happen.**
