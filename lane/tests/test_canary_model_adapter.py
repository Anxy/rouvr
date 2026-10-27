import hashlib
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

PATH = Path(__file__).parents[1] / "tools" / "canary_model_adapter.py"
SPEC = importlib.util.spec_from_file_location("canary_model_adapter", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

OK_BODY = (
    "thinking aloud\n```result\n"
    '{"status": "succeeded", "summary": "did the thing", '
    '"sha": "abc123", "evidence": ["e1"]}\n```\n'
)


class _FakeProc:
    """Scripted stand-in for subprocess.Popen."""

    def __init__(self, script, out=b"", pid=424242):
        self._script = list(script)
        self._out = out
        self.pid = pid
        self.returncode = None
        self.killed = False
        self.kwargs = {}

    def poll(self):
        rc = self._script.pop(0) if self._script else self.returncode
        if rc is not None:
            self.returncode = rc
            handle = self.kwargs.get("stdout")
            if self._out and handle is not None and not handle.closed:
                handle.write(self._out)
                self._out = b""
        return rc

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = -9


def _spawn(proc):
    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        proc.kwargs = kwargs
        return proc

    fake_popen.calls = calls
    return fake_popen


def _run(proc, tmp_path, out=None, **kw):
    if out is None:
        out = OK_BODY.encode()
    proc._out = out
    params = {
        "cwd": str(tmp_path),
        "timeout_s": 30,
        "max_tokens": 4000,
        "log_path": str(tmp_path / "model.log"),
        "popen": _spawn(proc),
    }
    params.update(kw)
    return M.run_headless_model("codex", "do the thing", **params)


def test_codex_argv_is_exact_headless_shape():
    assert M.build_argv("codex", "do it") == [
        "codex",
        "exec",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "do it",
    ]


def test_claude_argv_is_headless_print_json():
    assert M.build_argv("claude", "do it") == [
        "claude",
        "-p",
        "--output-format",
        "json",
        "do it",
    ]


def test_construction_refusals_fail_closed_before_spawn(tmp_path):
    spawned = []

    def no_spawn(*args, **kwargs):
        spawned.append(True)
        raise AssertionError("must not spawn")

    for backend, prompt, kw in [
        ("gemini", "do it", {}),
        ("codex", "   ", {}),
        ("codex", "do it", {"timeout_s": 0}),
        ("codex", "do it", {"timeout_s": -5}),
        ("codex", "do it", {"max_tokens": 0}),
        ("codex", "do it", {"cwd": str(tmp_path / "missing")}),
        ("codex", "do it", {"log_path": None}),
    ]:
        params = {
            "cwd": str(tmp_path),
            "timeout_s": 30,
            "max_tokens": 100,
            "log_path": str(tmp_path / "x.log"),
            "popen": no_spawn,
        }
        params.update(kw)
        try:
            M.run_headless_model(backend, prompt, **params)
        except M.AdapterError:
            pass
        else:
            raise AssertionError(f"refusal expected for {kw!r}")
    assert not spawned


def test_success_returns_parsed_structured_result(tmp_path):
    for backend in ("codex", "claude"):
        proc = _FakeProc([None, 0], out=OK_BODY.encode())
        out = M.run_headless_model(
            backend,
            "do the thing",
            cwd=str(tmp_path),
            timeout_s=30,
            max_tokens=4000,
            log_path=str(tmp_path / (backend + ".log")),
            popen=_spawn(proc),
        )
        assert out["status"] == "succeeded"
        assert out["result"]["status"] == "succeeded"
        assert out["result"]["sha"] == "abc123"
        assert out["evidence"]["timed_out"] is False
        assert out["evidence"]["returncode"] == 0
        # Prompt text never lands in evidence: only its hash does.
        assert "do the thing" not in str(out["evidence"])


def test_spawn_has_no_tty_and_own_session(tmp_path):
    proc = _FakeProc([0])
    _run(proc, tmp_path)
    kwargs = proc.kwargs
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is not None


def test_child_env_is_credential_free(tmp_path):
    proc = _FakeProc([0])
    env = {
        "PATH": "/venv/bin:/usr/bin",
        "LANG": "C",
        "SOME_API_KEY": "shh",
        "WORKHUB_JWT": "shh",
        "NORMAL": "kept",
    }
    _run(proc, tmp_path, env=env)
    child = proc.kwargs["env"]
    assert child["PATH"] == "/venv/bin:/usr/bin"
    assert child["LANG"] == "C"
    # The allowlist applies to caller env too: anything outside it,
    # secret-named or not, never reaches the child.
    assert "NORMAL" not in child
    assert "SOME_API_KEY" not in child
    assert "WORKHUB_JWT" not in child
    assert child["HOME"] and child["USER"] and child["CODEX_HOME"]


def test_allowlist_env_drops_secrets_from_base(tmp_path):
    assert M.credential_free_env({"PATH": "/x", "LANG": "C", "EVIL_TOKEN": "shh"}) == {
        "PATH": "/x",
        "LANG": "C",
        "HOME": M.credential_free_env()["HOME"],
        "USER": M.credential_free_env()["USER"],
        "LOGNAME": M.credential_free_env()["LOGNAME"],
        "CODEX_HOME": M.credential_free_env()["CODEX_HOME"],
    }


def test_timeout_kills_process_group_with_evidence(tmp_path):
    proc = _FakeProc([None])
    out = _run(proc, tmp_path, timeout_s=0.2)
    assert out["status"] == "failed"
    assert out["reason"] == "timeout"
    assert out["evidence"]["timed_out"] is True
    assert out["evidence"]["timeout_s"] == 0.2
    assert "output_sha256" in out["evidence"]
    assert proc.killed


def test_real_process_group_killed_on_expiry(tmp_path):
    if shutil.which("sleep") is None:
        return
    old = M.build_argv
    M.build_argv = lambda backend, prompt, **kwargs: ["sleep", "30"]
    try:
        out = M.run_headless_model(
            "codex",
            "do the thing",
            cwd=str(tmp_path),
            timeout_s=2,
            max_tokens=4000,
            log_path=str(tmp_path / "sleep.log"),
        )
    finally:
        M.build_argv = old
    assert out["status"] == "failed"
    assert out["reason"] == "timeout"
    assert out["evidence"]["process_group_killed"] is True
    pid = out["evidence"]["pid"]
    try:
        os.kill(pid, 0)
    except OSError:
        pass
    else:
        raise AssertionError("timed-out process group still alive")


def test_malformed_output_fails_closed_with_evidence(tmp_path):
    raw = b"all done, trust me, no json here"
    proc = _FakeProc([None, 0])
    out = _run(proc, tmp_path, out=raw)
    assert out["status"] == "failed"
    assert out["reason"] == "malformed-output"
    assert out["evidence"]["output_sha256"] == hashlib.sha256(raw).hexdigest()
    assert out["evidence"]["output_bytes"] == len(raw)
    assert out["evidence"]["returncode"] == 0


def test_malformed_fence_without_status_refused(tmp_path):
    raw = b'```result\n{"note": "no status key"}\n```\n'
    proc = _FakeProc([0])
    out = _run(proc, tmp_path, out=raw)
    assert out["status"] == "failed"
    assert out["reason"] == "malformed-output"


def test_token_budget_exceeded_fails_closed(tmp_path):
    proc = _FakeProc([None, 0])
    out = _run(proc, tmp_path, max_tokens=1)
    assert out["status"] == "failed"
    assert out["reason"] == "token-budget-exceeded"
    assert out["evidence"]["output_tokens_estimate"] > 1
    assert out["evidence"]["max_tokens"] == 1


def test_nonzero_exit_fails_closed(tmp_path):
    proc = _FakeProc([2])
    out = _run(proc, tmp_path, out=b"boom")
    assert out["status"] == "failed"
    assert out["reason"] == "nonzero-exit"
    assert out["evidence"]["returncode"] == 2


def test_spawn_failure_fails_closed(tmp_path):
    def missing(argv, **kwargs):
        raise FileNotFoundError("no such backend")

    out = M.run_headless_model(
        "codex",
        "do the thing",
        cwd=str(tmp_path),
        timeout_s=30,
        max_tokens=100,
        log_path=str(tmp_path / "m.log"),
        popen=missing,
    )
    assert out["status"] == "failed"
    assert out["reason"] == "spawn-refused"


def test_canary_lane_wires_verifier_through_adapter():
    lane_path = Path(__file__).parents[1] / "tools" / "canary_lane.py"
    spec = importlib.util.spec_from_file_location("canary_lane", lane_path)
    lane = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lane)
    assert hasattr(lane.MODEL_ADAPTER, "run_headless_model")
    assert float(lane.VERIFIER_MAX_TOKENS) > 0


def test_estimate_tokens_is_ceil_chars_over_four():
    assert M.estimate_output_tokens("") == 0
    assert M.estimate_output_tokens("abcd") == 1
    assert M.estimate_output_tokens("abcde") == 2


def test_explicit_network_cli_is_scoped_and_default_stays_restricted():
    enabled = M.build_argv('codex', 'review', network_access=True)
    assert enabled[enabled.index('-s') + 1] == 'workspace-write'
    assert 'sandbox_workspace_write.network_access=true' in enabled
    assert M.build_argv('codex', 'review')[3] == 'read-only'


def test_final_response_budget_excludes_tool_transcript(tmp_path):
    proc = _FakeProc([0], out=b'tool stdout\n' * 10000)
    def spawn(argv, **kwargs):
        proc.kwargs = kwargs
        Path(argv[argv.index('--output-last-message') + 1]).write_text(OK_BODY)
        return proc
    outcome = M.run_headless_model('codex', 'review', cwd=tmp_path,
        timeout_s=30, max_tokens=100, log_path=tmp_path / 'final.log',
        popen=spawn, final_response_file=True)
    assert outcome['status'] == 'succeeded'
    assert outcome['evidence']['output_bytes'] > 10000
    assert outcome['evidence']['output_tokens_estimate'] <= 100
    assert outcome['evidence']['budget_source'] == 'final_response'


def test_missing_final_response_never_parses_transcript_verdict(tmp_path):
    proc = _FakeProc([0], out=OK_BODY.encode())
    outcome = M.run_headless_model('codex', 'review', cwd=tmp_path,
        timeout_s=30, max_tokens=100, log_path=tmp_path / 'missing.log',
        popen=_spawn(proc), final_response_file=True)
    assert outcome['reason'] == 'malformed-output'


def test_oversized_final_response_still_refuses(tmp_path):
    proc = _FakeProc([0])
    def spawn(argv, **kwargs):
        proc.kwargs = kwargs
        Path(argv[argv.index('--output-last-message') + 1]).write_text(OK_BODY * 100)
        return proc
    outcome = M.run_headless_model('codex', 'review', cwd=tmp_path,
        timeout_s=30, max_tokens=100, log_path=tmp_path / 'large.log',
        popen=spawn, final_response_file=True)
    assert outcome['reason'] == 'token-budget-exceeded'
