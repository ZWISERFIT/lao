"""工具语义分类表（180号 §4.2「36 工具 → 8 域」）。

分类策略（三层，按序命中）：
    ① 显式名单 EXPLICIT_DOMAINS —— 逐工具核对源码得到的确定映射
    ② 正则模式 DOMAIN_PATTERNS —— 覆盖同族命名（如 *_search / browser_*）
    ③ 兜底 "unknown" —— 未知工具默认保留（见 CompressionConfig.keep_unknown）

为什么要第 ②③ 层（这是对 180号 §七风险的正面处理）：
    180号 §4.2 要求「逐工具核对源码，写入 tool_taxonomy.json 静态配置」。但 S1 现场
    实测发现宿主工具文件名本身就在漂移：180号写的 `agent-tools-DkIWbsdu.js` 在
    2026-09-06 的安装里已不存在，实际为 `agent-tools-BcItJjg1.js`（openclaw 2026.6.5），
    且当时正在升级到 2026.9.1。若把正确性绑死在静态全量清单上，宿主一次升级就会让
    裁剪失效或误裁。因此：静态清单只决定「压缩率」，不决定「正确性」——认不出的工具
    一律保留。

两套语境的工具名都收录（195号 §3.1 跨语境复用）：
    - OpenClaw：名字取自 180号 §4.2 + 运行时枚举
    - DSH headless：名字取自 191号实证（`p14_report.txt` 段[1] 的 toolNames 与
      `p13_report.txt` L39 框架回吐的 known global tools）
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

#: 八域 + 通用域 + unknown 兜底域（180号 §4.2 表共 9 行：8 域 + 「通用」5 工具）
#: "common" 对应 §4.2 的「通用」行。按 §4.4 伪代码它不属于任何 intent 的 kept_domains，
#: 因此在非 general 意图下一律被裁——这与 §4.4 效果表自洽（chat 保留域数 1 / 3-5 工具，
#: 即只留 meta 的 4 个，不含通用 5 个）。若实测发现 thinking/llm_task 被误裁触发兜底 3，
#: 用 CompressionConfig.always_keep_domains={"common"} 一个开关即可转为常留，无需改码。
DOMAINS: Tuple[str, ...] = (
    "code",
    "search",
    "browser",
    "fetch",
    "file",
    "media",
    "comm",
    "meta",
    "common",
    "unknown",
)

#: 显式映射：工具名（小写）→ 域
EXPLICIT_DOMAINS: Dict[str, str] = {
    # ── code ──────────────────────────────────────────────────────────
    "exec": "code",
    "bash": "code",
    "shell": "code",
    "apply_patch": "code",
    "diffs": "code",
    "code_execution": "code",
    "python": "code",
    # ── search（含仓内检索）────────────────────────────────────────────
    "brave_search": "search",
    "duckduckgo": "search",
    "exa": "search",
    "perplexity": "search",
    "tavily": "search",
    "searxng": "search",
    "tool_search": "search",
    "web_search": "search",
    "grep": "search",
    "glob": "search",
    # ── browser ───────────────────────────────────────────────────────
    "browser_control": "browser",
    "browser_login": "browser",
    "browser_linux_troubleshooting": "browser",
    # ── fetch ─────────────────────────────────────────────────────────
    "web_fetch": "fetch",
    "firecrawl": "fetch",
    "web": "fetch",
    # ── file ──────────────────────────────────────────────────────────
    "read": "file",
    "write": "file",
    "edit": "file",
    "pdf": "file",
    "image_read": "file",
    "file_ops": "file",
    # ── media ─────────────────────────────────────────────────────────
    "image_generation": "media",
    "video_generation": "media",
    "music_generation": "media",
    "tts": "media",
    # ── comm ──────────────────────────────────────────────────────────
    "agent_send": "comm",
    "acp_agents": "comm",
    "subagents": "comm",
    "subagent": "comm",
    # ── meta（180号 §4.2 meta 行 + 同族的任务/编排类）────────────────────
    "skills": "meta",
    "slash_commands": "meta",
    "steer": "meta",
    "loop_detection": "meta",
    "heartbeat": "meta",
    "jobs": "meta",
    "goal": "meta",
    "create_goal": "meta",
    "workflow": "meta",
    "ralph": "meta",
    "todo": "meta",
    # ── common（180号 §4.2「通用」行，原文 5 个）──────────────────────────
    "llm_task": "common",
    "thinking": "common",
    "tokenjuice": "common",
    "reactions": "common",
    "trajectory": "common",
    # ── DSH headless 实测 28 工具中，正则会误判或判不出的，逐个显式钉死 ────────
    # 证据：probe/evidence/baseline.jsonl 的 toolNames（toolCount=28）。
    # 命名与 OpenClaw 不同族，正则兜底在这批名字上出过 6 处偏差，故显式优先：
    "read_image": "file",           # 正则会按 *_image 判成 media，实为读图
    "str_replace_editor": "file",   # "editor" 不匹配 edit 词元，原判 unknown
    "todo_write": "meta",           # 正则会按 *_write 判成 file，实为待办清单
    "subagent_fork": "comm",        # "subagent" 前无下划线，原判 unknown
    "list_agents": "comm",          # "agents" 带复数，原判 unknown
    "interrupt_agent": "comm",
    "exit_plan_mode": "meta",
    "job_kill": "meta",
    "job_list": "meta",
    "job_output": "meta",
    "get_goal": "meta",
    "update_goal": "meta",
    "skill": "meta",
    "send_message": "comm",
    # 注：lao_probe_echo / lao_probe_blocked 是 191号探针插件的临时工具，不入产线
    # 分类表；它们会落到 unknown 并被 keep_unknown 保留，这正是期望行为。
}

#: 正则模式兜底：按序第一个命中者生效
DOMAIN_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"(^|_)browser(_|$)"), "browser"),
    (re.compile(r"(^|_)search(_|$)|(^|_)find(_|$)"), "search"),
    (re.compile(r"(^|_)fetch(_|$)|(^|_)crawl|(^|_)scrape"), "fetch"),
    (re.compile(r"(^|_)(image|video|music|audio|speech|tts)(_|$)"), "media"),
    (re.compile(r"(^|_)(read|write|file|dir|ls|cat)(_|$)"), "file"),
    (re.compile(r"(^|_)(exec|run|patch|diff|compile|lint|test)(_|$)"), "code"),
    (re.compile(r"(^|_)(agent|send|notify|message|acp)(_|$)"), "comm"),
    (re.compile(r"(^|_)(skill|command|memory|plan|task|goal|job)(s?)(_|$)"), "meta"),
)

#: 意图 → 保留域（180号 §4.3 表，逐行照抄）
#: browse 只留 browser：§4.3 表写的是 browser，§4.4 效果表也写「保留域数 1 / 3 工具」，
#: 加进 fetch 会与规格自带的验收数字矛盾，故不擅自扩域。
INTENT_DOMAIN_MAP: Dict[str, FrozenSet[str]] = {
    "chat": frozenset({"meta"}),
    "code": frozenset({"code", "file"}),
    "search": frozenset({"search"}),
    "browse": frozenset({"browser"}),
    "fetch": frozenset({"fetch"}),
    "media": frozenset({"media"}),
    "comm": frozenset({"comm"}),
    # general 不在表内：命中 general 即 noop（180号 §4.4 兜底）
}


class ToolTaxonomy:
    """工具 → 域 的判定器。

    Args:
        overrides: 额外的显式映射（工具名 → 域），用于宿主侧补充或纠偏。
            键会被小写化；值必须属于 DOMAINS，否则忽略。
    """

    def __init__(self, overrides: Optional[Dict[str, str]] = None) -> None:
        self._explicit: Dict[str, str] = dict(EXPLICIT_DOMAINS)
        if overrides:
            for name, domain in overrides.items():
                if domain in DOMAINS:
                    self._explicit[str(name).strip().lower()] = domain

    def domain_of(self, tool_name: str) -> str:
        """判定单个工具所属域。

        Returns:
            DOMAINS 中的一个；认不出时返回 "unknown"。
        """
        name = str(tool_name or "").strip().lower()
        if not name:
            return "unknown"
        hit = self._explicit.get(name)
        if hit:
            return hit
        for pattern, domain in DOMAIN_PATTERNS:
            if pattern.search(name):
                return domain
        return "unknown"

    def classify_all(self, tool_names: Iterable[str]) -> Dict[str, str]:
        """批量判定，返回 {工具名: 域}（保留原始大小写作为键）。"""
        return {str(n): self.domain_of(n) for n in tool_names}

    def coverage(self, tool_names: Iterable[str]) -> Tuple[int, int, List[str]]:
        """分类覆盖度自检。

        Returns:
            (已识别数, 总数, 未识别工具名列表)——供 S1 核对表与漂移告警使用。
        """
        unknown: List[str] = []
        total = 0
        for n in tool_names:
            total += 1
            if self.domain_of(n) == "unknown":
                unknown.append(str(n))
        return total - len(unknown), total, unknown
