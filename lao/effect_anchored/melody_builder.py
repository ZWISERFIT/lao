"""
Melody Builder — LAO 2.7 P1-5（积木式 Agent 组合·仅接口定义·不实现）
==================================================================

五步经验复利流水线的末环「自由交易」：用户用确权经验积木自由组合 Agent。

边界（对齐 LAO = Trust Layer）:
  - LAO 提供「已验证 + 已确权」的经验积木（ExperienceAtom 语义标注）
  - Melody 负责把积木组合成 Agent 画像（个人化/交易域·不在此实现）

本文件只定义接口契约（dataclass + 方法签名），**不实现逻辑**。
实现由 Melody 提供（P1 暂缓，当前仅为架构落位）。

运行: 本模块不执行逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExperienceAtom:
    """经验积木块：一段已验证 + 已确权的最小可复用单元。

    语义:
      - content      : 经验内容(规则/原则/事实)
      - owner        : 所有方 (human/agent)
      - domain       : 所属领域(防跨域污染)
      - anchor_type  : decision / cognitive / fact
      - attestation_id: 若已存证, 该积木可审计溯源(LAO L3 确权产物)
      - confidence / trust_weight : 可信度
    """
    content: str
    owner: str
    domain: str
    anchor_type: str = "decision"          # decision | cognitive | fact
    attestation_id: Optional[str] = None   # Ethan 存证 ID(可审计)
    confidence: float = 0.5
    trust_weight: float = 0.5
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "owner": self.owner,
            "domain": self.domain,
            "anchor_type": self.anchor_type,
            "attestation_id": self.attestation_id,
            "confidence": self.confidence,
            "trust_weight": self.trust_weight,
            "tags": self.tags,
        }


@dataclass
class AgentProfile:
    """Melody 积木组合出的 Agent 画像（面向某 owner 的个性化工作描述）。"""
    owner: str
    agent_id: str = "customer_service"
    atoms: List[ExperienceAtom] = field(default_factory=list)      # 组合用的积木
    rules: List[str] = field(default_factory=list)                 # 组合出的可执行规则
    composed_at: str = field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat())


class MelodyBuilder:
    """Melody 的实现契约（不实现逻辑，由 Melody 提供）。

    职责范围（Melody 域）:
      - 列出某 owner 已确权的经验积木
      - 把积木组合成 Agent 画像
      - 校验组合兼容性

    不做（LAO 域）:
      - 不存储/校验经验真实性(LAO Kernel)
      - 不做存证(Ethan)
    """

    def list_attested_experiences(self, owner: str) -> List[Dict[str, Any]]:
        """列出某 owner 所有「已确权存证」的经验积木(ExperienceAtom 列表)。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 list_attested_experiences")

    def compose_agent_profile(self, owner: str, atom_ids: List[str]) -> AgentProfile:
        """将指定经验积木组合为面向 owner 的 Agent 画像。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 compose_agent_profile")

    def validate_composition(self, profile: AgentProfile) -> List[str]:
        """校验组合出的画像兼容性，返回违规项列表(空=合法)。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 validate_composition")
