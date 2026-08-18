# v3.5.1-fix: R4/R1/A1-A3
# v3.5.1-glm: R1
# v3.5.1-wiring: C-1 注释对齐(2026-08-19 三方验证·头部标签更新为 v3.4 定义)
"""
Feedback Bus — LAO v3.4 三层Loop组件 (L2 经验工厂核心)
=========================================================

三层数据不再单向流动。Feedback Bus 让经验/锚点/路由形成闭环回流
(v3.4 权威定义·2026-08-16 创始人令·三方验证定稿 2026-08-19):

    L1(命中率) ←─ 反哺 ─ L3(确权经验) ←─ L2(经验工厂产锚点)
            ↘              ↗
             Agent运营经验(反哺L1命中率+RIS免疫)

方向(对齐 experience_loop.py 头部权威定义):
    L1→L2: capture_route_result — 路由结果 → 经验事件(供萃取·产锚点)
    L2→L1: promote_to_anchor/constrain_route — 锚点(约束) → 反哺 Router
    L2→L3: confirm_experiences — 锚点 → 幻觉门/readiness → 授权 → 存证

核心机制:
    emit(event)              — 经验/决策事件入总线
    L2: promote_to_anchor — 事件累积 → 升级为锚点(对齐Experience Atom Engine)
    L2→L1: constrain_route   — 锚点(约束) → 给 Router 的 route() 注入约束
    L1→L2: capture_route_result — 路由结果 → 回写事件(供L2萃取)

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
    """总线事件(经验/决策/路由的统一载体)。"""
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
    L3→L2:   promote_to_anchor  - 经验事件 → 锚点(调用认知anchor存储)
    L2→L1:   constrain_route    - 锚点约束 → Router 路由优化钩子
    L1→L3:   capture_route_result - 路由结果 → 经验回流

    2026-08-16 三层Loop闭环修复:
    - state_path: 约束/免疫/错误计数持久化(重启不丢·错误复利可累积)
    - configure_auto_promote: 错误证据≥阈值 自动升级锚点 + 自动生成路由约束
      (原 promote_to_anchor 需手工调用·生产零调用 → 现在自动闭环)
    - active_route_constraints: ModelRouter 消费约束的接口(反哺L1)
    - _events 封顶(缓存空间收敛)
    """

    MAX_EVENTS = 1000   # 事件环形上限(只留最近·缓存空间收敛)

    def __init__(self, state_path: Optional[str] = None):
        self._events: List[FeedbackEvent] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._route_constraints: List[Dict[str, Any]] = []
        self._state_path = state_path
        self._promoted: Dict[str, str] = {}   # 错误签名指纹 → 升级出的 anchor_id
        self._anchor_store: Any = None
        self._make_anchor_fn: Optional[Callable] = None
        self._min_evidence: int = 2
        # LAO v3.1 P0-18: 内嵌三层认知系统(CognitiveSystem)
        from lao.effect_anchored.cognitive_engine import CognitiveSystem
        self.cognitive = CognitiveSystem()
        # R1: TimeoutMatrix集成
        try:
            from lao.effect_anchored.routing.timeout_matrix import TimeoutMatrix
            self._timeout_matrix = TimeoutMatrix()
        except Exception:
            self._timeout_matrix = None
        if state_path:
            self._load_state()
            # 自动升级接线: 错误事件 → (证据≥阈值) → 锚点 + 路由约束
            self.configure_auto_promote(self._auto_anchor_store_from_state())

    # -- 持久化(约束/免疫/错误计数·错误复利跨进程累积) ----------------------

    def _load_state(self) -> None:
        import json as _json
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, _json.JSONDecodeError):
            return
        self._route_constraints = list(data.get("route_constraints", []))
        self._promoted = dict(data.get("promoted", {}))
        imm = data.get("immunity")
        if isinstance(imm, dict) and imm:
            self._ensure_immunity()
            self._immunity._immune.update(imm)
        cog = data.get("cognitive", {})
        if isinstance(cog, dict):
            self.cognitive._error_counts.update(cog.get("error_counts", {}))
            self.cognitive._weights.update(cog.get("compound_weights", {}))

    def _save_state(self) -> None:
        import json as _json
        import os
        if not self._state_path:
            return
        try:
            snap = self.cognitive.snapshot()
            payload = {
                "route_constraints": self._route_constraints,
                "promoted": self._promoted,
                "immunity": self._ensure_immunity().to_dict() if hasattr(self, "_immunity") else {},
                "cognitive": {
                    "error_counts": snap.get("error_counts", {}),
                    "compound_weights": snap.get("compound_weights", {}),
                },
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            d = os.path.dirname(self._state_path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    def _auto_anchor_store_from_state(self):
        """自动升级的默认锚点库: 状态文件同目录 anchors.json(持久化)。"""
        return None  # 由 configure_auto_promote 显式注入(避免隐性写文件)

    # -- 自动升级配置(L2 锚点记忆接入·错误复利闭环) -------------------------

    def configure_auto_promote(self, anchor_store: Any,
                               make_anchor_fn: Optional[Callable] = None,
                               min_evidence: int = 2) -> None:
        """注册锚点库后, 错误证据≥min_evidence 自动升级锚点+路由约束。"""
        self._anchor_store = anchor_store
        self._make_anchor_fn = make_anchor_fn or self._default_error_anchor
        self._min_evidence = max(1, int(min_evidence))

    @staticmethod
    def _default_error_anchor(event: "FeedbackEvent", evidence_count: int):
        """默认错误锚点构造: DecisionAnchor(防复发·可被锚点库淘汰收敛)。A1-A3: 自动生成FixturePair。"""
        import hashlib as _hl
        from lao.effect_anchored.cognitive_anchor import make_decision_anchor
        p = event.payload or {}
        sig = str(p.get("error_signature") or p.get("error") or p.get("provider") or "unknown")
        provider = str(p.get("provider") or "")
        model = str(p.get("model") or "")
        fp = _hl.sha1(f"{sig}|{provider}|{model}".encode()).hexdigest()[:10]
        anchor = make_decision_anchor(
            anchor_id=f"route-error-{fp}",
            principle=f"规避反复出错的组合: {sig}",
            trigger_condition=f"{provider}/{model} 出现 {sig}" if provider else sig,
            action_rule=(f"路由避开 provider={provider}" if provider
                         else "同类错误出现时优先降级切换"),
            derived_from_events=[event.timestamp],
            source=f"feedback_bus:auto:{event.source}",
            trust_weight=0.6,   # 非 Tier0: 长期不再触发可被缓存淘汰
            tags=["route-error", "auto-promoted"],
        )
        # 证据计数入 value: readiness 判定(trigger_count)可读·确权可审计
        if isinstance(anchor.value, dict):
            anchor.value["evidence_count"] = int(evidence_count)
        # A1-A3: 自动生成 FixturePair 并关联到锚点
        try:
            from lao.effect_anchored.validation.fixture_pair import FixturePair
            pair_id = f"fp-{fp}"
            _fp = FixturePair(
                pair_id=pair_id,
                anchor_id=anchor.anchor_id,
                bad_path_context={"provider": provider, "model": model,
                                  "error_signature": sig},
                valid_path_context={"provider": provider, "model": model,
                                    "error_signature": ""},
            )
            anchor.fixture_pair_id = _fp.pair_id
        except Exception:
            pass
        return anchor

    # -- 事件入总线 ---------------------------------------------------------

    def _extract_elapsed_ms(self, payload: Dict[str, Any], event: FeedbackEvent) -> float:
        """从 payload 提取或推算 elapsed 毫秒数(R1 TimeoutMatrix 集成兜底)。

        提取优先级:
          1. payload.elapsed_ms / payload.latency_ms - 直传字段
          2. payload 内 started_at → completed_at 差值 - 双时间戳推算
          3. payload 内 started_at → event.timestamp 差值 - 事件创建≈调用完成

        全部无法计算时返回 0.0,调用方据此静默跳过 judge。
        """
        # 1. 直传字段
        direct = float(payload.get("elapsed_ms") or payload.get("latency_ms") or 0)
        if direct > 0:
            return direct

        # 2/3. 时间戳差值兜底
        def _parse_ts(val: Any) -> Optional[datetime]:
            """解析 ISO-8601 时间戳字符串(Z 后缀兼容)。"""
            if not val or not isinstance(val, str):
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        start_keys = ("started_at", "start_time", "request_start", "ts")
        end_keys = ("completed_at", "end_time", "response_end")

        start_ts = next((_parse_ts(payload.get(k)) for k in start_keys if payload.get(k)), None)
        if start_ts is None:
            return 0.0

        end_ts = next((_parse_ts(payload.get(k)) for k in end_keys if payload.get(k)), None)
        if end_ts is None:
            # 3. 无显式 end → 用 event.timestamp(事件创建≈调用完成时刻)
            end_ts = _parse_ts(event.timestamp)
        if end_ts is None:
            return 0.0

        delta_ms = (end_ts - start_ts).total_seconds() * 1000
        return max(0.0, round(delta_ms, 1))

    def emit(self, event: FeedbackEvent) -> None:
        """事件入总线 + 触发对应监听器 + 自动分发到三层认知系统(P0-18)。

        分发规则:
          "error"    → L1.on_error + L2.ingest (+证据≥阈值 自动升级锚点)
          "conflict" → L1.on_conflict(冲突修正: 403/超时→避让)
          "pattern"  → L1.on_success(经验复利: +0.3) + L2.ingest
          "decision" → L3.judge
        """
        self._events.append(event)
        if len(self._events) > self.MAX_EVENTS:   # 缓存空间收敛·只留最近
            self._events = self._events[-self.MAX_EVENTS:]
        self._dispatch_cognitive(event)
        self._maybe_auto_promote(event)
        self._conflict_avoidance(event)
        # R1: route_result事件 → timeout_matrix judge → 自动emit冲突事件
        if event.event_type == "route_result" and self._timeout_matrix is not None:
            try:
                p = event.payload or {}
                mode = str(p.get("mode") or p.get("tier") or p.get("task_type") or "default")
                elapsed = self._extract_elapsed_ms(p, event)
                if elapsed > 0:
                    verdict = self._timeout_matrix.judge(mode, elapsed)
                    if verdict["action"] in ("slow", "fallback"):
                        self.emit(FeedbackEvent(
                            event_type="conflict",
                            source="timeout_matrix",
                            payload={
                                "provider": str(p.get("provider") or ""),
                                "model": str(p.get("model") or ""),
                                "error_signature": f"timeout:{verdict['action']}:{mode}",
                                "detail": f"elapsed={elapsed}ms action={verdict['action']}",
                                "timeout_verdict": verdict,
                            },
                            severity="critical" if verdict["action"] == "fallback" else "warning",
                        ))
            except Exception:
                pass
        if event.event_type in ("error", "conflict", "conflict_resolution"):
            self._save_state()
        for key in (event.event_type, "*"):
            for fn in self._listeners.get(key, []):
                try:
                    fn(event)
                except Exception:
                    pass

    # -- 冲突修正(即时避让·TTL 临时约束) -----------------------------------

    CONFLICT_AVOID_TTL_SEC = 600   # 单次冲突避让 10 分钟(非永久·可恢复)

    def _conflict_avoidance(self, event: FeedbackEvent) -> None:
        """冲突事件(403/超时/限流) → 即时 provider 避让约束(短TTL)。"""
        if event.event_type != "conflict":
            return
        provider = str((event.payload or {}).get("provider") or "")
        model = str((event.payload or {}).get("model") or "")
        if not provider:
            return
        # 去重(缓存空间收敛): 同 provider 已有未过期临时避让 → 不重复追加
        for c in self._route_constraints:
            if (c.get("temporary") and provider in (
                    c.get("provider_avoid") if isinstance(c.get("provider_avoid"), list)
                    else [c.get("provider_avoid")])):
                return
        from datetime import timedelta
        expires = (datetime.now(timezone.utc)
                   + timedelta(seconds=self.CONFLICT_AVOID_TTL_SEC)).isoformat()
        self._route_constraints.append({
            "anchor_id": f"conflict:{event.timestamp}",
            "provider_avoid": [provider],
            **({"model_avoid": [model]} if model else {}),
            "reason": f"冲突即时避让: {(event.payload or {}).get('error_signature', '')[:80]}",
            "expires_at": expires,
            "temporary": True,
        })
        self._save_state()

    # -- 错误复利自动升级(2026-08-16 闭环: 错误→锚点→路由约束 全自动) -------

    def _maybe_auto_promote(self, event: FeedbackEvent) -> None:
        """错误事件证据≥min_evidence 且尚未升级 → 升级锚点 + 注入路由约束。"""
        if event.event_type != "error" or self._anchor_store is None:
            return
        p = event.payload or {}
        sig = str(p.get("error_signature") or p.get("signature")
                  or p.get("provider") or "unknown")
        provider = str(p.get("provider") or "")
        model = str(p.get("model") or "")
        fp = error_fingerprint(provider or "-", model or "-", sig)
        count = int(self.cognitive._error_counts.get(sig, 0))
        if count < self._min_evidence:
            return
        if fp in self._promoted:
            # 已升级: 证据续涨时刷新锚点 evidence_count(错误复利累积·节流:
            # 每 min_evidence 次刷一次, 版本历史由锚点库 max_history 封顶)
            if count % self._min_evidence == 0:
                self._refresh_promoted_evidence(self._promoted[fp], count)
            return
        try:
            anchor = self._make_anchor_fn(event, evidence_count=count)
            if anchor is None:
                return
            self._anchor_store.put(anchor)
            self._promoted[fp] = anchor.anchor_id
            constraint: Dict[str, Any] = {"anchor_id": anchor.anchor_id,
                                          "reason": f"错误×{count}: {sig[:80]}"}
            if provider:
                constraint["provider_avoid"] = [provider]
            if model:
                constraint["model_avoid"] = [model]
            self._route_constraints.append(constraint)
            self._save_state()
        except Exception:
            pass  # 自动升级失败不阻塞事件流

    def _refresh_promoted_evidence(self, anchor_id: str, count: int) -> None:
        """已升级锚点的证据计数刷新(保持类型/结构·只更新 evidence_count)。"""
        cur = self._anchor_store.get(anchor_id)
        if not cur:
            return
        value = cur.get("value")
        if not isinstance(value, dict):
            return
        if int(value.get("evidence_count", 0) or 0) == count:
            return
        value["evidence_count"] = int(count)
        from lao.effect_anchored.cognitive_anchor import Anchor as _Anchor
        refreshed = _Anchor(
            anchor_id=cur.get("anchor_id", anchor_id),
            anchor_type=cur.get("anchor_type", "decision"),
            value=value,
            source=cur.get("source"),
            created_at=cur.get("created_at"),
            tags=list(cur.get("tags", []) or []),
            trust_weight=float(cur.get("trust_weight", 0.6) or 0.6),
        )
        refreshed.version = cur.get("version")
        self._anchor_store.put(refreshed)

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
        """订阅某一类事件(如 'error' 自动萃取)。"""
        self._listeners.setdefault(event_type, []).append(fn)

    # -- L3 → L2: 经验升级为锚点 ------------------------------------------

    def promote_to_anchor(self, event: FeedbackEvent, anchor_store: Any,
                          make_anchor_fn: Callable, min_evidence: int = 2) -> Optional[str]:
        """
        经验事件 → 升级为认知锚点。
        - 同类错误事件 ≥ min_evidence → 自动生成 DecisionAnchor(防复发)
        - 返回生成的 anchor_id;不足证据则返回 None(不强推)
        """
        # 按 source 聚合同类事件
        evts = [e for e in self._events
                if e.event_type == event.event_type and e.source == event.source]
        if len(evts) < min_evidence:
            return None
        # 生成锚点(由 make_anchor_fn 决定具体层/结构)
        anchor = make_anchor_fn(event, evidence_count=len(evts))
        if anchor:
            anchor_store.put(anchor)
            return anchor.anchor_id
        return None

    # -- L2 → L1: 锚点约束回流 Router -------------------------------------

    def add_route_constraint(self, anchor_id: str, constraint: Dict[str, Any]) -> None:
        """
        锚点 → Router 约束(L2→L1)。
        constraint 例: {"provider_avoid": ["token-plan"], "model_avoid": ["deepseek-v4-flash"],
                        "budget_cap": 5.0, "reason": "..."}
        """
        self._route_constraints.append({"anchor_id": anchor_id, **constraint})
        self._save_state()

    def active_route_constraints(self) -> List[Dict[str, Any]]:
        """当前生效的路由约束(ModelRouter 消费·L2/L3→L1 反哺接口)。

        - 过滤已过期(expires_at < now)的临时冲突约束
        - 顺带清理过期条目(缓存空间收敛)
        """
        now = datetime.now(timezone.utc)
        active, expired = [], []
        for c in self._route_constraints:
            exp = c.get("expires_at")
            if exp:
                try:
                    if datetime.fromisoformat(exp) < now:
                        expired.append(c)
                        continue
                except ValueError:
                    pass
            active.append(c)
        if expired:
            self._route_constraints = [c for c in self._route_constraints if c not in expired]
            self._save_state()
        return active

    def apply_constraints(self, route_selection: Any) -> Any:
        """
        把锚点约束应用到 Router 决策结果。
        - provider/model 规避: 若首选命中规避名单, 跳到 fallback
        - budget_cap: 注入预算提醒
        """
        constraints = self.active_route_constraints()
        if not constraints:
            return route_selection
        avoid_providers = set()
        avoid_models = set()
        for c in constraints:
            if "provider_avoid" in c:
                pv = c["provider_avoid"]
                avoid_providers.update(pv if isinstance(pv, list) else [pv])
            if "model_avoid" in c:
                mv = c["model_avoid"]
                avoid_models.update(mv if isinstance(mv, list) else [mv])
        cur = route_selection
        if cur.provider in avoid_providers or cur.model in avoid_models:
            # 跳 fallback(找非规避的)
            for fc in getattr(cur, "fallback_chain", []):
                prov, mod = fc.split("/", 1)
                if prov not in avoid_providers and mod not in avoid_models:
                    cur.provider = prov
                    cur.model = mod
                    break
        return cur

    # -- L1 → L3: 路由结果回写经验 ----------------------------------------

    # 冲突特征(冲突修正: 即时避让而非等错误复利累积)
    _CONFLICT_MARKERS = ("403", "timeout", "timed out", "429", "rate limit",
                         "502", "503", "504", "connection")

    def capture_route_result(self, provider: str, model: str, success: bool,
                             error: Optional[str] = None,
                             usage_present: bool = True) -> FeedbackEvent:
        """记录一次路由调用结果(成功/失败),供经验萃取。

        Loop 闭环(2026-08-16):
        - error_signature 归一(错误前120字符): 认知层计数与自动升级同键
        - 403/超时/限流等冲突型错误 → 追加 conflict 事件(即时路由避让)
        - R4: usage_present=False → emit error 事件(usage缺失故障信号)
        """
        sig = (error or "")[:120] or f"{provider}/{model} unspecified-error"
        evt = FeedbackEvent(
            event_type="error" if not success else "pattern",
            source="l1_router",
            payload={"provider": provider, "model": model,
                     "success": success, "error": error,
                     "error_signature": sig},
            severity="critical" if (not success and error) else "info",
        )
        self.emit(evt)
        if not success and error:
            low = str(error).lower()
            if any(m in low for m in self._CONFLICT_MARKERS):
                self.emit(FeedbackEvent(
                    event_type="conflict", source="l1_router",
                    payload={"provider": provider, "model": model,
                             "error_signature": sig, "signature": sig,
                             "detail": str(error)[:200]},
                    severity="critical",
                ))
        # R4: usage缺失故障信号
        if not usage_present:
            self.emit(FeedbackEvent(
                event_type="error", source="l1_router",
                payload={"provider": provider, "model": model,
                         "error_signature": f"usage_missing:{provider}/{model}",
                         "detail": "usage field absent in response"},
                severity="warning",
            ))
        return evt

    # -- 诊断 ---------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """总线统计(贡献了数据驱动自动萃取的可见性)。"""
        from collections import Counter
        c = Counter(e.event_type for e in self._events)
        return {"total_events": len(self._events),
                "by_type": dict(c),
                "route_constraints": len(self.active_route_constraints()),
                "promoted_anchors": len(self._promoted),
                "immune_fingerprints": len(self._ensure_immunity().to_dict())}

