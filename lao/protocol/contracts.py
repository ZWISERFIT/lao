"""
LAO Protocol · 协议契约（稳定开放层, P0①）
============================================
本模块定义 6 大稳定协议契约。接入方依赖本层, 不依赖实现。
实现(open/)可迭代, 契约(protocol/)保持稳定版本化。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 协议版本(三分离后首个稳定契约版)
PROTOCOL_VERSION = "3.2.0"


@dataclass
class RoutingProtocol:
    """L1 路由契约: 选模型/Provider 的稳定接口"""
    # 选品策略: safety→efficiency→cost 三级(不暴露采购价=private)
    policy_hint: str = ""  # "safety" | "efficiency" | "cost" | ""
    def suggest(self, task: Dict[str, Any]) -> List[str]:
        """返回候选 flag(不含价格·价格在 private Policy)"""
        return ["routing.ok"]


@dataclass
class ExperienceProtocol:
    """L2 经验工厂契约: 认知/经验/约束的稳定接口"""
    def ingest(self, observation: Dict[str, Any]) -> str:
        """沉淀观察→经验, 返回 atom_id"""
        return ""


@dataclass
class AttestationProtocol:
    """L3 确权契约: Experience Ownership & Attestation (Ethan 协同)"""
    def attest(self, owner: str, experience_hash: str, sig: str) -> str:
        """确权存证, 返回 attestation_id"""
        return ""


@dataclass
class ConsentProtocol:
    """授权契约: 开源/确权/交易 逐项授权门 (FourStageConsent)"""
    def is_granted(self, owner: str, stage: str) -> bool:
        """查询某授权阶段是否已授予 (cost/cleanse/upload/trade)"""
        return False


@dataclass
class TrustEventProtocol:
    """信任事件契约: 结果账本(append-only·dedup·hash·verify)"""
    def emit(self, event: Dict[str, Any]) -> str:
        """写入一条信任事件, 返回 event_id"""
        return ""


@dataclass
class PolicyProtocol:
    """策略契约: policy.get() 抽象(不直接读私有文件)"""
    def get(self, key: str, default: Any = None) -> Any:
        """按 key 取策略值 (cognitive.retrieval_weights / routing.provider_priority / ...)"""
        return default


# 暴露统一的协议入口(供 LAO 内部/外部接入方对齐)
ALL_PROTOCOLS = [
    "routing", "experience", "attestation", "consent", "trust_event", "policy",
]
