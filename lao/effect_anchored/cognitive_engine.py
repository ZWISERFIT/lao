"""
Cognitive Engine — LAO v3.1 P0-17
==================================

三层认知系统检索排列（创始人设计·完整编码化）。

认知系统 = L1(实时迭代) + L2(短期品味) + L3(长期判断) 三层综合检索排列。

三层定义（创始人 7/29-30 原设计）:
  L1 实时迭代 (权重 0.4): 冲突修正 + 错误复利 + 经验复利
    - 冲突修正: 403/超时/幻觉 → 即时路由避让(Router 立即避开)
    - 错误复利: 刚才犯的错 → 即时生成临时约束 → L2 异步升级锚点
    - 经验复利: 刚才做对的事 → 即时 +0.3 trigger_weight → 锚点立刻变强

  L2 短期品味 (权重 0.35): 修养 + 见识 + 情感
    - 修养: 近期的行为模式积累(本周任务×模型匹配度·pattern)
    - 见识: 近期的信息消化(学了什么新东西·insight)
    - 情感: 近期的倾向性(喜欢怎样的处理方式·preference)

  L3 长期判断 (权重 0.25): 世界观 + 价值观 + 人生观
    - 世界观: 什么是对的——不可妥协的原则·Tier0 永固锚点
    - 价值观: 什么是值得的——优先级排序·权重分配
    - 人生观: 什么是目标——为什么存在·往哪走·existential anchor

  retrieve(query) = 0.4*L1 得分 + 0.35*L2 得分 + 0.25*L3 得分

与 L2 经验工厂的关系:
  L1 实时迭代是 L2 经验工厂的前置实时层——同一生产线·两速:
    L1 抄在毫秒级(冲突修正+错误复利+经验复利)
    L2 沉淀在分钟/小时级(修养+见识+情感)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# 三层权重(创始人设计·不可改)
W_L1 = 0.40
W_L2 = 0.35
W_L3 = 0.25

# L1 经验复利增量(成功 → trigger_weight 立即 +0.3)
EXPERIENCE_COMPOUND_DELTA = 0.3

# L3 Tier0 永固阈值(>=0.8 视为不可改)
TIER0_THRESHOLD = 0.8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class L1RealTime:
    """L1 实时迭代: 冲突修正 + 错误复利 + 经验复利(毫秒级)。"""

    class _Engine:
        def __init__(self, cognitive: "CognitiveSystem"):
            self._cog = cognitive

        def on_conflict(self, error_signature: str, provider: str = "", model: str = "") -> str:
            """冲突修正: 检测到 403/超时/幻觉 → 即时路由避让。

            记入 conflict_blocks(即时避让中的签名), 返回冲突指纹。
            """
            fp = f"conflict:{error_signature}"
            self._cog._conflicts[fp] = {
                "provider": provider, "model": model,
                "at": _now_iso(), "signature": error_signature,
            }
            return fp

        def on_error(self, error_signature: str, detail: str = "") -> str:
            """错误复利: 即时生成临时约束 → L2 异步升级锚点。

            等 2 次相同错误证据 → 升级为更强的约束/锚点。
            """
            self._cog._error_counts[error_signature] = self._cog._error_counts.get(error_signature, 0) + 1
            count = self._cog._error_counts[error_signature]
            constraint = {
                "signature": error_signature, "detail": detail,
                "temporary": True, "evidence_count": count,
                "escalated": count >= 2,   # 2 次证据 → 升级 L2
                "at": _now_iso(),
            }
            self._cog._temporary_constraints[error_signature] = constraint
            return error_signature

        def on_success(self, anchor_id: str, delta: float = EXPERIENCE_COMPOUND_DELTA) -> float:
            """经验复利: 做对的事 → 立即 +0.3 trigger_weight → 锚点立刻变强。"""
            new_w = self._cog._weights.get(anchor_id, 0.0) + delta
            self._cog._weights[anchor_id] = round(new_w, 4)
            return self._cog._weights[anchor_id]

    def __init__(self, cognitive: "CognitiveSystem"):
        self.cognitive = cognitive
        self.on_conflict = self._Engine(cognitive).on_conflict
        self.on_error = self._Engine(cognitive).on_error
        self.on_success = self._Engine(cognitive).on_success


class L2ShortTermTaste:
    """L2 短期品味: 修养 + 见识 + 情感(近期模式·分钟/小时级)。"""

    class _Engine:
        def __init__(self, cognitive: "CognitiveSystem"):
            self._cog = cognitive

        def ingest(self, experience: Dict[str, Any]) -> None:
            """近期模式消化: 记录一条经验(任务×模型匹配/insight/偏好)。"""
            self._cog._recent_experiences.append({
                **experience, "ingested_at": _now_iso()})
            # 只保留近期(近 N 条, 滑动窗口)
            if len(self._cog._recent_experiences) > self._cog._recent_cap:
                self._cog._recent_experiences = self._cog._recent_experiences[-self._cog._recent_cap:]

        def taste(self, query: str) -> float:
            """近期品味偏好: 近期经验与 query 的相关性(0-1)。"""
            if not self._cog._recent_experiences:
                return 0.0
            hits = 0
            q = query.lower()
            for exp in self._cog._recent_experiences:
                blob = " ".join(str(v) for v in exp.values()).lower()
                if q and q in blob:
                    hits += 1
            return round(min(1.0, hits / len(self._cog._recent_experiences)), 3)

    def __init__(self, cognitive: "CognitiveSystem"):
        self.cognitive = cognitive
        self.ingest = self._Engine(cognitive).ingest
        self.taste = self._Engine(cognitive).taste


class L3LongTermJudgment:
    """L3 长期判断: 世界观 + 价值观 + 人生观(Tier0 永固)。"""

    class _Engine:
        def __init__(self, cognitive: "CognitiveSystem"):
            self._cog = cognitive

        def judge(self, decision_context: str) -> float:
            """基于三观判断: 与 Tier0 永固原则的匹配度(0-1)。"""
            if not self._cog._unalterable:
                return 0.5
            dc = decision_context.lower()
            hits = 0
            for anchor_id in self._cog._unalterable:
                if anchor_id.lower() in dc:
                    hits += 1
            return round(min(1.0, hits / len(self._cog._unalterable)), 3)

        def is_unalterable(self, anchor: Dict[str, Any]) -> bool:
            """Tier0 永固锚点判定(trust_weight >= 0.8 或显式 permanent)。"""
            tw = float(anchor.get("trust_weight") or anchor.get("trust") or 0.0)
            if tw >= TIER0_THRESHOLD:
                return True
            if anchor.get("status") == "permanent" or anchor.get("permanent"):
                return True
            return False

    def __init__(self, cognitive: "CognitiveSystem"):
        self.cognitive = cognitive
        self.judge = self._Engine(cognitive).judge
        self.is_unalterable = self._Engine(cognitive).is_unalterable


class CognitiveSystem:
    """三层认知系统(完整编码化)。

    L1 实时迭代(0.4) + L2 短期品味(0.35) + L3 长期判断(0.25)。

    用法:
      cs = CognitiveSystem()
      cs.L1.on_conflict("qwen3.8-max 403")
      cs.L1.on_error("timeout", "高峰期")
      cs.L1.on_success("refund-rule", +0.3)
      cs.L2.ingest({"task": "退款", "model": "deepseek", "satisfied": True})
      cs.L3.is_unalterable(anchor) -> Tier0?
      result = cs.retrieve("退款")   # 0.4*L1 + 0.35*L2 + 0.25*L3
    """

    def __init__(self, recent_cap: int = 200):
        self._recent_cap = recent_cap
        self._conflicts: Dict[str, Any] = {}          # L1 冲突(即时避让)
        self._error_counts: Dict[str, int] = {}       # L1 错误复利计数
        self._temporary_constraints: Dict[str, Any] = {}  # L1 临时约束
        self._weights: Dict[str, float] = {}          # L1 经验复利权重
        self._recent_experiences: List[Dict[str, Any]] = []  # L2 近期经验
        self._unalterable: List[str] = []             # L3 Tier0 永固锚点 id
        # 三层
        self.L1 = L1RealTime(self)
        self.L2 = L2ShortTermTaste(self)
        self.L3 = L3LongTermJudgment(self)

    # -- L3 注册 Tier0 ------------------------------------------------------

    def register_unalterable(self, anchor_id: str) -> None:
        """注册 Tier0 永固锚点(世界观/价值观/人生观·不可改变)。"""
        if anchor_id not in self._unalterable:
            self._unalterable.append(anchor_id)

    # -- L1 检索得分 --------------------------------------------------------

    def _l1_score(self, query: str) -> float:
        """L1 实时迭代得分: 冲突/约束与 query 的相关(0-1)。"""
        q = query.lower()
        score = 0.0
        n = 0
        for fp in self._conflicts:
            if q in fp.lower():
                score += 1.0
            n += 1
        for sig in self._temporary_constraints:
            if q in sig.lower():
                score += 1.0
            n += 1
        if n == 0:
            return 0.0
        return round(min(1.0, score / n), 3)

    # -- 综合检索排列 --------------------------------------------------------

    def retrieve(self, query: str) -> Dict[str, Any]:
        """三层综合检索排列:
            0.4*L1 得分 + 0.35*L2 得分 + 0.25*L3 得分
        """
        l1 = self._l1_score(query)
        l2 = self.L2.taste(query)
        l3 = self.L3.judge(query)
        final = round(W_L1 * l1 + W_L2 * l2 + W_L3 * l3, 4)
        return {
            "query": query,
            "l1": l1, "l2": l2, "l3": l3,
            "weights": {"l1": W_L1, "l2": W_L2, "l3": W_L3},
            "final_score": final,
        }

    def snapshot(self) -> Dict[str, Any]:
        """当前三层状态快照(审计/调试用)。"""
        return {
            "conflicts": len(self._conflicts),
            "temporary_constraints": len(self._temporary_constraints),
            "error_counts": dict(self._error_counts),
            "recent_experiences": len(self._recent_experiences),
            "unalterable_anchors": list(self._unalterable),
            "compound_weights": dict(self._weights),
        }
