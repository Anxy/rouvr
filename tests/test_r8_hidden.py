"""R8a/R8: hidden-test store, criterion parsing, cheat-scan. Pure logic, no network."""
import pytest

from rouvr.cheatscan import scan_diff
from rouvr.errors import Code, RouvrError
from rouvr.hiddentests import (assert_builder_clean, fetch_hidden_suite, hidden_literals,
                               parse_criterion_outcomes, write_hidden_suite)

HIDDEN = {"test_hidden.py": "def test_c1_returns_200():\n    assert check() == 'zx-AlPhA-42'\n"}


def test_write_fetch_roundtrip_and_lock(tmp_path):
    m = write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="test_author")
    assert m.tree_sha256
    d, m2 = fetch_hidden_suite(tmp_path, "t1")
    assert m2 == m and (d / "test_hidden.py").exists()
    with pytest.raises(RouvrError) as e:  # locked at creation
        write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="builder")
    assert e.value.code is Code.CONTRACT


def test_write_rejects_empty_and_bad_names(tmp_path):
    with pytest.raises(RouvrError):
        write_hidden_suite(tmp_path, task_id="t", files={}, created_by="x")
    with pytest.raises(RouvrError):
        write_hidden_suite(tmp_path, task_id="t", files={"../evil.py": "x"}, created_by="x")
    with pytest.raises(RouvrError):
        write_hidden_suite(tmp_path, task_id="t", files={"notes.txt": "x"}, created_by="x")


def test_fetch_detects_tamper_and_missing(tmp_path):
    write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="test_author")
    (tmp_path / "t1" / "test_hidden.py").write_text("def test_c1_x():\n    assert True\n")
    with pytest.raises(RouvrError) as e:
        fetch_hidden_suite(tmp_path, "t1")
    assert e.value.code is Code.EVIDENCE
    with pytest.raises(RouvrError) as e:
        fetch_hidden_suite(tmp_path, "nope")
    assert e.value.code is Code.EVIDENCE


def test_builder_clean(tmp_path):
    m = write_hidden_suite(tmp_path / "hidden", task_id="t1", files=HIDDEN, created_by="test_author")
    d, _ = fetch_hidden_suite(tmp_path / "hidden", "t1")
    ws = tmp_path / "ws"
    assert_builder_clean(ws, d, m)  # missing workspace is clean
    ws.mkdir()
    (ws / "app.py").write_text("print('hi')\n")
    assert_builder_clean(ws, d, m)
    (ws / "test_hidden.py").write_text("# lookalike name, different bytes\n")
    with pytest.raises(RouvrError) as e:
        assert_builder_clean(ws, d, m)
    assert e.value.code is Code.EVIDENCE
    (ws / "test_hidden.py").unlink()
    (ws / "notes.txt").write_bytes((d / "test_hidden.py").read_bytes())  # same bytes, other name
    with pytest.raises(RouvrError):
        assert_builder_clean(ws, d, m)


def test_hidden_literals(tmp_path):
    d = tmp_path / "h"
    d.mkdir()
    (d / "a.py").write_text("X = 'zx-AlPhA-42'\nY = 'ok'\nZ = 'has space here'\nN = '200'\n")
    assert hidden_literals(d) == frozenset({"zx-alpha-42"})


def test_parse_criterion_outcomes():
    out = ("test_hidden.py::test_c1_returns_200 PASSED\n"
           "test_hidden.py::test_c1_edge_case FAILED\n"
           "test_hidden.py::test_c2_happy ERROR\n"
           "test_hidden.py::test_c3_ok PASSED\n"
           "1 failed, 1 passed, 1 error\n")
    assert parse_criterion_outcomes(out) == {"c1": False, "c2": False, "c3": True}
    assert parse_criterion_outcomes("no tests ran\n") == {}


DIFF = """\
+++ b/tests/test_a.py
@@ -1,3 +1,6 @@
+@pytest.mark.skip(reason="flaky")
+from unittest.mock import patch
+    pass
+    except Exception:
+    # TODO: real assert
-def test_old_thing
+++ b/app.py
@@ -10,2 +10,3 @@
+RESULT = "zx-alpha-42"
+from unittest.mock import Mock
"""


def test_scan_diff_flags_all_rules():
    rules = {(f.rule, f.file) for f in scan_diff(DIFF, hidden_literals=frozenset({"zx-alpha-42"}))}
    assert ("SKIP", "tests/test_a.py") in rules
    assert ("MOCK", "tests/test_a.py") in rules
    assert ("EMPTY_TODO", "tests/test_a.py") in rules
    assert ("BROAD_EXCEPT", "tests/test_a.py") in rules
    assert ("DELETED_TEST", "tests/test_a.py") in rules
    assert ("LITERAL", "app.py") in rules
    assert ("MOCK", "app.py") not in rules  # test-file rules don't fire on prod files


def test_scan_diff_clean():
    diff = "+++ b/app.py\n@@ -1 +1,2 @@\n+def add(a, b):\n+    return a + b\n"
    assert scan_diff(diff, hidden_literals=frozenset({"zx-alpha-42"})) == []
