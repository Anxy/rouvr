"""Backend reorg batch 8 (2026-08-16): this package's contents were split across
aix_core/runtime/{objective,planner,executor,verification,recovery,governance}/,
aix_core/intelligence/{repository,context}/, and infrastructure/model_gateway/ —
see BACKEND_REORGANIZATION_MANIFEST.md.

objective_coverage.py is intentionally still here (not moved in batch 8): it had
substantial uncommitted, actively-changing edits from outside this reorg at move
time. No code depended on this package's former eager __init__ re-export surface
(verified before retiring it), so this file is now a plain package marker.
"""
