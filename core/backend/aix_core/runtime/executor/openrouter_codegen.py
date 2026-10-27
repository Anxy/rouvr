"""OpenRouter helper for objective-cycle bounded codegen (001P HTML/CSS path)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class OpenRouterUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    provider: str = "openrouter"
    ok: bool = False
    detail: str = ""


@dataclass
class HtmlCssBundle:
    html: str
    css: str
    positive_tests: list[dict[str, Any]]
    negative_tests: list[dict[str, Any]]
    usage: OpenRouterUsage


def _openrouter_key(settings: object | None) -> str:
    if settings is not None:
        key = str(getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
        if key:
            return key
    return str(os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_API_KEY", "") or "").strip()


def _openrouter_url(settings: object | None) -> str:
    if settings is not None:
        url = str(getattr(settings, "OPENROUTER_API_URL", "") or "").strip()
        if url:
            return url.rstrip("/")
    return str(os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_API_URL", "https://openrouter.ai/api/v1") or "").rstrip("/")


def _openrouter_model(settings: object | None, *, complexity: str = "NORMAL") -> str:
    if settings is not None:
        model = str(getattr(settings, "OPENROUTER_DEFAULT_MODEL", "") or "").strip()
        if model:
            return model
    from backend.aix_core.runtime.model_registry.registry import get_model_registry

    role = "bulk_executor" if complexity == "LOW" else "executor"
    return get_model_registry().get_route(role, complexity=complexity).model_id


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def local_fallback_html_css_bundle(objective: str) -> HtmlCssBundle:
    """Deterministic local login-page artifacts when OpenRouter is unavailable."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Login</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="page">
    <h1>Sign in</h1>
    <p class="lede">Enter your email and password to continue.</p>
    <form id="login-form" novalidate>
      <label>
        Email
        <input id="email" name="email" type="email" autocomplete="username" required />
      </label>
      <label>
        Password
        <input id="password" name="password" type="password" autocomplete="current-password" required minlength="8" />
      </label>
      <p id="form-error" class="error" role="alert" hidden></p>
      <p id="form-ok" class="ok" role="status" hidden>Credentials look valid (proposed demo — not submitted).</p>
      <button type="submit">Sign in</button>
    </form>
  </main>
  <script>
    const form = document.getElementById('login-form');
    const err = document.getElementById('form-error');
    const ok = document.getElementById('form-ok');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      ok.hidden = true;
      err.hidden = true;
      const email = document.getElementById('email').value.trim();
      const password = document.getElementById('password').value;
      if (!email.includes('@') || !email.includes('.')) {
        err.textContent = 'Enter a valid email.';
        err.hidden = false;
        return;
      }
      if (password.length < 8) {
        err.textContent = 'Password must be at least 8 characters.';
        err.hidden = false;
        return;
      }
      ok.hidden = false;
    });
  </script>
</body>
</html>
"""
    css = """:root {
  --bg: #0f172a;
  --panel: #111827;
  --ink: #e5e7eb;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --danger: #f87171;
  --ok: #34d399;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: "Segoe UI", system-ui, sans-serif;
  color: var(--ink);
  background:
    radial-gradient(circle at 20% 20%, rgba(56, 189, 248, 0.18), transparent 40%),
    radial-gradient(circle at 80% 0%, rgba(52, 211, 153, 0.12), transparent 35%),
    var(--bg);
}
.page {
  width: min(24rem, calc(100vw - 2rem));
  padding: 1.75rem;
  background: color-mix(in srgb, var(--panel) 92%, white 8%);
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 1rem;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}
h1 { margin: 0 0 0.35rem; letter-spacing: -0.03em; }
.lede { margin: 0 0 1.25rem; color: var(--muted); }
label { display: grid; gap: 0.4rem; margin: 0.85rem 0; font-weight: 600; }
input, button {
  font: inherit;
  padding: 0.7rem 0.8rem;
  border-radius: 0.55rem;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(15, 23, 42, 0.55);
  color: var(--ink);
}
button {
  width: 100%;
  margin-top: 0.9rem;
  background: var(--accent);
  color: #082f49;
  border-color: var(--accent);
  font-weight: 700;
  cursor: pointer;
}
.error { color: var(--danger); }
.ok { color: var(--ok); }
"""
    positive = [
        {
            "id": "pos-valid-login",
            "description": "Valid email and password (>=8) passes client validation",
            "input": {"email": "ada@example.com", "password": "secret123"},
            "expected": {"ok": True, "error": None},
        },
        {
            "id": "pos-min-password",
            "description": "Exactly 8-character password is accepted",
            "input": {"email": "a@b.co", "password": "12345678"},
            "expected": {"ok": True, "error": None},
        },
    ]
    negative = [
        {
            "id": "neg-bad-email",
            "description": "Email without @ is rejected",
            "input": {"email": "ada.example.com", "password": "secret123"},
            "expected": {"ok": False, "error_contains": "email"},
        },
        {
            "id": "neg-short-password",
            "description": "Password shorter than 8 chars is rejected",
            "input": {"email": "ada@example.com", "password": "short"},
            "expected": {"ok": False, "error_contains": "Password"},
        },
        {
            "id": "neg-empty-email",
            "description": "Empty email is rejected",
            "input": {"email": "", "password": "secret123"},
            "expected": {"ok": False, "error_contains": "email"},
        },
    ]
    return HtmlCssBundle(
        html=html,
        css=css,
        positive_tests=positive,
        negative_tests=negative,
        usage=OpenRouterUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            model="local-fallback",
            provider="local",
            ok=True,
            detail=f"fallback login template used for objective: {objective[:120]}",
        ),
    )


async def generate_html_css_page(
    *,
    objective: str,
    settings: object | None = None,
    vision_notes: str | None = None,
    image_parts: list[dict[str, Any]] | None = None,
    model_override: str | None = None,
) -> HtmlCssBundle:
    """Ask OpenRouter for HTML+CSS + tests; recreate from screenshot when image parts exist.

    AIX-EXECUTION-006-S2: `model_override` is the executor model bound via the
    Execution Kernel's durable route (see cycle_adapter.py / durable_api_backend.py
    `_maybe_real_execute`). When supplied it wins over the settings default —
    routing a caller selects is what actually runs, not a silent fallback.
    """
    key = _openrouter_key(settings)
    if not key:
        bundle = local_fallback_html_css_bundle(objective)
        bundle.usage.detail = "OPENROUTER_API_KEY missing; used local login fallback"
        return bundle

    model = str(model_override or "").strip() or _openrouter_model(settings)
    # Prefer a vision-capable model when screenshots are supplied.
    if image_parts:
        vision_model = str(
            getattr(settings, "OPENROUTER_VISION_MODEL", "")
            or os.getenv("HYEVE_AIX_SERVICE_OPENROUTER_VISION_MODEL", "")
            or "qwen/qwen2.5-vl-72b-instruct"
        ).strip()
        if vision_model:
            model = vision_model.removeprefix("openrouter/")
    base = _openrouter_url(settings)

    recreate = bool(image_parts) or bool((vision_notes or "").strip())
    lowered_objective = (objective or "").lower()
    asks_like_this = any(
        token in lowered_objective
        for token in (
            "like this",
            "recreate",
            "clone",
            "exact same",
            "screenshot",
            "as shown",
            "this ui",
            "this design",
            "mockup",
        )
    )
    if asks_like_this and not recreate:
        # Refuse silent login invention when the user pointed at a missing screenshot.
        stub = local_fallback_html_css_bundle(objective)
        stub.html = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'/>"
            "<title>Screenshot required</title><link rel='stylesheet' href='styles.css'/></head>"
            "<body><main class='page'><h1>Screenshot required</h1>"
            "<p>The objective asks to recreate a UI, but no image attachment reached execute. "
            "Re-run Create &amp; run with the screenshot attached.</p></main></body></html>"
        )
        stub.css = "body{font-family:system-ui;padding:2rem} h1{margin:0 0 .5rem}"
        stub.usage.detail = "screenshot_missing_for_recreate_objective"
        stub.usage.ok = False
        return stub

    instructions = (
        "Recreate the ATTACHED UI screenshot as closely as possible in HTML/CSS.\n"
        "Ground truth is the image + vision inventory — not a template.\n"
        "Match layout regions, nav items, headings, cards, buttons, spacing, and colors.\n"
        "FORBIDDEN unless the screenshot itself is an auth screen: inventing login, signup, "
        "Create account, Northstar, or any unrelated marketing/auth page.\n"
        if recreate
        else (
            "Create a polished page that satisfies the objective.\n"
            "Only build login/registration if the objective explicitly asks for that.\n"
            "If the objective asks for attendance, build an attendance UI — never invent "
            "an unrelated product brand, teacher signup marketing page, or Lessonly-style "
            "account form.\n"
            "Stay faithful to domain nouns in the objective (attendance, PHP, teacher-only).\n"
        )
    )
    text_prompt = (
        f"{instructions}"
        f"OBJECTIVE: {objective}\n\n"
        f"VISION INVENTORY:\n{(vision_notes or '(none)').strip()}\n\n"
        "Requirements:\n"
        "- Complete HTML5 document that links styles.css\n"
        "- Separate CSS stylesheet\n"
        "- Interactive behavior only where the screenshot / objective implies it\n"
        "- At least 2 positive and 3 negative test cases (UI checks / validation)\n\n"
        "Return ONLY a JSON object with keys:\n"
        "html, css, positive_tests, negative_tests.\n"
        "positive_tests/negative_tests items: {id, description, input, expected}.\n"
        "Do not wrap in markdown."
    )
    user_content: Any
    if image_parts:
        user_content = [{"type": "text", "text": text_prompt}, *image_parts]
    else:
        user_content = text_prompt

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:8010",
        "X-Title": "hyeve-aix-objective-cycle",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful frontend engineer. Recreate UI from screenshots faithfully. "
                    "Never substitute a stock login/signup page for a dashboard or other screen. "
                    "Output valid JSON only."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1 if recreate else 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001
        bundle = local_fallback_html_css_bundle(objective)
        bundle.usage.detail = f"openrouter_failed:{type(exc).__name__}:{exc}"
        return bundle

    usage_raw = body.get("usage") if isinstance(body, dict) else {}
    usage_raw = usage_raw if isinstance(usage_raw, dict) else {}
    prompt_tokens = int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0)
    completion_tokens = int(usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or 0)
    total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))
    usage = OpenRouterUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        model=str(body.get("model") or model),
        provider="openrouter",
        ok=True,
        detail="openrouter chat.completions",
    )

    choices = body.get("choices") if isinstance(body, dict) else None
    content = ""
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = str((message or {}).get("content") or "")
    parsed = _extract_json_object(content)
    html = str(parsed.get("html") or "").strip()
    css = str(parsed.get("css") or "").strip()
    positive = parsed.get("positive_tests") if isinstance(parsed.get("positive_tests"), list) else []
    negative = parsed.get("negative_tests") if isinstance(parsed.get("negative_tests"), list) else []
    positive_tests = [item for item in positive if isinstance(item, dict)]
    negative_tests = [item for item in negative if isinstance(item, dict)]

    if not html or not css or len(positive_tests) < 1 or len(negative_tests) < 1:
        # Never silently replace a screenshot recreate with the stock login template.
        if recreate:
            usage.ok = False
            usage.detail = "openrouter_recreate_incomplete; refusing login fallback"
            if not html:
                html = (
                    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'/>"
                    "<title>Recreate incomplete</title><link rel='stylesheet' href='styles.css'/></head>"
                    "<body><main><h1>Recreate incomplete</h1>"
                    "<p>Model did not return full HTML/CSS for the attached screenshot. Retry resume.</p>"
                    "</main></body></html>"
                )
            if not css:
                css = "body{font-family:system-ui;padding:2rem}"
            if len(positive_tests) < 1:
                positive_tests = [
                    {
                        "id": "pos-visible-shell",
                        "description": "Page shell renders",
                        "input": {},
                        "expected": "Main heading visible",
                    }
                ]
            if len(negative_tests) < 1:
                negative_tests = [
                    {
                        "id": "neg-not-login",
                        "description": "Must not be a stock login page",
                        "input": {},
                        "expected": "No invented Sign in form when screenshot was not auth",
                    }
                ]
        else:
            fallback = local_fallback_html_css_bundle(objective)
            fallback.usage = usage
            fallback.usage.detail = "openrouter response incomplete; merged with local login fallback content"
            if not html:
                html = fallback.html
            if not css:
                css = fallback.css
            if len(positive_tests) < 1:
                positive_tests = fallback.positive_tests
            if len(negative_tests) < 1:
                negative_tests = fallback.negative_tests

    return HtmlCssBundle(
        html=html,
        css=css,
        positive_tests=positive_tests,
        negative_tests=negative_tests,
        usage=usage,
    )
