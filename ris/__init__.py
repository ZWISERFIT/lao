"""
RIS — Agent Runtime Reliability Layer（运行免疫层）

ZWISERFIT AI 基础设施分层（创始人 2026-08-14 令）：
  Reliable Agent Infrastructure = LAO + RIS

  LAO（认知可靠层）：Model Selection / Anchor / Memory / Correction / Verification / Asset
  RIS（运行免疫层）：Process Health / Session Recovery / Provider Availability / Auto Recovery

本模块是 RIS 的逻辑入口——通过 re-export 现有运行层模块，建立清晰的运行免疫边界。
不删除、不物理移动任何 LAO 现有代码（方案 B：软分层 re-export）。

输出事件：RuntimeHealthEvent（运行健康事件）
  LAO 输出：AgentBehaviorEvent（行为事件）
  RIS 输出：RuntimeHealthEvent（运行事件）
"""

# ── B1. Process Health（进程健康）──
from lao.effect_anchored.runtime_registry import (  # noqa: F401
    RuntimeRegistry,
)
from lao.effect_anchored.failure_domain import (  # noqa: F401
    FailureDomainDetector,
)

# ── B2. Session Recovery（会话恢复）──
from lao.effect_anchored.recovery_budget import RecoveryBudget  # noqa: F401
from lao.effect_anchored.recovery_replay import RecoveryMemory  # noqa: F401
from lao.effect_anchored.recovery_verifier import RecoveryVerification  # noqa: F401

# ── B3. Provider Availability（Provider 可用性）──
from lao.effect_anchored.provider_health_gate import ProviderHealthGate  # noqa: F401
from lao.effect_anchored.routing_state_guard import RoutingStateGuard  # noqa: F401

# ── 第三阶段：RuntimeHealthEvent（RIS 运行健康事件）──
from ris.events import RuntimeHealthEvent, RIS_EVENT_TYPES  # noqa: F401

# ── RIS→LAO 数据桥（P0-3·成熟部署加速·Shuyu立项）──
from ris.bridge import RISToLAOBridge, sync_bridge, BRIDGE_FILE  # noqa: F401

__all__ = [
    # Process Health
    "RuntimeRegistry",
    "FailureDomainDetector",
    # Session Recovery
    "RecoveryBudget",
    "RecoveryMemory",
    "RecoveryVerification",
    # Provider Availability
    "ProviderHealthGate",
    "RoutingStateGuard",
    # Events（第三阶段）
    "RuntimeHealthEvent",
    "RIS_EVENT_TYPES",
    # RIS→LAO 数据桥（P0-3）
    "RISToLAOBridge",
    "sync_bridge",
    "BRIDGE_FILE",
]

__version__ = "1.0.0"
LAYER = "ris"  # 运行免疫层标识
