"""DeterministicCognitiveSystem — L2 认知系统 (LAO v3.5)

理解用户思维模式，为"翻译官"(DeterministicTranslator)提供认知侧依据：
  - 认知锚点(Cognitive Anchor)：用户的核心决策原则
  - 推理模式识别：用户常见的推理路径

类名说明：现有 cognitive_engine.py 已定义 CognitiveSystem(三层认知引擎)，
本模块按 v3.5 规格采用 DeterministicCognitiveSystem，避免类名冲突。

match_cognitive_pattern(user_id, question) → CognitivePattern
    纯规则确定性匹配(关键词/锚点重叠打分)，无概率成分。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CognitiveAnchorPoint:
    """认知锚点：用户的一条核心决策原则。"""

    anchor_id: str
    principle: str                 # 原则表述，如 "安全优先于成本"
    domain: str = "general"
    keywords: List[str] = field(default_factory=list)  # 触发关键词
    weight: float = 1.0            # 锚点强度
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> Dict:
        return {
            "anchor_id": self.anchor_id,
            "principle": self.principle,
            "domain": self.domain,
            "keywords": self.keywords,
            "weight": self.weight,
            "created_at": self.created_at,
        }


@dataclass
class CognitivePattern:
    """识别出的用户推理模式。"""

    pattern_id: str
    name: str                          # 模式名，如 "风险规避型"
    anchors: List[str] = field(default_factory=list)      # 关联认知锚点 principle
    reasoning_path: List[str] = field(default_factory=list)  # 常见推理路径步骤
    keywords: List[str] = field(default_factory=list)
    match_score: float = 0.0           # 本次匹配得分 0-1
    matched_anchor_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "anchors": self.anchors,
            "reasoning_path": self.reasoning_path,
            "keywords": self.keywords,
            "match_score": self.match_score,
            "matched_anchor_ids": self.matched_anchor_ids,
        }


# 内置通用推理模式库(可通过 add_pattern 覆盖/扩展)
BUILTIN_PATTERNS: List[Dict] = [
    {
        "pattern_id": "pat-safety-first",
        "name": "安全优先型",
        "anchors": ["安全优先于成本", "质量底线不可破"],
        "reasoning_path": ["识别风险", "设定底线", "在底线内求最优"],
        "keywords": ["安全", "风险", "质量", "底线", "故障", "幻觉"],
    },
    {
        "pattern_id": "pat-cost-efficiency",
        "name": "成本效率型",
        "anchors": ["性价比最优而非成本最低", "用数据说话"],
        "reasoning_path": ["量化成本", "对比收益", "选性价比最优"],
        "keywords": ["成本", "预算", "价格", "节省", "性价比", "开销"],
    },
    {
        "pattern_id": "pat-evidence-driven",
        "name": "证据驱动型",
        "anchors": ["无证据不下结论", "事实优先于推断"],
        "reasoning_path": ["收集事实", "交叉验证", "再下结论"],
        "keywords": ["证据", "事实", "验证", "数据", "依据", "实测"],
    },
]


class DeterministicCognitiveSystem:
    """L2 认知系统：认知锚点 + 推理模式识别(确定性规则匹配)。

    Usage:
        cog = DeterministicCognitiveSystem()
        cog.add_anchor("founder", "安全优先于成本", keywords=["安全", "质量"])
        pattern = cog.match_cognitive_pattern("founder", "这次路由要考虑安全风险")
        # → CognitivePattern(name="安全优先型", match_score=...)
    """

    def __init__(self, patterns: Optional[List[Dict]] = None):
        # user_id -> List[CognitiveAnchorPoint]
        self._anchors: Dict[str, List[CognitiveAnchorPoint]] = {}
        # pattern_id -> CognitivePattern (全用户共享的模式库)
        self._patterns: Dict[str, CognitivePattern] = {}
        for p in (patterns if patterns is not None else BUILTIN_PATTERNS):
            self.add_pattern(p)

    # ── 认知锚点 ─────────────────────────────────────────────────────────

    def add_anchor(
        self,
        user_id: str,
        principle: str,
        domain: str = "general",
        keywords: Optional[List[str]] = None,
        weight: float = 1.0,
        anchor_id: Optional[str] = None,
    ) -> CognitiveAnchorPoint:
        """登记一条用户核心决策原则(同原则去重)。"""
        principle = (principle or "").strip()
        if not principle:
            raise ValueError("认知锚点 principle 不能为空")
        for existing in self._anchors.get(user_id, []):
            if existing.principle == principle:
                existing.keywords = list(set(existing.keywords) | set(keywords or []))
                existing.weight = max(existing.weight, weight)
                return existing
        anchor_id = anchor_id or f"anchor-{len(self._anchors.get(user_id, [])) + 1:04d}"
        anchor = CognitiveAnchorPoint(
            anchor_id=anchor_id, principle=principle, domain=domain,
            keywords=[k.lower() for k in (keywords or [])], weight=weight,
        )
        self._anchors.setdefault(user_id, []).append(anchor)
        return anchor

    def anchors_for(self, user_id: str, domain: Optional[str] = None) -> List[CognitiveAnchorPoint]:
        """取用户认知锚点(可按域过滤)。"""
        anchors = self._anchors.get(user_id, [])
        if domain is None:
            return list(anchors)
        return [a for a in anchors if a.domain == domain]

    # ── 推理模式 ─────────────────────────────────────────────────────────

    def add_pattern(self, pattern: Dict) -> CognitivePattern:
        """登记/覆盖一个推理模式。"""
        pid = pattern.get("pattern_id") or f"pat-{len(self._patterns) + 1:04d}"
        cp = CognitivePattern(
            pattern_id=pid,
            name=pattern.get("name", pid),
            anchors=list(pattern.get("anchors", [])),
            reasoning_path=list(pattern.get("reasoning_path", [])),
            keywords=[k.lower() for k in pattern.get("keywords", [])],
        )
        self._patterns[pid] = cp
        return cp

    def patterns(self) -> List[CognitivePattern]:
        """全部推理模式。"""
        return list(self._patterns.values())

    # ── 模式匹配(核心接口) ───────────────────────────────────────────────

    def match_cognitive_pattern(self, user_id: str, question: str) -> Optional[CognitivePattern]:
        """识别问题命中的用户推理模式。

        打分规则(确定性)：
          - 模式关键词在问题中出现 1 个 +0.5，每多 1 个 +0.2(上限 1.0)
          - 用户认知锚点 principle 与模式 anchors 重叠 → +0.3(乘锚点权重)
        返回得分最高且 >0 的模式副本(带 match_score)；无命中返回 None。
        """
        q = (question or "").lower()
        user_anchor_principles = [a.principle for a in self._anchors.get(user_id, [])]

        best: Optional[CognitivePattern] = None
        best_score = 0.0
        for pat in self._patterns.values():
            score = 0.0
            kw_hits = [k for k in pat.keywords if k and k in q]
            if not kw_hits:
                continue  # 无关键词命中→跳过此模式，不计算锚点重叠
            score += min(1.0, 0.5 + 0.2 * (len(kw_hits) - 1))
            anchor_overlap = [p for p in pat.anchors if p in user_anchor_principles]
            if anchor_overlap:
                weights = [
                    a.weight for a in self._anchors.get(user_id, [])
                    if a.principle in anchor_overlap
                ]
                score += 0.3 * max(weights) if weights else 0.3
            if score > best_score:
                best_score = min(score, 1.0)
                best = pat

        if best is None or best_score <= 0.0:
            return None
        matched = CognitivePattern(
            pattern_id=best.pattern_id,
            name=best.name,
            anchors=list(best.anchors),
            reasoning_path=list(best.reasoning_path),
            keywords=list(best.keywords),
            match_score=round(best_score, 4),
            matched_anchor_ids=[
                a.anchor_id for a in self._anchors.get(user_id, [])
                if a.principle in best.anchors
            ],
        )
        return matched

    # ------------------------------------------------------------------
    # P1 回站验收 (2026-08-19 · 产品规格 v1.0 1.2 Step2)
    # ------------------------------------------------------------------

    def check_consistency(self, response_text: str) -> Dict:
        """认知一致性检查（规格 1.2 Step2·新建方法）。

        逻辑:
            - 响应文本与任一认知锚点 principle 冲突关键词（不/禁止/绝不能/必须/永远）
              同现时 → 严重矛盾（consistent=False）
            - 否则 → 一致（consistent=True）

        Returns:
            {"consistent": bool, "conflicts": List[str]}

        fail-open: 异常返回 {"consistent": True, "conflicts": []}。
        """
        try:
            if not response_text:
                return {"consistent": True, "conflicts": []}
            conflicts = []
            neg_constraint = ("禁止", "不能", "不得", "绝不", "必须", "永远不")
            stop = ("的", "了", "与", "和", "或", "对", "在", "是", "客户")
            for uid, anchors in self._anchors.items():
                for a in anchors:
                    principle = str(a.principle or "")
                    if not principle:
                        continue
                    # 原则含否定约束词 → 需要校验响应是否违背
                    has_constraint = any(nc in principle for nc in neg_constraint)
                    if not has_constraint:
                        continue
                    # 原则主题词: 去掉约束词/停用词后的核心内容
                    theme = principle
                    for nc in neg_constraint:
                        theme = theme.replace(nc, "")
                    theme_words = [
                        w for w in theme.replace("，", " ").replace(",", " ").split()
                        if len(w) >= 2 and w not in stop
                    ]
                    if not theme_words and len(theme) >= 2:
                        theme_words = [theme]
                    # 响应出现主题词 → 违背约束(除非响应明确否定主题行为)
                    response_denies = any(dw in response_text for dw in
                                          ("绝不", "不会", "不能", "禁止", "不得", "避免"))
                    for w in theme_words[:3]:
                        if w in response_text and not response_denies:
                            conflicts.append(
                                f"principle '{principle[:30]}' vs response '{w}'"
                            )
                            break
            return {"consistent": len(conflicts) == 0, "conflicts": conflicts[:5]}
        except Exception:
            return {"consistent": True, "conflicts": []}
        return {
            "anchors": {
                uid: [a.to_dict() for a in lst]
                for uid, lst in self._anchors.items()
            },
            "patterns": [p.to_dict() for p in self._patterns.values()],
        }
