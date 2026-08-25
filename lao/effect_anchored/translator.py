"""DeterministicTranslator — L2 翻译官核心 (LAO v3.5)

LAO 既懂用户(萃取事实依据和认知系统)，也懂 LLM 推理的概率性，
把概率性回答翻译成确定性回答。

工作流程：
    1. 接收 LLM 的概率性回答(含 confidence score)
    2. 查询用户事实依据库(UserFactBase)
    3. 查询认知系统(DeterministicCognitiveSystem)
    4. 用事实交叉验证 → 输出确定性回答
    5. 标注置信度来源(事实依据 vs 模型推理)

判定规则(确定性，无概率成分)：
    - corroborated(事实佐证) ≥1 条 且 修正后置信度 ≥ 0.8 → deterministic=True
    - 出现 conflict(与事实矛盾) → deterministic=False, 置信度下调
    - 其余 → 维持模型推理属性(deterministic=False)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from lao.effect_anchored.cognitive_system import DeterministicCognitiveSystem
from lao.effect_anchored.user_fact_base import UserFactBase, Fact

# 事实佐证对置信度的加成(每条 corroborated +0.1，封顶)
CORROBORATION_BOOST_PER_FACT = 0.1
CORROBORATION_BOOST_CAP = 0.3
# 矛盾事实的置信度惩罚
CONFLICT_PENALTY = 0.2
# 确定性判定阈值
DETERMINISTIC_CONFIDENCE_THRESHOLD = 0.8

# 矛盾信号词：紧邻事实关键内容时视为与事实相抵触
NEGATION_MARKERS = ("不", "没有", "并非", "不是", "未", "无", "并非如此")
# 佐证/矛盾判定最低词条匹配率(防粗分词假阳性：单词命中≠事实佐证)
MIN_TERM_MATCH_RATIO = 0.25


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_response(llm_response: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """归一化 LLM 回答输入：str → {"content": str}; dict 原样(取默认值)。"""
    if isinstance(llm_response, str):
        return {"content": llm_response, "confidence": None}
    if isinstance(llm_response, dict):
        return {
            "content": str(llm_response.get("content", llm_response.get("text", ""))),
            "confidence": llm_response.get("confidence"),
        }
    raise TypeError(f"llm_response 需为 str 或 dict, 实际 {type(llm_response).__name__}")


def _norm_user_context(user_context: Union[Dict[str, Any], None]) -> Dict[str, Any]:
    """归一化用户上下文：user_id / domain / facts / fact_base / cognitive_system。"""
    ctx = dict(user_context or {})
    ctx.setdefault("user_id", "anonymous")
    ctx.setdefault("domain", None)
    return ctx


def _fact_terms(content: str) -> List[str]:
    """从事实内容提取关键词条(去停用词的粗粒度分词)。

    简体中文无空格分隔，采用 2-gram + 长词优先的确定性切分：
    """
    text = (content or "").strip()
    if not text:
        return []
    terms = [t for t in text.replace("，", " ").replace("。", " ").split() if t]
    if not terms:
        # 无分隔符: 取 2-gram(≥2 字才有语义)
        terms = [text[i:i + 2] for i in range(len(text) - 1)] or [text]
    return [t.lower() for t in terms if len(t) >= 2]


@dataclass
class FactBasis:
    """事实依据萃取结果。"""

    user_id: str
    domain: Optional[str] = None
    facts: List[str] = field(default_factory=list)        # 事实内容引用
    fact_ids: List[str] = field(default_factory=list)     # 事实 id 引用
    confidences: List[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.facts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "domain": self.domain,
            "facts": self.facts,
            "fact_ids": self.fact_ids,
            "confidences": self.confidences,
        }


@dataclass
class ValidationResult:
    """事实 × 推理 交叉验证结果。"""

    consistent: bool
    corroborated: List[str] = field(default_factory=list)  # 被推理印证的事实
    conflicts: List[str] = field(default_factory=list)     # 与推理矛盾的事实
    score: float = 0.0                                      # 一致性得分 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consistent": self.consistent,
            "corroborated": self.corroborated,
            "conflicts": self.conflicts,
            "score": self.score,
        }


@dataclass
class DeterministicAnswer:
    """翻译官输出的确定性回答。"""

    content: str
    deterministic: bool = False
    confidence: float = 0.0
    confidence_source: str = "model_inference"  # fact_basis | model_inference | mixed
    fact_basis: List[str] = field(default_factory=list)
    confidence_boost: float = 0.0
    validation: Optional[ValidationResult] = None
    cognitive_pattern: Optional[str] = None     # 命中的认知模式名
    translated_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "deterministic": self.deterministic,
            "confidence": self.confidence,
            "confidence_source": self.confidence_source,
            "fact_basis": self.fact_basis,
            "confidence_boost": self.confidence_boost,
            "validation": self.validation.to_dict() if self.validation else None,
            "cognitive_pattern": self.cognitive_pattern,
            "translated_at": self.translated_at,
        }


class DeterministicTranslator:
    """L2 翻译官：LLM 概率性回答 + 用户事实依据 → 确定性回答。"""

    def __init__(
        self,
        fact_base: Optional[UserFactBase] = None,
        cognitive_system: Optional[DeterministicCognitiveSystem] = None,
    ):
        self.fact_base = fact_base or UserFactBase()
        self.cognitive_system = cognitive_system or DeterministicCognitiveSystem()

    # ── 事实依据萃取 ─────────────────────────────────────────────────────

    def extract_fact_basis(self, user_context: Union[Dict[str, Any], None]) -> FactBasis:
        """从用户上下文萃取事实依据。

        优先取 ctx["facts"](已萃取内容列表)；
        否则查询注入的 UserFactBase(user_id + domain)。
        """
        ctx = _norm_user_context(user_context)
        user_id = ctx["user_id"]
        domain = ctx.get("domain")

        raw_facts = ctx.get("facts")
        if raw_facts:
            contents, ids, confs = [], [], []
            for f in raw_facts:
                if isinstance(f, Fact):
                    contents.append(f.content)
                    ids.append(f.fact_id)
                    confs.append(f.confidence)
                elif isinstance(f, dict):
                    contents.append(str(f.get("content", "")))
                    ids.append(str(f.get("fact_id", "")))
                    confs.append(float(f.get("confidence", 1.0)))
                else:
                    contents.append(str(f))
                    ids.append("")
                    confs.append(1.0)
            return FactBasis(user_id=user_id, domain=domain,
                             facts=contents, fact_ids=ids, confidences=confs)

        facts = self.fact_base.query_facts(user_id, domain)
        return FactBasis(
            user_id=user_id, domain=domain,
            facts=[f.content for f in facts],
            fact_ids=[f.fact_id for f in facts],
            confidences=[f.confidence for f in facts],
        )

    # ── 交叉验证 ─────────────────────────────────────────────────────────

    @staticmethod
    def cross_validate(
        facts: Union[List[str], FactBasis],
        inference: str,
    ) -> ValidationResult:
        """用事实交叉验证推理内容。

        规则(确定性)：
          - 事实词条匹配率 ≥ MIN_TERM_MATCH_RATIO 才计入；
            匹配词条出现在推理中 → corroborated(佐证)
            匹配词条前缀带矛盾信号词 → conflicts(矛盾)
          - score = corroborated / (corroborated + conflicts)，全空=0
          - consistent = 无矛盾 且 至少 1 条佐证
        """
        if isinstance(facts, FactBasis):
            fact_contents = facts.facts
        else:
            fact_contents = [str(f) for f in (facts or [])]
        text = (inference or "").lower()

        corroborated: List[str] = []
        conflicts: List[str] = []
        for fact in fact_contents:
            terms = _fact_terms(fact)
            if not terms:
                continue
            matched = [t for t in terms if t in text]
            if not matched or len(matched) / len(terms) < MIN_TERM_MATCH_RATIO:
                continue
            # 检查匹配词前缀是否带矛盾信号(窗口 3 字符)
            contradicted = False
            for t in matched:
                idx = text.find(t)
                prefix = text[max(0, idx - 3):idx]
                if any(neg in prefix for neg in NEGATION_MARKERS):
                    contradicted = True
                    break
            if contradicted:
                conflicts.append(fact)
            else:
                corroborated.append(fact)

        total = len(corroborated) + len(conflicts)
        score = len(corroborated) / total if total > 0 else 0.0
        return ValidationResult(
            consistent=(len(conflicts) == 0 and len(corroborated) > 0),
            corroborated=corroborated,
            conflicts=conflicts,
            score=round(score, 4),
        )

    # ── 翻译主流程 ───────────────────────────────────────────────────────

    def translate(
        self,
        llm_response: Union[str, Dict[str, Any]],
        user_context: Union[Dict[str, Any], None] = None,
    ) -> DeterministicAnswer:
        """把 LLM 概率性回答翻译成确定性回答。

        Args:
            llm_response: {"content": str, "confidence": float} 或纯文本 str。
            user_context: {"user_id", "domain", "facts"(可选直供事实)}。

        Returns:
            DeterministicAnswer(含 deterministic 标记/置信度来源/事实引用)。
        """
        resp = _norm_response(llm_response)
        ctx = _norm_user_context(user_context)
        content = resp["content"]
        model_conf = resp["confidence"]
        base_confidence = float(model_conf) if model_conf is not None else 0.5

        basis = self.extract_fact_basis(ctx)
        validation = self.cross_validate(basis.facts, content)

        boost = min(CORROBORATION_BOOST_CAP,
                    CORROBORATION_BOOST_PER_FACT * len(validation.corroborated))
        if validation.conflicts:
            boost -= CONFLICT_PENALTY
        confidence = max(0.0, min(1.0, base_confidence + boost))

        deterministic = (
            len(validation.corroborated) > 0
            and len(validation.conflicts) == 0
            and round(confidence, 6) >= DETERMINISTIC_CONFIDENCE_THRESHOLD
        )

        if validation.corroborated and validation.conflicts:
            source = "mixed"
        elif validation.corroborated:
            source = "fact_basis"
        else:
            source = "model_inference"

        pattern = self.cognitive_system.match_cognitive_pattern(
            ctx["user_id"], content
        )

        return DeterministicAnswer(
            content=content,
            deterministic=deterministic,
            confidence=round(confidence, 4),
            confidence_source=source,
            fact_basis=list(basis.fact_ids) if basis.fact_ids else list(basis.facts),
            confidence_boost=round(boost, 4),
            validation=validation,
            cognitive_pattern=pattern.name if pattern else None,
        )
