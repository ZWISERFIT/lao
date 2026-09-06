"""N1 认知压缩核心引擎（195号施工令 / 180号规格 / 191号实证）。

跨语境复用设计（195号 §3.1）：
    核心引擎只做「决策」——吃工具名列表 + 用户文本，吐出「保留哪些工具名」，
    不修改任何宿主数据结构。语境差异全部落在 adapters/：
      - OpenClaw 适配器：直接裁剪 payload["tools"] 数组（180号方案）
      - DSH 适配器：把保留名单交给 agent.ctx.tools.restrict({allow})（191号实证）

红线（180号 §二 + 191号 §六）：
    C3/C1  不碰 user message、不碰 messages 结构——本引擎只看不改
    C5     默认关闭（LAO_N1_ENABLED 出厂为 0），一键回滚
"""

from .config import CompressionConfig, load_config
from .engine import CognitiveCompressor, CompressionDecision
from .intent import ToolIntentClassifier
from .taxonomy import DOMAINS, INTENT_DOMAIN_MAP, ToolTaxonomy

__all__ = [
    "CompressionConfig",
    "load_config",
    "CognitiveCompressor",
    "CompressionDecision",
    "ToolIntentClassifier",
    "ToolTaxonomy",
    "DOMAINS",
    "INTENT_DOMAIN_MAP",
]

__version__ = "0.1.0"
