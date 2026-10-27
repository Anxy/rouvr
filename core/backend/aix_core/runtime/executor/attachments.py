"""Resolve discuss/admit attachments so models can read uploaded file content."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

# Keep discuss prompts bounded — large dumps belong in execute workspaces.
_MAX_INLINE_CHARS = 40_000
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".html", ".css", ".js", ".ts", ".tsx", ".py"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _looks_image(filename: str, mime: str) -> bool:
    mime_l = (mime or "").lower()
    if mime_l.startswith("image/"):
        return True
    return Path(filename or "").suffix.lower() in _IMAGE_EXTENSIONS


def _file_upload_base() -> str:
    return str(
        os.getenv("HYEVE_AIX_SERVICE_FILE_UPLOAD_BASE_URL")
        or os.getenv("AIX_FILE_UPLOAD_BASE_URL")
        or "http://127.0.0.1:8213"
    ).rstrip("/")


def _service_token() -> str:
    return str(
        os.getenv("HYEVE_AIX_SERVICE_FILE_UPLOAD_SERVICE_TOKEN")
        or os.getenv("AIX_FILE_UPLOAD_SERVICE_TOKEN")
        or os.getenv("HYEVE_LOCAL_AUTH_TOKEN")
        or "local-admin"
    ).strip()


def _local_upload_roots() -> list[Path]:
    roots: list[Path] = []
    configured = str(os.getenv("HYEVE_AIX_SERVICE_LOCAL_UPLOAD_ROOT") or os.getenv("AIX_LOCAL_UPLOAD_ROOT") or "").strip()
    if configured:
        roots.append(Path(configured))
    # AIX workspace-relative fallback used by the FE BFF local store.
    roots.append(Path(r"D:\v2.1\AIX\.tmp\aix-file-uploads"))
    file_upload_root = str(os.getenv("HYEVE_FILE_UPLOAD_SERVICE_FILE_STORAGE_ROOT") or "").strip()
    if file_upload_root:
        roots.append(Path(file_upload_root))
    roots.append(Path(r"D:\v2.1\hyeve\services\hyeve-file-upload-service\.state\file-assets"))
    return roots


def _looks_text(filename: str, mime: str) -> bool:
    mime_l = (mime or "").lower()
    if any(mime_l.startswith(prefix) for prefix in _TEXT_MIME_PREFIXES):
        return True
    if mime_l in {"application/json", "application/javascript"}:
        return True
    return Path(filename or "").suffix.lower() in _TEXT_EXTENSIONS


def _read_local_bytes(storage_key: str | None, local_path: str | None) -> bytes | None:
    candidates: list[Path] = []
    if local_path:
        candidates.append(Path(local_path))
    if storage_key:
        for root in _local_upload_roots():
            candidates.append(root / storage_key)
    for path in candidates:
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError:
            continue
    return None


async def _download_from_file_upload(*, asset_id: str, tenant_id: str) -> bytes | None:
    if not asset_id:
        return None
    url = f"{_file_upload_base()}/api/file-assets/{asset_id}/download"
    headers = {
        "Authorization": f"Bearer {_service_token()}",
        "X-Hyeve-Tenant-Id": tenant_id,
        "X-Tenant-ID": tenant_id,
        "X-Hyeve-User-Id": "aix-service",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            return None
        return response.content
    except Exception:
        return None


async def load_attachment_bytes(
    *,
    attachment: dict[str, Any],
    tenant_id: str,
) -> tuple[bytes | None, dict[str, Any]]:
    """Load raw bytes plus normalized metadata for an attachment ref."""
    asset_id = str(attachment.get("id") or attachment.get("asset_id") or "").strip()
    filename = str(attachment.get("filename") or attachment.get("name") or "attachment.bin").strip()
    mime = str(attachment.get("mime") or attachment.get("content_type") or "application/octet-stream")
    storage_key = str(attachment.get("storage_key") or "").strip() or None
    local_path = str(attachment.get("local_path") or "").strip() or None
    meta = {
        "id": asset_id or None,
        "filename": filename,
        "mime": mime,
        "storage_key": storage_key,
        "local_path": local_path,
    }
    raw = _read_local_bytes(storage_key, local_path)
    if raw is None and asset_id:
        raw = await _download_from_file_upload(asset_id=asset_id, tenant_id=tenant_id)
    return raw, meta


async def resolve_attachment_text(
    *,
    attachment: dict[str, Any],
    tenant_id: str,
    objective_hint: str = "",
    settings: object | None = None,
) -> dict[str, Any]:
    """Return a normalized attachment descriptor with optional inline text / vision inventory."""
    raw, meta = await load_attachment_bytes(attachment=attachment, tenant_id=tenant_id)
    filename = str(meta["filename"])
    mime = str(meta["mime"])

    result: dict[str, Any] = {
        **meta,
        "bytes": len(raw) if raw is not None else None,
        "readable": False,
        "text": None,
        "raw": raw,
        "is_image": False,
        "vision_model": None,
        "note": None,
    }
    if raw is None:
        result["note"] = "Attachment metadata received but file bytes could not be loaded."
        return result

    if _looks_image(filename, mime):
        from backend.aix_core.runtime.executor.image_vision import describe_image_bytes

        result["is_image"] = True
        vision = await describe_image_bytes(
            raw=bytes(raw),
            mime=mime,
            filename=filename,
            objective_hint=objective_hint,
            settings=settings,
        )
        result["vision_model"] = vision.model or None
        if vision.ok and vision.description.strip():
            result["readable"] = True
            result["text"] = vision.description.strip()
            result["note"] = "Image analyzed with vision model for UI recreation."
        else:
            result["note"] = (
                f"Image attached but vision analysis failed ({vision.detail}). "
                "Create & run may still attempt screenshot recreation."
            )
        return result

    if not _looks_text(filename, mime):
        result["note"] = "Binary/non-text attachment — content not inlined for chat; available for execute workspace copy."
        return result
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > _MAX_INLINE_CHARS:
        text = text[:_MAX_INLINE_CHARS] + "\n\n…[truncated for discuss context]"
    result["readable"] = True
    result["text"] = text
    return result


async def build_attachment_context_block(
    *,
    attachments: list[dict[str, Any]],
    tenant_id: str,
    objective_hint: str = "",
    settings: object | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build a system-visible context block + resolved attachment summaries."""
    if not attachments:
        return "", []
    resolved: list[dict[str, Any]] = []
    parts = [
        "The user attached file(s) to this discuss thread. Read and use them when answering. "
        "If an attachment is a UI screenshot, the VISION INVENTORY below is your view of the image "
        "(AIX already ran a vision model). Treat it as ground truth and plan an exact HTML/CSS "
        "recreation. NEVER say you cannot view/see the image when an inventory is present. "
        "Do not ask the user to re-describe layout/colors already listed. "
        "If inventory is missing, say vision analysis failed and still offer Create & run."
    ]
    for index, item in enumerate(attachments, start=1):
        if not isinstance(item, dict):
            continue
        resolved_item = await resolve_attachment_text(
            attachment=item,
            tenant_id=tenant_id,
            objective_hint=objective_hint,
            settings=settings,
        )
        resolved.append(resolved_item)
        kind = "image/screenshot" if resolved_item.get("is_image") else "file"
        header = f"\n--- Attachment {index} ({kind}): {resolved_item['filename']} ({resolved_item['mime']}) ---"
        parts.append(header)
        if resolved_item.get("readable") and resolved_item.get("text"):
            parts.append(str(resolved_item["text"]))
        else:
            parts.append(str(resolved_item.get("note") or "No inline content."))
    return "\n".join(parts).strip(), resolved
