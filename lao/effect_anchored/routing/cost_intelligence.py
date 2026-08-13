"""
CostIntelligence — Cost Intelligence Engine (Phase2 P1-1·创始人令 v3.4)
=============================================================================
第一价值 = Cost Saving。用户第一次打开 Dashboard 必须看到"LAO 今天帮你省了多少钱"。

从 CostTracker(只记录成本) 升级为 SavingsEngine(产出节省证据):
    CostTracker → SavingsEngine → SavingsEvidence → CostSavingsEvent(TrustEvent)

CostSavingsEvent(继承 TrustEvent · subtype=EconomicEvent):
    event_id / agent_id / task_type / original_model / selected_model /
    original_cost / optimized_cost / saving_amount / saving_ratio /
    quality_score / switch_reason / evidence_hash

Dashboard "LAO Impact Report":
    Requests: 523 | Original: $8.42 | LAO Optimized: $2.31 | Saved: $6.11
    Efficiency: 72.5% | Quality: 96%

验收: 同一 Agent 不开 LAO 成本 X · 开 LAO 成本 < X · 产生 CostSavingsEvent。
"""
from __future__ import annotations
import hashlib, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# 模型基准成本($/1K tokens·用于计算 original_cost)
MODEL_BASELINE_COST = {
    # 若不用 LAO 路由, 默认全用 pro(最贵) → original_cost 基线
    "deepseek-v4-pro": {"input": 0.003, "output": 0.006},   # $/1K tokens
    "deepseek-v4-flash": {"input": 0.001, "output": 0.002},
    # 其他模型按 pro 兜底(未路由=用贵模型)
    "default": {"input": 0.003, "output": 0.006},
}


@dataclass
class CostSavingsEvent:
    """一次成本节省事件(TrustEvent · subtype=EconomicEvent)。"""
    event_id: str
    agent_id: str
    task_type: str
    original_model: str          # 不开 LAO 用的模型(默认 pro·贵)
    selected_model: str          # LAO 路由选择的模型
    original_cost: float         # 不开 LAO 成本($)
    optimized_cost: float        # 开 LAO 成本($)
    saving_amount: float         # 节省额
    saving_ratio: float          # 节省比例 0~1
    quality_score: float = 0.0   # 质量分(route quality gate)
    switch_reason: str = ""      # 切换原因(成本红线/缓存命中/任务匹配)
    evidence_hash: str = ""
    ts: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def to_trust_event(self) -> dict:
        """→ TrustEvent(经济·可审计)。"""
        return {
            "event": "CostSavings",
            "subtype": "EconomicEvent",
            "event_id": self.event_id, "agent_id": self.agent_id,
            "task_type": self.task_type, "original_model": self.original_model,
            "selected_model": self.selected_model, "original_cost": self.original_cost,
            "optimized_cost": self.optimized_cost, "saving_amount": round(self.saving_amount, 5),
            "saving_ratio": round(self.saving_ratio, 4), "quality_score": self.quality_score,
            "switch_reason": self.switch_reason, "evidence_hash": self.evidence_hash,
            "ts": self.ts,
        }


class SavingsEngine:
    """成本节省引擎: 计算 LAO 路由带来的真实成本下降。"""

    def __init__(self):
        self._events: Dict[str, CostSavingsEvent] = {}
        self._counter = 0

    # -- 成本计算 --
    @staticmethod
    def model_cost(model: str, in_tok: int, out_tok: int) -> float:
        """计算一次调用的 $ 成本。"""
        key = "default"
        for m in ("deepseek-v4-pro", "deepseek-v4-flash"):
            if m in model:
                key = m
                break
        p = MODEL_BASELINE_COST.get(key, MODEL_BASELINE_COST["default"])
        return (in_tok * p["input"] + out_tok * p["output"]) / 1000.0

    # -- 核心: 计算节省 --
    def compute_saving(self, agent_id: str, task_type: str,
                       original_model: str, selected_model: str,
                       in_tok: int, out_tok: int,
                       quality_score: float = 0.0,
                       switch_reason: str = "") -> CostSavingsEvent:
        """同一请求: 不开 LAO(original_model) vs 开 LAO(selected_model) 成本对比。"""
        self._counter += 1
        orig_cost = self.model_cost(original_model, in_tok, out_tok)
        opt_cost = self.model_cost(selected_model, in_tok, out_tok)
        saving = max(0.0, orig_cost - opt_cost)
        ratio = (saving / orig_cost) if orig_cost > 0 else 0.0
        ev = CostSavingsEvent(
            event_id=f"CEV-{self._counter:05d}",
            agent_id=agent_id, task_type=task_type,
            original_model=original_model, selected_model=selected_model,
            original_cost=orig_cost, optimized_cost=opt_cost,
            saving_amount=saving, saving_ratio=ratio,
            quality_score=quality_score, switch_reason=switch_reason,
            evidence_hash=_fp(f"{agent_id}:{original_model}:{selected_model}:{in_tok}:{out_tok}"),
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        self._events[ev.event_id] = ev
        return ev

    # -- Dashboard "LAO Impact Report" --
    def impact_report(self) -> dict:
        """LAO Impact Report(用户第一价值视图)。"""
        if not self._events:
            return {"requests": 0, "original_cost": 0, "optimized_cost": 0,
                    "saved": 0, "efficiency": 0, "quality": 100}
        reqs = len(self._events)
        orig = sum(e.original_cost for e in self._events.values())
        opt = sum(e.optimized_cost for e in self._events.values())
        saved = orig - opt
        eff = (saved / orig * 100) if orig > 0 else 0.0
        qual = sum(e.quality_score for e in self._events.values()) / reqs
        return {
            "requests": reqs, "original_cost": round(orig, 2),
            "optimized_cost": round(opt, 2), "saved": round(saved, 2),
            "efficiency": round(eff, 1), "quality": round(qual, 1),
        }

    def events(self) -> List[CostSavingsEvent]:
        return list(self._events.values())


def _fp(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
