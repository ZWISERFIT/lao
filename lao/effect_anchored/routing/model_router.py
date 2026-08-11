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

    # === 选品三级过滤：安全 > 效率 > 成本 (创始人 2026-08-11 批准) ===
    # SAFETY_GATE: 每 tier 的质量底线(quality)。低于底线 = 安全不达标 = 不能选(不可绕过)。
    #   - 任务越重, 底线越高(错配=幻觉风险)
    #   - heavy/reasoning/code 要求 pro 级(flash 被 safety 拦)
    #   - ultra_light/light/cn_explain 可用 flash
    SAFETY_GATE = {
        "ultra_light": 0.50,
        "light": 0.50,
        "medium": 0.80,
        "heavy": 0.85,
        "reasoning": 0.85,
        "code": 0.80,
        "cn_explain": 0.50,
        "cn_creative": 0.80,
    }

    def select_optimal(self, pool: list, tier: str,
                       credit_mode: str = "prefer") -> dict:
        """三级过滤选品(安全 > 效率 > 成本)。

        1. safety gate: 过滤掉质量低于 tier 底线的(不可绕过)
        2. efficiency: 同质量集内, 优先低延迟/credit模式偏好
        3. cost: 同质量同效率集内, 选成本最低

        创始人定调: 性价比最优 ≠ 成本最低, 安全是第一门禁。
        """
        if not pool:
            return {}
        gate = self.SAFETY_GATE.get(tier, 0.50)
        # ① safety gate: 质量底线(第一门禁·不可突破)
        safe = [e for e in pool if float(e.get("quality", 0)) >= gate]
        if not safe:   # 底线不可破: 全不达标时取该tier最高的(宁缺勿乱·保安全)
            safe = [max(pool, key=lambda e: float(e.get("quality", 0)))]
        # ② efficiency: 同质量集内优先低延迟 + credit_mode偏好
        if credit_mode == "avoid":
            safe = [e for e in safe if not e.get("credit", False)] or safe
        elif credit_mode == "force" and tier != "reasoning":
            cr = [e for e in safe if e.get("credit", False)]
            if cr:
                safe = cr
        eff = sorted(safe, key=lambda e: float(e.get("latency", 1.0)))
        # ③ cost: 同质量同效率集(前25%效率)内选成本最低
        best_eff = eff[:max(1, len(eff)//4 + (len(eff)%4>0))]
        # 解析成本 "$X/$Y" 取第一个(输入价)
        def cost_val(e):
            c = str(e.get("cost", "$999"))
            try:
                return float(c.replace("$", "").split("/")[0])
            except (ValueError, IndexError):
                return 999.0
        chosen = min(best_eff, key=cost_val)
        return dict(chosen)
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
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # light: 日常问答/总结/翻译 → flash
        "light": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # medium: 分析/推断 → pro 首选(更稳)
        "medium": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
        ],
        # heavy: 复杂推理/战略分析 → DeepSeek v4-pro 不可替代
        "heavy": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # reasoning: 深度推理 → DeepSeek v4-pro 唯一（无替代）
        "reasoning": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # code: 代码生成 → deepseek-v4-pro 首选 (代码专项·稳)
        "code": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # cn_explain: 中文解释/说明 → flash(省)
        "cn_explain": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
        # cn_creative: 中文创意/写作 → pro 首选(创作质量)
        "cn_creative": [
            {"model": "deepseek-v4-pro", "provider": "deepseek", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "novarouteai", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-pro", "provider": "token-plan", "credit": False, "quality": 0.92, "latency": 0.6, "cost": "$2.20/$8.80"},
        ],
    }

    def __init__(self, task_classifier=None, consent=None, consent_owner="default"):
        """初始化路由器。

        Args:
            task_classifier: 可选的自定义分类器实例。
            consent: 可选的四阶段授权门(P1-4 集成)。
            consent_owner: 授权归属 owner。
        """
        from lao.effect_anchored.routing.task_classifier import TaskClassifier

        self.classifier = task_classifier or TaskClassifier()
        self._consent = consent
        self._consent_owner = consent_owner

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

        Raises:
            PermissionError: 未授权「①成本追踪」时抛错(P1-4 集成接线·Router→①)。
        """
        # P1-4 集成接线: Router → ①成本授权
        # cost/cleanse 为 default=True(默认同意):
        #   - 默认内部consent → 首次自动授予不阻塞
        #   - 显式注入consent → 尊重其授权状态(可拒则拒)
        # upload/trade 为 default=False(需显式) → 必须显式授权才放行
        from lao.effect_anchored.consent_gate import FourStageConsent
        from lao.effect_anchored.consent_integration import guard_route
        _consent = self._consent or FourStageConsent()
        _owner = getattr(self, "_consent_owner", "default")
        _ok, _why = guard_route(_consent, _owner)
        if not _ok and self._consent is None and "成本追踪" in _why:
            # 仅默认内部consent: 自动授予 default=True 的 cost(不阻塞默认工作流)
            _consent.grant_stage("cost", _owner, "routing")
            _ok = True
        if not _ok:
            raise PermissionError(f"[route] {_why}")

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

        # 三级选品: 安全 > 效率 > 成本 (创始人 2026-08-11 批准·非固定pool[0])
        primary = self.select_optimal(pool, tier, credit_mode=credit_mode)
        # 降级链: 安全集内按效率排序其余(已过safety gate)
        gate = self.SAFETY_GATE.get(tier, 0.50)
        fallback_pool = [e for e in pool if float(e.get("quality", 0)) >= gate]
        fallbacks = [f"{e['provider']}/{e['model']}"
                     for e in fallback_pool if e != primary]

        return RouteSelection(
            task=task,
            model=primary.get("model", pool[0]["model"]),
            provider=primary.get("provider", pool[0]["provider"]),
            tier=tier,
            cost=primary.get("cost", pool[0]["cost"]),
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
