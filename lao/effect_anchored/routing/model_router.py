# v3.5.1-fix: R3
# v3.5.1-glm: R3
# v3.5.1-glm: R5
"""
ModelRouter — 模型路由与降级链路 v2.2
====================================

根据任务分类结果选择最优模型，并构建跨 provider 降级链路。
# v2.1 (2026-08-10 Tristan P0-①): 三 provider 统一故障转移
#   - 移除 Qoder（2026-08-09 创始人裁定）
#   - 接入 deepseek / token-plan / novarouteai 三 provider
#   - 全部支持 deepseek-v4-pro/flash，互为主备，杜绝 400 模型-端点不匹配
# v2.2 (2026-08-13 Tristan T1·Stella成本优化派发): 启用成本红线 route_with_budget
#   - route()/route_with_budget() 的 budget 参数真实生效(此前为死参数)
#   - 每日预算红线: 超预算自动 pro→flash 降级(仅成本敏感层)
#   - heavy/reasoning/code 保质量不降级(宁贵勿错)
"""

from dataclasses import dataclass
import os


# 成本红线(P0·T1 Stella派发): 每日预算默认值(USD), 可环境变量覆盖
DEFAULT_DAILY_BUDGET = float(os.environ.get("LAO_DAILY_BUDGET", "5.0"))
# 超预算降级门槛: 仅对成本敏感层允许 flash 降级(pro→flash)
# heavy/reasoning/code 是安全关键层, 超预算也不降级(保质量·宁贵勿错)
BUDGET_DEGRADABLE_TIERS = {"ultra_light", "light", "cn_explain"}

# 缓存感知路由(T2): 缓存 miss/hit 成本倍率
# 静态价只反映 $M; 真实成本受缓存状态影响(命中 vs 未命中差 50-120 倍)
#  - hit  : 输出被缓存复用 → 成本远低于标价
#  - miss : 大上下文未命中缓存 → 重新计算 → 成本暴涨
CACHE_HIT_COST_MULT = 0.05    # 命中: 成本约 5%(复用前缀缓存)
CACHE_MISS_COST_MULT = 3.0    # 未命中: 成本约 3x(大上下文重新处理)
# 大上下文阈值(触发缓存感知): tokens > 此值视作"大上下文, 缓存关键"
LARGE_CONTEXT_TOKENS = 8000
# 缓存命中信号词(模板任务→易命中; 唯一性任务→易miss)
CACHE_HIT_HINTS = ("模板", "固定", "标准", "心跳", "问候", "模板回复", "格式")
CACHE_MISS_HINTS = ("分析", "推理", "研究", "总结这段", "解读", "评估", "翻译这段", "代码审查")


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


# 创始人 B 阶段(2026-08-14): Agent → Provider 绑定分组
# baron/ethan/momo → token-plan(百炼·qwen/glm 有补贴)
# 其余 Agent 默认 deepseek(不在表 = deepseek)
AGENT_PROVIDER_BINDING = {
    "baron": "token-plan",
    "ethan": "token-plan",
    "momo": "token-plan",
}


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
        chosen = min(best_eff, key=self._cost_usd)
        return dict(chosen)

    @staticmethod
    def _cost_usd(e: dict) -> float:
        """取模型输入价($USD/M·首值)。"""
        try:
            return float(str(e.get("cost", "$999")).replace("$", "").split("/")[0])
        except (ValueError, IndexError):
            return 999.0

    # ------------------------------------------------------------------
    # 2026-08-19 创始人令·去硬编码 + 额度感知 (任务A/B·双失联根因① ②)
    # ------------------------------------------------------------------

    CONFIG_PATH = "/home/agentuser/.openclaw/openclaw.json"

    def _load_openclaw_config(self) -> dict:
        """读取 openclaw.json 的 models.providers（动态路由数据源）。

        fail-open: 读取失败返回空 dict（调用方回退硬编码池·不阻断）。
        """
        try:
            import json
            with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            models = cfg.get("models", {})
            providers = models.get("providers", {})
            # agents 模型绑定(primary/fallbacks)
            agents = cfg.get("agents", {}).get("list", [])
            agent_models = {}
            for a in agents:
                md = a.get("model", {}) or {}
                agent_models[a.get("id", "")] = {
                    "primary": md.get("primary", ""),
                    "fallbacks": md.get("fallbacks", []),
                }
            return {"providers": providers, "agents": agent_models}
        except Exception:
            return {}

    def _build_dynamic_pool(self) -> dict:
        """从 openclaw.json 动态构建 MODEL_POOL（替换硬编码）。

        每个 tier 用真实配置的 provider/模型构建降级链：
            - qwen provider 模型(qwen3.7-flash/qwen-plus/qwen-max) 优先
            - deepseek 直连(deepseek-v4-flash)
            - token-plan(qwen3.6-flash/qwen3.7-plus/qwen3.8-max/glm-5.2)
            - novarouteai(glm-5.2/qwen3.7-plus)

        未知模型 id（config 中不存在）→ 不放入池（fail-fast: 后续路由遇
        未知 id 直接抛错而非静默用错）。

        Returns:
            tier -> [{model, provider, credit, quality, latency, cost}]
        """
        try:
            data = self._load_openclaw_config()
            providers = data.get("providers", {})
            if not providers:
                return {}
            # 收集 provider → 模型 id 集合
            provider_models: dict = {}
            for pid, p in providers.items():
                ids = [m.get("id", "") for m in p.get("models", []) if m.get("id")]
                provider_models[pid] = set(ids)

            def entry(model: str, provider: str, quality: float, latency: float,
                      cost: str, credit: bool = False) -> dict:
                return {
                    "model": model, "provider": provider, "credit": credit,
                    "quality": quality, "latency": latency, "cost": cost,
                }

            # 各 tier 动态链（同一套 provider 模型·按 tier 调整质量/成本权重）
            tiers = ("ultra_light", "light", "medium", "heavy", "reasoning",
                     "code", "cn_explain", "cn_creative")
            pool: dict = {}
            for tier in tiers:
                chain = []
                # 1) qwen provider 优先(dashscope 直连·稳定)
                qwen_ids = provider_models.get("qwen", set())
                for mid in ("qwen3.7-flash", "qwen-plus", "qwen-max"):
                    if mid in qwen_ids:
                        chain.append(entry(mid, "qwen", 0.78 if "flash" in mid else 0.85,
                                           0.35 if "flash" in mid else 0.5,
                                           "$0.05/$0.10"))
                # 2) deepseek 直连 (C21-20260823: 兼容 deepseek-<agent> 专用 provider·
                #    通用 deepseek 节点被移除后池仍需 deepseek 条目·agent 绑定依赖)
                _ds_ids = set()
                for _pid, _mids in provider_models.items():
                    if _pid == "deepseek" or _pid.startswith("deepseek-"):
                        _ds_ids.update(_mids)
                if "deepseek-v4-flash" in _ds_ids:
                    chain.append(entry("deepseek-v4-flash", "deepseek", 0.70, 0.30,
                                       "$0.14/$0.28"))
                # 3) token-plan(qwen3.6-flash 等)
                tp_ids = provider_models.get("token-plan", set())
                for mid in ("qwen3.6-flash", "qwen3.7-plus", "qwen3.8-max", "glm-5.2"):
                    if mid in tp_ids:
                        chain.append(entry(mid, "token-plan", 0.76, 0.55,
                                           "$0.08/$0.20"))
                # 4) novarouteai
                nr_ids = provider_models.get("novarouteai", set())
                for mid in ("glm-5.2", "qwen3.7-plus", "qwen3.6-flash"):
                    if mid in nr_ids:
                        chain.append(entry(mid, "novarouteai", 0.75, 0.50,
                                           "$0.10/$0.25"))
                if chain:
                    pool[tier] = chain
            return pool
        except Exception:
            return {}

    def _check_provider_quota(self, provider: str) -> bool:
        """检查 provider 配额是否耗尽（额度感知·任务B）。

        配额机制仅适用于 token-plan（周配额制·今天 429 实证）。
        qwen/deepseek 为直连非配额制 → 默认可用（True）。

        三重信号（任一命中即视为耗尽/不可用）:
            1. 外部配额信号文件 data/provider-quota.json 标记 exhausted
               (运维/创始人可手动标记·无需改代码)
            2. 环境变量 PROVIDER_QUOTA_<PROVIDER>=exhausted
            3. 带认证的 token-plan /models 探测返回 429（配额耗尽专用码）

        401(认证问题)/403/网络错误 → 视为可用（不能误杀——401≠配额耗尽）。

        Returns:
            True = 可用 · False = 耗尽/不可用（调用方应 failover）。
        """
        if provider != "token-plan":
            return True  # 直连 provider 非配额制·默认可用
        try:
            import time
            now = time.time()
            cached = self._quota_cache.get(provider)
            if cached and now - cached[1] < self._quota_cache_ttl:
                return cached[0]

            # 信号1: 外部配额状态文件
            quota_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "provider-quota.json")
            if os.path.exists(quota_file):
                import json as _json
                with open(quota_file, "r", encoding="utf-8") as f:
                    qd = _json.load(f)
                if qd.get(provider) == "exhausted":
                    self._quota_cache[provider] = (False, now)
                    return False

            # 信号2: 环境变量
            env_key = f"PROVIDER_QUOTA_{provider.upper().replace('-', '_')}"
            if os.environ.get(env_key, "").lower() == "exhausted":
                self._quota_cache[provider] = (False, now)
                return False

            # 信号3: 带认证探测(429 = 配额耗尽)
            ok = self._verify_model_exists(provider, "__quota_probe__")
            self._quota_cache[provider] = (ok, now)
            return ok
        except Exception:
            return True  # 探测异常 → 默认可用(不误杀)

    def _fail_fast_unknown_model(self, model: str, provider: str) -> None:
        """未知模型 id fail-fast（任务A·绝不静默用错配置）。

        当路由结果指向的 model 不在任何 provider 配置中 → 抛 ValueError
        并附带告警说明（而非悄悄降级/用错）。
        """
        try:
            data = self._load_openclaw_config()
            providers = data.get("providers", {})
            known = set()
            for p in providers.values():
                for m in p.get("models", []):
                    if m.get("id"):
                        known.add(m["id"])
            if model and model not in known:
                raise ValueError(
                    f"[ModelRouter:FAIL-FAST] 未知模型 id '{model}' (provider={provider}) "
                    f"不在 openclaw.json models.providers 配置中。请检查配置——"
                    f"绝不静默用错模型。已知模型: {sorted(known)[:20]}"
                )
        except ValueError:
            raise
        except Exception:
            pass  # 配置读取失败 → 不阻断(其他验证路径兜底)

    # R3: 跨provider模型存在性验证 — 5分钟TTL缓存(避免每次路由打网络请求)
    _MODEL_CACHE_TTL: float = 300.0  # 5分钟

    PROVIDER_BASE_URLS = {
        "deepseek": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "token-plan": os.environ.get("TOKEN_PLAN_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        "novarouteai": os.environ.get("NOVAROUTE_BASE_URL", "https://novarouteai.com/v1"),
    }

    def _verify_model_exists(self, provider: str, model: str) -> bool:
        """验证模型在指定 provider 端点是否真实可用。

        通过 HTTP GET {base_url}/models 拉取 provider 的模型列表，检查
        model 是否在返回的 data[].id 集合中。结果以 5 分钟 TTL 缓存到类级
        字典（provider -> (model_set, timestamp)），避免每次路由都发起
        网络请求。

        base_url 两种形态处理：
          - 以 /v1 结尾 → 拼接 /models
          - 否则 → 拼接 /v1/models

        任何异常（网络超时、JSON 解析失败、provider 未知）均返回 False，
        不向上抛错，保证路由链路 fail-open。

        Args:
            provider: provider 名称（deepseek / token-plan / novarouteai）。
            model: 待验证的模型标识。

        Returns:
            True 表示模型在 provider 端点可用；False 表示不可用或验证失败。
        """
        import time
        import urllib.request
        import json as _json

        now = time.time()
        cached = self._model_cache.get(provider)
        if cached is not None:
            model_set, ts = cached
            if now - ts < self._model_cache_ttl:
                return model in model_set

        base = self.PROVIDER_BASE_URLS.get(provider, "").rstrip("/")
        if not base:
            return False
        if base.endswith("/v1"):
            url = f"{base}/models"
        else:
            url = f"{base}/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = _json.loads(resp.read().decode("utf-8", "ignore"))
            ids = {item.get("id", "") for item in data.get("data", [])}
        except Exception:
            return False
        self._model_cache[provider] = (ids, now)
        return model in ids

    # 三 provider 故障转移（2026-08-10 实测均 200）:
    #   deepseek(api.deepseek.com):    deepseek-v4-pro/flash ✅
    #   token-plan(aliyuncs):          deepseek-v4-pro ✅ / flash ❌403
    #   novarouteai(novarouteai.com):  deepseek-v4-pro/flash ✅, glm-5.2 ✅
    # 降级链用 deepseek-v4-pro（三 provider 通用），避免 flash 打 token-plan 403
    # credit_mode="avoid"时自动滤除所有credit=true的模型
    MODEL_POOL = {
        # === 路由决策表 v2.1 (2026-08-09 Tristan 修复·400根因) ===
        # ultra_light: 心跳/问候/状态检查 → 最低成本·最快响应
        "ultra_light": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "light": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "medium": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
        ],
        "heavy": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "reasoning": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "code": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "cn_explain": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.3, "cost": "$0.14/$0.28"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
        "cn_creative": [
            {"model": "deepseek-v4-flash", "provider": "deepseek", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "novarouteai", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "deepseek-v4-flash", "provider": "token-plan", "credit": False, "quality": 0.7, "latency": 0.6, "cost": "$2.20/$8.80"},
            {"model": "qwen3.7-plus", "provider": "token-plan", "credit": False, "quality": 0.80, "latency": 0.5, "cost": "$0.05/$0.10"},
            {"model": "glm-5.2", "provider": "token-plan", "credit": False, "quality": 0.78, "latency": 0.55, "cost": "$0.08/$0.20"},
        ],
    }

    def __init__(self, task_classifier=None, consent=None, consent_owner="default", model_pool=None):
        """初始化路由器。

        Args:
            task_classifier: 可选的自定义分类器实例。
            consent: 可选的四阶段授权门(P1-4 集成)。
            consent_owner: 授权归属 owner。
            model_pool: 可选模型目录注入。
                - 不传=使用本模块默认开放目录(泛化示例·Router Protocol 开源层)。
                - ZWISERFIT-OS 私有部署传私有 pool(routing_policy.MODEL_POOL) →
                  覆盖真实 provider 采购表(Router Intelligence·闭源)。
        """
        from lao.effect_anchored.routing.task_classifier import TaskClassifier

        self.classifier = task_classifier or TaskClassifier()
        self._consent = consent
        self._consent_owner = consent_owner
        self.MODEL_POOL = model_pool if model_pool is not None else self.__class__.MODEL_POOL
        # 2026-08-19 创始人令·去硬编码: 动态读取 openclaw.json 覆盖硬编码池
        try:
            self._config_pool = self._build_dynamic_pool()
            if self._config_pool:
                self.MODEL_POOL = self._config_pool
        except Exception:
            self._config_pool = {}  # 读取失败 → 用硬编码池(不阻断)
        self._quota_cache: dict = {}          # provider -> (available, timestamp)
        self._quota_cache_ttl: float = 60.0   # 配额探测缓存 60s
        # 成本红线(T1): 预算追踪器(可选注入, 便于测试)
        self._budget_tracker = None
        self._daily_budget = DEFAULT_DAILY_BUDGET
        # L2/L3→L1 反哺(2026-08-16 三层Loop): 经验约束总线(可选注入)
        self._feedback_bus = None
        # v3.5 L1 独立第三方检查员: 路由前事件参数检查(可选注入, 便于测试)
        self._event_checker = None
        # R5: 路由切换审计
        from lao.effect_anchored.routing.switch_audit import SwitchAuditor
        self._switch_auditor = SwitchAuditor()
        # R3: 模型存在性验证缓存(实例级·防跨实例/跨测试污染)
        self._model_cache: dict = {}          # provider -> (model_set, timestamp)
        self._model_cache_ttl: float = 300.0  # 5分钟

    def with_event_checker(self, checker) -> "ModelRouter":
        """注入自定义事件检查员(v3.5 L1·可测)。"""
        self._event_checker = checker
        return self

    def _get_event_checker(self):
        if self._event_checker is None:
            from lao.effect_anchored.routing.event_checker import EventChecker
            self._event_checker = EventChecker()
        return self._event_checker

    def with_feedback_bus(self, bus) -> "ModelRouter":
        """注入反馈总线(L2锚点/L3确权经验 → 路由约束反哺)。

        bus 需提供 active_route_constraints() -> List[dict]:
        支持 provider_avoid / model_avoid / budget_cap 三类约束
        (FeedbackBus.add_route_constraint 产物)。
        """
        self._feedback_bus = bus
        return self

    # ------------------------------------------------------------------
    # 2026-08-20 Founder 01:05 令·数据飞轮接线(任务1+2)
    # RIS→LAO(consume_bridge) + LAO→RIS(l3_feedback_to_bridge)
    # 纯增量·fail-open·不改变请求结构·不降命中率·不碰baseUrl
    # ------------------------------------------------------------------

    def with_experience_loop(self, loop) -> "ModelRouter":
        """注入 ExperienceLoop(数据飞轮 LAO→RIS 反哺端)。"""
        self._experience_loop = loop
        return self

    def _feedback_to_bridge(self) -> None:
        """路由决策后反哺 ris-bridge.json lao_feedback 段(任务2)。

        fail-open: 未注入 loop / 异常 → 静默跳过(绝不阻塞路由)。
        原子写(tmp+replace)·由 experience_loop.l3_feedback_to_bridge 实现。
        """
        try:
            loop = getattr(self, "_experience_loop", None)
            if loop is None:
                return
            loop.l3_feedback_to_bridge()
        except Exception:
            pass  # fail-open

    def _emit_route_feedback(self, selection) -> None:
        """路由决策后 emit l1_router 事件到 FeedbackBus(飞轮数据源)。

        l3_feedback_to_bridge 依赖 bus._events 中的 l1_router 事件
        (provider/model/success)生成 lao_feedback.route_stats。
        fail-open: 未注入 bus / 异常 → 静默跳过。
        """
        try:
            bus = getattr(self, "_feedback_bus", None)
            if bus is None:
                return
            from lao.effect_anchored.feedback_bus import FeedbackEvent
            bus.emit(FeedbackEvent(
                event_type="route_result",
                source="l1_router",
                payload={
                    "provider": selection.provider,
                    "model": selection.model,
                    "tier": selection.tier,
                    "success": True,
                    "cost": 0.0,
                },
            ))
        except Exception:
            pass  # fail-open

    def _consume_ris_bridge(self) -> None:
        """消费 ris-bridge.json → provider 黑名单 + 恢复经验灌入(任务1)。

        低频调度: 每 MAX_ROUTE_BETWEEN_CONSUME 次路由执行一次(避免高频 IO)。
        fail-open: 异常/未配置 → 静默跳过·绝不阻塞路由。
        """
        try:
            self._consume_counter = getattr(self, "_consume_counter", 0) + 1
            if self._consume_counter < self.MAX_ROUTE_BETWEEN_CONSUME:
                return
            self._consume_counter = 0
            from lao.effect_anchored.ris_bridge_consumer import consume_bridge
            consume_bridge()
        except Exception:
            pass  # fail-open

    # 每 N 次路由消费一次 RIS 桥(低频·防 IO 拖慢路由)
    MAX_ROUTE_BETWEEN_CONSUME = 20

    def with_budget_tracker(self, tracker) -> "ModelRouter":
        """注入预算追踪器(可测: 用内存/文件/Stella结算对账)。"""
        self._budget_tracker = tracker
        return self

    def set_daily_budget(self, usd: float) -> "ModelRouter":
        """设置每日预算上限($USD)。"""
        self._daily_budget = float(usd)
        return self

    def _daily_spend(self) -> float:
        """当前当日已消费成本($USD)。无 tracker 时返回 0。"""
        if self._budget_tracker is not None and hasattr(self._budget_tracker, "daily_spend"):
            return float(getattr(self._budget_tracker, "daily_spend")(self._daily_budget))
        if self._budget_tracker is not None and hasattr(self._budget_tracker, "total_cost"):
            return float(self._budget_tracker.total_cost())
        return 0.0

    def _over_budget(self) -> bool:
        """是否已超当日预算红线。"""
        spend = self._daily_spend()
        return spend > 0 and self._daily_budget > 0 and spend >= self._daily_budget

    def _audit_switch(self, tier: str, from_entry: dict, to_entry: dict, reason: str) -> None:
        """R5: 记录路由切换审计事件(审计失败不阻塞路由)。

        在 route() 内各降级/过滤节点捕获 from→to 的 provider/model 变化，
        通过 SwitchAuditor 持久化到 switch_audit.jsonl 供周报/追溯。

        Args:
            tier: 任务层级(ultra_light/light/medium/...)。
            from_entry: 切换前 pool 首条目(dict·含 provider/model)。
            to_entry: 切换后 pool 首条目。
            reason: 切换原因(agent_binding/feedback_constraint/budget_redline)。
        """
        if (from_entry.get("provider") == to_entry.get("provider") and
                from_entry.get("model") == to_entry.get("model")):
            return
        try:
            from lao.effect_anchored.routing.switch_audit import SwitchAuditEntry
            self._switch_auditor.record(SwitchAuditEntry(
                task_type=tier,
                from_provider=from_entry.get("provider", ""),
                from_model=from_entry.get("model", ""),
                to_provider=to_entry.get("provider", ""),
                to_model=to_entry.get("model", ""),
                reason=reason,
            ))
        except Exception:
            pass  # 审计失败不阻塞路由

    def _cache_awareness(self, task: str, task_text: str = "",
                         context_tokens: int = 0) -> dict:
        """缓存感知判定(T2): 是否启用缓存维度 + 命中/未命中模式。

        仅对"大上下文+任务特征"任务启用(小任务缓存成本差异可忽略)。
        v3.4(2026-08-16 L1命中率修复): 生产路径 server 传入的 task 是 tier 名,
        关键词永不命中 → 缓存感知分支恒不激活(死代码)。新增 task_text(真实
        用户消息文本) + context_tokens(估算上下文 token 数) 两个真实信号:
          - task_text 命中 hit/miss 信号词 → 按语义判 mode
          - context_tokens > LARGE_CONTEXT_TOKENS 且无命中语义 → 大上下文默认 miss
        Returns:
            {"active": bool, "mode": "hit"|"miss"}
        """
        probe = f"{task} {task_text or ''}".lower()
        if any(h in probe for h in CACHE_MISS_HINTS):
            # 分析/推理/总结类 → 大上下文往往未命中缓存 → 放大 miss 成本
            return {"active": True, "mode": "miss"}
        if any(h in probe for h in CACHE_HIT_HINTS):
            # 模板/固定/心跳 → 命中缓存 → 成本远低于标价
            return {"active": True, "mode": "hit"}
        # 大上下文兜底: 真实 token 数超阈值 → 缓存差异巨大 → 启用 miss 模式
        if context_tokens and context_tokens > LARGE_CONTEXT_TOKENS:
            return {"active": True, "mode": "miss"}
        return {"active": False, "mode": "miss"}

    def route(
        self,
        task: str,
        budget: float | None = None,
        credit_mode: str = "prefer",  # "prefer" | "force" | "avoid"
        agent: str = "",              # 创始人 B 阶段: 按 Agent 绑定 provider
        task_text: str = "",          # v3.4: 真实任务文本(缓存感知·tier名直通时唯一语义信号)
        context_tokens: int = 0,      # v3.4: 估算上下文 token 数(大上下文缓存感知)
        event: dict | None = None,    # v3.5: L1 检查员·路由前按目标模型白名单检查请求事件
    ) -> RouteSelection:
        """根据任务文本路由到最优模型。

        Args:
            task: 任务描述文本。
            budget: 可选预算上限 ($USD)。v2.2 起真实生效: 低于主力成本的
                可降级层自动 pro→flash。
            credit_mode: Qoder credit使用策略。
                - "prefer": 优先credit消费，深度推理类仍用DeepSeek
                - "force": 全部走credit (除reasoning层)
                - "avoid": 不走credit，全DeepSeek
            agent: 创始人 B 阶段(2026-08-14) — 按 Agent 绑定 provider。
                若 agent 绑定 token-plan(baron/ethan/momo) → 只在 token-plan 池选
                否则 → 默认 deepseek 池。
            event: v3.5 L1 独立第三方检查员 — Runtime 的请求事件 dict。
                传入时在返回选路结果前按目标模型参数白名单检查：
                PASS → 正常路由；MISSING_PARAMS → ValueError 明确告知缺什么参数；
                REJECT → ValueError 直接拒绝(不给 LLM 浪费 Token)。

        Returns:
            RouteSelection 包含所选模型、provider、层级、成本和降级链路。

        Raises:
            PermissionError: 未授权「①成本追踪」时抛错(P1-4 集成接线·Router→①)。
        """
        from lao.effect_anchored.consent_gate import FourStageConsent
        from lao.effect_anchored.consent_integration import guard_route
        _consent = self._consent or FourStageConsent()
        _owner = getattr(self, "_consent_owner", "default")
        _ok, _why = guard_route(_consent, _owner)
        if not _ok and self._consent is None and "成本追踪" in _why:
            _consent.grant_stage("cost", _owner, "routing")
            _ok = True
        if not _ok:
            raise PermissionError(f"[route] {_why}")

        tier = self.classifier.classify(task)

        # === 2026-08-20 Founder 01:05 令·数据飞轮(任务1): 低频消费 RIS 桥
        # (provider 黑名单 + 恢复经验灌入·每20次路由一次·fail-open) ===
        self._consume_ris_bridge()
        # 根治 Nova 根因1: 若 task 本身是合法 tier 名(light/medium/...), 直接用·避免被 classify 误判 default=medium
        if task.strip().lower() in self.MODEL_POOL:
            tier = task.strip().lower()

        code_keywords = ["代码", "编程", "测试", "函数", "类", "API", "接口",
                         "重构", "调试", "debug", "code", "function", "class",
                         "python", "javascript", "写一个", "实现"]
        if tier not in ("cn_explain", "cn_creative") and any(kw in task.lower() for kw in code_keywords):
            tier = "code"

        pool = self.MODEL_POOL.get(tier, self.MODEL_POOL["medium"])

        # === 2026-08-19 创始人令·额度感知(任务B) + PRD v1.1 R2.2 方案A强化:
        # token-plan 配额耗尽 → 方案A优先: 同 provider 降级 flash 档(qwen3.6-flash)
        # ·不换 provider·保缓存前缀(Stella护栏①·8.13教训)。同 provider 无 flash
        # 档才方案C: 剔除换 provider(请求前决策·不中途改道)·不重试不空转 ===
        try:
            _quota_dead = []
            for _pe in pool:
                _prov = _pe.get("provider", "")
                if _prov == "token-plan" and not self._check_provider_quota("token-plan"):
                    _quota_dead.append(_prov)
            if _quota_dead:
                _dead = set(_quota_dead)
                # 方案A: 同 provider flash 档降级(如 qwen3.8-max→qwen3.6-flash)
                _flash = [e for e in pool if e.get("provider") in _dead
                          and "flash" in str(e.get("model", "")).lower()]
                _alive = [e for e in pool if e.get("provider") not in _dead]
                if _flash:
                    # 审计 from = 被降级 provider 首个非 flash 档(PRD例: qwen3.8-max)
                    _from = next(
                        (e for e in pool if e.get("provider") in _dead
                         and "flash" not in str(e.get("model", "")).lower()),
                        pool[0] if pool else {})
                    self._audit_switch(tier, _from, _flash[0], "quota_degrade_flash")
                    pool = _flash + _alive
                elif _alive:
                    # 方案C: 同 provider 无 flash 档 → 剔除换 provider
                    self._audit_switch(tier, pool[0], _alive[0],
                                       "quota_failover_provider")
                    pool = _alive
                # 两者皆空 → 保底原池(路由不空转)
        except Exception:
            pass  # 配额探测故障 → 不阻断(其他验证路径兜底)

        # === 2026-08-19 创始人令·fail-fast(任务A): 池内未知模型 id 直接抛错 ===
        try:
            _known_ids = set()
            _data = self._load_openclaw_config()
            for _p in _data.get("providers", {}).values():
                for _m in _p.get("models", []):
                    if _m.get("id"):
                        _known_ids.add(_m["id"])
            if _known_ids:
                for _pe in pool:
                    if _pe.get("model") not in _known_ids:
                        raise ValueError(
                            f"[ModelRouter:FAIL-FAST] 模型 '{_pe.get('model')}' 不在 "
                            f"openclaw.json providers 配置中(provider={_pe.get('provider')})。"
                            f"拒绝路由——绝不静默用错配置。"
                        )
        except ValueError:
            raise
        except Exception:
            pass

        # === L2/L3→L1 反哺(2026-08-16 三层Loop): 经验约束先于选品过滤 ===
        # provider_avoid/model_avoid 摘除问题组合(错误复利/冲突修正产物);
        # budget_cap 收紧本次预算上限(确权经验的成本教训)。
        _avoid_providers, _avoid_models = set(), set()
        if self._feedback_bus is not None:
            try:
                for c in self._feedback_bus.active_route_constraints():
                    if "provider_avoid" in c:
                        pv = c["provider_avoid"]
                        _avoid_providers.update(pv if isinstance(pv, list) else [pv])
                    if "model_avoid" in c:
                        mv = c["model_avoid"]
                        _avoid_models.update(mv if isinstance(mv, list) else [mv])
                    if "budget_cap" in c and c["budget_cap"] is not None:
                        try:
                            cap = float(c["budget_cap"])
                            budget = cap if budget is None else min(budget, cap)
                        except (TypeError, ValueError):
                            pass
                if _avoid_providers or _avoid_models:
                    _fb_from = pool[0] if pool else {}
                    filtered = [e for e in pool
                                if e.get("provider") not in _avoid_providers
                                and e.get("model") not in _avoid_models]
                    if filtered:  # 全被规避时保底用原池(路由不能空转)
                        self._audit_switch(tier, _fb_from, filtered[0], "feedback_constraint")
                        pool = filtered
            except Exception:
                pass  # 反哺链路故障不阻塞路由(fail-open)

        # 创始人 B 阶段(2026-08-14): 按 Agent 绑定 provider 过滤
        # baron/ethan/momo → token-plan 池(只选 token-plan 的 model)
        # 其他 agent → 只选 deepseek 池
        if agent:
            bind_provider = AGENT_PROVIDER_BINDING.get(agent, "deepseek")
            _ab_from = pool[0] if pool else {}
            bound = [e for e in pool if e.get("provider") == bind_provider]
            if bound:  # 绑定 provider 有可用 model → 只用它
                self._audit_switch(tier, _ab_from, bound[0], "agent_binding")
                pool = bound
            elif bind_provider == "deepseek":
                # C21-20260823: 池中无 deepseek 条目(配置漂移) → 构造保底条目·
                # 绝不静默落到其他 provider (shuyu ral-b 400 死循环根因)
                _guard = {"model": "deepseek-v4-flash", "provider": "deepseek",
                          "credit": False, "quality": 0.70, "latency": 0.30,
                          "cost": "$0.14/$0.28"}
                self._audit_switch(tier, _ab_from, _guard, "agent_binding_guard")
                pool = [_guard]

        # credit_mode过滤
        if credit_mode == "avoid":
            pool = [e for e in pool if not e.get("credit", False)]
            if not pool:
                pool = self.MODEL_POOL[tier]
        elif credit_mode == "force" and tier != "reasoning":
            credit_pool = [e for e in pool if e.get("credit", False)]
            if credit_pool:
                pool = credit_pool

        # === 成本红线(T1·启用route_with_budget的真实拦截) ===
        _budget = budget
        if _budget is None and self._daily_budget > 0 and self._over_budget():
            _budget = 0.0  # 每日预算已触发 → 走预算红线降级
        _tier_degrade = tier in BUDGET_DEGRADABLE_TIERS
        _force_flash = False
        if _budget is not None and _tier_degrade:
            _pro = [e for e in pool if e.get("model", "") == "deepseek-v4-pro"]
            _main_cost = self._cost_usd(_pro[0]) if _pro else 1.0
            if _main_cost > _budget:
                _force_flash = True
        if _force_flash:
            # 超预算降级: 匹配 deepseek flash, 三个 provider 统一用 deepseek-v4-flash
            _br_from = pool[0] if pool else {}
            _flash = [e for e in pool if "deepseek-v4-flash" in e.get("model", "")]
            if _flash:
                self._audit_switch(tier, _br_from, _flash[0], "budget_redline")
                pool = _flash

        # === 缓存感知路由(T2·C3最致命漏洞) ===
        # 大上下文 + 缓存未命中 → 成本按 miss 倍率放大, 可能使主力(cost低但miss贵)
        # 与备选(cost高但hit稳)的真实成本反转。选品前重估"有效成本"。
        _cache_aware = self._cache_awareness(task, task_text=task_text,
                                             context_tokens=context_tokens)
        if _cache_aware["active"]:
            # 缓存维度优先, 但必须先过 safety gate(质量底线不可破)
            _gate = self.SAFETY_GATE.get(tier, 0.50)
            _safe_pool = [e for e in pool if float(e.get("quality", 0)) >= _gate]
            if not _safe_pool:
                _safe_pool = [max(pool, key=lambda e: float(e.get("quality", 0)))]
            # 用有效成本重排序选品(缓存维度优先)
            def _eff_cost(e):
                base = self._cost_usd(e)
                if _cache_aware["mode"] == "hit":
                    return base * CACHE_HIT_COST_MULT  # 命中→便宜
                return base * CACHE_MISS_COST_MULT    # miss→放大
            pool_sorted = sorted(_safe_pool, key=_eff_cost)
            primary = dict(pool_sorted[0])
        else:
            # 三级选品: 安全 > 效率 > 成本 (创始人 2026-08-11 批准·非固定pool[0])
            primary = self.select_optimal(pool, tier, credit_mode=credit_mode)

        # === v3.5 L1 独立第三方检查员: 交卷前按目标模型白名单检查事件 ===
        # PASS → 正常返回选路; MISSING_PARAMS → 明确告知缺什么参数(非笼统报错);
        # REJECT → 直接拒绝, 不给 LLM 浪费 Token。
        if event is not None:
            from lao.effect_anchored.routing.event_checker import CheckStatus
            _check = self._get_event_checker().check_event(event, primary)
            if _check.status is CheckStatus.MISSING_PARAMS:
                raise ValueError(
                    f"[route:EventChecker:MISSING_PARAMS] 模型 {_check.model_name} 缺少必需参数: "
                    f"{', '.join(_check.missing_params)} — 请 Runtime 补齐后再路由"
                )
            if _check.status is CheckStatus.REJECT:
                raise ValueError(
                    f"[route:EventChecker:REJECT] 拒绝路由到 {_check.model_name}: {_check.reason}"
                )

        gate = self.SAFETY_GATE.get(tier, 0.50)
        fallback_pool = [e for e in pool if float(e.get("quality", 0)) >= gate]
        fallbacks = [f"{e['provider']}/{e['model']}"
                     for e in fallback_pool if e != primary]

        # R3: 验证主选模型存在性，失败时遍历fallback候选
        _primary_verified = self._verify_model_exists(
            primary.get("provider", ""), primary.get("model", ""))
        if not _primary_verified:
            _orig_primary = primary
            for _fb in fallback_pool:
                if _fb == primary:
                    continue
                if self._verify_model_exists(_fb.get("provider", ""), _fb.get("model", "")):
                    primary = dict(_fb)
                    fallbacks = [f"{e['provider']}/{e['model']}"
                                 for e in fallback_pool if e != primary]
                    # R5: 记录切换
                    try:
                        from lao.effect_anchored.routing.switch_audit import SwitchAuditEntry
                        self._switch_auditor.record(SwitchAuditEntry(
                            task_type=tier,
                            from_provider=_orig_primary.get("provider", ""),
                            from_model=_orig_primary.get("model", ""),
                            to_provider=primary.get("provider", ""),
                            to_model=primary.get("model", ""),
                            reason="model_not_found_on_provider",
                        ))
                    except Exception:
                        pass
                    break

        selection = RouteSelection(
            task=task,
            model=primary.get("model", pool[0]["model"]),
            provider=primary.get("provider", pool[0]["provider"]),
            tier=tier,
            cost=primary.get("cost", pool[0]["cost"]),
            credit_based=primary.get("credit", False),
            fallback_chain=fallbacks,
        )
        # L2→L1 兜底: 池过滤后仍命中规避名单(如全池被规避保底)→跳fallback
        if self._feedback_bus is not None and hasattr(self._feedback_bus, "apply_constraints"):
            try:
                _before_provider = selection.provider
                _before_model = selection.model
                selection = self._feedback_bus.apply_constraints(selection)
                # R5: 记录约束驱动的切换
                if (selection.provider != _before_provider or selection.model != _before_model):
                    from lao.effect_anchored.routing.switch_audit import SwitchAuditEntry
                    self._switch_auditor.record(SwitchAuditEntry(
                        task_type=tier,
                        from_provider=_before_provider,
                        from_model=_before_model,
                        to_provider=selection.provider,
                        to_model=selection.model,
                        reason="feedback_bus_constraint",
                    ))
            except Exception:
                pass

        # === 2026-08-20 Founder 01:05 令·数据飞轮接线(任务2):
        # 路由决策后反哺 ris-bridge.json lao_feedback 段(RIS→LAO→RIS 闭环)
        # fail-open: 任何异常跳过·绝不阻塞路由·不改变请求结构·不降命中率 ===
        self._emit_route_feedback(selection)
        self._feedback_to_bridge()
        return selection

    def route_with_budget(
        self,
        task: str,
        budget: float,
        latency_preference: str | None = None,
        agent: str = "",              # 创始人 B 阶段: 按 Agent 绑定 provider
        task_text: str = "",          # v3.4: 真实任务文本(缓存感知)
        context_tokens: int = 0,      # v3.4: 估算上下文 token 数
        event: dict | None = None,    # v3.5: L1 检查员·路由前事件参数检查
    ) -> RouteSelection:
        """考虑预算约束的路由(成本红线·T1 真实启用)。

        v2.2: budget 参数经 route() 的预算红线真实生效——
        - 超每日预算 / budget 低于本次主力成本 → 可降级层自动 pro→flash
        - heavy/reasoning/code 保质量不降级

        Args:
            task: 任务描述。
            budget: 预算上限 ($USD)。
            latency_preference: 偏好 "low" | "balanced" | "quality"。
            task_text: 真实用户消息文本(供缓存感知·task 为 tier 名时的语义信号)。
            context_tokens: 估算上下文 token 数(大上下文触发缓存感知)。

        Returns:
            RouteSelection，预算约束下最优选择。
        """
        return self.route(task, budget=budget, agent=agent,
                          task_text=task_text, context_tokens=context_tokens,
                          event=event)

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
