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
    # [V·Nova unit-price-model-v2·官方CSV固定价·20260814]
    # input = cache miss 档(缓存失效=成本暴涨场景·作为基线)
    # input_cache_hit = cache hit 档(M4: 官方价差 hit 远低于 miss·pro 1/5·flash 1/10)
    "deepseek-v4-pro": {"input": 0.003, "output": 0.006, "input_cache_hit": 0.0006},  # $/1K (= $3/$6 per 1M·miss档)
    "deepseek-v4-flash": {"input": 0.001, "output": 0.002, "input_cache_hit": 0.0001},  # $/1K (= $1/$2 per 1M·miss档)
    # 其他模型按 pro 兜底(未路由=用贵模型)
    "default": {"input": 0.003, "output": 0.006, "input_cache_hit": 0.0006},
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
    def model_cost(model: str, in_tok: int, out_tok: int,
                   cache_hit: int = 0, cache_miss: Optional[int] = None) -> float:
        """计算一次调用的 $ 成本。

        M4: 区分 cache hit/miss 价差(DeepSeek 官方: prompt = hit + miss·hit 价远低于 miss)。
        - 缺省(cache_hit=0, cache_miss=None): 全部按 miss 档 = 旧行为(基线口径)。
        - 传 cache_hit(+可选 cache_miss): hit 部分按 hit 档折扣。
        """
        key = "default"
        for m in ("deepseek-v4-pro", "deepseek-v4-flash"):
            if m in model:
                key = m
                break
        p = MODEL_BASELINE_COST.get(key, MODEL_BASELINE_COST["default"])
        if cache_miss is None:
            cache_miss = max(0, in_tok - cache_hit)
        hit_cost = min(cache_hit, in_tok) * p["input_cache_hit"]
        miss_cost = cache_miss * p["input"]
        return (hit_cost + miss_cost + out_tok * p["output"]) / 1000.0

    # -- 核心: 计算节省 --
    def compute_saving(self, agent_id: str, task_type: str,
                       original_model: str, selected_model: str,
                       in_tok: int, out_tok: int,
                       quality_score: float = 0.0,
                       switch_reason: str = "",
                       cache_hit: int = 0,
                       cache_miss: Optional[int] = None) -> CostSavingsEvent:
        """同一请求: 不开 LAO(original_model) vs 开 LAO(selected_model) 成本对比。

        基线 original_cost 全按 miss 档(不开 LAO = 不做前缀治理·保守口径);
        optimized_cost 按真实缓存状态计(M4 价差)。
        """
        self._counter += 1
        orig_cost = self.model_cost(original_model, in_tok, out_tok)
        opt_cost = self.model_cost(selected_model, in_tok, out_tok,
                                   cache_hit=cache_hit, cache_miss=cache_miss)
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
