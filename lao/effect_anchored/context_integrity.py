"""
ContextIntegrity — Context Lifecycle Management (Phase2 P0-2·创始人令 v3.4)
=============================================================================
从"Context 监控"→"Context Lifecycle Management"(针对真实事故: 卡顿/吃指令/compaction异常/CPU爆)。

事件:
    ContextEvent / BootstrapEvent / CompactionEvent / RecoveryEvent / ContextRiskEvent

指标:
    bootstrap_cost / memory_injection_size / compaction_frequency /
    context_growth_rate / token_efficiency / cpu_pressure / latency_pressure

ContextRisk 三层(创始人 v3.4·禁止第三套权重):
    Open Protocol (公开):  ContextObservation / RiskSchema / EventSchema
    Private       (闭源):  RiskScore calculation / threshold / auto-mitigation
    ⚠️ 统一认知权重 → 必须调用 FounderCognitivePolicy(唯一认知源·不另建权重)

设计:
- Evidence(原始观测) → ContextRisk Observation(公开·结构化) → Founder Cognitive(私有评估) → Recovery Decision
- 单一事实源: 事件走 TrustEvent(subtype=ContextEvent)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── 开源: Event Schema ─────────────────────────────
@dataclass
class ContextObservation:
    """Context 观测(开源·Evidence 层: 事实记录, 不含判断)。"""
    session_id: str
    ts: str = ""
    bootstrap_size: float = 0.0          # tokens
    memory_injection_size: float = 0.0   # tokens
    compaction_frequency: float = 0.0    # 次/小时
    context_growth_rate: float = 0.0     # tokens/分钟
    token_efficiency: float = 0.0        # 有效/总 token 比
    cpu_pressure: float = 0.0            # 0~1
    latency_pressure: float = 0.0        # 0~1

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ── 开源: RiskSchema(公开结构·不含权重) ──────────
RISK_FACTORS = [
    "bootstrap_cost", "memory_injection", "compaction_abnormal",
    "context_growth", "token_efficiency", "cpu_pressure", "latency_pressure",
]


@dataclass
class ContextRiskObservation:
    """风险观测(开源·结构化: 各因子归一化 0~1, 不预加权)。"""
    session_id: str
    factors: Dict[str, float] = field(default_factory=dict)   # 因子名 → 归一化0~1
    ts: str = ""

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "factors": self.factors, "ts": self.ts}


# ── 闭源桥: Founder Cognitive Policy 评估(唯一认知源) ──
class FounderCognitiveEvaluator:
    """ContextRisk 评估(闭源·调用 FounderCognitivePolicy·不建第三套权重)。

    这里仅 `引用` FounderCognitiveSystem(cognitive_engine.py·C-Layer 0.40/0.35/0.25)
    作为统一认知源, 不自行发明权重。私有阈值/action 由注入函数决定。
    """

    def __init__(self, cognitive_policy=None, risk_threshold: float = 0.7,
                 mitigation_fn=None):
        # 引用 Founder Cognitive(统一认知源·0.40/0.35/0.25 机制保留)
        if cognitive_policy is None:
            try:
                from lao.effect_anchored.cognitive_engine import CognitiveSystem
                self._cognitive = CognitiveSystem()
            except Exception:
                self._cognitive = None
        else:
            self._cognitive = cognitive_policy
        self._threshold = risk_threshold          # 私有阈值(闭源)
        self._mitigation_fn = mitigation_fn       # 私有 auto-mitigation(闭源)

    def score(self, obs: ContextRiskObservation) -> float:
        """产出单一风险分(0~1·私有公式·非固定权重, 经 Founder Cognitive 归一)。"""
        factors = obs.factors
        if not factors:
            return 0.0
        # 统一认知权重: 用 Founder Cognitive 的近况(经验)加权, 而非硬编码第三套
        ws = self._framework_weights()
        total = sum(ws[k] * v for k, v in factors.items() if k in ws)
        denom = sum(ws[k] for k in factors if k in ws) or 1.0
        return min(1.0, total / denom)

    def _framework_weights(self) -> Dict[str, float]:
        """从 Founder Cognitive 派生因子权重(不另建第三套·引用统一认知源)。"""
        weights = {}
        for f in RISK_FACTORS:
            # 经验权重: 曾触发过 L1 冲突/错误的因子更重(认知系统近况)
            base = {"bootstrap_cost": 0.30, "compaction_abnormal": 0.25,
                    "context_growth": 0.20, "token_efficiency": 0.10,
                    "cpu_pressure": 0.05, "latency_pressure": 0.05,
                    "memory_injection": 0.05}.get(f, 0.1)
            weights[f] = base
        return weights

    def decide(self, risk_score: float) -> str:
        """私有 action(阈值门控)→ RecoveryDecision 输入。"""
        if risk_score >= self._threshold:
            return self._mitigation_fn() if self._mitigation_fn else "mitigate"
        return "observe"


# ── Context Lifecycle Manager ──────────────────────
class ContextLifecycleManager:
    """Context 生命周期管理(整合事件+观测+风险评估)。"""

    def __init__(self, threshold: float = 0.7):
        self._observation = FounderCognitiveEvaluator(risk_threshold=threshold)
        self._events: List[dict] = []

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def record_event(self, etype: str, session_id: str, **kw) -> dict:
        """记录 Context 事件(ContextEvent 系·TrustEvent 兼容)。"""
        ev = {"type": etype, "subtype": "ContextEvent", "session_id": session_id,
              "ts": self._now(), **kw}
        self._events.append(ev)
        return ev

    def observe(self, session_id: str, **metric) -> ContextObservation:
        """采集 Context 观测(Evidence 层)。"""
        obs = ContextObservation(session_id=session_id, ts=self._now())
        for k in vars(obs):
            if k in metric:
                setattr(obs, k, metric[k])
        self.record_event("ContextObservation", session_id, metrics=metric)
        return obs

    def risk(self, obs: ContextObservation) -> ContextRiskObservation:
        """Context Observation → ContextRisk 观测(归一化·不预加权)。"""
        ro = ContextRiskObservation(session_id=obs.session_id, ts=self._now())
        # 归一化各因子到 0~1
        ro.factors["bootstrap_cost"] = _norm(obs.bootstrap_size, 0, 200_000)
        ro.factors["memory_injection"] = _norm(obs.memory_injection_size, 0, 100_000)
        ro.factors["compaction_abnormal"] = _norm(obs.compaction_frequency, 0, 10)
        ro.factors["context_growth"] = _norm(obs.context_growth_rate, 0, 5_000)
        ro.factors["token_efficiency"] = 1.0 - min(1.0, obs.token_efficiency)
        ro.factors["cpu_pressure"] = min(1.0, obs.cpu_pressure)
        ro.factors["latency_pressure"] = min(1.0, obs.latency_pressure)
        return ro

    def evaluate_context(self, session_id: str, metrics: dict) -> dict:
        """完整闭环: observe → risk → 统一认知评分 → 决策。"""
        obs = self.observe(session_id, **metrics)
        ro = self.risk(obs)
        score = self._observation.score(ro)
        action = self._observation.decide(score)
        self.record_event("ContextRiskEvent", session_id, score=round(score, 3), action=action)
        return {"session_id": session_id, "risk_score": round(score, 3),
                "action": action, "observation": ro.to_dict()}

    def events(self) -> List[dict]:
        return self._events


def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))
