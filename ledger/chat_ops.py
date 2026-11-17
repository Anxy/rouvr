"""Supervisor chat op catalog: NL phrases + chip ids → ledger mutations.

Mutations run only through named ops (fail closed). Secrets never travel in
chat text — ops with ``needs_dialog`` return a UI handoff instead of running.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# Typed confirm / cancel for a pending op_offer (distinct from plan offer yes/no).
OP_CONFIRM = {
    "yes", "y", "confirm", "do it", "go ahead", "ok", "okay", "approve",
    "approved", "ship it", "proceed", "run it",
}
OP_CANCEL = {
    "no", "n", "cancel", "never mind", "nevermind", "stop", "abort",
    "don't", "do not",
}


@dataclass(frozen=True)
class OpSpec:
    id: str
    label: str
    phrases: tuple[str, ...] = ()
    # Keys pulled from client args or conversation context.
    arg_keys: tuple[str, ...] = ()
    # If set, chat must not execute — UI opens this dialog instead.
    needs_dialog: str | None = None
    # If True, NL match posts an op_offer; chip click / confirm runs it.
    confirm: bool = False
    # Destructive / rare — always require explicit chip or confirm.
    require_confirm: bool = False


@dataclass
class OpMatch:
    op: str
    args: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    needs_dialog: str | None = None
    confirm: bool = False
    source: str = "phrase"  # phrase | explicit | confirm | cancel


@dataclass
class ChatContext:
    """Resolved conversation context for phrase matching."""
    tenant: str
    project: str
    plan_slug: str | None = None
    plan_entry_id: int | None = None
    task_ids: list[int] = field(default_factory=list)
    knowledge_key: str | None = None
    # Latest pending op_offer from prior turn (if any).
    pending_offer: dict[str, Any] | None = None


OPS: dict[str, OpSpec] = {}


def _reg(spec: OpSpec) -> OpSpec:
    OPS[spec.id] = spec
    return spec


# --- Plans -----------------------------------------------------------------
_reg(OpSpec(
    "plan.convert", "Convert plan",
    phrases=("convert", "convert plan", "convert this plan", "materialize",
             "turn into tasks", "make tasks"),
    arg_keys=("plan_entry",),
))
_reg(OpSpec(
    "plan.keep", "Keep as notes",
    phrases=("keep", "just keep it", "keep as notes", "don't convert"),
    arg_keys=("plan_entry",),
))
_reg(OpSpec(
    "plan.edit", "Edit plan",
    phrases=("edit plan", "revise plan", "change the plan"),
    arg_keys=("plan_entry",),
))
_reg(OpSpec(
    "plan.approve", "Approve plan",
    phrases=("approve plan", "approve this plan", "approve the plan",
             "materialize plan", "confirm plan"),
    arg_keys=("slug",),
))
_reg(OpSpec(
    "plan.execute", "Dispatch plan",
    phrases=("execute plan", "dispatch plan", "dispatch", "assign workers",
             "record execute"),
    arg_keys=("slug",),
    confirm=True,
))
_reg(OpSpec(
    "plan.start", "Start delivery",
    # Direct start (chip after repo is bound). NL prefers the dialog handoff.
    phrases=(),
    arg_keys=("slug",),
))
_reg(OpSpec(
    "plan.restart", "Restart delivery",
    phrases=("restart delivery", "restart plan", "rebuild from coder",
             "restart from build"),
    arg_keys=("slug",),
    confirm=True,
    require_confirm=True,
))
_reg(OpSpec(
    "plan.start.dialog", "Start delivery…",
    phrases=("start delivery", "start the plan", "start plan", "run delivery",
             "begin delivery", "kick off delivery"),
    arg_keys=("slug", "title", "tenant", "project"),
    needs_dialog="delivery_start",
))

# --- Delivery / tasks ------------------------------------------------------
_reg(OpSpec(
    "task.start", "Start task",
    phrases=("start task", "resume task", "resume delivery", "continue delivery"),
    arg_keys=("task_id",),
))
_reg(OpSpec(
    "task.restart", "Restart task",
    phrases=("restart task", "restart this task"),
    arg_keys=("task_id",),
    confirm=True,
    require_confirm=True,
))
_reg(OpSpec(
    "task.transition.done", "Approve task",
    phrases=("approve task", "mark done", "mark as done", "close task",
             "accept task", "approve #"),
    arg_keys=("task_id",),
))
_reg(OpSpec(
    "task.transition.open", "Retry task",
    phrases=("retry task", "reopen task", "send back", "retry #"),
    arg_keys=("task_id",),
))
_reg(OpSpec(
    "task.transition.claimed", "Request changes",
    phrases=("request changes", "send to builder", "needs changes"),
    arg_keys=("task_id", "note"),
))
_reg(OpSpec(
    "task.seal", "Retry seal",
    phrases=("retry seal", "seal task", "reseal", "seal again"),
    arg_keys=("task_id",),
))
_reg(OpSpec(
    "task.patch", "Edit task",
    phrases=("rename task", "edit task", "update task title"),
    arg_keys=("task_id", "title", "intent"),
    confirm=True,
))
_reg(OpSpec(
    "task.evidence", "Add evidence",
    phrases=("add evidence", "attach evidence", "note evidence"),
    arg_keys=("task_id", "kind", "body"),
    confirm=True,
))
_reg(OpSpec(
    "task.retire", "Retire task",
    phrases=("retire task", "archive task", "drop task"),
    arg_keys=("task_id",),
    confirm=True,
    require_confirm=True,
))
_reg(OpSpec(
    "task.git_grant", "Grant GitHub PAT",
    phrases=("grant pat", "git grant", "add github token", "grant token",
             "grant access"),
    arg_keys=("task_id",),
    needs_dialog="git_grant",
))
_reg(OpSpec(
    "task.create", "Create task",
    phrases=("create task", "new task", "add task"),
    arg_keys=("title", "intent"),
    confirm=True,
))

# --- Projects --------------------------------------------------------------
_reg(OpSpec(
    "project.create", "Create project",
    phrases=("create project", "new project", "add project"),
    arg_keys=("name", "title"),
    confirm=True,
))
_reg(OpSpec(
    "project.patch", "Update project",
    phrases=("rename project", "update project", "edit project"),
    arg_keys=("name", "title", "new_name"),
    confirm=True,
))
_reg(OpSpec(
    "project.archive", "Archive project",
    phrases=("archive project", "archive this project"),
    arg_keys=("name",),
    confirm=True,
    require_confirm=True,
))
_reg(OpSpec(
    "project.restore", "Restore project",
    phrases=("restore project", "unarchive project"),
    arg_keys=("name",),
))
_reg(OpSpec(
    "project.exec_spec", "Bind repo",
    phrases=("bind repo", "set repo", "exec spec", "set github repo",
             "use repo", "bind github"),
    arg_keys=("name", "repo"),
    confirm=True,
))
_reg(OpSpec(
    "project.git_grant", "Project GitHub PAT",
    phrases=("project grant", "project pat", "default github token"),
    arg_keys=("name",),
    needs_dialog="project_git_grant",
))

# --- Knowledge -------------------------------------------------------------
_reg(OpSpec(
    "knowledge.review", "Review knowledge",
    phrases=("verify knowledge", "verify entry", "reject knowledge",
             "needs revision"),
    arg_keys=("key", "verdict"),
))
_reg(OpSpec(
    "knowledge.pin", "Pin knowledge",
    phrases=("pin knowledge", "pin entry", "unpin knowledge", "unpin entry"),
    arg_keys=("key", "pinned"),
))
_reg(OpSpec(
    "knowledge.archive", "Archive knowledge",
    phrases=("archive knowledge", "archive entry", "unarchive knowledge"),
    arg_keys=("key", "archived"),
))
_reg(OpSpec(
    "knowledge.version", "New knowledge version",
    phrases=("new version", "version knowledge", "update knowledge body"),
    arg_keys=("key", "body", "title"),
    confirm=True,
))

# --- Session ---------------------------------------------------------------
_reg(OpSpec(
    "session.logout", "Sign out",
    phrases=("log out", "logout", "sign out", "signout"),
    needs_dialog="logout",
))


_TASK_ID_RE = re.compile(
    r"(?:#|task\s+)(\d+)\b",
    re.IGNORECASE,
)
_REPO_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b",
)
_SLUG_RE = re.compile(
    r"\bplan(?:\s+slug)?\s+[\"']?([a-z0-9][a-z0-9._-]{1,80})",
    re.IGNORECASE,
)


def normalize(message: str) -> str:
    return " ".join((message or "").strip().lower().split())


def action_chip(
    op_id: str,
    *,
    args: dict | None = None,
    label: str | None = None,
) -> dict:
    spec = OPS[op_id]
    chip = {
        "id": op_id,
        "label": label or spec.label,
        "op": op_id,
        "args": dict(args or {}),
    }
    if spec.needs_dialog:
        chip["needs_dialog"] = spec.needs_dialog
    return chip


def contextual_actions(ctx: ChatContext, *, plan_status: str | None = None) -> list[dict]:
    """Suggest chips from known context (no model involved)."""
    out: list[dict] = []
    if ctx.plan_slug:
        if plan_status in (None, "draft", "agreed"):
            out.append(action_chip("plan.approve", args={"slug": ctx.plan_slug}))
        if plan_status in (None, "materialized", "agreed"):
            out.append(action_chip(
                "plan.start.dialog",
                args={
                    "slug": ctx.plan_slug,
                    "title": ctx.plan_slug,
                    "tenant": ctx.tenant,
                    "project": ctx.project,
                },
                label="Start delivery…",
            ))
            out.append(action_chip("plan.execute", args={"slug": ctx.plan_slug}))
    for tid in ctx.task_ids[:5]:
        out.append(action_chip(
            "task.start", args={"task_id": tid}, label=f"Start #{tid}"))
        out.append(action_chip(
            "task.transition.done", args={"task_id": tid},
            label=f"Approve #{tid}"))
        out.append(action_chip(
            "task.git_grant", args={"task_id": tid},
            label=f"Grant PAT #{tid}"))
    if ctx.knowledge_key:
        out.append(action_chip(
            "knowledge.review",
            args={"key": ctx.knowledge_key, "verdict": "verified"},
            label="Verify knowledge",
        ))
        out.append(action_chip(
            "knowledge.pin",
            args={"key": ctx.knowledge_key, "pinned": True},
            label="Pin knowledge",
        ))
    if not out:
        out.append(action_chip(
            "project.create",
            args={},
            label="Create project",
        ))
        out.append(action_chip(
            "project.exec_spec",
            args={"name": ctx.project},
            label="Bind repo",
        ))
        out.append(action_chip(
            "project.git_grant",
            args={"name": ctx.project},
            label="Project PAT…",
        ))
    return out


def _extract_task_id(norm: str, ctx: ChatContext) -> int | None:
    m = _TASK_ID_RE.search(norm)
    if m:
        return int(m.group(1))
    if len(ctx.task_ids) == 1:
        return ctx.task_ids[0]
    return None


def _extract_slug(norm: str, ctx: ChatContext) -> str | None:
    m = _SLUG_RE.search(norm)
    if m:
        return m.group(1)
    return ctx.plan_slug


def _extract_repo(message: str) -> str | None:
    m = _REPO_RE.search(message or "")
    if not m:
        return None
    repo = m.group(1)
    if repo.lower().startswith("http"):
        return None
    return repo


def _fill_args(spec: OpSpec, message: str, norm: str, ctx: ChatContext,
               explicit: dict | None) -> dict[str, Any]:
    args = dict(explicit or {})
    for key in spec.arg_keys:
        if key in args and args[key] not in (None, ""):
            continue
        if key in ("task_id",):
            tid = _extract_task_id(norm, ctx)
            if tid is not None:
                args["task_id"] = tid
        elif key == "slug":
            slug = _extract_slug(norm, ctx)
            if slug:
                args["slug"] = slug
        elif key == "plan_entry":
            if ctx.plan_entry_id is not None:
                args["plan_entry"] = ctx.plan_entry_id
        elif key == "name":
            # "create project foo" / "archive project foo" → foo
            mname = re.search(
                r"\b(?:create|new|add|archive|restore|rename|update|edit|bind)\s+"
                r"project\s+([A-Za-z0-9._-]{1,64})\b",
                message or "",
                re.IGNORECASE,
            )
            if mname:
                args["name"] = mname.group(1)
            else:
                args.setdefault("name", ctx.project)
        elif key == "repo":
            repo = _extract_repo(message)
            if repo:
                args["repo"] = repo
        elif key == "key":
            if ctx.knowledge_key:
                args["key"] = ctx.knowledge_key
        elif key == "verdict":
            if "reject" in norm:
                args["verdict"] = "rejected"
            elif "revision" in norm:
                args["verdict"] = "needs_revision"
            else:
                args["verdict"] = "verified"
        elif key == "pinned":
            args["pinned"] = "unpin" not in norm
        elif key == "archived":
            args["archived"] = "unarchive" not in norm
        elif key == "title" and "task" in spec.id:
            # Remainder after phrase — best-effort.
            pass
        elif key in ("tenant", "project"):
            args.setdefault("tenant", ctx.tenant)
            args.setdefault("project", ctx.project)
    if "title" in spec.arg_keys and "slug" in args and "title" not in args:
        args["title"] = args["slug"]
    return args


def match_explicit(op_id: str, op_args: dict | None, ctx: ChatContext) -> OpMatch:
    if op_id not in OPS:
        raise ValueError(f"unknown op {op_id!r}")
    spec = OPS[op_id]
    args = _fill_args(spec, "", "", ctx, op_args)
    return OpMatch(
        op=op_id,
        args=args,
        label=spec.label,
        needs_dialog=spec.needs_dialog,
        confirm=False,
        source="explicit",
    )


def match_pending(message: str, ctx: ChatContext) -> OpMatch | None:
    pending = ctx.pending_offer
    if not pending or not isinstance(pending, dict):
        return None
    norm = normalize(message)
    if not norm:
        return None
    if norm in OP_CONFIRM:
        op_id = pending.get("op")
        if not isinstance(op_id, str) or op_id not in OPS:
            return None
        spec = OPS[op_id]
        args = dict(pending.get("args") or {})
        return OpMatch(
            op=op_id,
            args=args,
            label=spec.label,
            needs_dialog=spec.needs_dialog,
            confirm=False,
            source="confirm",
        )
    if norm in OP_CANCEL:
        return OpMatch(
            op="__cancel__",
            args={},
            label="Cancelled",
            source="cancel",
        )
    return None


def match_phrase(message: str, ctx: ChatContext) -> OpMatch | None:
    """Longest-phrase wins. Skips bare plan yes/no that the offer path owns."""
    norm = normalize(message)
    if not norm:
        return None
    # Bare convert/keep/edit words are owned by pending plan offers.
    from ledger import chat as _chatmod
    if norm in _chatmod.PLAN_YES or norm in _chatmod.PLAN_NO or norm in _chatmod.PLAN_EDIT:
        return None

    best: tuple[int, OpSpec] | None = None
    for spec in OPS.values():
        for phrase in spec.phrases:
            p = normalize(phrase)
            if not p:
                continue
            if norm == p or norm.startswith(p + " ") or f" {p} " in f" {norm} ":
                score = len(p)
                if best is None or score > best[0]:
                    best = (score, spec)
    if best is None:
        return None
    _, spec = best
    args = _fill_args(spec, message, norm, ctx, None)
    needs_confirm = spec.confirm or spec.require_confirm
    # Missing required identity args → still match but confirm with clarifying chip.
    missing = [k for k in spec.arg_keys
               if k not in ("note", "title", "intent", "body", "kind",
                            "new_name", "tenant", "project", "pinned",
                            "archived", "verdict", "repo")
               and (k not in args or args[k] in (None, ""))]
    if missing and not spec.needs_dialog:
        needs_confirm = True
    return OpMatch(
        op=spec.id,
        args=args,
        label=spec.label,
        needs_dialog=spec.needs_dialog,
        confirm=needs_confirm,
        source="phrase",
    )


def missing_args(match: OpMatch) -> list[str]:
    spec = OPS.get(match.op)
    if not spec or spec.needs_dialog:
        return []
    required = {
        "plan.approve": ("slug",),
        "plan.execute": ("slug",),
        "plan.start": ("slug",),
        "plan.restart": ("slug",),
        "task.start": ("task_id",),
        "task.restart": ("task_id",),
        "task.transition.done": ("task_id",),
        "task.transition.open": ("task_id",),
        "task.transition.claimed": ("task_id",),
        "task.seal": ("task_id",),
        "task.retire": ("task_id",),
        "task.patch": ("task_id",),
        "task.evidence": ("task_id", "body"),
        "task.create": ("title",),
        "project.create": ("name",),
        "project.patch": ("name",),
        "project.archive": ("name",),
        "project.restore": ("name",),
        "project.exec_spec": ("name", "repo"),
        "knowledge.review": ("key", "verdict"),
        "knowledge.pin": ("key",),
        "knowledge.archive": ("key",),
        "knowledge.version": ("key", "body"),
        "plan.convert": ("plan_entry",),
        "plan.keep": ("plan_entry",),
        "plan.edit": ("plan_entry",),
    }.get(match.op, ())
    return [k for k in required if match.args.get(k) in (None, "")]


# Type alias for dispatch callables installed by the ledger app.
OpRunner = Callable[[OpMatch, Any], dict]
