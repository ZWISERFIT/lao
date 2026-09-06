"""N1 配置与开关（180号 §六「配置开关 on/off + 一键回滚」）。

全部通过环境变量读取，出厂默认 **关闭**。关闭时核心引擎的 decide() 直接返回 noop，
宿主侧一行 if 即可完整回退到透传（180号 C5 / 191号 C5）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_domains(name: str) -> FrozenSet[str]:
    """逗号分隔的域名单；非法值交由 ToolTaxonomy 侧忽略。"""
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


@dataclass
class CompressionConfig:
    """N1 运行配置。

    Attributes:
        enabled: 总开关。出厂 False（红线 C5）。
        min_tools: 保底工具数。裁剪前工具数 ≤ 此值不裁；裁剪后 < 此值回退不裁
            （180号 §4.6 兜底 2，原文阈值 3）。
        cache_ttl_s: 决策缓存 TTL 秒（180号 §4.5，原文 5 分钟）。
        cache_max: 决策缓存 LRU 条数上限（180号 §4.5，原文 500）。
        blacklist_ttl_s: 误裁黑名单 TTL 秒（180号 §4.6 兜底 3，原文 30 分钟）。
        metrics_path: 指标 JSONL 路径。空字符串 = 不落盘。
        keep_unknown: 未在分类表中的工具是否一律保留。默认 True。
            这是对 180号 §七「工具分类与源码漂移 → 裁剪失效」风险的正面处理：
            宿主升级导致工具改名时，未知工具被保留 → 压缩率下降（可被 §4.7 告警
            阈值捕获），而不是误裁导致任务失败。fail-open 优先于压缩率。
        always_keep_domains: 无论何种意图都保留的域。出厂为空，即严格按 180号 §4.4
            伪代码行事（只留 kept_domains）。若实测发现 §4.2「通用」5 工具
            （thinking / llm_task / tokenjuice / reactions / trajectory）被裁后频触兜底 3，
            置为 {"common"} 即可转为常留，不需改分类表。
    """

    enabled: bool = False
    min_tools: int = 3
    cache_ttl_s: int = 300
    cache_max: int = 500
    blacklist_ttl_s: int = 1800
    metrics_path: str = ""
    keep_unknown: bool = True
    always_keep_domains: FrozenSet[str] = frozenset()
    # 语境标签，仅用于指标区分（openclaw / dsh / test）
    context: str = "unknown"
    # 允许宿主追加自定义域映射：{"intent": frozenset({"domain", ...})}
    extra_intent_domains: Dict[str, FrozenSet[str]] = field(default_factory=dict)


def load_config(context: str = "unknown", metrics_path: Optional[str] = None) -> CompressionConfig:
    """从环境变量装配配置。

    Args:
        context: 语境标签（openclaw / dsh），仅进指标。
        metrics_path: 显式指定指标路径；None 时读 LAO_N1_METRICS_PATH。

    Returns:
        CompressionConfig
    """
    return CompressionConfig(
        enabled=_env_flag("LAO_N1_ENABLED", "0"),
        min_tools=_env_int("LAO_N1_MIN_TOOLS", 3),
        cache_ttl_s=_env_int("LAO_N1_CACHE_TTL_S", 300),
        cache_max=_env_int("LAO_N1_CACHE_MAX", 500),
        blacklist_ttl_s=_env_int("LAO_N1_BLACKLIST_TTL_S", 1800),
        metrics_path=(
            metrics_path
            if metrics_path is not None
            else (os.environ.get("LAO_N1_METRICS_PATH", "") or "")
        ),
        keep_unknown=not _env_flag("LAO_N1_DROP_UNKNOWN", "0"),
        always_keep_domains=_env_domains("LAO_N1_ALWAYS_KEEP_DOMAINS"),
        context=context,
    )
