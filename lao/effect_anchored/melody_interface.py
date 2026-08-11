"""
Melody 工作接口 — LAO 2.7 P0-③（架构层定义·不实现）
=====================================================

边界（对齐 LAO = Trust Layer）:
  - LAO Kernel = Storage(契约/锚点) + Verification(已验证检索) + Retrieval
  - Melody     = Identity + Preference + Matching + Personal Adaptation

本文件**只定义 Melody 需要实现的契约**（dataclass + 方法签名 + 语义说明），
**不实现任何逻辑**。Melody 接入时按此接口落地（Composition / 个性化域）。

关联:
  - P0-① Ethan POST /attest —— 存证基础
  - P0-② ExperienceContract.attest_experience() —— 确权链路
  - experience_matching.retrieve_verified_experience() —— 已验证经验检索入口

运行: 本模块不执行逻辑，仅被 Melody 引用接口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentProfile:
    """Melody 组合出的 Agent 画像（面向某 owner 的个性化工作描述）。

    由 compose_agent_profile 产出；validate_composition 校验其兼容性。
    """
    owner: str                                                   # human/agent 拥有者
    agent_id: str = "customer_service"                           # 生成画像的 Agent
    experiences: List[Dict[str, Any]] = field(default_factory=list)  # 引用的已验证经验(锚点dict)
    rules: List[str] = field(default_factory=list)               # 组合出的可执行规则(由经验派生)
    composed_at: str = field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat())
    # 溯源: 每条经验来源(存证ID) → 可审计
    attestation_refs: List[str] = field(default_factory=list)


class MelodyBuilder:
    """Melody 的实现契约（接口定义，不实现逻辑）。

    职责范围（Melody 域）:
      - 从 LAO 已验证经验中组合个性化 Agent 画像
      - 校验组合兼容性

    不做（LAO 域）:
      - 不存储/校验经验真实性（LAO Kernel 负责）
      - 不做存证（Ethan + ExperienceContract 负责）
    """

    def list_attested_experiences(self, owner: str) -> List[Dict[str, Any]]:
        """列出某 owner 所有「已存证」的可组合经验。

        实现提示（由 Melody 提供）:
          - 从 LAO Registry / ERGE 取得 owner 的已验证经验
          - 需包含 attestation_refs（可审计溯源）
        """
        raise NotImplementedError("Melody 域: 由 Melody 实现 list_attested_experiences")

    def compose_agent_profile(
        self, owner: str, experience_ids: List[str]
    ) -> AgentProfile:
        """将指定已验证经验组合为面向 owner 的 Agent 画像。

        实现提示（由 Melody 提供）:
          - 输入 experience_ids → 取对应已验证经验 → 组合规则
          - 产出 AgentProfile（含 rules + attestation_refs）

        Raises:
            PermissionError: 未授权「④确权交易」时抛错(P1-4·Melody交易→④)。
        """
        # P1-4 集成接线: Melody 交易 → ④确权交易授权
        from lao.effect_anchored.consent_gate import FourStageConsent
        from lao.effect_anchored.consent_integration import guard_trade
        _consent = getattr(self, "_consent", None) or FourStageConsent()
        _ok, _why = guard_trade(_consent, owner)
        if not _ok and getattr(self, "_consent", None) is None:
            # 默认内部consent: trade 非默认授权 → 需用户显式
            raise PermissionError(f"[compose_agent_profile] {_why}")
        if not _ok:
            raise PermissionError(f"[compose_agent_profile] {_why}")
        raise NotImplementedError("Melody 域: 由 Melody 实现 compose_agent_profile")

    def validate_composition(self, profile: AgentProfile) -> List[str]:
        """校验组合出的画像兼容性，返回违规项列表（空=合法）。

        实现提示（由 Melody 提供）:
          - 检查规则间冲突 / 域污染 / 权限越界
          - 复用 ExperienceContract.can_apply() 做域校验
        """
        raise NotImplementedError("Melody 域: 由 Melody 实现 validate_composition")
