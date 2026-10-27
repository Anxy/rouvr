#!/usr/bin/env python3
"""Protocol-exact fake `muse serve` endpoint for transport tests.

Speaks the maintained MSP shapes (initialize/initialized, session/start with
viewCursor, profile/hook/mcp lists, turn/start ack with top-level turnId +
command echo + admission status/disposition, turn/started, item/completed
with sourceRange/viewCursor, turn/completed with usage) so tests drive the
PRODUCTION transport against exact frames — never against a shim of the
implementation's internals. Behavior is selected by MUSE_FAKE_SCENARIO; the
entry argv must start with `serve` (proving the spawn shape).

Frames are literal and minimal; sourceRange is relayed opaquely exactly as
the production code treats it (mapping required, never parsed).
"""

import json
import os
import subprocess
import sys
import time

SESSION = "11111111-1111-7000-8000-000000000001"
TURN = "22222222-2222-7000-8000-000000000002"
FOREIGN_TURN = "33333333-3333-7000-8000-000000000003"
CURSORS = [f"cur-{i}" for i in range(1, 40)]
USAGE = {"cachedTokens": 0, "inputTokens": 10, "outputTokens": 5, "reasoningTokens": 0}


def _initialize_result():
    # Installed-schema InitializeResult shape: all required members with
    # listed closed-enum values (unix/linux) and a well-formed SchemaInfo.
    return {
        "experimentalApi": True, "grantedCapabilities": ["userShell"],
        "museHome": "/tmp/fake-muse-home", "platformFamily": "unix",
        "platformOs": "linux",
        "schema": {"fingerprint": "sha256:" + "0" * 64, "version": 1},
        "serverInfo": {"name": "fake-muse", "version": "0"},
        "userAgent": "fake",
    }


def _approval_params(cursor):
    # Installed-schema ApprovalRequestParams shape: every required member.
    return {
        "approvalId": "ap-1", "availableChoices": [],
        "currentRequirementId": {"approvalId": "ap-1", "sourceIndex": 0},
        "itemId": "it-1", "judgeEscalated": False, "protectedWrite": False,
        "rawArgs": "", "sessionId": SESSION, "sourceRange": {"anchor": "fake"},
        "subject": {"kind": "shell", "command": "echo fake"},
        "taskId": "task-1", "toolCallId": "tool-1", "toolName": "fake-tool",
        "turnId": TURN, "viewCursor": cursor,
    }


def _log(line):
    path = os.environ.get("MUSE_FAKE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _send(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _respond(rid, result):
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _fail(rid, code, message):
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _event(method, params):
    _send({"jsonrpc": "2.0", "method": method, "params": params})


def _turn_started(command_id, cursor):
    _event("turn/started", {
        "commandId": command_id, "sessionId": SESSION,
        "sourceRange": {"anchor": "fake"}, "turnId": TURN, "viewCursor": cursor,
    })


def _item_completed(item_id, kind, text, cursor, status="completed"):
    # Installed-schema Item shape: itemId/kind/revision/status are required;
    # single-shot revision 1 (no item/updated is ever emitted for it).
    _event("item/completed", {
        "item": {"itemId": item_id, "turnId": TURN, "kind": kind,
                 "status": status, "text": text, "revision": 1},
        "sessionId": SESSION, "sourceRange": {"anchor": "fake"}, "viewCursor": cursor,
    })


def _terminal(cursor, terminal="completed", usage=USAGE, extra=None):
    params = {"sessionId": SESSION, "sourceRange": {"anchor": "fake"},
              "terminal": terminal, "turnId": TURN, "viewCursor": cursor,
              "usage": usage, "durationMs": 12}
    if extra:
        params.update(extra)
    _event("turn/completed", params)


def main():
    scenario = os.environ.get("MUSE_FAKE_SCENARIO", "happy")
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        sys.stderr.write("fake muse: expected serve entrypoint\n")
        return 2
    _log("argv:" + json.dumps(sys.argv[1:]))
    _log("cwd:" + os.getcwd())
    _log("pid:" + str(os.getpid()))
    _log("env:" + json.dumps(sorted(os.environ.keys())))
    stdin = sys.stdin
    turn_command = None
    turn_prompt = ""
    if scenario == "bad-bytes":
        sys.stdout.buffer.write(b"\xff\xfe not json \n")
        sys.stdout.buffer.flush()
        _linger(stdin)
        return 0
    if scenario == "malformed":
        sys.stdout.write("{not valid json\n")
        sys.stdout.flush()
        _linger(stdin)
        return 0
    if scenario == "dup-keys":
        sys.stdout.write('{"jsonrpc":"2.0","id":1,"id":2}\n')
        sys.stdout.flush()
        _linger(stdin)
        return 0
    if scenario == "early-exit":
        # Complete the handshake, then die with no terminal.
        for line in stdin:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("method") == "initialize":
                _respond(obj["id"], _initialize_result())
                os._exit(3)
        return 3
    for line in stdin:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        method = obj.get("method")
        if "id" in obj and "method" not in obj:
            # Client reply to a server-initiated request (e.g. the -32601
            # refusal of approval/request): record for the test.
            _log("client-reply:" + json.dumps(obj))
            continue
        if method == "initialize":
            if scenario == "init-error":
                _fail(obj["id"], -32000, "nope")
                continue
            _respond(obj["id"], _initialize_result())
        elif method == "permissionProfile/list":
            _respond(obj["id"], {"profiles": [
                {"profileId": ":read-only", "available": True,
                 "label": "Read-only", "reviewerAuthority": "none"},
            ]})
        elif method == "session/start":
            params = obj.get("params", {})
            _log("session-start:" + json.dumps(params))
            _respond(obj["id"], {
                "session": {"sessionId": SESSION,
                            "workspaceRoot": params.get("workspaceRoot"),
                            "providerId": params.get("providerId"),
                            "modelId": params.get("modelId"),
                            "activeTurnId": None},
                "viewCursor": CURSORS[0],
            })
        elif method in ("hook/list",):
            _respond(obj["id"], {"hooks": []})
        elif method in ("mcpServer/list",):
            _respond(obj["id"], {"servers": []})
        elif method == "turn/start":
            params = obj.get("params", {})
            turn_command = params.get("commandId")
            inputs = params.get("input") or []
            turn_prompt = inputs[0].get("text", "") if inputs else ""
            _log("turn-start:" + json.dumps(params))
            status = os.environ.get("MUSE_FAKE_ACK_STATUS", "accepted")
            disposition = os.environ.get("MUSE_FAKE_DISPOSITION", "started")
            ack = {"commandId": params.get("commandId"), "status": status,
                   "disposition": disposition,
                   "startedNewTurn": disposition == "started", "turnId": TURN}
            if scenario == "bad-ack-cmd":
                ack["commandId"] = "00000000-0000-7000-8000-999999999999"
            if scenario == "no-turn-id":
                del ack["turnId"]
            _respond(obj["id"], ack)
            if status != "accepted":
                continue
            _run_turn_scenario(scenario, turn_command, turn_prompt)
            break
        elif "id" in obj:
            # Client request with an unknown method: log it for the test.
            # (No response is sent; the client must time out explicitly.)
            _log("unknown-method:" + str(method))
    # Linger until the client closes stdin, still recording client replies
    # (e.g. the -32601 refusal of a solicited server request).
    _linger(stdin)
    return 0


def _linger(stdin):
    for _line in stdin:
        if not _line.strip():
            continue
        try:
            _obj = json.loads(_line)
        except ValueError:
            continue
        if "id" in _obj and "method" not in _obj:
            _log("client-reply:" + json.dumps(_obj))


def _run_turn_scenario(scenario, turn_command, turn_prompt):
    if scenario in ("happy", "happy-write", "approval", "tool-fail", "empty-text",
                    "slow", "quiet"):
        if scenario == "happy-write":
            for name in os.environ.get("MUSE_FAKE_WRITE_FILES", "helper.py").split(","):
                name = name.strip()
                if name:
                    with open(name, "w", encoding="utf-8") as handle:
                        handle.write("# fake worker output\n")
        _turn_started(turn_command, CURSORS[1])
        if scenario == "approval":
            _event("approval/requested", _approval_params(CURSORS[2]))
            _event("approval/reviewing", _approval_params(CURSORS[3]))
            _event("approval/resolved", _approval_params(CURSORS[4]))
            # Solicit a client decision; the correct client answers -32601.
            _send({"jsonrpc": "2.0", "id": 99, "method": "approval/request",
                   "params": _approval_params(CURSORS[7])})
        _item_completed("it-echo", "userMessage", turn_prompt, CURSORS[5])
        if scenario == "empty-text":
            _terminal(CURSORS[6])
            return
        _item_completed("it-1", "agentMessage", "part one", CURSORS[6])
        if scenario == "tool-fail":
            _item_completed("it-tool", "toolCall", "", CURSORS[7], status="failed")
        _item_completed("it-2", "agentMessage", "part two", CURSORS[8])
        if scenario in ("slow", "quiet"):
            time.sleep(30)
            return
        _terminal(CURSORS[9])
    elif scenario == "gap":
        _turn_started(turn_command, CURSORS[1])
        _send({"jsonrpc": "2.0", "method": "view/gap",
               "params": {"after": CURSORS[1], "next": CURSORS[9], "sessionId": SESSION}})
    elif scenario == "late":
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-1", "agentMessage", "on time", CURSORS[2])
        _terminal(CURSORS[3])
        time.sleep(0.3)
        _item_completed("it-late", "agentMessage", "too late", CURSORS[4])
    elif scenario == "no-cursor":
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-1", "agentMessage", "text", CURSORS[2])
        params = {"sessionId": SESSION, "sourceRange": {"anchor": "fake"},
                  "terminal": "completed", "turnId": TURN, "usage": USAGE}
        _event("turn/completed", params)
    elif scenario == "bad-usage":
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-1", "agentMessage", "text", CURSORS[2])
        _terminal(CURSORS[3], usage={"cachedTokens": "many", "inputTokens": 1,
                                     "outputTokens": 1, "reasoningTokens": 0})
    elif scenario == "malformed-after-terminal":
        # A valid terminal, then a truncated frame, then a clean exit. The
        # terminal is already accepted when the garbage arrives, so only a
        # postmortem stream check can refuse it.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-1", "agentMessage", "text", CURSORS[2])
        _terminal(CURSORS[3])
        sys.stdout.write('{"jsonrpc": "2.0", "method": "item/complete\n')
        sys.stdout.flush()
    elif scenario == "contradictory-terminal":
        # A valid terminal followed by a second terminal for the same turn
        # that disagrees on the outcome. Only a postmortem comparison of
        # the queued second record can refuse it.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-1", "agentMessage", "text", CURSORS[2])
        _terminal(CURSORS[3])
        _terminal(CURSORS[4], terminal="failed",
                   extra={"error": {"kind": "workerError", "message": "boom"}})
    elif scenario == "late-delta":
        # A valid terminal followed by a delta for a known item. Deltas
        # carry only an itemId (no turnId, no sourceRange per the installed
        # schema); only registry correlation can prove it belongs to the
        # finished turn.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-echo", "userMessage", turn_prompt, CURSORS[5])
        _item_completed("it-1", "agentMessage", "part one", CURSORS[6])
        _item_completed("it-2", "agentMessage", "part two", CURSORS[8])
        _terminal(CURSORS[9])
        _event("item/delta", {"delta": "more", "itemId": "it-1",
                              "sessionId": SESSION, "viewCursor": CURSORS[10]})
    elif scenario == "late-foreign":
        # A valid terminal followed by an item bound to a different turn on
        # its nested item object. Proven foreign: the turn evidence stands.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-echo", "userMessage", turn_prompt, CURSORS[5])
        _item_completed("it-1", "agentMessage", "part one", CURSORS[6])
        _item_completed("it-2", "agentMessage", "part two", CURSORS[8])
        _terminal(CURSORS[9])
        _event("item/completed", {
            "item": {"itemId": "it-x", "turnId": FOREIGN_TURN,
                     "kind": "agentMessage", "status": "completed",
                     "text": "foreign", "revision": 1},
            "sessionId": SESSION, "sourceRange": {"anchor": "fake"},
            "viewCursor": CURSORS[10],
        })
    elif scenario == "late-unbound":
        # A valid terminal followed by an item event with no turn binding
        # at all (ephemeral open shape, empty itemId). Unexplained in a
        # fresh single-turn host: refused.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-echo", "userMessage", turn_prompt, CURSORS[5])
        _item_completed("it-1", "agentMessage", "part one", CURSORS[6])
        _item_completed("it-2", "agentMessage", "part two", CURSORS[8])
        _terminal(CURSORS[9])
        _event("item/started", {
            "item": {"itemId": "", "kind": "agentMessage",
                     "status": "inProgress", "text": "x", "revision": 1},
            "sessionId": SESSION, "viewCursor": CURSORS[10],
        })
    elif scenario == "orphan-child":
        # A valid terminal, then the parent exits cleanly on EOF while a
        # same-group child (no pipes inherited) survives it. Group cleanup
        # must send signals, which makes the shutdown forced, never clean.
        _turn_started(turn_command, CURSORS[1])
        _item_completed("it-echo", "userMessage", turn_prompt, CURSORS[5])
        _item_completed("it-1", "agentMessage", "part one", CURSORS[6])
        _item_completed("it-2", "agentMessage", "part two", CURSORS[8])
        _terminal(CURSORS[9])
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    raise SystemExit(main())
