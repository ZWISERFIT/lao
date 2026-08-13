"""
RecoveryExperienceReplay — Recovery Experience Replay (Phase2 P0-4·创始人令 v3.4)
=============================================================================
Phase1 证明"可以恢复"; P0-4 证明"可以学习"。

    Failure → Search Experience → Recommend Recovery → Verify → Update Experience

能力:
- RecoveryMemory    : 历史恢复模式库(problem/solution/outcome)
- SimilarityMatch   : 故障相似度匹配(domain/symptom)
- PreviousSolution  : 推荐历史解法
- SuccessProbability: 推荐方案成功概率(基于历史 outcome)

设计:
- 单一事实源: 经验来自 TrustEvent/已验证 Recovery(不另建事实账本)
- 衔接 P0-3: Recommendation 可生成 ExperienceAsset; 更新回写
- 闭环: 第二次同类故障 → 调用历史经验(创始人 Test4)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class RecoveryPattern:
    """一条历史恢复经验。"""
    pattern_id: str
    domain: str                # gateway/context/provider/network/...
    symptom: str               # 症状关键词
    solution: str              # 解法(恢复动作)
    success_count: int = 0
    fail_count: int = 0
    last_outcome: bool = True
    created_ts: str = ""
    verification_pct: float = 0.0
    attestation: str = ""

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return round(self.success_count / total, 3)


@dataclass
class RecoveryRecommendation:
    """一次恢复推荐。"""
    pattern_id: str
    domain: str
    solution: str
    success_probability: float   # 0~1
    matched: bool
    confidence: str = "low"      # low/medium/high
    ts: str = ""


class RecoveryMemory:
    """恢复经验库(相似度匹配 + 推荐)。"""

    def __init__(self):
        self._patterns: Dict[str, RecoveryPattern] = {}
        self._counter = 0

    def record(self, domain: str, symptom: str, solution: str,
               outcome: bool = True, verification_pct: float = 0.0,
               attestation: str = "", pattern_id: str = "") -> RecoveryPattern:
        """记录一次恢复结果(outcome=已验证正确性)。

        同 (domain, symptom) 复用同一 pattern, 累积 success/fail(知识沉淀)。
        """
        # 查找已有 pattern(同 domain+symptom)
        existing = [p for p in self._patterns.values()
                    if p.domain == domain and p.symptom == symptom]
        if existing and not pattern_id:
            p = existing[0]
        elif pattern_id and pattern_id in self._patterns:
            p = self._patterns[pattern_id]
        else:
            self._counter += 1
            pid = pattern_id or f"PAT-{self._counter:03d}"
            p = RecoveryPattern(pattern_id=pid, domain=domain, symptom=symptom,
                                solution=solution, created_ts=_now(),
                                verification_pct=verification_pct, attestation=attestation)
            self._patterns[p.pattern_id] = p
        if outcome:
            p.success_count += 1
            p.last_outcome = True
        else:
            p.fail_count += 1
            p.last_outcome = False
        return p

    def search(self, domain: str, symptom: str, top_k: int = 3) -> List[RecoveryPattern]:
        """相似度匹配: 同 domain 优先 + symptom 关键词命中。仅返回有实质匹配的。"""
        scored = []
        sym_kw = set(str(symptom).lower().split())
        for p in self._patterns.values():
            score = 0.0
            domain_hit = p.domain == domain
            kw_hit = 0
            if sym_kw:
                pat_kw = set(p.symptom.lower().split())
                kw_hit = len(sym_kw & pat_kw)
            if domain_hit:
                score += 1.0
            if kw_hit:
                score += 0.5 * (kw_hit / max(len(sym_kw), 1))
            # 无实质匹配(域不同且无关键词命中)→ 不进入候选
            if not domain_hit and kw_hit == 0:
                continue
            if p.last_outcome:
                score += 0.1
            scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:top_k]]

    def recommend(self, domain: str, symptom: str) -> RecoveryRecommendation:
        """推荐历史解法 + 成功概率。"""
        best = self.search(domain, symptom, top_k=1)
        if not best:
            return RecoveryRecommendation(pattern_id="", domain=domain, solution="",
                                           success_probability=0.0, matched=False, ts=_now())
        p = best[0]
        return RecoveryRecommendation(
            pattern_id=p.pattern_id, domain=p.domain, solution=p.solution,
            success_probability=p.success_rate,
            matched=True, confidence="high" if p.success_rate >= 0.7 else "medium",
            ts=_now())

    def count(self) -> int:
        return len(self._patterns)

    def to_trust_event(self, r: RecoveryRecommendation) -> dict:
        """→ TrustEvent 负载(可审计)。"""
        return {
            "event": "RecoveryRecommended",
            "subtype": "RecoveryEvent",
            "domain": r.domain,
            "pattern_id": r.pattern_id,
            "solution": r.solution,
            "success_probability": r.success_probability,
            "matched": r.matched,
            "confidence": r.confidence,
            "ts": r.ts,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
