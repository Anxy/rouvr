"""Diagnose empty model streams: 5 direct calls varying the knobs.

Prints per call: label, HTTP status, data lines, content chars, finish
reason, usage. Never prints the key.
"""
import json
import os
import sys
import urllib.request

BASE = os.environ["META_MODEL_API_BASE_URL"].rstrip("/")
MODEL = os.environ.get("META_MODEL_NAME") or "muse-spark-1.3"
KEY = os.environ["META_API_KEY"]

FULL_SYS = ("You are the Rouvr supervisor. Either ask exactly one clarifying "
            "question or propose a work slice. Reply with STRICT JSON only, no "
            "prose outside the JSON.")
SHORT_SYS = "Reply with STRICT JSON only."
USER = "Slice a demo login smoke test into small tasks for the web workspace."

CASES = [
    ("stream=T max=1024 sys=FULL", True, 1024, FULL_SYS),
    ("stream=F max=1024 sys=FULL", False, 1024, FULL_SYS),
    ("stream=T max=1024 sys=SHORT", True, 1024, SHORT_SYS),
    ("stream=F max=1024 sys=SHORT", False, 1024, SHORT_SYS),
    ("stream=T max=256  sys=FULL", True, 256, FULL_SYS),
]


def call(stream, max_tokens, system):
    body = json.dumps({"model": MODEL,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": USER}],
                       "max_tokens": max_tokens, "stream": stream}).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
    except Exception as e:
        return {"http_error": f"{type(e).__name__}: {str(e)[:100]}"}
    if not stream:
        try:
            d = json.loads(raw)
            c = d["choices"][0]
            return {"nonstream": True,
                    "content_chars": len(c["message"].get("content") or ""),
                    "finish": c.get("finish_reason"), "usage": d.get("usage")}
        except Exception as e:
            return {"parse_error": str(e)[:100], "bytes": len(raw)}
    lines = [ln for ln in raw.splitlines()
             if ln.startswith("data:") and "DONE" not in ln]
    chars, finish, usage = 0, None, None
    for ln in lines:
        try:
            ch = json.loads(ln[5:])["choices"][0]
        except ValueError:
            continue
        delta = ch.get("delta", {})
        chars += len(delta.get("content") or "")
        finish = ch.get("finish_reason") or finish
    return {"data_lines": len(lines), "content_chars": chars,
            "finish": finish, "bytes": len(raw)}


def main():
    only = sys.argv[1:] or None
    for i, (label, stream, mx, system) in enumerate(CASES, 1):
        if only and str(i) not in only:
            continue
        print(f"[{i}] {label} -> {json.dumps(call(stream, mx, system))}", flush=True)


main()
