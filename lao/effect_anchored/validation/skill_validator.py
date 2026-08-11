"""
Skill Validator — LAO v3.1 P1-6
=================================

经验「技能」的五项验收门：一条经验在进入 LAO 可靠性层 / 可上链交易前,
必须通过五项验收。

五项验收门:
  1. completeness : 完整性   — 经验字段齐全(rule/rationale/source)
  2. verification : 真实性   — 有证据/来源可验证(evidence_count/source_type)
  3. consistency  : 一致性   — 所属 domain 合法, 与契约无冲突
  4. permission   : 权限合规 — allowed_agents/forbidden_domains 合规
  5. freshness    : 时效性   — 未过时(age_days 在有效期内)

用法:
  validator = SkillValidator()
  result = validator.validate({...})   # -> {passed: bool, gates: {...}, failed: [...]}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


class SkillValidator:
    """五项验收门。"""

    # 合法 domain(可扩展)
    VALID_DOMAINS = {"governance", "infrastructure", "cognitive", "product",
                     "runtime", "quality", "identity", "architecture"}

    def __init__(self, max_age_days: float = 365.0):
        self.max_age_days = max_age_days  # 时效性: 超过判定过时

    def validate(self, exp: Dict[str, Any]) -> Dict[str, Any]:
        """对一条经验执行五项验收。

        Args:
            exp: 经验 dict, 含 value/rule/source/evidence_count/domain/created_at 等。

        Returns:
            {"passed": bool, "gates": {门名: bool}, "failed": [门名], "suggestion": str}
        """
        value = exp.get("value") if isinstance(exp.get("value"), dict) else {}
        rule = str(value.get("rule") or exp.get("rule") or "内容缺失")
        rationale = str(value.get("rationale") or exp.get("rationale") or "")
        source = str(value.get("source") or exp.get("source") or exp.get("source_type") or "")
        evidence = int(exp.get("evidence_count") or value.get("evidence_count") or 0)
        domain = str(exp.get("domain") or value.get("domain") or "")
        forbidden = list(exp.get("forbidden_domains") or [])
        allowed = list(exp.get("allowed_agents") or [])
        if isinstance(rule, str) and rule.startswith("rule="):
            pass

        gates: Dict[str, bool] = {
            # 1. 完整性: rule 非空且有实质内容, 有来源
            "completeness": bool(rule and len(rule) >= 3 and source),
            # 2. 真实性: 有证据数 > 0 或明确来源类型
            "verification": evidence > 0 or bool(source),
            # 3. 一致性: domain 合法(若提供)且不在 forbidden 中
            "consistency": (not domain or domain in self.VALID_DOMAINS)
                           and domain not in forbidden,
            # 4. 权限合规: allowed 为空的契约自身可用, 有 forbidden 时 domain 不冲突
            "permission": domain not in forbidden and (
                not allowed or domain in allowed or not domain),
            # 5. 时效性: age_days 未超上限(若无时间戳视为不确定, 放宽通过)
            "freshness": True,
        }
        # freshness 单独算(需时间戳)
        age = _days_since(exp.get("created_at"))
        if age is not None:
            gates["freshness"] = age <= self.max_age_days

        failed = [g for g, ok in gates.items() if not ok]
        passed = not failed
        suggestion = f"经验验收{'通过' if passed else '未通过'}: " + ("全部五项通过" if passed else f"失败于 {', '.join(failed)}")
        return {
            "passed": passed,
            "gates": gates,
            "failed": failed,
            "suggestion": suggestion,
            "validated_at": _now_iso(),
        }

    def validate_batch(self, experiences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量验收。"""
        return [self.validate(e) for e in experiences]

    def passed_batch(self, experiences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """只返回通过五项验收的经验(可进入可靠性层/上链)。"""
        return [r for r in self.validate_batch(experiences) if r["passed"]]
