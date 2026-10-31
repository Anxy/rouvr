# Roadmap

Shipped items are checked (see `docs/system/ROUVR-SPRINT-INDEX.json`, 19/19 PASS); the rest below is planned, not done.

## Before hackathon submission (Nov 2026)
- [ ] Run the full stack (`make up`) from a clean checkout and document it
- [ ] Demo against the live Meta Model API (contract compile, narration, judge)
- [ ] Recorded end-to-end demo video

## Next
- [x] Move `demo/morning_report.py` onto Temporal workflows — done via `OpsTaskWorkflow` (S018)
- [ ] Real mail delivery with provider message-id readback
- [ ] WorkHub-lite: principals/auth, leases, multi-tenancy
- [ ] Core backend WorkHub client speaking the WorkHub-lite API
- [ ] Get the full `core/backend/tests` suite green without private services
- [ ] Rename internal `aix`/`hyeve_aix` modules to `rouvr`

## Later
- [ ] Business-operations task packs beyond software delivery
- [ ] Hardened compose / deployment

## Website vision (rouvr.ai), not yet in this repo
- [ ] "A pack of specialists, chosen for the job": parallel researchers, writers, developers and reviewers under one supervising colleague
- [ ] "A colleague who understands your world": a shared understanding you can inspect and revise
- [ ] "Your tools, put to work": authorised actions through connected systems with checked receipts
- [ ] "A clear view of what needs you": a judgement / in-progress / waiting / ready-for-review view
