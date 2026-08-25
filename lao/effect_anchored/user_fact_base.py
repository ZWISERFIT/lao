"""UserFactBase — L2 用户事实依据库 (LAO v3.5)

存储用户明确陈述的事实、历史决策、偏好。
供 DeterministicTranslator 做事实交叉验证，把 LLM 的概率性回答
锚定到用户事实之上，输出确定性回答。

Fact 结构: {fact_id, content, source, confidence, timestamp, domain}
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

# 事实来源类型
SOURCE_USER_STATEMENT = "user_statement"   # 用户明确陈述的事实
SOURCE_DECISION = "decision"               # 用户历史决策
SOURCE_PREFERENCE = "preference"           # 用户偏好

DEFAULT_DOMAIN = "general"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fact_id(content: str, user_id: str) -> str:
    import hashlib
    seed = f"{user_id}|{content}"
    return "fact-" + hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


@dataclass
class Fact:
    """单条用户事实。"""

    fact_id: str
    content: str
    source: str = SOURCE_USER_STATEMENT
    confidence: float = 1.0            # 0.0-1.0(用户明确陈述默认 1.0)
    timestamp: str = field(default_factory=_utcnow)
    domain: str = DEFAULT_DOMAIN

    def to_dict(self) -> Dict:
        return {
            "fact_id": self.fact_id,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "domain": self.domain,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Fact":
        return cls(
            fact_id=d.get("fact_id", ""),
            content=d.get("content", ""),
            source=d.get("source", SOURCE_USER_STATEMENT),
            confidence=float(d.get("confidence", 1.0)),
            timestamp=d.get("timestamp", _utcnow()),
            domain=d.get("domain", DEFAULT_DOMAIN),
        )


class UserFactBase:
    """用户事实依据库：按 user_id 分域存储与查询。"""

    def __init__(self):
        # user_id -> domain -> List[Fact]
        self._facts: Dict[str, Dict[str, List[Fact]]] = {}

    # ── 写入 ─────────────────────────────────────────────────────────────

    def add_fact(
        self,
        user_id: str,
        content: str,
        source: str = SOURCE_USER_STATEMENT,
        domain: str = DEFAULT_DOMAIN,
        confidence: float = 1.0,
        fact_id: Optional[str] = None,
    ) -> Fact:
        """新增一条用户事实(同内容去重，重复则刷新时间戳)。"""
        content = (content or "").strip()
        if not content:
            raise ValueError("事实内容不能为空")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence 必须在 0-1")
        fact_id = fact_id or _fact_id(content, user_id)

        # 同 id/同内容去重: 刷新时间戳与置信度
        for existing in self._facts.get(user_id, {}).get(domain, []):
            if existing.fact_id == fact_id or existing.content == content:
                existing.timestamp = _utcnow()
                existing.confidence = confidence
                existing.source = source
                return existing

        fact = Fact(
            fact_id=fact_id, content=content, source=source,
            confidence=confidence, domain=domain,
        )
        self._facts.setdefault(user_id, {}).setdefault(domain, []).append(fact)
        return fact

    def add_statement(self, user_id: str, content: str, domain: str = DEFAULT_DOMAIN) -> Fact:
        """记录用户明确陈述的事实。"""
        return self.add_fact(user_id, content, SOURCE_USER_STATEMENT, domain)

    def add_decision(self, user_id: str, content: str, domain: str = DEFAULT_DOMAIN) -> Fact:
        """记录用户历史决策。"""
        return self.add_fact(user_id, content, SOURCE_DECISION, domain)

    def add_preference(self, user_id: str, content: str, domain: str = DEFAULT_DOMAIN) -> Fact:
        """记录用户偏好。"""
        return self.add_fact(user_id, content, SOURCE_PREFERENCE, domain)

    # ── 查询 ─────────────────────────────────────────────────────────────

    def query_facts(
        self,
        user_id: str,
        domain: Optional[str] = None,
    ) -> List[Fact]:
        """查询用户事实。domain=None 时返回该用户全部域的事实。"""
        by_domain = self._facts.get(user_id, {})
        if domain is None:
            facts: List[Fact] = []
            for lst in by_domain.values():
                facts.extend(lst)
            return facts
        return list(by_domain.get(domain, []))

    def get_fact(self, user_id: str, fact_id: str) -> Optional[Fact]:
        """按 fact_id 精确取一条。"""
        for fact in self.query_facts(user_id):
            if fact.fact_id == fact_id:
                return fact
        return None

    def domains(self, user_id: str) -> List[str]:
        """列出该用户已有事实的域。"""
        return sorted(self._facts.get(user_id, {}).keys())

    def count(self, user_id: Optional[str] = None) -> int:
        """事实条数(全局或单用户)。"""
        if user_id is None:
            return sum(len(lst) for by_d in self._facts.values() for lst in by_d.values())
        return sum(len(lst) for lst in self._facts.get(user_id, {}).values())

    # ── 导出/导入 ────────────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        return {
            uid: {dom: [f.to_dict() for f in lst]
                  for dom, lst in by_d.items()}
            for uid, by_d in self._facts.items()
        }

    def load_dict(self, data: Dict) -> None:
        """从 to_dict() 产物恢复(覆盖当前内容)。"""
        self._facts = {}
        for uid, by_d in (data or {}).items():
            for dom, lst in by_d.items():
                for d in lst:
                    self._facts.setdefault(uid, {}).setdefault(dom, []).append(
                        Fact.from_dict(d)
                    )
