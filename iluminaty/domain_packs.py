"""
ILUMINATY Domain Packs
======================
Pluggable domain-specialization layer for IPA WorldState.

Domain packs let ILUMINATY adapt semantic interpretation to the current context
(coding, trading, support, etc.) and expose policy hints without hard-coding
project-specific behavior in perception loops.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib
except Exception:  # pragma: no cover - Python >=3.11 ships tomllib
    tomllib = None  # type: ignore[assignment]


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def _norm_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen = set()
    for item in values:
        token = _norm(str(item))
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _cap(values: list[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return values
    return values[:limit]


def _match_keywords(text: str, keywords: list[str], prefix: str) -> list[str]:
    hay = _norm(text)
    out = []
    for kw in keywords:
        if kw and kw in hay:
            out.append(f"{prefix}:{kw}")
    return out


def _match_items(items: list[str], keywords: list[str], prefix: str) -> list[str]:
    if not items or not keywords:
        return []
    item_blob = " | ".join(_norm(x) for x in items)
    out = []
    for kw in keywords:
        if kw and kw in item_blob:
            out.append(f"{prefix}:{kw}")
    return out


@dataclass
class DomainStalenessPolicy:
    safe: int = 1500
    hybrid: int = 1200
    raw: int = 4000

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "DomainStalenessPolicy":
        source = data or {}
        return cls(
            safe=max(1, int(source.get("safe", 1500))),
            hybrid=max(1, int(source.get("hybrid", 1200))),
            raw=max(1, int(source.get("raw", 4000))),
        )

    def to_dict(self) -> dict:
        return {
            "safe": int(self.safe),
            "hybrid": int(self.hybrid),
            "raw": int(self.raw),
        }


@dataclass
class DomainPack:
    name: str
    description: str
    version: str = "1.0"
    source: str = "builtin"
    priority: int = 50
    match_workflows: list[str] = field(default_factory=list)
    match_apps: list[str] = field(default_factory=list)
    match_titles: list[str] = field(default_factory=list)
    match_entities: list[str] = field(default_factory=list)
    match_events: list[str] = field(default_factory=list)
    match_phases: list[str] = field(default_factory=list)
    match_visual: list[str] = field(default_factory=list)
    affordances: list[str] = field(default_factory=list)
    attention_hints: list[str] = field(default_factory=list)
    uncertainty_ceiling: Optional[float] = None
    min_confidence: float = 0.34
    staleness_policy: DomainStalenessPolicy = field(default_factory=DomainStalenessPolicy)
    metadata: dict = field(default_factory=dict)

    def score(
        self,
        *,
        workflow: str,
        app_name: str,
        window_title: str,
        task_phase: str,
        entities: list[str],
        events: list[str],
        visual_texts: list[str],
    ) -> tuple[float, list[str]]:
        signals: list[str] = []
        score = 0.0

        workflow_norm = _norm(workflow)
        if workflow_norm and workflow_norm in self.match_workflows:
            score += 0.34
            signals.append(f"workflow:{workflow_norm}")

        app_hits = _match_keywords(app_name, self.match_apps, "app")
        if app_hits:
            score += 0.24
            signals.extend(app_hits[:2])

        title_hits = _match_keywords(window_title, self.match_titles, "title")
        if title_hits:
            score += 0.16
            signals.extend(title_hits[:2])

        entity_hits = _match_items(entities, self.match_entities, "entity")
        if entity_hits:
            score += 0.10
            signals.extend(entity_hits[:2])

        event_hits = _match_items(events, self.match_events, "event")
        if event_hits:
            score += 0.08
            signals.extend(event_hits[:2])

        phase_norm = _norm(task_phase)
        if phase_norm and phase_norm in self.match_phases:
            score += 0.04
            signals.append(f"phase:{phase_norm}")

        visual_hits = _match_items(visual_texts, self.match_visual, "visual")
        if visual_hits:
            score += 0.04
            signals.extend(visual_hits[:2])

        # Confidence bonus for multiple independent signals.
        if len(signals) >= 3:
            score += 0.05
        if len(signals) >= 5:
            score += 0.03
        return (_clamp01(score), _cap(signals, 8))

    def public_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "source": self.source,
            "priority": int(self.priority),
            "min_confidence": round(float(self.min_confidence), 3),
            "uncertainty_ceiling": (
                None if self.uncertainty_ceiling is None else round(float(self.uncertainty_ceiling), 3)
            ),
            "match": {
                "workflows": list(self.match_workflows),
                "apps": list(self.match_apps),
                "titles": list(self.match_titles),
                "entities": list(self.match_entities),
                "events": list(self.match_events),
                "phases": list(self.match_phases),
                "visual": list(self.match_visual),
            },
            "affordances": list(self.affordances),
            "attention_hints": list(self.attention_hints),
            "staleness_policy": self.staleness_policy.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass
class DomainDecision:
    name: str
    confidence: float
    source: str = "builtin"
    matched_signals: list[str] = field(default_factory=list)
    affordances: list[str] = field(default_factory=list)
    attention_hints: list[str] = field(default_factory=list)
    uncertainty_ceiling: Optional[float] = None
    staleness_policy: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_world_fields(self) -> dict:
        return {
            "domain_pack": self.name,
            "domain_confidence": round(_clamp01(self.confidence), 3),
            "domain_source": self.source,
            "domain_signals": _cap(list(self.matched_signals), 8),
            "domain_policy": {
                "max_staleness_ms": dict(self.staleness_policy or {}),
            },
            "domain_context": dict(self.metadata or {}),
        }


def _pack_from_mapping(data: dict, *, source: str, fallback_name: str) -> DomainPack:
    match = data.get("match") or {}
    policy_data = data.get("staleness_policy") or data.get("policy") or {}
    if "max_staleness_ms" in policy_data and isinstance(policy_data.get("max_staleness_ms"), dict):
        policy_data = policy_data.get("max_staleness_ms") or {}

    name = _norm(data.get("name") or fallback_name)
    if not name:
        raise ValueError("domain pack name is required")
    desc = str(data.get("description") or f"Custom domain pack: {name}").strip()
    return DomainPack(
        name=name,
        description=desc,
        version=str(data.get("version", "1.0"))[:20],
        source=source,
        priority=int(data.get("priority", 50)),
        match_workflows=_norm_list(match.get("workflows") or data.get("match_workflows")),
        match_apps=_norm_list(match.get("apps") or data.get("match_apps")),
        match_titles=_norm_list(match.get("titles") or data.get("match_titles")),
        match_entities=_norm_list(match.get("entities") or data.get("match_entities")),
        match_events=_norm_list(match.get("events") or data.get("match_events")),
        match_phases=_norm_list(match.get("phases") or data.get("match_phases")),
        match_visual=_norm_list(match.get("visual") or data.get("match_visual")),
        affordances=_norm_list(data.get("affordances")),
        attention_hints=_norm_list(data.get("attention_hints")),
        uncertainty_ceiling=(
            None
            if data.get("uncertainty_ceiling") is None
            else _clamp01(float(data.get("uncertainty_ceiling")))
        ),
        min_confidence=_clamp01(float(data.get("min_confidence", 0.34))),
        staleness_policy=DomainStalenessPolicy.from_dict(policy_data),
        metadata=dict(data.get("metadata") or {}),
    )


def _builtin_packs() -> list[DomainPack]:
    return [
        DomainPack(
            name="coding",
            description="IDE + terminal workflows with code execution loops.",
            priority=90,
            match_workflows=["coding", "development", "debugging"],
            match_apps=["vscode", "visual studio code", "cursor", "pycharm", "intellij", "terminal", "powershell"],
            match_titles=[".py", ".ts", ".js", ".tsx", "stack trace", "pytest", "terminal", "pull request"],
            match_entities=["workflow:coding", "event:run_command", "event:text_appeared"],
            match_events=["text_appeared", "window_change"],
            match_phases=["editing", "interaction", "navigation"],
            affordances=["run_command", "read_file", "write_file", "find_ui_element"],
            attention_hints=["editor", "terminal", "problems_panel"],
            uncertainty_ceiling=0.62,
            staleness_policy=DomainStalenessPolicy(safe=1300, hybrid=1100, raw=4500),
            metadata={"sector": "engineering"},
        ),
        DomainPack(
            name="trading",
            description="Market UI operations with stricter context freshness.",
            priority=95,
            match_workflows=["trading", "finance", "market"],
            match_apps=["tradingview", "metatrader", "bybit", "binance", "coinbase", "kraken"],
            match_titles=["chart", "candlestick", "order", "position", "portfolio", "btc", "eth", "nasdaq", "spx"],
            match_entities=["workflow:finance", "event:page_navigation"],
            match_events=["page_navigation", "window_change"],
            match_phases=["interaction", "navigation"],
            match_visual=["chart", "order", "position", "candle"],
            affordances=["browser_navigate", "find_ui_element", "do_action"],
            attention_hints=["price_axis", "order_panel", "position_summary"],
            uncertainty_ceiling=0.45,
            staleness_policy=DomainStalenessPolicy(safe=650, hybrid=500, raw=2200),
            metadata={"sector": "fintech"},
        ),
        DomainPack(
            name="support",
            description="Ticketing and customer support operations.",
            priority=85,
            match_workflows=["support", "customer_support", "helpdesk"],
            match_apps=["zendesk", "freshdesk", "intercom", "salesforce", "gmail", "outlook"],
            match_titles=["ticket", "case", "customer", "inbox", "chat", "sla", "priority"],
            match_entities=["workflow:support", "event:text_appeared"],
            match_events=["text_appeared", "page_navigation"],
            match_phases=["interaction", "editing"],
            affordances=["browser_navigate", "type_text", "find_ui_element", "do_action"],
            attention_hints=["customer_name", "priority_badge", "reply_box"],
            uncertainty_ceiling=0.55,
            staleness_policy=DomainStalenessPolicy(safe=1200, hybrid=900, raw=3200),
            metadata={"sector": "customer_ops"},
        ),
        DomainPack(
            name="backoffice",
            description="High-volume administrative workflows and data entry.",
            priority=80,
            match_workflows=["backoffice", "operations", "admin", "data_entry"],
            match_apps=["excel", "google sheets", "sap", "oracle", "erp", "crm", "airtable", "notion"],
            match_titles=["invoice", "report", "approval", "form", "dashboard", "reconciliation"],
            match_entities=["workflow:backoffice", "event:page_navigation"],
            match_events=["page_navigation", "window_change"],
            match_phases=["editing", "interaction", "navigation"],
            affordances=["type_text", "scroll", "click", "do_action"],
            attention_hints=["row_focus", "approval_state", "validation_error"],
            uncertainty_ceiling=0.58,
            staleness_policy=DomainStalenessPolicy(safe=1400, hybrid=1200, raw=4000),
            metadata={"sector": "operations"},
        ),
        DomainPack(
            name="research",
            description="Knowledge extraction and analysis across browser docs.",
            priority=75,
            match_workflows=["research", "browsing", "analysis"],
            match_apps=["brave", "chrome", "edge", "firefox", "browser"],
            match_titles=["documentation", "docs", "wikipedia", "paper", "arxiv", "github"],
            match_entities=["workflow:browsing", "event:scrolling"],
            match_events=["scrolling", "page_navigation"],
            match_phases=["consuming", "navigation", "interaction"],
            affordances=["browser_navigate", "find_ui_element", "scroll", "do_action"],
            attention_hints=["search_box", "main_content", "references"],
            uncertainty_ceiling=0.64,
            staleness_policy=DomainStalenessPolicy(safe=1500, hybrid=1300, raw=4500),
            metadata={"sector": "knowledge"},
        ),
        DomainPack(
            name="qa_ops",
            description="QA and incident triage workflows across issue trackers.",
            priority=78,
            match_workflows=["qa", "quality", "testing", "incident"],
            match_apps=["jira", "github", "linear", "sentry", "datadog", "new relic"],
            match_titles=["issue", "bug", "incident", "error", "regression", "test run"],
            match_entities=["event:text_appeared", "event:window_change"],
            match_events=["text_appeared", "page_navigation"],
            match_phases=["interaction", "editing"],
            affordances=["run_command", "browser_navigate", "find_ui_element", "do_action"],
            attention_hints=["severity", "repro_steps", "logs_panel"],
            uncertainty_ceiling=0.57,
            staleness_policy=DomainStalenessPolicy(safe=1100, hybrid=900, raw=3500),
            metadata={"sector": "qa"},
        ),
    ]


class DomainPackRegistry:
    def __init__(self, *, custom_dir: Optional[str] = None):
        self._lock = threading.Lock()
        self._packs: dict[str, DomainPack] = {}
        self._custom_names: set[str] = set()
        self._custom_dir = Path(custom_dir) if custom_dir else self._default_custom_dir()

        for pack in _builtin_packs():
            self._packs[_norm(pack.name)] = pack
        self.reload_custom_packs()

    @classmethod
    def from_environment(cls) -> "DomainPackRegistry":
        configured = os.environ.get("ILUMINATY_DOMAIN_PACKS_DIR")
        return cls(custom_dir=configured if configured else None)

    @staticmethod
    def _default_custom_dir() -> Path:
        return Path.cwd() / "domain_packs"

    def has_pack(self, name: str) -> bool:
        with self._lock:
            return _norm(name) in self._packs

    def register(self, pack: DomainPack, *, is_custom: bool = False) -> None:
        key = _norm(pack.name)
        if not key:
            raise ValueError("domain pack name is required")
        with self._lock:
            self._packs[key] = pack
            if is_custom:
                self._custom_names.add(key)

    def reload_custom_packs(self) -> dict:
        loaded = 0
        errors: list[str] = []
        custom_dir = self._custom_dir
        try:
            custom_dir = custom_dir.expanduser()
        except Exception:
            pass  # noqa: suppressed Exception

        with self._lock:
            for key in list(self._custom_names):
                self._packs.pop(key, None)
            self._custom_names.clear()

        if not custom_dir.exists() or not custom_dir.is_dir():
            return {
                "custom_dir": str(custom_dir),
                "loaded": 0,
                "errors": [],
                "total": len(self.list_packs()),
            }

        files = sorted(
            [p for p in custom_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".toml"}],
            key=lambda p: p.name.lower(),
        )
        for path in files:
            try:
                if path.suffix.lower() == ".json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                else:
                    if tomllib is None:
                        raise RuntimeError("tomllib unavailable")
                    data = tomllib.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("pack definition must be an object")
                pack = _pack_from_mapping(data, source="custom", fallback_name=path.stem)
                self.register(pack, is_custom=True)
                loaded += 1
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        return {
            "custom_dir": str(custom_dir),
            "loaded": loaded,
            "errors": errors[:20],
            "total": len(self.list_packs()),
        }

    def list_packs(self) -> list[dict]:
        with self._lock:
            packs = list(self._packs.values())
        packs.sort(key=lambda p: (-int(p.priority), p.name))
        return [p.public_dict() for p in packs]

    def resolve(
        self,
        *,
        app_name: str,
        workflow: str,
        window_title: str,
        task_phase: str,
        entities: list[str],
        recent_events: list[dict],
        visual_facts: list[dict],
        override: Optional[str] = None,
    ) -> DomainDecision:
        with self._lock:
            packs = list(self._packs.values())
            override_pack = self._packs.get(_norm(override or "")) if override else None

        event_types = [
            _norm(e.get("type", ""))
            for e in recent_events[-8:]
            if _norm(e.get("type", ""))
        ]
        visual_texts = [
            _norm(f.get("text", ""))
            for f in visual_facts[:12]
            if _norm(f.get("text", ""))
        ]

        if override_pack is not None:
            score, signals = override_pack.score(
                workflow=workflow,
                app_name=app_name,
                window_title=window_title,
                task_phase=task_phase,
                entities=entities,
                events=event_types,
                visual_texts=visual_texts,
            )
            return self._build_decision(override_pack, max(score, 0.99), signals + ["override:forced"])

        best_pack: Optional[DomainPack] = None
        best_score = 0.0
        best_signals: list[str] = []
        for pack in packs:
            score, signals = pack.score(
                workflow=workflow,
                app_name=app_name,
                window_title=window_title,
                task_phase=task_phase,
                entities=entities,
                events=event_types,
                visual_texts=visual_texts,
            )
            if score > best_score:
                best_pack = pack
                best_score = score
                best_signals = signals
            elif score == best_score and best_pack and pack.priority > best_pack.priority:
                best_pack = pack
                best_signals = signals

        if best_pack is None or best_score < best_pack.min_confidence:
            return DomainDecision(
                name="general",
                confidence=0.0,
                source="builtin",
                matched_signals=[],
                affordances=[],
                attention_hints=[],
                uncertainty_ceiling=None,
                staleness_policy=DomainStalenessPolicy().to_dict(),
                metadata={"fallback": True},
            )
        return self._build_decision(best_pack, best_score, best_signals)

    def _build_decision(self, pack: DomainPack, score: float, signals: list[str]) -> DomainDecision:
        return DomainDecision(
            name=pack.name,
            confidence=score,
            source=pack.source,
            matched_signals=_cap(signals, 8),
            affordances=_cap(list(pack.affordances), 12),
            attention_hints=_cap(list(pack.attention_hints), 4),
            uncertainty_ceiling=pack.uncertainty_ceiling,
            staleness_policy=pack.staleness_policy.to_dict(),
            metadata={"version": pack.version, "priority": pack.priority, **(pack.metadata or {})},
        )
