"""
TaskDecomposer — 复杂指令拆解·多模型组合路由 v2.0
==================================================

增效降本四大令·线三: LAO 任务拆分引擎 (lao-task-decomposition-v2)

将一个包含多个子需求的复杂指令, 拆解为独立的子任务列表, 每个子任务
独立分类 → 独立路由 → 选择各自最优最便宜的模型. 避免"一刀切"
用高成本模型处理所有子需求.

设计: 2026-08-09 (Tristan 主笔, DRI: Shuyu+Tristan+Ethan)
实现: 2026-08-10 (Phase 1: 拆解+路由建议; 兼容 Phase 2 多模型执行)

5 阶段管道:
    TaskDecomposer(拆解) → SubTaskClassifier(规则分类)
    → PerSubTaskRouter(逐子任务选模型) → SubTaskScheduler(依赖排序)
    → ResultMerger(合并)

模型池对齐: 与 model_router.py.MODEL_POOL 一致 (8/9 修复后全 deepseek 池,
避免 qwen/kimi 打到 deepseek endpoint 的 400 问题).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubTask:
    """一个独立的原子子任务."""
    id: str                       # 子任务ID (t1, t2, ...)
    type: str                     # code|analysis|search|translate|summarize|creative|classify|extract|verify|store
    task: str                     # 具体任务描述
    order: int = 0                # 执行顺序
    depends_on: list = field(default_factory=list)  # 依赖的子任务ID列表
    # 路由结果 (Phase 1: 建议; Phase 2: 实际执行)
    tier: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    cost: Optional[str] = None
    execution_group: int = 0      # 并行组号 (拓扑排序结果)


@dataclass
class DecompositionResult:
    """拆解+分类+路由+调度 的完整结果."""
    subtasks: list = field(default_factory=list)
    execution_plan: list = field(default_factory=list)   # [[并行子任务], [并行子任务], ...]
    merged_suggestion: str = ""    # Phase 1: 可读的拆解建议
    fallback_used: bool = False    # 是否回退到单任务模式


# ---------------------------------------------------------------------------
# 子任务分类器 (规则映射·零延迟)
# ---------------------------------------------------------------------------

class SubTaskClassifier:
    """将子任务 type 映射到 MODEL_POOL tier.

    对齐 model_router.py.MODEL_POOL 的 8 个 tier:
        ultra_light / light / code / cn_explain / cn_creative / medium / heavy / reasoning
    """

    # type → tier 映射
    TYPE_TO_TIER = {
        "code": "code",
        "analysis": "medium",
        "search": "ultra_light",
        "translate": "light",
        "summarize": "light",
        "creative": "cn_creative",
        "classify": "ultra_light",
        "extract": "light",
        "verify": "light",
        "store": "ultra_light",
        "explain": "cn_explain",
        "reason": "reasoning",
        "debug": "code",
        "review": "medium",
    }

    # 任务文本关键词 → type (TaskDecomposer 拆解后粗分类用)
    KEYWORD_TO_TYPE = [
        # 代码类 (高优先级)
        ("code", ["写代码", "编写", "编程", "实现函数", "写一个函数", "写个脚本",
                  "python", "javascript", "写函数", "写类", "写接口", "写API",
                  "生成代码", "重构", "debug", "调试", "修bug", "代码"]),
        ("debug", ["报错", "错误", "异常", "bug", "失败"]),
        # 分析类
        ("analysis", ["分析", "解读", "评估", "评测", "审查", "盘点", "review",
                      "总结归纳", "全面分析"]),
        # 搜索类
        ("search", ["搜索", "查一下", "找一下", "查询", "检索", "web_search",
                    "搜一搜", "资料"]),
        # 翻译类
        ("translate", ["翻译", "translate", "译成", "翻成", "英文", "中文", "日文"]),
        # 摘要类
        ("summarize", ["摘要", "总结", "归纳", "概括", "提炼", "要点"]),
        # 创意类
        ("creative", ["写文案", "文案", "写诗", "写故事", "写一篇文章", "创意",
                      "宣传语", "口号", "标题", "剧本", "小说", "小红书", "朋友圈"]),
        # 解释类
        ("explain", ["解释", "说明", "讲解", "科普", "介绍", "为什么", "怎么回事",
                     "含义", "原理", "意思是"]),
        # 推理类
        ("reason", ["推理", "逻辑推导", "多步推理", "复杂数学", "财务模型", "估值建模",
                    "证明", "推导"]),
        # 验证类
        ("verify", ["验证", "核实", "检查", "校验", "确认", "是否"]),
        # 存储/归类
        ("store", ["记录", "保存", "存入", "存储", "归档"]),
        ("classify", ["分类", "归类", "标签", "标注", "打标签"]),
        ("extract", ["提取", "抽取", "抓取", "摘录", "解析"]),
    ]

    def classify_type(self, task: str) -> str:
        """根据任务文本粗分类到 type."""
        t = task.lower()
        for type_name, kws in self.KEYWORD_TO_TYPE:
            for kw in kws:
                if kw.lower() in t:
                    return type_name
        return "analysis"  # 默认分析类 (medium)

    def tier_for(self, type_name: str) -> str:
        """type → tier."""
        return self.TYPE_TO_TIER.get(type_name, "medium")


# ---------------------------------------------------------------------------
# 调度器 (拓扑排序·并行分组·零延迟)
# ---------------------------------------------------------------------------

class SubTaskScheduler:
    """按依赖关系排序子任务, 生成并行执行计划."""

    def sort(self, subtasks: list[SubTask]) -> list:
        """拓扑排序, 返回 execution_plan = [[group0_parallel], [group1_parallel], ...].

        Phase 1: 无依赖的所有任务同组; 有 depends_on 的排在其依赖组之后.
        """
        remaining = list(subtasks)
        plan: list[list[SubTask]] = []
        placed: set = set()

        while remaining:
            group = []
            for st in remaining:
                deps = st.depends_on
                # 依赖已全部入组 或 无语义依赖 → 本组可执行
                if not deps or all(d in placed for d in deps):
                    group.append(st)
            if not group:
                # 防环: 无法前进的依赖 → 全部并入最后一组 (保险)
                group = remaining[:]
            for st in group:
                st.execution_group = len(plan)
                placed.add(st.id)
                remaining.remove(st)
            plan.append(group)
        return plan


# ---------------------------------------------------------------------------
# 任务拆解器 (核心·Phase 1 用轻量规则 + 可选 LLM)
# ---------------------------------------------------------------------------

class TaskDecomposer:
    """将复杂指令拆解为子任务列表.

    Phase 1 用规则启发式拆解: 按连接词/分号/换行/编号 切分意图.
    后续可升级为 LLM 端 (qwen3.7-flash/$0.07 极低成本) — 见设计 docs.
    """

    # 连接词/分割符, 用于拆分多个子需求
    SPLIT_PATTERNS = [
        r"并且", r"而且", r"同时", r"然后", r"接着", r"再", r"以及",
        r"；", r"\n", r"\d+[\.、)]\s*",
    ]

    # 动作动词: 用于识别逗号分隔的多个独立动作短语
    ACTION_VERBS = [
        "分析", "翻译", "写", "写一个", "生成", "搜索", "查一查", "查一下",
        "总结", "解释", "评估", "列出", "创建", "实现", "转换为", "转成",
        "整理", "计算", "对比", "比较", "设计", "建议", "给出", "列举",
        "复盘", "审查", "提炼", "导出", "生成一个", "写一段", "编写",
    ]

    def __init__(self, use_llm: bool = False):
        self.classifier = SubTaskClassifier()
        self.scheduler = SubTaskScheduler()
        self.use_llm = use_llm

    def _split(self, message: str) -> list[str]:
        """启发式切分. 合并空段.

        优先显式连接词切分; 未拆出多段时, 尝试"逗号+动作动词"识别
        多个并列子需求 (如"分析财报, 翻译成英文, 写建议").
        """
        segs = [message]
        for pat in self.SPLIT_PATTERNS:
            new_segs = []
            for s in segs:
                parts = re.split(pat, s)
                new_segs.extend([p.strip() for p in parts if p.strip()])
            segs = new_segs
            if len(segs) > 6:  # 上限, 防止过度拆分
                break
        # 仍为单段 → 尝试逗号/顿号 + 动作动词识别
        if len(segs) <= 1:
            phrase_parts = self._split_action_phrases(message)
            if len(phrase_parts) > 1:
                segs = phrase_parts
        return segs

    def _split_action_phrases(self, text: str) -> list[str]:
        """按逗号/顿号将文本拆为多段, 各段以动作动词开头视为独立子任务."""
        parts = re.split(r"[,，、]", text)
        cleaned = [p.strip() for p in parts if p.strip()]
        # 仅当至少2段且每段都含动作动词时才认定多任务
        if len(cleaned) >= 2 and all(
            any(v in p for v in self.ACTION_VERBS) for p in cleaned
        ):
            return cleaned
        return [text]

    def _classify_seg(self, task: str) -> SubTask:
        type_name = self.classifier.classify_type(task)
        return SubTask(
            id="", type=type_name, task=task,
            tier=self.classifier.tier_for(type_name),
        )

    # --- LLM 端骨架 (Phase 2 启用) ---
    PROMPT_TEMPLATE = (
        "你是一个任务拆解器。将用户指令拆解为独立的子任务列表。规则:\n"
        "1. 识别所有独立的需求\n"
        "2. 每个子任务应是可以单独完成的原子操作\n"
        "3. 不要合并不同类型的需求\n"
        "4. 保持子任务之间的依赖关系\n"
        "输出 JSON 数组: [{\"type\": \"code|analysis|...\", \"task\": \"具体描述\", "
        "\"order\": 0, \"depends_on\": []}]"
    )

    def decompose(self, message: str, agent_id: str = "generic") -> DecompositionResult:
        """主入口: 拆解 + 分类 → 结果."""
        if self.use_llm:
            # Phase 2 预留: 调用轻量 LLM (qwen3.7-flash) 拆解
            return self._decompose_llm(message)

        segs = self._split(message)
        if len(segs) <= 1:
            # 单个需求 → 直接单任务
            st = self._classify_seg(message)
            st.id = "t1"
            return DecompositionResult(
                subtasks=[st],
                execution_plan=[[st]],
                merged_suggestion=f"单任务模式: [{st.type}] {message} → tier={st.tier}",
                fallback_used=True,
            )

        subtasks = []
        for i, seg in enumerate(segs):
            st = self._classify_seg(seg)
            st.id = f"t{i+1}"
            st.order = i
            subtasks.append(st)

        plan = self.scheduler.sort(subtasks)
        # Phase 1 建议文本
        lines = [f"拆解为 {len(subtasks)} 个子任务:"]
        for g in plan:
            for st in g:
                lines.append(
                    f"  · {st.id} [{st.type}] {st.task} → 建议 tier={st.tier}"
                )
        return DecompositionResult(
            subtasks=subtasks,
            execution_plan=plan,
            merged_suggestion="\n".join(lines),
            fallback_used=False,
        )

    def _decompose_llm(self, message: str) -> DecompositionResult:
        """Phase 2 预留: 调用轻量 LLM. 当前返回降级单任务."""
        st = self._classify_seg(message)
        st.id = "t1"
        return DecompositionResult(
            subtasks=[st], execution_plan=[[st]],
            merged_suggestion=f"[LLM模式未启用·降级] [{st.type}] {message}",
            fallback_used=True,
        )


# ---------------------------------------------------------------------------
# 结果合并器 (Phase 2 启用·当前为骨架)
# ---------------------------------------------------------------------------

class ResultMerger:
    """合并所有子任务结果. Phase 1: 生成可读建议; Phase 2: 拼接实际执行输出."""

    def merge_suggestion(self, result: DecompositionResult) -> str:
        return result.merged_suggestion

    def merge_outputs(self, outputs: dict[str, str], subtasks: list[SubTask]) -> str:
        """Phase 2: 按 order 拼接实际执行结果."""
        ordered = sorted(subtasks, key=lambda s: s.order)
        parts = [outputs.get(s.id, "") for s in ordered]
        return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 门面 API
# ---------------------------------------------------------------------------

class TaskRoutingDecomposer:
    """对外统一入口: LAO 拆解 + 逐子任务路由建议.

    Phase 1 完整可用: decompose() 返回子任务计划 + 每子任务的模型建议.
    Phase 2 可扩展: 接入实际多模型执行.
    """

    def __init__(self, use_llm: bool = False):
        self.decomposer = TaskDecomposer(use_llm=use_llm)
        self.merger = ResultMerger()
        # 延迟导入避免循环
        from lao.effect_anchored.routing.model_router import ModelRouter
        self.router = ModelRouter()

    def route_decomposed(self, message: str, agent_id: str = "generic",
                         credit_mode: str = "prefer") -> dict[str, Any]:
        """拆解 → 每子任务路由建议 → 调度计划. 返回完整可审计 JSON."""
        result = self.decomposer.decompose(message, agent_id)

        # 逐子任务路由 (复用 ModelRouter)
        for st in result.subtasks:
            sel = self.router.route(st.task, credit_mode=credit_mode)
            st.tier = sel.tier
            st.provider = sel.provider
            st.model = sel.model
            st.cost = sel.cost

        plan = self.decomposer.scheduler.sort(result.subtasks)
        result.execution_plan = plan
        result.merged_suggestion = self.merger.merge_suggestion(result)

        return {
            "decomposed": True,
            "fallback_used": result.fallback_used,
            "subtask_count": len(result.subtasks),
            "subtasks": [asdict(s) for s in result.subtasks],
            "execution_plan": [[s.id for s in g] for g in result.execution_plan],
            "merged_suggestion": result.merged_suggestion,
        }

    def suggest_only(self, message: str) -> str:
        """Phase 1 精简入口: 只返回拆解+路由建议文本 (不改执行)."""
        result = self.decomposer.decompose(message)
        return result.merged_suggestion


# 便捷单例
_default: Optional[TaskRoutingDecomposer] = None


def get_decomposer(use_llm: bool = False) -> TaskRoutingDecomposer:
    global _default
    if _default is None:
        _default = TaskRoutingDecomposer(use_llm=use_llm)
    return _default
