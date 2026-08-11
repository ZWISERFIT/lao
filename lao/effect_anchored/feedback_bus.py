"""
Feedback Bus — LAO 2.7 P0-① 步骤③
=================================

三层数据不再单向流动。Feedback Bus 让经验/锚点/路由形成闭环回流：

    L3 经验 → L2 Anchor 升级 → L1 Router 优化
      ↑__________↖______________↙

方向:
    L3→L2: 经验事件 → 促生/强化锚点(DecisionAnchor/CognitiveAnchor)
    L2→L1: 锚点(尤其失败约束) → 反向影响 Router 决策(预算/降级/模型选择)
    L1→L3: 路由结果(成功/失败) → 沉淀为经验事件 → 回流L2

核心机制:
    emit(event)              — 经验/决策事件入总线
    L3→L2: promote_to_anchor — 事件累积 → 升级为锚点(对齐Experience Atom Engine)
    L2→L1: constrain_route   — 锚点(约束) → 给 Router 的 route() 注入约束
    L1→L3: capture_route_result — 路由结果 → 回写事件

这实现创始人"自动错误/经验萃取形成复利和自动化闭环"(问题4)的接口层:
    不再需要手动 lao atom → lao verify → 手动注册,
    Feedback Bus 提供程序化管道: 失败事件 → 自动萃取 → 自动生成约束 → 自动激活
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime, timezone
import json


@dataclass
class FeedbackEvent:
    """总线事件（经验/决策/路由的统一载体）。"""
    event_type: str            # "error" | "pattern" | "decision" | "route_result"
    source: str                # 来源层: "l1_router" | "l2_anchor" | "l3_experience" | "agent"
    payload: Dict[str, Any]
    severity: str = "info"     # "info" | "warning" | "critical"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def error_fingerprint(provider: str, model: str, error_type: str) -> str:
    """生成错误指纹(sha256 前12位)。"""
    import hashlib
    raw = f"{provider}|{model}|{error_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class ErrorImmunity:
    """错误免疫(跨 provider 共享)。"""

    def __init__(self, store: Optional[dict] = None):
        self._immune: Dict[str, str] = store or {}

    def mark_immune(self, fingerprint: str, error_type: str = "") -> None:
        self._immune[fingerprint] = error_type or "unknown"

    def is_immune(self, fingerprint: str) -> bool:
        return fingerprint in self._immune

    def shared_immunity(self, error_type: str) -> List[str]:
        return [fp for fp, et in self._immune.items() if et == error_type]

    def to_dict(self) -> Dict[str, str]:
        return dict(self._immune)


class _FeedbackBusImmunityMixin:
    """给 FeedbackBus 挂载免疫能力(避免改动其 __init__)。"""

    def _ensure_immunity(self) -> ErrorImmunity:
        if not hasattr(self, "_immunity"):
            self._immunity = ErrorImmunity()
        return self._immunity

    def mark_immune(self, provider: str, model: str, error_type: str) -> str:
        """标记某错误的指纹为免疫。返回指纹。"""
        fp = error_fingerprint(provider, model, error_type)
        self._ensure_immunity().mark_immune(fp, error_type)
        return fp

    def is_immune(self, provider: str, model: str, error_type: str) -> bool:
        """该 provider+model+错误类型是否已免疫(含跨 provider 共享免疫)。

        同 error_type 已有任一免疫 → 其他 provider 的同类错误也视为免疫
        (GLM403 免疫后 → deepseek/X 的 403 也自动免疫)。
        """
        imm = self._ensure_immunity()
        fp = error_fingerprint(provider, model, error_type)
        if imm.is_immune(fp):
            return True
        # 跨 provider 共享: 同 error_type 已有免疫 → 视为免疫
        return len(imm.shared_immunity(error_type)) > 0

    def shared_immunity(self, error_type: str) -> List[str]:
        """同错误类型跨 provider 的已免疫指纹(GLM403→DS同类型也免疫)。"""
        return self._ensure_immunity().shared_immunity(error_type)


class FeedbackBus(_FeedbackBusImmunityMixin):
    """
    L1/L2/L3 双向反馈总线。

    emit:     事件入总线
    L3→L2:   promote_to_anchor  — 经验事件 → 锚点(调用认知anchor存储)
    L2→L1:   constrain_route    — 锚点约束 → Router 路由优化钩子
    L1→L3:   capture_route_result — 路由结果 → 经验回流
    """

    def __init__(self):
        self._events: List[FeedbackEvent] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._route_constraints: List[Dict[str, Any]] = []
        # LAO v3.1 P0-18: 内嵌三层认知系统(CognitiveSystem)
        from lao.effect_anchored.cognitive_engine import CognitiveSystem
        self.cognitive = CognitiveSystem()

    # -- 事件入总线 ---------------------------------------------------------

    def emit(self, event: FeedbackEvent) -> None:
        """事件入总线 + 触发对应监听器 + 自动分发到三层认知系统(P0-18)。

        分发规则:
          "error"    → L1.on_error + L2.ingest
          "conflict" → L1.on_conflict(冲突修正: 403/超时→避让)
          "pattern"  → L1.on_success(经验复利: +0.3) + L2.ingest
          "decision" → L3.judge
        """
        self._events.append(event)
        self._dispatch_cognitive(event)
        for key in (event.event_type, "*"):
            for fn in self._listeners.get(key, []):
                try:
                    fn(event)
                except Exception:
                    pass

    def _dispatch_cognitive(self, event: FeedbackEvent) -> None:
        """按事件类型自动分发到三层认知系统。"""
        et = event.event_type
        payload = event.payload or {}
        sig = str(payload.get("error_signature") or payload.get("signature")
                  or payload.get("provider", "") or "")
        anchor_id = str(payload.get("anchor_id") or payload.get("id") or "")
        if et == "error":
            self.cognitive.L1.on_error(sig or "unknown", str(payload.get("detail") or ""))
            self.cognitive.L2.ingest({"event": "error", "signature": sig})
        elif et == "conflict" or et == "conflict_resolution":
            self.cognitive.L1.on_conflict(
                sig or "unknown", provider=str(payload.get("provider", "")),
                model=str(payload.get("model", "")))
        elif et == "pattern" or et == "success":
            if anchor_id:
                self.cognitive.L1.on_success(anchor_id)   # 经验复利 +0.3
            self.cognitive.L2.ingest({"event": "pattern", **payload})
        elif et == "decision":
            self.cognitive.L3.judge(str(payload.get("context") or sig or ""))

    def retrieve(self, query: str) -> Dict[str, Any]:
        """走三层认知系统综合检索排列(P0-17)。"""
        return self.cognitive.retrieve(query)

    def subscribe(self, event_type: str, fn: Callable) -> None:
        """订阅某一类事件（如 'error' 自动萃取）。"""
        self._listeners.setdefault(event_type, []).append(fn)

    # -- L3 → L2: 经验升级为锚点 ------------------------------------------

    def promote_to_anchor(self, event: FeedbackEvent, anchor_store: Any,
                          make_anchor_fn: Callable, min_evidence: int = 2) -> Optional[str]:
        """
        经验事件 → 升级为认知锚点。
        - 同类错误事件 ≥ min_evidence → 自动生成 DecisionAnchor（防复发）
        - 返回生成的 anchor_id；不足证据则返回 None（不强推）
        """
        # 按 source 聚合同类事件
        evts = [e for e in self._events
                if e.event_type == event.event_type and e.source == event.source]
        if len(evts) < min_evidence:
            return None
        # 生成锚点（由 make_anchor_fn 决定具体层/结构）
        anchor = make_anchor_fn(event, evidence_count=len(evts))
        if anchor:
            anchor_store.put(anchor)
            return anchor.anchor_id
        return None

    # -- L2 → L1: 锚点约束回流 Router -------------------------------------

    def add_route_constraint(self, anchor_id: str, constraint: Dict[str, Any]) -> None:
        """
        锚点 → Router 约束（L2→L1）。
        constraint 例: {"provider_avoid": ["token-plan"], "model_avoid": ["deepseek-v4-flash"],
                        "budget_cap": 5.0, "reason": "..."}
        """
        self._route_constraints.append({"anchor_id": anchor_id, **constraint})

    def apply_constraints(self, route_selection: Any) -> Any:
        """
        把锚点约束应用到 Router 决策结果。
        - provider/model 规避: 若首选命中规避名单, 跳到 fallback
        - budget_cap: 注入预算提醒
        """
        if not self._route_constraints:
            return route_selection
        avoid_providers = set()
        avoid_models = set()
        for c in self._route_constraints:
            if "provider_avoid" in c:
                pv = c["provider_avoid"]
                avoid_providers.update(pv if isinstance(pv, list) else [pv])
            if "model_avoid" in c:
                mv = c["model_avoid"]
                avoid_models.update(mv if isinstance(mv, list) else [mv])
        cur = route_selection
        if cur.provider in avoid_providers or cur.model in avoid_models:
            # 跳 fallback（找非规避的）
            for fc in getattr(cur, "fallback_chain", []):
                prov, mod = fc.split("/", 1)
                if prov not in avoid_providers and mod not in avoid_models:
                    cur.provider = prov
                    cur.model = mod
                    break
        return cur

    # -- L1 → L3: 路由结果回写经验 ----------------------------------------

    def capture_route_result(self, provider: str, model: str, success: bool,
                             error: Optional[str] = None) -> FeedbackEvent:
        """记录一次路由调用结果（成功/失败），供经验萃取。"""
        evt = FeedbackEvent(
            event_type="error" if not success else "pattern",
            source="l1_router",
            payload={"provider": provider, "model": model,
                     "success": success, "error": error},
            severity="critical" if (not success and error) else "info",
        )
        self.emit(evt)
        return evt

    # -- 诊断 ---------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """总线统计（贡献了数据驱动自动萃取的可见性）。"""
        from collections import Counter
        c = Counter(e.event_type for e in self._events)
        return {"total_events": len(self._events),
                "by_type": dict(c),
                "route_constraints": len(self._route_constraints)}

