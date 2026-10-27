"""One-off: replace ULID() with new_ulid() across aix_core."""
from __future__ import annotations

import re
from pathlib import Path

COMPAT_IMPORT = "from backend.aix_core.runtime.objective.ulid_compat import new_ulid"


def insert_import(text: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_at = 0
    in_doc = False
    quote = ""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and (stripped.startswith('"""') or stripped.startswith("'''")):
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and len(stripped) > 3:
                continue
            in_doc = True
            continue
        if in_doc:
            if quote in line:
                in_doc = False
            continue
        if line.startswith("from __future__"):
            insert_at = i + 1
        elif line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        elif stripped and not stripped.startswith("#"):
            break
    lines.insert(insert_at, COMPAT_IMPORT + "\n")
    return "".join(lines)


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "aix_core"
    for path in sorted(root.rglob("*.py")):
        if path.name == "ulid_compat.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "ULID" not in text:
            continue
        orig = text
        text = re.sub(r"^from ulid import ULID\s*\n", "", text, flags=re.M)
        text = re.sub(r"^\s*from ulid import ULID\s*\n", "", text, flags=re.M)
        text = re.sub(r"str\(ULID\(\)\)", "new_ulid()", text)
        text = re.sub(r"\bULID\(\)", "new_ulid()", text)
        if text == orig:
            continue
        if COMPAT_IMPORT not in text:
            text = insert_import(text)
        path.write_text(text, encoding="utf-8")
        print(f"fixed {path.relative_to(root.parent)}")


if __name__ == "__main__":
    main()
