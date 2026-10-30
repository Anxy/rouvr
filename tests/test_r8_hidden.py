"""R8a/R8: hidden-test store, criterion parsing, cheat-scan. Pure logic, no network."""
import pytest

from rouvr.cheatscan import scan_diff
from rouvr.errors import Code, RouvrError
from rouvr.hiddentests import (assert_coder_clean, fetch_hidden_suite, hidden_literals,
                               parse_criterion_outcomes, write_hidden_suite)

HIDDEN = {"test_hidden.py": "def test_c1_returns_200():\n    assert check() == 'zx-AlPhA-42'\n"}


def test_write_fetch_roundtrip_and_lock(tmp_path):
    m = write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="test_author")
    assert m.tree_sha256
    d, m2 = fetch_hidden_suite(tmp_path, "t1")
    assert m2 == m and (d / "test_hidden.py").exists()
    with pytest.raises(RouvrError) as e:  # locked at creation
        write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="coder")
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


@pytest.mark.parametrize("task_id", ["../escape", "a/b", "a\\b", "..x"])
def test_hidden_suite_rejects_traversal_task_id(tmp_path, task_id):
    with pytest.raises(RouvrError):
        write_hidden_suite(tmp_path, task_id=task_id, files=HIDDEN, created_by="x")
    with pytest.raises(RouvrError):
        fetch_hidden_suite(tmp_path, task_id)
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("manifest", ["{not json", '{"unexpected": 1}', '{"task_id": "t1"}', "42"])
def test_fetch_corrupt_manifest_is_evidence_error(tmp_path, manifest):
    d = tmp_path / "t1"
    d.mkdir()
    (d / "MANIFEST.json").write_text(manifest)
    with pytest.raises(RouvrError) as e:
        fetch_hidden_suite(tmp_path, "t1")
    assert e.value.code is Code.EVIDENCE


def test_fetch_missing_hidden_file_is_evidence_error(tmp_path):
    write_hidden_suite(tmp_path, task_id="t1", files=HIDDEN, created_by="test_author")
    (tmp_path / "t1" / "test_hidden.py").unlink()
    with pytest.raises(RouvrError) as e:
        fetch_hidden_suite(tmp_path, "t1")
    assert e.value.code is Code.EVIDENCE


def test_coder_clean(tmp_path):
    m = write_hidden_suite(tmp_path / "hidden", task_id="t1", files=HIDDEN, created_by="test_author")
    d, _ = fetch_hidden_suite(tmp_path / "hidden", "t1")
    ws = tmp_path / "ws"
    assert_coder_clean(ws, d, m)  # missing workspace is clean
    ws.mkdir()
    (ws / "app.py").write_text("print('hi')\n")
    assert_coder_clean(ws, d, m)
    (ws / "test_hidden.py").write_text("# lookalike name, different bytes\n")
    with pytest.raises(RouvrError) as e:
        assert_coder_clean(ws, d, m)
    assert e.value.code is Code.EVIDENCE
    (ws / "test_hidden.py").unlink()
    (ws / "notes.txt").write_bytes((d / "test_hidden.py").read_bytes())  # same bytes, other name
    with pytest.raises(RouvrError):
        assert_coder_clean(ws, d, m)


def test_coder_clean_same_size_different_bytes_passes(tmp_path):
    m = write_hidden_suite(tmp_path / "hidden", task_id="t1", files=HIDDEN, created_by="test_author")
    d, _ = fetch_hidden_suite(tmp_path / "hidden", "t1")
    ws = tmp_path / "ws"
    ws.mkdir()
    hidden = (d / "test_hidden.py").read_bytes()
    (ws / "other.py").write_bytes(b"#" * len(hidden))  # size gate passes it, bytes differ: clean
    assert_coder_clean(ws, d, m)


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


def test_scan_diff_flags_deleted_method_test():
    diff = "+++ b/tests/test_a.py\n@@ -1,2 +1,1 @@\n-    def test_method(self):\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["DELETED_TEST"]


def test_scan_diff_flags_ellipsis_stub():
    diff = "+++ b/tests/test_a.py\n@@ -1 +1,2 @@\n+def test_new():\n+    ...\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["EMPTY_TODO"]


def test_scan_diff_flags_deleted_file_and_async_tests():
    diff = ("--- a/tests/test_gone.py\n+++ /dev/null\n@@ -1,4 +0,0 @@\n"
            "-def test_old():\n-    pass\n-async def test_async_old():\n-    pass\n")
    flags = scan_diff(diff, hidden_literals=frozenset())
    assert [(f.rule, f.file) for f in flags] == [("DELETED_TEST", "tests/test_gone.py")] * 2


def test_scan_diff_flags_bare_pytest_xfail():
    diff = "+++ b/tests/test_a.py\n@@ -1 +1,2 @@\n+def test_new():\n+    pytest.xfail('later')\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_conftest_and_multiline_pytestmark():
    diff = ("+++ b/conftest.py\n@@ -1 +1,3 @@\n+pytestmark = [\n+    pytest.mark.skip(reason='x'),\n+]\n")
    flags = scan_diff(diff, hidden_literals=frozenset())
    assert [(f.rule, f.file) for f in flags] == [("SKIP", "conftest.py")]


@pytest.mark.parametrize("line", ["collect_ignore = ['test_x.py']", "collect_ignore_glob = ['test_*.py']"])
def test_scan_diff_flags_collect_ignore(line):
    diff = f"+++ b/tests/conftest.py\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


@pytest.mark.parametrize("cfg,line", [
    ("pytest.ini", "addopts = --deselect tests/test_x.py::test_y"),
    ("pyproject.toml", 'addopts = "--ignore=tests/test_x.py"'),
    ("setup.cfg", "addopts = --ignore tests/test_x.py"),
    ("tox.ini", "commands = pytest --deselect tests/test_x.py::test_y"),
])
def test_scan_diff_flags_pytest_config_deselect(cfg, line):
    diff = f"+++ b/{cfg}\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_ignores_deselect_outside_pytest_config():
    diff = "+++ b/app.py\n@@ -1 +1 @@\n+X = '--deselect foo'\n"
    assert scan_diff(diff, hidden_literals=frozenset()) == []


@pytest.mark.parametrize("cfg,line", [
    ("pytest.ini", 'addopts = -k "not test_x"'),
    ("pytest.ini", "addopts = -k 'not test_x'"),
    ("pyproject.toml", 'addopts = "--keyword not test_x"'),
    ("setup.cfg", "addopts = --keyword=not test_x"),
])
def test_scan_diff_flags_negative_keyword(cfg, line):
    diff = f"+++ b/{cfg}\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_collection_modifyitems_filter():
    diff = ("+++ b/tests/conftest.py\n@@ -1 +1,3 @@\n"
            "+def pytest_collection_modifyitems(session, config, items):\n"
            "+    items[:] = [i for i in items if 'test_x' not in i.nodeid]\n")
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]
    diff2 = ("+++ b/tests/test_a.py\n@@ -1 +1,3 @@\n"
             "+def pytest_collection_modifyitems(items):\n"
             "+    items = [i for i in items if i.name != 'test_x']\n")
    assert [f.rule for f in scan_diff(diff2, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_collection_modifyitems_readonly_clean():
    diff = ("+++ b/tests/conftest.py\n@@ -1 +1,3 @@\n"
            "+def pytest_collection_modifyitems(session, config, items):\n"
            "+    print(len(items))\n")
    assert scan_diff(diff, hidden_literals=frozenset()) == []


@pytest.mark.parametrize("hook", ["pytest_ignore_collect", "pytest_collection", "pytest_collection_finish"])
def test_scan_diff_flags_other_collection_hooks(hook):
    diff = f"+++ b/tests/conftest.py\n@@ -1 +1,2 @@\n+def {hook}(*a):\n+    return True\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_collection_hooks_in_plugin_py():
    diff = ("+++ b/myplugin.py\n@@ -1 +1,3 @@\n"
            "+def pytest_collection_modifyitems(session, config, items):\n"
            "+    items[:] = []\n")
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]
    diff2 = "+++ b/myplugin.py\n@@ -1 +1 @@\n+def pytest_ignore_collect(path, config):\n"
    assert [f.rule for f in scan_diff(diff2, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_multiline_toml_negative_keyword():
    diff = ("+++ b/pyproject.toml\n@@ -1 +1,4 @@\n"
            '+addopts = [\n+    "-k",\n+    "not hidden",\n+]\n')
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


@pytest.mark.parametrize("mark", ["skip", "xfail"])
def test_scan_diff_flags_pytestmark(mark):
    diff = f"+++ b/tests/test_a.py\n@@ -1 +1 @@\n+pytestmark = pytest.mark.{mark}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


# round 13
@pytest.mark.parametrize("body", ["kept[:] = []", "del kept", "kept.clear()", "kept.remove(items[0])", "kept.pop()"])
def test_scan_diff_flags_aliased_items_filter(body):
    diff = ("+++ b/tests/conftest.py\n@@ -1 +1,4 @@\n"
            "+def pytest_collection_modifyitems(session, config, items):\n"
            "+    kept = items\n"
            f"+    {body}\n")
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_indexed_args_slice_assign():
    diff = ("+++ b/tests/conftest.py\n@@ -1 +1,2 @@\n"
            "+def pytest_collection_modifyitems(*args):\n"
            "+    args[2][:] = []\n")
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


@pytest.mark.parametrize("line", [
    "    pytest.mark.skip(reason='x'),",
    "@pytest.param(1, marks=pytest.mark.xfail(reason='x'))",
    "    item.add_marker(pytest.mark.skip(reason='x'))",
])
def test_scan_diff_flags_bare_mark(line):
    diff = f"+++ b/tests/test_a.py\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_comment_split_negative_keyword():
    diff = ("+++ b/pytest.ini\n@@ -1,3 +1,3 @@\n"
            '+addopts = "-k",\n'
            " # unchanged comment\n"
            '+    "not hidden",\n')
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


@pytest.mark.parametrize("line", ["pytest.importorskip('mod')", "    self.skipTest('nope')"])
def test_scan_diff_flags_importorskip_and_skiptest(line):
    diff = f"+++ b/tests/test_a.py\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


def test_scan_diff_flags_dunder_test_false():
    diff = "+++ b/tests/test_a.py\n@@ -1 +1 @@\n+__test__ = False\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]


@pytest.mark.parametrize("cfg,line", [
    ("pytest.ini", "testpaths = tests_hidden"),
    ("pytest.ini", "norecursedirs = .* hidden"),
    ("pyproject.toml", 'testpaths = ["tests_hidden"]'),
    ("setup.cfg", "norecursedirs = hidden"),
])
def test_scan_diff_flags_config_testpaths(cfg, line):
    diff = f"+++ b/{cfg}\n@@ -1 +1 @@\n+{line}\n"
    assert [f.rule for f in scan_diff(diff, hidden_literals=frozenset())] == ["SKIP"]
