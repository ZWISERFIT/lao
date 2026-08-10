"""
ModelRouter — 模型路由与降级链路 v2.1
====================================

根据任务分类结果选择最优模型，并构建跨 provider 降级链路。
# v2.1 (2026-08-10 Tristan P0-①): 三 provider 统一故障转移
#   - 移除 Qoder（2026-08-09 创始人裁定）
#   - 接入 deepseek / token-plan / novarouteai 三 provider
#   - 全部支持 deepseek-v4-pro/flash，互为主备，杜绝 400 模型-端点不匹配
"""

from dataclasses import dataclass


@dataclass
class RouteSelection:
    """单次路由决策结果"""

    task: str
    model: str
    provider: str  # "deepseek" | "token-plan" | "novarouteai"
    tier: str
    cost: str  # "$/M tokens"
    credit_based: bool  # True = 用套餐Credit，不走DeepSeek余额
    fallback_chain: list


class ModelRouter:
    """根据任务难度层级路由到最合适的模型。

    每个层级定义了优先级降序的候选模型池，
    主模型为 pool[0]，其余为降级链路。

    路由策略：
    - 三 provider 故障转移（deepseek / token-plan / novarouteai）
    - 首选 deepseek-v4-pro（最稳），降级链跨 provider 用同型号（三 provider 均 200）
    - 每个 tier 保证降级链内每一环都在该 provider 端点真实可用（防 400）
    """

    # === 路由决策权重（创始人铁律 2026-08-04）===
    # 安全 > 效率 > 成本
    # - 安全(depth)：模型能力层级匹配任务复杂度，错配=幻觉风险
    # - 效率(speed)：credit消费模型优先（但不牺牲安全层级）
    # - 成本(cost)：同安全+效率层内，低成本优先
    #
    # 池内从左到右=优先级递减
    # 三 provider 故障转移（2026-08-10 实测均 200）:
    #   deepseek(api.deepseek.com):    deepseek-v4-pro/flash ✅
    #   token-plan(aliyuncs):          deepseek-v4-pro ✅ / flash ❌403
    #   novarouteai(novarouteai.com):  deepseek-v4-pro/flash ✅, glm-5.2 ✅
    # 降级链用 deepseek-v4-pro（三 provider 通用），避免 flash 打 token-plan 403
    # credit_mode="avoid"时自动滤除所有credit=true的模型
    MODEL_POOL = {
        # === 路由决策表 v2.1 (2026-08-09 Tristan 修复·400根因) ===
        # 修复背景: Momo Hermes 对话框 HTTP 400。根因 = MODEL_POOL 首选大量为
        #   provider=qwen/novarouteai 的模型(qwen3.7-flash/qwen3.7-plus/kimi-k2.7-code)，
        #   但 Hermes 运行时仅注册 deepseek provider(api.deepseek.com/v1)。
        #   经 route_for_hermes 路由命中这些层级 → 模型名打到 deepseek endpoint → HTTP 400。
        #   实测: qwen3.7-flash/qwen3.7-plus/kimi-k2.7-code 全 400；deepseek 仅认
        #   deepseek-v4-pro / deepseek-v4-flash / deepseek-reasoner。
        # 修复: 所有 tier 首选/降级链统一为 deepseek 可用模型 + deepseek provider。
        #   轻量→flash(省) · 分析/代码/重推理→pro(稳) · 全部 deepseek 直连可用。

        # ultra_light: 心跳/问候/状态检查 → 最低成本·最快响应
        "ultra_light": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # light: 日常问答/总结/翻译 → flash
        "light": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # medium: 分析/推断 → pro 首选(更稳)
        "medium": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "cost": "$0.14/$0.28"},
        ],
        # heavy: 复杂推理/战略分析 → DeepSeek v4-pro 不可替代
        "heavy": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # reasoning: 深度推理 → DeepSeek v4-pro 唯一（无替代）
        "reasoning": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # code: 代码生成 → deepseek-v4-pro 首选 (代码专项·稳)
        "code": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # cn_explain: 中文解释/说明 → flash(省)
        "cn_explain": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
        # cn_creative: 中文创意/写作 → pro 首选(创作质量)
        "cn_creative": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "cost": "$2.20/$8.80"},
        ],
    }

    def __init__(self, task_classifier=None):
        """初始化路由器。

        Args:
            task_classifier: 可选的自定义分类器实例。
        """
        from lao.effect_anchored.routing.task_classifier import TaskClassifier

        self.classifier = task_classifier or TaskClassifier()

    def route(
        self,
        task: str,
        budget: float | None = None,
        credit_mode: str = "prefer",  # "prefer" | "force" | "avoid"
    ) -> RouteSelection:
        """根据任务文本路由到最优模型。

        Args:
            task: 任务描述文本。
            budget: 可选预算上限 ($USD)。
            credit_mode: Qoder credit使用策略。
                - "prefer": 优先credit消费，深度推理类仍用DeepSeek
                - "force": 全部走credit (除reasoning层)
                - "avoid": 不走credit，全DeepSeek

        Returns:
            RouteSelection 包含所选模型、provider、层级、成本和降级链路。
        """
        tier = self.classifier.classify(task)

        # 代码生成类任务特殊处理
        # 2026-08-08 Shuyu裁定: cn_explain/cn_creative 已由classifier识别 → 不再被code_keywords覆盖
        # （否则"解释API接口"含API会被误判为code，违背中文说明→Qwen的成本裁定）
        code_keywords = ["代码", "编程", "测试", "函数", "类", "API", "接口",
                         "重构", "调试", "debug", "code", "function", "class",
                         "python", "javascript", "写一个", "实现"]
        if tier not in ("cn_explain", "cn_creative") and any(kw in task.lower() for kw in code_keywords):
            tier = "code"

        pool = self.MODEL_POOL.get(tier, self.MODEL_POOL["medium"])

        # credit_mode过滤
        if credit_mode == "avoid":
            pool = [e for e in pool if not e.get("credit", False)]
            if not pool:
                pool = self.MODEL_POOL[tier]
        elif credit_mode == "force" and tier != "reasoning":
            # 强制credit但reasoning层无credit可用
            credit_pool = [e for e in pool if e.get("credit", False)]
            if credit_pool:
                pool = credit_pool

        primary = pool[0]
        fallbacks = [f"{e['provider']}/{e['model']}" for e in pool[1:]]

        return RouteSelection(
            task=task,
            model=primary["model"],
            provider=primary["provider"],
            tier=tier,
            cost=primary["cost"],
            credit_based=primary.get("credit", False),
            fallback_chain=fallbacks,
        )


    def route_with_budget(
        self,
        task: str,
        budget: float,
        latency_preference: str | None = None,
    ) -> RouteSelection:
        """考虑预算约束的路由。

        Args:
            task: 任务描述。
            budget: 预算上限 ($USD)。
            latency_preference: 偏好 "low" | "balanced" | "quality"。

        Returns:
            RouteSelection，预算约束下最优选择。
        """
        # 预算极低 → 强制走ultra_light
        if budget < 0.001:
            pool = self.MODEL_POOL["ultra_light"]
            primary = pool[0]

            if primary.get("model", "") not in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner"):
                primary["model"] = "deepseek-v4-flash"

            return RouteSelection(
                task=task,
                model=primary["model"],
                provider=primary["provider"],
                tier="ultra_light",
                cost=primary["cost"],
                credit_based=primary.get("credit", False),
                fallback_chain=[],
            )

        # 预算低 → 强制走light层
        if budget < 0.01:
            pool = self.MODEL_POOL["light"]
            primary = pool[0]

            if primary.get("model", "") not in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner"):
                primary["model"] = "deepseek-v4-flash"

            return RouteSelection(
                task=task,
                model=primary["model"],
                provider=primary["provider"],
                tier="light",
                cost=primary["cost"],
                credit_based=primary.get("credit", False),
                fallback_chain=[],
            )

        return self.route(task, budget=budget)

    def explain_route(self, selection: RouteSelection) -> str:
        """生成人类可读的路由解释。

        Args:
            selection: RouteSelection。

        Returns:
            路由决策解释字符串。
        """
        credit_note = "🟢 CREDIT消费" if selection.credit_based else "💰 DeepSeek余额"
        return (
            f"[{selection.tier}] {selection.task[:40]}... "
            f"→ {selection.provider}/{selection.model} "
            f"({selection.cost}) {credit_note}"
            + (f" | fallback: {' > '.join(selection.fallback_chain)}"
               if selection.fallback_chain else "")
        )
