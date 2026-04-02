"""
ILUMINATY - Capa 6: Task Planner / Decomposer
================================================
Descompone tareas complejas en sub-acciones con dependencias.

"Crea un archivo Python con tests y ejecutalos" →
  1. write_file("test_app.py", content)
  2. terminal_exec("python -m pytest test_app.py")  [depends: 1]
  3. read_file("test_results.txt")  [depends: 2]

Genera planes ejecutables que el resolver puede correr.
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """Un paso en un plan de ejecucion."""
    step_id: int
    action: str
    params: dict
    category: str  # safe, normal, destructive
    depends_on: list[int] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "params": self.params,
            "category": self.category,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class ExecutionPlan:
    """Un plan completo de ejecucion."""
    plan_id: str
    description: str
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.plan_id:
            self.plan_id = f"plan_{int(self.created_at * 1000) % 1000000}"

    def add_step(self, action: str, params: dict, category: str = "normal",
                 depends_on: Optional[list[int]] = None) -> PlanStep:
        step_id = len(self.steps) + 1
        step = PlanStep(
            step_id=step_id,
            action=action,
            params=params,
            category=category,
            depends_on=depends_on or [],
        )
        self.steps.append(step)
        return step

    def get_next_steps(self) -> list[PlanStep]:
        """Retorna pasos que pueden ejecutarse (dependencias completadas)."""
        ready = []
        completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.COMPLETED}
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in step.depends_on):
                ready.append(step)
        return ready

    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self.steps)

    def has_failed(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def to_dict(self) -> dict:
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return {
            "plan_id": self.plan_id,
            "description": self.description,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "progress": f"{completed}/{len(self.steps)}",
            "created_at": time.strftime("%H:%M:%S", time.localtime(self.created_at)),
        }


class TaskPlanner:
    """
    Descompone tareas en planes ejecutables.
    Los planes se pueden inspeccionar (dry run) antes de ejecutar.
    """

    def __init__(self):
        self._plans: dict[str, ExecutionPlan] = {}
        self._templates: dict[str, list[dict]] = {}
        self._behavior_cache = None
        self._register_templates()

    def set_behavior_cache(self, cache) -> None:
        """Inject optional AppBehaviorCache for history-based planning hints."""
        self._behavior_cache = cache

    def suggest_step_hints(self, *, action: str, app_name: str, window_title: str) -> dict:
        """Return per-step operational hints when behavior history exists."""
        if not self._behavior_cache:
            return {"found": False, "reason": "behavior_cache_unavailable"}
        try:
            return self._behavior_cache.suggest(
                action=action,
                app_name=app_name,
                window_title=window_title,
            )
        except Exception as e:
            return {"found": False, "reason": f"behavior_cache_error:{e}"}

    def _register_templates(self):
        """Templates para tareas comunes multi-paso."""
        self._templates["create_and_test"] = [
            {"action": "write_file", "params_keys": ["path", "content"], "category": "normal"},
            {"action": "terminal_exec", "params_keys": ["test_command"], "category": "normal", "depends": [1]},
        ]
        self._templates["git_commit_push"] = [
            {"action": "git_status", "params_keys": [], "category": "safe"},
            {"action": "git_add", "params_keys": ["files"], "category": "normal", "depends": [1]},
            {"action": "git_commit", "params_keys": ["message"], "category": "normal", "depends": [2]},
            {"action": "git_push", "params_keys": [], "category": "destructive", "depends": [3]},
        ]
        self._templates["search_and_replace"] = [
            {"action": "search_files", "params_keys": ["pattern", "contains"], "category": "safe"},
            {"action": "read_file", "params_keys": ["path"], "category": "safe", "depends": [1]},
            {"action": "write_file", "params_keys": ["path", "content"], "category": "normal", "depends": [2]},
        ]

    def create_plan(self, description: str) -> ExecutionPlan:
        """Crea un plan vacio."""
        plan = ExecutionPlan(plan_id="", description=description)
        self._plans[plan.plan_id] = plan
        return plan

    def create_from_template(self, template_name: str, params: dict) -> Optional[ExecutionPlan]:
        """Crea un plan a partir de un template."""
        template = self._templates.get(template_name)
        if not template:
            return None

        plan = self.create_plan(f"Template: {template_name}")
        for step_def in template:
            step_params = {}
            for key in step_def.get("params_keys", []):
                if key in params:
                    step_params[key] = params[key]
            plan.add_step(
                action=step_def["action"],
                params=step_params,
                category=step_def.get("category", "normal"),
                depends_on=step_def.get("depends", []),
            )
        plan.status = PlanStatus.READY
        return plan

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        return self._plans.get(plan_id)

    def list_plans(self, count: int = 10) -> list[dict]:
        plans = sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)
        return [p.to_dict() for p in plans[:count]]

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    @property
    def stats(self) -> dict:
        return {
            "total_plans": len(self._plans),
            "active_plans": sum(1 for p in self._plans.values() if p.status == PlanStatus.RUNNING),
            "behavior_cache": bool(self._behavior_cache is not None),
            "templates": list(self._templates.keys()),
        }
