"""ExperienceClassifier — L3 三类经验自动分流器 (LAO v3.5)

三类经验：
    1. AGENT_RUNTIME       → Agent 运行经验 → 同步给 Momo 优化产品
    2. USER_PERSONAL       → 用户个人经验 → 经授权确权后上平台交易
    3. HUMAN_AGENT_COLLAB  → 人-Agent 协同经验 → 经授权确权后上平台交易

分类为确定性规则(信号字段判定)，分流路由到对应管道：
    - momo_feedback          : Momo 反馈通道(产品优化)
    - ethan_rights_pipeline  : Ethan 确权管道(授权→DID 签名→上链交易)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Union


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperienceCategory(str, Enum):
    """三类经验。"""

    AGENT_RUNTIME = "AGENT_RUNTIME"
    USER_PERSONAL = "USER_PERSONAL"
    HUMAN_AGENT_COLLAB = "HUMAN_AGENT_COLLAB"


# 分流通道
CHANNEL_MOMO_FEEDBACK = "momo_feedback"
CHANNEL_ETHAN_RIGHTS = "ethan_rights_pipeline"

# 分类信号字段
_USER_SIGNAL_KEYS = ("user_id", "user_statement", "user_preference", "personal_data")
_COLLAB_SIGNAL_KEYS = ("human_feedback", "human_input", "user_correction",
                       "agent_action", "collaboration", "human_agent")
_EXPLICIT_TYPE_VALUES = {c.value for c in ExperienceCategory}


@dataclass
class PipelineResult:
    """经验分流结果。"""

    category: ExperienceCategory
    channel: str                       # momo_feedback | ethan_rights_pipeline
    payload: Dict[str, Any] = field(default_factory=dict)
    authorization_required: bool = False  # 上平台交易前是否需用户授权确权
    routed_at: str = field(default_factory=_utcnow)
    routed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "channel": self.channel,
            "payload": self.payload,
            "authorization_required": self.authorization_required,
            "routed_at": self.routed_at,
            "routed": self.routed,
        }


class ExperienceClassifier:
    """L3 三类经验自动分流器。"""

    def classify(self, experience: Union[Dict[str, Any], Any]) -> ExperienceCategory:
        """判定经验的三类归属。

        规则(优先级从高到低，确定性)：
          1. 显式声明: experience["experience_type"] ∈ 三类枚举值 → 直接采用
          2. 人-Agent 协同信号: 同时含人类侧输入(human_*)与 Agent 侧产物
             (agent_* / source_agent)，或显式 collaboration 标记
          3. 用户个人信号: 含 user_id / user_statement / user_preference /
             personal_data 等用户数据字段
          4. 兜底: AGENT_RUNTIME(纯 Agent 运行经验)
        """
        exp = self._as_dict(experience)

        # 规则1: 显式声明
        explicit = str(exp.get("experience_type", "")).upper()
        if explicit in _EXPLICIT_TYPE_VALUES:
            return ExperienceCategory(explicit)

        # 规则2: 人-Agent 协同(人类侧信号 + Agent 侧信号同时出现)
        has_human = any(k in exp and exp.get(k) for k in
                        ("human_feedback", "human_input", "user_correction"))
        has_agent = any(k in exp and exp.get(k) for k in
                        ("agent_action", "agent_output", "source_agent"))
        if has_human and has_agent:
            return ExperienceCategory.HUMAN_AGENT_COLLAB
        if exp.get("collaboration") or exp.get("human_agent"):
            return ExperienceCategory.HUMAN_AGENT_COLLAB

        # 规则2.5: 含 error_type 且无协同信号 → AGENT_RUNTIME
        # (用户描述Agent问题 ≠ 用户个人经验)
        if any(k in exp and exp.get(k) for k in ("error_type", "severity", "root_cause")):
            return ExperienceCategory.AGENT_RUNTIME

        # 规则3: 用户个人经验(用户数据信号)
        if any(k in exp and exp.get(k) for k in _USER_SIGNAL_KEYS):
            return ExperienceCategory.USER_PERSONAL

        # 规则4: 兜底
        return ExperienceCategory.AGENT_RUNTIME

    def route_to_pipeline(
        self,
        experience: Union[Dict[str, Any], Any],
        category: Optional[ExperienceCategory] = None,
    ) -> PipelineResult:
        """按类别分流到对应管道。

        - AGENT_RUNTIME → momo_feedback(Momo 产品优化通道·无需用户授权)
        - USER_PERSONAL / HUMAN_AGENT_COLLAB → ethan_rights_pipeline
          (Ethan 确权管道·授权确权后方可上平台交易)
        """
        exp = self._as_dict(experience)
        cat = category or self.classify(exp)

        if cat is ExperienceCategory.AGENT_RUNTIME:
            channel = CHANNEL_MOMO_FEEDBACK
            auth_required = False
        else:
            channel = CHANNEL_ETHAN_RIGHTS
            auth_required = True

        payload = dict(exp)
        payload["category"] = cat.value

        return PipelineResult(
            category=cat,
            channel=channel,
            payload=payload,
            authorization_required=auth_required,
        )

    def classify_and_route(self, experience: Union[Dict[str, Any], Any]) -> PipelineResult:
        """classify + route_to_pipeline 一步到位。"""
        return self.route_to_pipeline(experience)

    @staticmethod
    def _as_dict(experience: Union[Dict[str, Any], Any]) -> Dict[str, Any]:
        """兼容 dict / dataclass / 对象(取 to_dict 或 __dict__)。"""
        if isinstance(experience, dict):
            return experience
        if hasattr(experience, "to_dict"):
            try:
                d = experience.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
        if hasattr(experience, "__dict__"):
            return dict(vars(experience))
        return {}
