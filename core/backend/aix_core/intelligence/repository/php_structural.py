"""Generic PHP structural operations (AIX-EC-004 Packet EC-05).

Brace-matched class/method/import inspection. No filename- or product-specific
branches. Used for inspect, region splice, method reorder, helper extraction,
and unused-import analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_USE_RE = re.compile(
    r"(?m)^(?P<indent>\s*)use\s+(?P<fqcn>[\w\\]+)(?:\s+as\s+(?P<alias>\w+))?\s*;"
)
_FUNC_RE = re.compile(
    r"(?P<full>(?P<vis>public|protected|private)\s+(?:static\s+)?function\s+(?P<name>[A-Za-z_]\w*)\s*\()",
    re.IGNORECASE,
)
_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)", re.IGNORECASE)
_DB_HINT_RE = re.compile(
    r"\\?DB::|->(?:select|table|where|insert|update|delete)\s*\(|"
    r"INFORMATION_SCHEMA|env\s*\(\s*['\"]DB_|mysqli_|PDO\b|->query\s*\(",
    re.IGNORECASE,
)
_VAR_RE = re.compile(r"\$[A-Za-z_]\w*")


@dataclass(frozen=True)
class PhpImport:
    fqcn: str
    alias: str | None
    short_name: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PhpMethod:
    name: str
    visibility: str
    start: int  # includes leading trivia (docblock)
    body_start: int  # 'function' keyword region
    end: int  # exclusive, after closing brace
    signature: str
    params: str
    is_static: bool = False

    @property
    def body(self) -> str:
        return ""


@dataclass
class PhpClassStructure:
    class_name: str
    class_start: int
    class_open: int
    class_end: int
    imports: list[PhpImport] = field(default_factory=list)
    methods: list[PhpMethod] = field(default_factory=list)
    namespace: str = ""


class PhpStructuralError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _skip_string(src: str, i: int) -> int:
    quote = src[i]
    i += 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return n


def _skip_comment(src: str, i: int) -> int:
    n = len(src)
    if i + 1 >= n:
        return i + 1
    if src[i : i + 2] == "//" or src[i : i + 2] == "#":
        nl = src.find("\n", i)
        return n if nl < 0 else nl + 1
    if src[i : i + 2] == "/*":
        end = src.find("*/", i + 2)
        return n if end < 0 else end + 2
    return i + 1


def matching_brace(src: str, open_idx: int) -> int:
    if open_idx < 0 or open_idx >= len(src) or src[open_idx] != "{":
        raise PhpStructuralError("BRACE_MISMATCH", "expected '{' at region start")
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in ("'", '"'):
            i = _skip_string(src, i)
            continue
        if ch == "/" or ch == "#":
            nxt = _skip_comment(src, i)
            if nxt != i + 1 or (ch == "/" and i + 1 < n and src[i + 1] in "/*"):
                i = nxt
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise PhpStructuralError("BRACE_MISMATCH", "unclosed '{'")


def _matching_paren(src: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in ("'", '"'):
            i = _skip_string(src, i)
            continue
        if ch == "/" or ch == "#":
            nxt = _skip_comment(src, i)
            if nxt != i + 1 or (ch == "/" and i + 1 < n and src[i + 1] in "/*"):
                i = nxt
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise PhpStructuralError("PAREN_MISMATCH", "unclosed '('")


def _line_start(src: str, idx: int) -> int:
    nl = src.rfind("\n", 0, idx)
    return 0 if nl < 0 else nl + 1


def _attach_leading_trivia(src: str, method_kw_start: int, floor: int) -> int:
    """Include the immediately preceding docblock / blank lines with the method."""
    start = _line_start(src, method_kw_start)
    cursor = start
    while cursor > floor:
        prev_nl = src.rfind("\n", 0, cursor - 1)
        line_begin = 0 if prev_nl < 0 else prev_nl + 1
        line = src[line_begin:cursor]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "/*", "*", "*/")):
            cursor = line_begin
            continue
        break
    return cursor


def parse_php_structure(src: str) -> PhpClassStructure:
    imports = [
        PhpImport(
            fqcn=m.group("fqcn"),
            alias=m.group("alias"),
            short_name=(m.group("alias") or m.group("fqcn").rsplit("\\", 1)[-1]),
            start=m.start(),
            end=m.end(),
            text=m.group(0),
        )
        for m in _USE_RE.finditer(src)
    ]
    ns_match = re.search(r"(?m)^namespace\s+([^;]+);", src)
    namespace = ns_match.group(1).strip() if ns_match else ""

    class_match = _CLASS_RE.search(src)
    if not class_match:
        raise PhpStructuralError("NO_CLASS", "no class declaration found")
    brace_rel = src.find("{", class_match.end())
    if brace_rel < 0:
        raise PhpStructuralError("NO_CLASS", "class has no opening brace")
    class_end = matching_brace(src, brace_rel) + 1
    class_body_start = brace_rel + 1

    raw: list[tuple] = []
    for m in _FUNC_RE.finditer(src):
        if not (class_body_start <= m.start() < class_end):
            continue
        paren_open = m.end() - 1
        try:
            paren_close = _matching_paren(src, paren_open)
        except PhpStructuralError:
            continue
        cursor = paren_close + 1
        while cursor < class_end and src[cursor] != "{":
            ch = src[cursor]
            if ch in " \t\r\n":
                cursor += 1
                continue
            if src[cursor : cursor + 2] in ("//", "/*") or ch == "#":
                cursor = _skip_comment(src, cursor)
                continue
            if ch in ("'", '"'):
                cursor = _skip_string(src, cursor)
                continue
            if ch == ";":
                break
            # Return types, nullable/union syntax, and leftover signature tokens.
            if ch.isalnum() or ch in ":_\\?|&(),[]":
                cursor += 1
                continue
            break
        open_brace = cursor if cursor < class_end and src[cursor] == "{" else -1
        vis = m.group("vis").lower()
        sig_end = open_brace if open_brace >= 0 else min(cursor, class_end)
        raw.append(
            (
                m.group("name"),
                vis,
                m.start(),
                src[m.start() : sig_end].strip(),
                src[paren_open + 1 : paren_close],
                "static" in src[m.start() : paren_open].lower(),
            )
        )

    methods: list[PhpMethod] = []
    for index, (name, vis, body_start, sig, params, is_static) in enumerate(raw):
        floor = methods[-1].start if methods else class_body_start
        trivia = _attach_leading_trivia(src, body_start, floor)
        methods.append(
            PhpMethod(
                name=name,
                visibility=vis,
                start=trivia,
                body_start=body_start,
                end=class_end - 1,
                signature=sig,
                params=params,
                is_static=is_static,
            )
        )
    # Regions run to the next method (or class close). Brace-matching a 4000-line
    # controller is not reliable; declaration-bounded slices cannot drop methods.
    bounded: list[PhpMethod] = []
    for index, method in enumerate(methods):
        end = methods[index + 1].start if index + 1 < len(methods) else class_end - 1
        bounded.append(
            PhpMethod(
                name=method.name,
                visibility=method.visibility,
                start=method.start,
                body_start=method.body_start,
                end=end,
                signature=method.signature,
                params=method.params,
                is_static=method.is_static,
            )
        )
    methods = bounded
    for index, method in enumerate(methods[:-1]):
        if method.end != methods[index + 1].start:
            raise PhpStructuralError(
                "METHOD_REGION_GAP",
                f"{method.name}:{method.end} != {methods[index + 1].name}:{methods[index + 1].start}",
            )
        if method.end <= method.start:
            raise PhpStructuralError("METHOD_REGION_EMPTY", method.name)
    return PhpClassStructure(
        class_name=class_match.group(1),
        class_start=class_match.start(),
        class_open=brace_rel,
        class_end=class_end,
        imports=imports,
        methods=methods,
        namespace=namespace,
    )


def inspect_methods(src: str) -> list[PhpMethod]:
    return parse_php_structure(src).methods


def method_region(src: str, name: str) -> str:
    struct = parse_php_structure(src)
    for method in struct.methods:
        if method.name == name:
            return src[method.start : method.end]
    raise PhpStructuralError("METHOD_NOT_FOUND", name)


def public_symbol_inventory(src: str) -> tuple[str, ...]:
    struct = parse_php_structure(src)
    names = [m.name for m in struct.methods if m.visibility == "public"]
    return tuple(names)


def public_signature_inventory(src: str) -> tuple[str, ...]:
    struct = parse_php_structure(src)
    return tuple(
        re.sub(r"\s+", " ", m.signature).strip()
        for m in struct.methods
        if m.visibility == "public"
    )


def splice_region(src: str, start: int, end: int, replacement: str) -> str:
    if start < 0 or end > len(src) or start > end:
        raise PhpStructuralError("REGION_OUT_OF_BOUNDS", f"{start}:{end}")
    return src[:start] + replacement + src[end:]


def _normalize_stmt(stmt: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", stmt, flags=re.DOTALL)
    no_line = re.sub(r"(//|#)[^\n]*", " ", no_block)
    return re.sub(r"\s+", " ", no_line).strip()


def _statements_in_method(src: str, method: PhpMethod) -> list[tuple[str, str]]:
    """Return (normalized, original) statements at brace-depth 0."""
    body = src[method.body_start : method.end]
    open_b = body.find("{")
    close_b = body.rfind("}")
    if open_b < 0 or close_b < 0:
        return []
    inner = body[open_b + 1 : close_b]
    stmts: list[tuple[str, str]] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch in ("'", '"'):
            nxt = _skip_string(inner, i)
            buf.append(inner[i:nxt])
            i = nxt
            continue
        if ch == "/" or ch == "#":
            nxt = _skip_comment(inner, i)
            if nxt != i + 1 or (ch == "/" and i + 1 < n and inner[i + 1] in "/*"):
                i = nxt
                continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        buf.append(ch)
        if ch == ";" and depth == 0:
            original = "".join(buf).strip()
            stmt = _normalize_stmt(original)
            if stmt:
                stmts.append((stmt, original))
            buf = []
        i += 1
    return stmts


def unused_imports(src: str) -> list[PhpImport]:
    struct = parse_php_structure(src)
    # Drop import lines from the scan so the `use` identifiers don't self-match.
    stripped = src
    for imp in reversed(struct.imports):
        stripped = stripped[: imp.start] + " " * (imp.end - imp.start) + stripped[imp.end :]
    code = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.DOTALL)
    code = re.sub(r"(//|#)[^\n]*", " ", code)
    unused: list[PhpImport] = []
    for imp in struct.imports:
        if re.search(r"\b" + re.escape(imp.short_name) + r"\b", code):
            continue
        unused.append(imp)
    return unused


def remove_unused_imports(src: str) -> tuple[str, int]:
    to_drop = unused_imports(src)
    if not to_drop:
        return src, 0
    out = src
    for imp in reversed(to_drop):
        end = imp.end
        if end < len(out) and out[end] == "\n":
            end += 1
        out = out[: imp.start] + out[end:]
    # Collapse leftover domain-comment blocks that now sit above nothing.
    out = re.sub(r"(?m)^(?:\s*//\s*=+\s*\n)+\s*", "", out)
    return out, len(to_drop)


def _method_name_tokens(name: str) -> set[str]:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)
    return {p.casefold() for p in parts if len(p) >= 3}


def assign_methods_to_groups(
    method_names: list[str],
    *,
    groups: list[tuple[str, tuple[str, ...], tuple[str, ...]]],
    grouping_keys: tuple[str, ...] = (),
) -> list[tuple[str, list[str]]]:
    """groups: (key, explicit_methods, match_tokens). Unmatched keep relative order in residual."""
    remaining = list(method_names)
    assigned: list[tuple[str, list[str]]] = []
    used: set[str] = set()

    specs = list(groups)
    if not specs and grouping_keys:
        specs = [(key, (), ()) for key in grouping_keys]

    for key, explicit, tokens in specs:
        bucket: list[str] = []
        explicit_set = {m for m in explicit if m}
        token_set = {t.casefold() for t in tokens if t} or set()
        if not token_set:
            from backend.aix_core.runtime.executor.required_effects import grouping_tokens

            token_set = set(grouping_tokens(key))
        for name in remaining:
            if name in used:
                continue
            if explicit_set and name in explicit_set:
                bucket.append(name)
                used.add(name)
                continue
            if not explicit_set and token_set & _method_name_tokens(name):
                bucket.append(name)
                used.add(name)
        if bucket:
            assigned.append((key, bucket))

    if not assigned:
        # Cluster by dominant CamelCase token (generic, not a product map).
        clusters: dict[str, list[str]] = {}
        order: list[str] = []
        for name in remaining:
            tokens = [t for t in _method_name_tokens(name) if t not in {"get", "set", "update", "send", "make"}]
            token = tokens[0] if tokens else "other"
            if token not in clusters:
                clusters[token] = []
                order.append(token)
            clusters[token].append(name)
            used.add(name)
        assigned = [(key, clusters[key]) for key in order]

    residual = [n for n in remaining if n not in used]
    if residual:
        assigned.append(("other", residual))
    return assigned


def reorder_methods(
    src: str,
    ordered_public_names: list[str],
) -> str:
    struct = parse_php_structure(src)
    if not struct.methods:
        return src
    by_name: dict[str, list[PhpMethod]] = {}
    for method in struct.methods:
        by_name.setdefault(method.name, []).append(method)

    header_end = struct.methods[0].start
    footer_start = struct.methods[-1].end
    header = src[struct.class_open + 1 : header_end]
    footer = src[footer_start : struct.class_end - 1]

    constructors = [m for m in struct.methods if m.name == "__construct"]
    public = [m for m in struct.methods if m.visibility == "public" and m.name != "__construct"]
    others = [m for m in struct.methods if m.visibility != "public" and m.name != "__construct"]

    public_by_name: dict[str, list[PhpMethod]] = {}
    for method in public:
        public_by_name.setdefault(method.name, []).append(method)

    ordered: list[PhpMethod] = []
    ordered.extend(constructors)
    seen_ids: set[int] = {id(m) for m in constructors}
    for name in ordered_public_names:
        for method in public_by_name.get(name, []):
            if id(method) not in seen_ids:
                ordered.append(method)
                seen_ids.add(id(method))
    for method in public:
        if id(method) not in seen_ids:
            ordered.append(method)
            seen_ids.add(id(method))
    for method in others:
        if id(method) not in seen_ids:
            ordered.append(method)
            seen_ids.add(id(method))

    chunks = [src[m.start : m.end] for m in ordered]
    body = header
    if body and not body.endswith("\n"):
        body += "\n"
    body += "".join(chunks)
    if footer and not body.endswith("\n"):
        body += "\n"
    body += footer
    rebuilt = src[: struct.class_open + 1] + body + src[struct.class_end - 1 :]
    after = parse_php_structure(rebuilt)
    before_names = sorted(m.name for m in struct.methods)
    after_names = sorted(m.name for m in after.methods)
    if before_names != after_names:
        missing = sorted(set(before_names) - set(after_names))
        extra = sorted(set(after_names) - set(before_names))
        raise PhpStructuralError(
            "REORDER_DROPPED_METHODS",
            f"before={len(before_names)} after={len(after_names)} missing={missing} extra={extra}",
        )
    return rebuilt


def group_public_methods(
    src: str,
    *,
    groups: list[tuple[str, tuple[str, ...], tuple[str, ...]]] | None = None,
    grouping_keys: tuple[str, ...] = (),
) -> tuple[str, list[tuple[str, list[str]]]]:
    struct = parse_php_structure(src)
    public_names = [m.name for m in struct.methods if m.visibility == "public" and m.name != "__construct"]
    assigned = assign_methods_to_groups(
        public_names,
        groups=groups or [],
        grouping_keys=grouping_keys,
    )
    ordered: list[str] = []
    for _key, names in assigned:
        ordered.extend(names)
    updated = reorder_methods(src, ordered)
    return updated, assigned


def _free_vars(stmt: str) -> list[str]:
    names = []
    for match in _VAR_RE.finditer(stmt):
        name = match.group(0)
        if name not in names and name != "$this":
            names.append(name)
    return names


def _slug_helper_name(stmt: str, index: int) -> str:
    if re.search(r"INFORMATION_SCHEMA", stmt, re.I):
        return "schemaExists"
    if re.search(r"env\s*\(\s*['\"]DB_DATABASE", stmt, re.I):
        return "envDatabaseName"
    table = re.search(r"from\s+\$?\w*\.?([A-Za-z_][\w]*)", stmt, re.I)
    if table:
        parts = [p.title() for p in re.split(r"[_\s]+", table.group(1)) if p]
        slug = "query" + "".join(parts[:4])
        if len(slug) > 5:
            return slug[:80]
    return f"extractedQuery{index}"


def _existing_helpers(src: str, struct: PhpClassStructure) -> dict[str, str]:
    """Map normalized private/protected single-statement bodies (and return RHS) to name."""
    found: dict[str, str] = {}
    for method in struct.methods:
        if method.visibility == "public":
            continue
        stmts = _statements_in_method(src, method)
        if len(stmts) == 1:
            norm = stmts[0][0]
            found[norm] = method.name
            ret = re.match(r"^return\s+(.+)$", norm, re.IGNORECASE)
            if ret:
                found[ret.group(1).rstrip(";")] = method.name
                found[f"return {ret.group(1)}"] = method.name
    return found


def extract_repeated_query_helpers(src: str, *, min_sites: int = 2) -> tuple[str, list[str]]:
    """Extract repeated DB/query statements into private helpers (reuse existing when equal)."""
    struct = parse_php_structure(src)
    originals_by_norm: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for method in struct.methods:
        for norm, original in _statements_in_method(src, method):
            if len(norm) < 24 or not _DB_HINT_RE.search(norm):
                continue
            counts[norm] = counts.get(norm, 0) + 1
            originals_by_norm.setdefault(norm, [])
            if original not in originals_by_norm[norm]:
                originals_by_norm[norm].append(original)

    candidates = [(stmt, n) for stmt, n in counts.items() if n >= min_sites]
    candidates.sort(key=lambda item: (-item[1], -len(item[0])))
    if not candidates:
        return src, []

    updated = src
    created: list[str] = []
    for index, (stmt, _n) in enumerate(candidates[:3], start=1):
        struct = parse_php_structure(updated)
        existing = _existing_helpers(updated, struct)
        rhs_match = None
        assign = re.match(r"^(\$[A-Za-z_]\w*)\s*=\s*(.+)$", stmt)
        if assign:
            rhs_match = existing.get(assign.group(2).rstrip(";"))
        helper = existing.get(stmt) or rhs_match or _slug_helper_name(stmt, index)
        existing_names = {m.name for m in struct.methods}
        reused = helper in existing_names and (
            existing.get(stmt) == helper or rhs_match == helper
        )
        if helper in existing_names and not reused:
            helper = f"{helper}Extracted"
            reused = False

        vars_in_stmt = _free_vars(stmt)
        params = [v[1:] for v in vars_in_stmt if not v.startswith("$this")]
        returns_assign = False
        assigned_var = None
        if assign:
            assigned_var = assign.group(1)
            assigned = assigned_var[1:]
            if assigned in params:
                params = [p for p in params if p != assigned]
            returns_assign = True

        call_args = ", ".join(f"${p}" for p in params)
        if returns_assign and assigned_var:
            replacement = f"{assigned_var} = $this->{helper}({call_args});"
        else:
            replacement = f"$this->{helper}({call_args});"

        originals = originals_by_norm.get(stmt, [])
        nsub = sum(updated.count(original) for original in originals)
        if nsub < min_sites:
            continue
        for original in originals:
            if original in updated:
                updated = updated.replace(original, replacement)

        if not reused:
            param_list = ", ".join(f"${p}" for p in params)
            if returns_assign and assign:
                rhs = assign.group(2).rstrip(";")
                helper_body = (
                    f"\n\tprivate function {helper}({param_list})\n"
                    f"\t{{\n\t\treturn {rhs};\n\t}}\n"
                )
            else:
                helper_body = (
                    f"\n\tprivate function {helper}({param_list})\n"
                    f"\t{{\n\t\t{stmt.rstrip(';')};\n\t}}\n"
                )
            insert_at = parse_php_structure(updated).class_end - 1
            updated = updated[:insert_at] + helper_body + updated[insert_at:]
            created.append(helper)
        else:
            created.append(f"reused:{helper}")

    return updated, created


def outline_lines(src: str, *, max_chars: int = 4000) -> str:
    try:
        struct = parse_php_structure(src)
    except PhpStructuralError as exc:
        return f"(php structure unavailable: {exc.code})"
    lines = [
        f"class {struct.class_name}" + (f" namespace {struct.namespace}" if struct.namespace else ""),
        f"imports ({len(struct.imports)}):",
    ]
    for imp in struct.imports[:40]:
        alias = f" as {imp.alias}" if imp.alias else ""
        lines.append(f"  use {imp.fqcn}{alias};")
    lines.append("methods:")
    for method in struct.methods:
        start_line = src[: method.body_start].count("\n") + 1
        end_line = src[: method.end].count("\n")
        lines.append(
            f"  {method.visibility} {method.name}({method.params.strip()}) L{start_line}-{end_line}"
        )
    text = "\n".join(lines)
    return text[:max_chars]


def _service_class_name(key: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", str(key or ""))
    titled = "".join(p[:1].upper() + p[1:].lower() for p in parts if p)
    if not titled:
        titled = "Domain"
    if not titled.endswith("Service"):
        titled += "Service"
    return titled[:80]


def _forward_arg_list(params: str) -> str:
    names = re.findall(r"\$([A-Za-z_]\w*)", params or "")
    return ", ".join(f"${n}" for n in names)


def _this_method_calls(body: str) -> set[str]:
    return set(re.findall(r"\$this->([A-Za-z_]\w*)\s*\(", body or ""))


def _delegate_stub(method: PhpMethod, service_class: str) -> str:
    params = method.params.strip()
    args = _forward_arg_list(params)
    static_kw = "static " if method.is_static else ""
    if method.is_static:
        call = f"{service_class}::{method.name}({args})"
    else:
        call = f"(new {service_class}())->{method.name}({args})"
    return (
        f"\tpublic {static_kw}function {method.name}({params}) {{\n"
        f"\t\treturn {call};\n"
        f"\t}}\n"
    )


def _php_file_header(struct: PhpClassStructure, src: str) -> str:
    parts: list[str] = ["<?php\n"]
    if struct.namespace:
        parts.append(f"\nnamespace {struct.namespace};\n")
    uses = [imp.text.rstrip() for imp in struct.imports]
    if uses:
        parts.append("\n" + "\n".join(uses) + "\n")
    return "".join(parts)


def extract_methods_to_service_files(
    src: str,
    *,
    source_path: str,
    grouping_keys: tuple[str, ...] = (),
    groups: list[tuple[str, tuple[str, ...], tuple[str, ...]]] | None = None,
    min_groups: int = 3,
) -> tuple[str, dict[str, str], list[str]]:
    """Move public method groups into sibling *Service.php classes; leave thin delegates.

    Returns (updated_controller, {relative_path: content}, created_service_class_names).
    Rebuilds the controller method list in original order so public symbols cannot vanish
    through overlapping region splices (live S6: PRESERVE_PUBLIC_INTERFACE failures).
    """
    struct = parse_php_structure(src)
    before_public = [m.name for m in struct.methods if m.visibility == "public" and m.name != "__construct"]
    public_names = list(before_public)
    assigned = assign_methods_to_groups(
        public_names,
        groups=groups or [],
        grouping_keys=grouping_keys,
    )
    move_groups = [(k, names) for k, names in assigned if k.casefold() != "other" and names]
    if len(move_groups) < min_groups:
        clustered = assign_methods_to_groups(public_names, groups=[], grouping_keys=())
        move_groups = [(k, names) for k, names in clustered if names]
        move_groups = sorted(move_groups, key=lambda item: (-len(item[1]), item[0]))
        if len(move_groups) < min_groups:
            raise PhpStructuralError(
                "EXTRACT_TO_FILES_INSUFFICIENT_GROUPS",
                f"need>={min_groups} domain groups, found={len(move_groups)} keys={[k for k, _ in move_groups]}",
            )
        move_groups = move_groups[: max(min_groups, 3)]
    elif grouping_keys:
        from backend.aix_core.runtime.executor.required_effects import grouping_tokens

        preferred: list[tuple[str, list[str]]] = []
        used_names: set[str] = set()
        for key in grouping_keys:
            key_tokens = set(grouping_tokens(key))
            best: tuple[str, list[str]] | None = None
            best_score = 0
            for bucket_key, names in move_groups:
                if not names or all(n in used_names for n in names):
                    continue
                score = len(key_tokens & set(grouping_tokens(bucket_key)))
                score += sum(1 for n in names if key_tokens & _method_name_tokens(n))
                if score > best_score:
                    best_score = score
                    best = (key, [n for n in names if n not in used_names])
            if best and best[1] and best_score > 0:
                preferred.append(best)
                used_names.update(best[1])
        if len(preferred) >= min_groups:
            move_groups = preferred
        elif preferred:
            # Fill with largest unused clusters so we still reach min_groups.
            fillers = sorted(
                ((k, [n for n in names if n not in used_names]) for k, names in move_groups),
                key=lambda item: (-len(item[1]), item[0]),
            )
            for key, names in fillers:
                if len(preferred) >= min_groups:
                    break
                if names:
                    preferred.append((key, names))
                    used_names.update(names)
            move_groups = preferred
        else:
            move_groups = sorted(move_groups, key=lambda item: (-len(item[1]), item[0]))[
                : max(min_groups, 3)
            ]

    by_name: dict[str, list[PhpMethod]] = {}
    for method in struct.methods:
        by_name.setdefault(method.name, []).append(method)
    private_methods = {
        m.name: m for m in struct.methods if m.visibility in {"private", "protected"} and m.name != "__construct"
    }

    parent = str(Path(source_path.replace("\\", "/")).parent).replace("\\", "/")
    if parent in (".", ""):
        parent = ""

    created: dict[str, str] = {}
    service_names: list[str] = []
    method_to_service: dict[str, str] = {}
    helpers_copied: set[str] = set()

    for key, names in move_groups[:12]:
        service_class = _service_class_name(key)
        if service_class == struct.class_name:
            service_class = f"{service_class}Domain"
        # Avoid colliding with an already-chosen service class name.
        base = service_class
        suffix = 2
        while service_class in service_names:
            service_class = f"{base}{suffix}"
            suffix += 1
        service_names.append(service_class)
        helper_names: set[str] = set()
        method_chunks: list[str] = []
        for name in names:
            methods = by_name.get(name) or []
            if not methods:
                continue
            method = methods[0]
            # Skip methods that are already thin delegates to a service.
            body = src[method.start : method.end]
            if re.search(r"return\s+\(new\s+\w+Service\s*\(", body):
                continue
            method_to_service[name] = service_class
            method_chunks.append(body if body.endswith("\n") else body + "\n")
            helper_names |= _this_method_calls(body) & set(private_methods)

        if not method_chunks:
            service_names.pop()
            continue

        helper_chunks: list[str] = []
        pending = set(helper_names)
        seen_helpers: set[str] = set()
        while pending:
            hname = pending.pop()
            if hname in seen_helpers:
                continue
            seen_helpers.add(hname)
            helpers_copied.add(hname)
            helper = private_methods.get(hname)
            if helper is None:
                continue
            hchunk = src[helper.start : helper.end]
            helper_chunks.append(hchunk if hchunk.endswith("\n") else hchunk + "\n")
            pending |= _this_method_calls(hchunk) & set(private_methods) - seen_helpers

        body = "".join(method_chunks + helper_chunks)
        rel = f"{parent}/{service_class}.php" if parent else f"{service_class}.php"
        rel = rel.lstrip("/")
        created[rel] = (
            _php_file_header(struct, src)
            + f"\nclass {service_class}\n{{\n"
            + body
            + "}\n"
        )

    if len(created) < min_groups:
        raise PhpStructuralError(
            "EXTRACT_TO_FILES_TOO_FEW_SERVICES",
            f"created={len(created)} need>={min_groups}",
        )

    # Rebuild controller methods in original order: stub moved publics, keep the rest.
    if not struct.methods:
        return src, created, service_names
    kept_public_bodies = []
    for method in struct.methods:
        if method.visibility != "public" or method.name == "__construct":
            continue
        if method.name in method_to_service:
            continue
        kept_public_bodies.append(src[method.start : method.end])
    still_needed_helpers: set[str] = set()
    for body in kept_public_bodies:
        still_needed_helpers |= _this_method_calls(body) & set(private_methods)

    header_end = struct.methods[0].start
    footer_start = struct.methods[-1].end
    pieces: list[str] = [src[:header_end]]
    for method in struct.methods:
        if method.name in method_to_service and method.visibility == "public":
            pieces.append(_delegate_stub(method, method_to_service[method.name]))
            continue
        if method.visibility in {"private", "protected"} and method.name in helpers_copied:
            if method.name in still_needed_helpers:
                pieces.append(src[method.start : method.end])
            continue
        pieces.append(src[method.start : method.end])
    pieces.append(src[footer_start:])
    updated = "".join(pieces)

    after_public = [
        m.name for m in parse_php_structure(updated).methods if m.visibility == "public" and m.name != "__construct"
    ]
    if sorted(before_public) != sorted(after_public):
        missing = sorted(set(before_public) - set(after_public))
        extra = sorted(set(after_public) - set(before_public))
        raise PhpStructuralError(
            "EXTRACT_TO_FILES_LOST_PUBLIC",
            f"missing={missing} extra={extra}",
        )
    return updated, created, service_names


__all__ = [
    "PhpClassStructure",
    "PhpImport",
    "PhpMethod",
    "PhpStructuralError",
    "assign_methods_to_groups",
    "extract_methods_to_service_files",
    "extract_repeated_query_helpers",
    "group_public_methods",
    "inspect_methods",
    "method_region",
    "outline_lines",
    "parse_php_structure",
    "public_signature_inventory",
    "public_symbol_inventory",
    "remove_unused_imports",
    "reorder_methods",
    "splice_region",
    "unused_imports",
]
