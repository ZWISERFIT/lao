"""语境适配器。

核心引擎（`..engine`）只吐「保留哪些工具名」；把决策落到具体宿主数据结构上的活
全部在这里，一个语境一个文件（195号 §3.1）：

    openclaw.py  —— OpenAI 兼容 payload：直接裁剪 payload["tools"] 数组（180号方案）
    dsh.py       —— Cordis/DSH 插件：产出 allow 名单交给 agent.ctx.tools.restrict()（191号实证）

两条线的指标各自带 context 字段（openclaw / dsh），基线数字不可混算：
    OpenClaw 基线 51,955 字符 / 36 工具（180号 §3）
    DSH  基线 32,829 字符 / 28 工具（191号实证）
"""

from .dsh import DSH_CONTEXT, build_allow_list, restrict_args
from .openclaw import OPENCLAW_CONTEXT, compress_payload, extract_tool_names, iter_user_text

__all__ = [
    "OPENCLAW_CONTEXT",
    "compress_payload",
    "extract_tool_names",
    "iter_user_text",
    "DSH_CONTEXT",
    "build_allow_list",
    "restrict_args",
]
