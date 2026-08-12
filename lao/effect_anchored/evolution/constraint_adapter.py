#!/usr/bin/env python3
"""
ConstraintAdapter — LAO Protocol ↔ SelfHealing 执行引擎 正式架构边界 (P0⑥#4)
=============================================================================
创始人终审(2026-08-13·方案D+) 三分语义架构分层:

    lao.Constraint (What·声明式契约·开源)
            ↓
    ConstraintAdapter (schema翻译·severity映射·版本兼容·provenance·TrustEvent)
            ↓
    SelfHealingConstraint (How·闭源执行引擎)

Adapter 是**正式架构边界**, 不允许长期直接互调。
第三方未来可实现自己的 Constraint Engine, 不被 LAO Core 锁死。

职责(仅适配·不含策略):
- schema translation : open Constraint(声明式id/domain/level/rule/trigger) ↔ how(exec_script/severity/check)
- severity mapping    : open ConstraintLevel(RED/YELLOW/GREEN) ↔ how severity(high/medium/low)
- version compatibility: 双轨版本对齐(不破坏既有注册)
- provenance          : 保留 open→how 溯源链(哪条声明式契约 → 哪个执行引擎)
- TrustEvent emission : 触发/适配动作发 TrustEvent(保持可验证·公开怎么证明)

⚠️ 本 Adapter 不含: 自愈算法 / auto_fix 策略 / promotion-rollback / 私有阈值
   (这些留 SelfHealingConstraint / Private Policy, 闭源)。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# 开源 ConstraintLevel → 闭源 severity 映射(公开映射·不涉私有阈值)
LEVEL_TO_SEVERITY = {
    "red": "high",       # ConstraintLevel.RED → severity=high
    "yellow": "medium",  # ConstraintLevel.YELLOW → severity=medium
    "green": "low",      # ConstraintLevel.GREEN → severity=low
}
SEVERITY_TO_LEVEL = {v: k for k, v in LEVEL_TO_SEVERITY.items()}


@dataclass
class AdapteredConstraint:
    """适配后的约束(桥接 open Constraint 与 how SelfHealingConstraint)。

    同时携带两观视图:
    - open_view : LAO Protocol 声明式契约视图(What·开源·可验证)
    - how_view  : SelfHealing 执行视图(How·执行引擎边界)
    """
    open_id: str = ""                    # lao.Constraint.id
    how_id: str = ""                     # SelfHealingConstraint.constraint_id
    domain: str = ""                     # ConstraintDomain(开源)
    level: str = ""                      # ConstraintLevel(red/yellow/green·开源)
    severity: str = ""                   # 闭源 severity(high/medium/low)
    rule: str = ""                       # 声明式规则文本(What·开源)
    trigger_pattern: str = ""            # 触发匹配(开源)
    exec_script: str = ""                # 执行脚本(How·闭源·可选仅引用)
    version: str = "3.2.0"               # 协议版本
    provenance: Dict[str, str] = field(default_factory=dict)  # 溯源链

    def to_open_dict(self) -> Dict[str, Any]:
        """开放视图(可公开·可验证): 不含 exec_script/私有阈值"""
        return {
            "id": self.open_id, "domain": self.domain,
            "level": self.level, "rule": self.rule,
            "trigger_pattern": self.trigger_pattern,
            "severity": self.severity,   # 映射结果可公开
            "provenance": self.provenance,
            "version": self.version,
        }


class ConstraintAdapter:
    """正式架构边界: lao.Constraint(What) ↔ SelfHealingConstraint(How)。

    第三方可实现自己的 Constraint Engine(实现 ConstraintBackend 接口),
    不被 LAO Core 锁死(创始人要求#4)。
    """

    def __init__(self, backend=None):
        """backend: 可选的自愈执行后端(SelfHealing 引擎/第三方引擎)。

        未注入 backend 时, 本 Adapter 仅做 schema/severity 适配,
        不调用具体执行(可独立用于开源契约层验证)。
        """
        self._backend = backend  # 不强制, 保持开源契约可独立工作

    # -- schema translation: open → how view ------------------------------
    def adapt(self, constraint: Any) -> AdapteredConstraint:
        """把一条 open Constraint 适配为桥接视图(含 severity 映射 + provenance)。

        Args:
            constraint: lao.Constraint 实例(声明式契约)。

        Returns:
            AdapteredConstraint(open_view + how_view 桥接)。
        """
        _id = getattr(constraint, "id", "")
        _domain = getattr(getattr(constraint, "domain", ""), "value", "") or \
            str(getattr(constraint, "domain", ""))
        _level = getattr(getattr(constraint, "level", ""), "value", "") or \
            str(getattr(constraint, "level", ""))
        _sev = LEVEL_TO_SEVERITY.get(_level, "medium")
        return AdapteredConstraint(
            open_id=_id,
            how_id=f"SH-{_id}" if _id else "",
            domain=_domain,
            level=_level,
            severity=_sev,
            rule=getattr(constraint, "rule", ""),
            trigger_pattern=getattr(constraint, "trigger_pattern", ""),
            provenance={
                "open_constraint": _id,
                "mapped_severity": _sev,
                "adapter_version": "3.2.0",
            },
        )

    def _to_exec(self, adapted: AdapteredConstraint):
        """(桥接)把适配视图交给执行后端(如有)。

        ⚠️ 仅引用 exec_script 句柄, 不在此定义自愈算法(闭源在 backend)。
        """
        if self._backend is not None and hasattr(self._backend, "execute"):
            return self._backend.execute(adapted)
        return None

    # -- severity mapping (双向) ------------------------------------------
    @staticmethod
    def severity_to_level(severity: str) -> str:
        """闭源 severity → 开源 ConstraintLevel(公开映射)。"""
        return SEVERITY_TO_LEVEL.get(severity, "yellow")

    # -- TrustEvent 桥接(保持可验证·创始人红线6) ---------------------------
    def evidence(self, adapted: AdapteredConstraint, action: str = "adapted") -> Dict[str, Any]:
        """生成适配动作的 TrustEvent 负载(公开可验证)。

        Returns:
            可写入 TrustEventLedger 的事件字段(不含私有策略)。
        """
        return {
            "type": "constraint_adapt",
            "action": action,
            "agent": "constraint_adapter",
            "open_id": adapted.open_id,
            "how_id": adapted.how_id,
            "domain": adapted.domain,
            "level": adapted.level,
            "severity": adapted.severity,
            "provenance": adapted.provenance,
            "verifiable": True,   # fingerprint/provenance 可验证(公开)
        }
