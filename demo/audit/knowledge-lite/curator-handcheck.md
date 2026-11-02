# Curator hand-check (2026-11-02, corpus after deterministic reseed)

10 flags checked: all 5 contradiction candidates + 5 duplicate pairs.

## Contradictions: 0/5 real

All five share an anchor with asymmetric negation but state complementary,
not opposing, claims:

- [211, 244] `~30 revisions`: "unit cannot be observed in current loop" vs
  "owner must name the task for the benchmark" — the second presupposes the
  first. Not opposing.
- [133, 139] `project_id`: single-scope deployment holds one id vs "introduce
  ids now while cheap" — proposal for what the first reports missing.
- [230, 244] `~30 revisions`: benchmark needs the named task vs owner input
  asks to name it — same direction.
- [179, 185] d14 (high): "fund D14 like a feature" vs the D13/D14 mechanism
  description — both pro-rule-authority.
- [233, 246] `expected: pass`: verifier PASS gate vs friction-risk mitigation
  via the `check` fast path — complementary.

Verdict: heuristic contradiction matches on this corpus are review hints, not
flags. Endpoint returns them ranked with `contradictions_mode=review_hints`
unless every kept match is high-confidence (shared D-number) and few.

## Duplicates: 5/5 real

Pairs [1,6], [1,7], [1,113], [1,114], [1,115] — identical title "Hackathon
knowledge check", near-identical demo canary bodies from repeated rehearsal
runs. Merge candidates, correctly flagged.
