"""F11 scale runtime bridge — project WorkGraph when AIX_FRAMEWORK_SCALE is on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.framework.compiler.stages.graph import GraphStage
from backend.aix_core.framework.scale.batch_scheduler import BatchScheduler
from backend.aix_core.framework.scale.hibernation import CycleHibernationManager
from backend.aix_core.framework.scale.work_graph_store import (
    DurableWorkGraphStore,
    WorkGraphStore,
)
from backend.aix_core.framework.work.models import WorkGraph
from backend.aix_core.runtime.objective.contracts import PlanRevisionV1


@dataclass
class ScaleProjection:
    graph: WorkGraph
    store: WorkGraphStore | DurableWorkGraphStore
    scheduler: BatchScheduler
    hibernation: CycleHibernationManager


def scale_enabled(flags: object | None) -> bool:
    return bool(getattr(flags, "scale", False))


def project_work_graph_from_plan(
    plan: PlanRevisionV1,
    *,
    store: WorkGraphStore | DurableWorkGraphStore,
    existing_graph: WorkGraph | None = None,
) -> WorkGraph:
    """Project admitted plan → WorkGraph into the durable/in-memory store."""
    graph = existing_graph or GraphStage.from_plan(plan)
    # Deterministic identity: prefer plan_id as graph_id when emitting
    if existing_graph is None and graph.work_id != plan.objective_id:
        graph = graph.model_copy(update={"work_id": plan.objective_id})
    store.save_work_graph(graph)
    return graph


def open_scale_session(
    plan: PlanRevisionV1,
    *,
    store: WorkGraphStore | DurableWorkGraphStore,
    graph: WorkGraph | None = None,
    checkpoint_append: Any = None,
) -> ScaleProjection:
    g = project_work_graph_from_plan(plan, store=store, existing_graph=graph)
    scheduler = BatchScheduler(store)
    hibernation = CycleHibernationManager(
        store, scheduler, checkpoint_append=checkpoint_append
    )
    return ScaleProjection(graph=g, store=store, scheduler=scheduler, hibernation=hibernation)


__all__ = [
    "ScaleProjection",
    "open_scale_session",
    "project_work_graph_from_plan",
    "scale_enabled",
]
