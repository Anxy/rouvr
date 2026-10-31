#!/usr/bin/env bash
# Decomposer-lite 3-minute demo (S021 T21.6): four beats, all honest.
# Beat 1: a good split validates. Beat 2: a hollow verifier is rejected.
# Beat 3: touching a pinned path fails the run gate. Beat 4: green
# children plus a red parent report DECOMPOSITION_FAILURE.
# Fixture tests are generated under /tmp (never collected by CI) with
# real bundle shas stamped at run time. Exit 0 only if every beat
# behaves exactly as labelled.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
FIX="$TMPD/demofix" && mkdir -p "$FIX"

cat > "$FIX/test_good.py" <<'EOF'
def test_ok_addition():
    assert 2 + 2 == 5, "not implemented yet"
EOF
cat > "$FIX/test_hollow.py" <<'EOF'
def test_always_true():
    assert True
EOF
cat > "$FIX/test_split.py" <<'EOF'
def test_child_a():
    assert "a".upper() == "A"


def test_child_b():
    assert "b".upper() == "B"


def test_parent_combined():
    assert ("a" + "b").upper() == "aB", "combined behaviour differs from the parts"
EOF

mkroadmap() { # mkroadmap <out> <test-file> <test-name> <allowed-dir>
  python3 - "$1" "$2" "$3" "$4" <<'EOF'
import json, sys, hashlib
out, testfile, testname, allowed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sha = hashlib.sha256(open(testfile, "rb").read()).hexdigest()
doc = {"version": "1.3", "sprint": "S021-DEMO",
       "requirements": [{"id": "R1", "text": "Demo.",
                         "source_ref": "demo",
                         "acceptance": {"test_passes": [f"{testfile}::{testname}", sha]}}],
       "tasks": [{"id": "T1", "title": "Demo task", "type": "build",
                  "allowed_paths": [allowed], "pinned_paths": ["/tmp/demo-pinned.json"],
                  "verifier": {"and": [{"path_absent": ["/tmp/demo-never-created"]},
                                       {"test_passes": [f"{testfile}::{testname}", sha]}]},
                  "depends_on": [], "covers": ["R1"], "verifier_fails_on_head": "assertion"}],
       "coverage_ledger": {"R1": ["T1"]}, "dropped_content": []}
json.dump(doc, open(out, "w"), indent=1)
EOF
}

echo "BEAT 1: a good split validates"
mkroadmap "$TMPD/good.json" "$FIX/test_good.py" test_ok_addition "$FIX"
python3 rouvr/decompose/validator.py "$TMPD/good.json" || { echo "BEAT 1 FAILED: good split rejected"; exit 1; }

echo "BEAT 2: a hollow verifier is rejected"
mkroadmap "$TMPD/hollow.json" "$FIX/test_hollow.py" test_always_true "$FIX"
if python3 rouvr/decompose/validator.py "$TMPD/hollow.json"; then
  echo "BEAT 2 FAILED: hollow verifier accepted"; exit 1
fi

echo "BEAT 3: a pinned-path edit fails the run gate"
GR="$TMPD/gaterepo" && mkdir -p "$GR" && cd "$GR" && git init -q && git config user.email d@d && git config user.name d
cp "$FIX/test_good.py" pinned_test.py && git add -A && git commit -qm base
echo "# touch" >> pinned_test.py
cd "$ROOT"
python3 - "$GR" <<'EOF'
import json, sys
sys.path.insert(0, ".")
from rouvr.decompose.runner import run_gate
r = run_gate({"id": "T", "allowed_paths": ["."], "pinned_paths": ["pinned_test.py"],
              "verifier": {"path_absent": ["nothing-here"]}}, sys.argv[1])
print(json.dumps(r, indent=1)[:400])
sys.exit(0 if r["verdict"] == "fail" and r["pinned_touched"] == ["pinned_test.py"] else 3)
EOF
[ $? -eq 0 ] || { echo "BEAT 3 FAILED: pinned touch passed the gate"; exit 1; }

echo "BEAT 4: green children plus a red parent flag DECOMPOSITION_FAILURE"
CH_A=$(python3 -c "import hashlib;print(hashlib.sha256(open('$FIX/test_split.py','rb').read()).hexdigest())")
python3 - <<EOF
import sys; sys.path.insert(0, ".")
from rouvr.decompose.runner import check_parent
out = check_parent(("test_passes", "$FIX/test_split.py::test_parent_combined", "$CH_A"),
                   [("test_passes", "$FIX/test_split.py::test_child_a", "$CH_A"),
                    ("test_passes", "$FIX/test_split.py::test_child_b", "$CH_A")], ".")
print(out["verdict"], "-", out.get("action", ""))
sys.exit(0 if out.get("verdict") == "DECOMPOSITION_FAILURE" else 4)
EOF
[ $? -eq 0 ] || { echo "BEAT 4 FAILED: non-composing split not flagged"; exit 1; }
echo "demo complete: good split valid; hollow rejected; pinned edit failed; non-composing split flagged"
