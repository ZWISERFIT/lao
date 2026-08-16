#!/usr/bin/env python3
"""
lao-router — LAO 成本优化 OpenAI 兼容代理 (方案A·9Agent共用)
=============================================================================
创始人最高指令(2026-08-13): LAO 修复 → ChatGPT审核 → 【审核通过立即真实接入运行】
真实运行+真实数据 = 给全球 Agent 用户的证据链。

架构:
    OpenClaw Agent (provider.baseUrl → http://127.0.0.1:8765/v1)
            │  POST /v1/chat/completions (OpenAI兼容)
            ▼
    lao-router (FastAPI)
        │  ① 任务分层(tier) ② ModelRouter.route_with_budget(成本红线·pro→flash降级)
        │  ③ 选择 provider/model ④ 转发真实 DeepSeek ⑤ 成本+路由决策日志(证据链)
            ▼
    https://api.deepseek.com (真实执行·route() 保证端点可用·防400)

证据链:
    - 每次请求记录: tier / 选择model / 预算 / 是否降级 / token用量 / 成本
    - 输出到 logs/lao-router-events.jsonl (接入前后成本对比·路径A最强铁证)

边界:
    - 只做路由+成本, 不碰 Agent 组织调度/董事会/公司资源(LAO L1边界)
    - 成本策略(权重/阈值)= 闭源 Private Policy; 本服务可执行可审计
"""
from __future__ import annotations
import asyncio, json, os, time, logging, threading, uuid
from datetime import datetime as _dt, timezone as _tzone, timedelta as _tdelta
from collections import deque
from typing import Optional, Dict, Any, List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openai import OpenAI

# LAO 路由核心(已实现·T1成本红线真实生效)
import sys
sys.path.insert(0, "/home/agentuser/lao-release")
from lao.effect_anchored.routing.model_router import ModelRouter, RouteSelection
from lao.effect_anchored.routing.cost_intelligence import SavingsEngine
# B2(2026-08-16 RIS审计修复): RIS 健康门——LAO 真正消费 ris-bridge/ris_summary,
# provider 被 RIS 判定 down/isolated 时阻断并降级切换(成本事故链路从"注释"变"阻断")
from lao.effect_anchored.routing.ris_health_gate import RISHealthGate

# ── 配置 ─────────────────────────────────────────────
PORT = int(os.environ.get("LAO_ROUTER_PORT", "8765"))
LOG_DIR = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs"
os.makedirs(LOG_DIR, exist_ok=True)
EVENT_LOG = os.path.join(LOG_DIR, "lao-router-events.jsonl")

# DeepSeek 真实端点
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# key 来源: 环境变量(OpenClaw secrets 注入) 或 secrets.env
def _get_deepseek_key() -> str:
    for var in ("OC_DEEPSEEK_TRISTAN_API_KEY", "OC_DEEPSEEK_API_KEY"):
        v = os.environ.get(var) or os.environ.get(var)
        if v and not v.startswith("placeholder"):
            return v
    # 兜底: 读 secrets.env
    try:
        for line in open("/home/agentuser/.openclaw/secrets.env"):
            line = line.strip()
            if line.startswith("OC_DEEPSEEK_TRISTAN_API_KEY=") or line.startswith("OC_DEEPSEEK_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v and not v.startswith("placeholder"):
                    return v
    except Exception as e:
        logging.warning(f"读secrets.env失败: {e}")
    return ""

DEEPSEEK_KEY = _get_deepseek_key()

# ── 多 Provider 转发配置(任务自动配对 LLM 的核心) ──
# 之前断点: 决策层(model_router)能选 provider, 但转发层硬编码 deepseek。
# 现在: 按 chosen_provider 动态选择 base_url + api_key, 实现跨 provider 自动配对。
def _read_secret(var_name: str) -> str:
    """从环境变量或 secrets.env 读 key。"""
    v = os.environ.get(var_name, "")
    if v and not v.startswith("placeholder"):
        return v
    try:
        for line in open("/home/agentuser/.openclaw/secrets.env"):
            line = line.strip()
            if line.startswith(var_name + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v and not v.startswith("placeholder"):
                    return v
    except Exception:
        pass
    return ""

# 三 provider 转发配置(与 model_router.MODEL_POOL 的 provider 字段对齐)
PROVIDER_CONFIG = {
    "deepseek": {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": DEEPSEEK_KEY,
    },
    "token-plan": {
        "base_url": os.environ.get("TOKEN_PLAN_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        "api_key": _read_secret("OC_TOKEN_PLAN_API_KEY"),
    },
    "novarouteai": {
        "base_url": os.environ.get("NOVAROUTE_BASE_URL", "https://novarouteai.com/v1"),
        "api_key": _read_secret("NOVAROUTEAI_API_KEY") or os.environ.get("NOVAROUTEAI_API_KEY", ""),
    },
}

# ── 按 Agent 分发独立 key(治本·解决共用Tristan key的B1盲点) ──
# DeepSeek 官方按 API key 归因用量。共用 1 个 key → 后台分不清哪个 Agent / 缓存失效 miss 暴增。
# 现在: 从请求 model_hint 前缀(如 deepseek-momo/...)或 x-lao-agent header 识别 Agent, 用其独立 key。
AGENT_KEYS = {
    "tristan": _read_secret("OC_DEEPSEEK_TRISTAN_API_KEY"),
    "baron": _read_secret("OC_DEEPSEEK_BARON_API_KEY"),
    "ethan": _read_secret("OC_DEEPSEEK_ETHAN_API_KEY"),
    "luna": _read_secret("OC_DEEPSEEK_LUNA_API_KEY"),
    "momo": _read_secret("OC_DEEPSEEK_MOMO_API_KEY"),
    "nova": _read_secret("OC_DEEPSEEK_NOVA_API_KEY"),
    "shuyu": _read_secret("OC_DEEPSEEK_SHUYU_API_KEY"),
    "stella": _read_secret("OC_DEEPSEEK_STELLA_API_KEY"),
    "zeus": _read_secret("OC_DEEPSEEK_ZEUS_API_KEY"),
}

def _extract_agent(model_hint: str, headers: Dict) -> str:
    """从请求提取 Agent 名(用于分发独立 key)。"""
    # 优先 header 显式标注
    h = (headers.get("x-lao-agent") or "").strip().lower()
    if h in AGENT_KEYS:
        return h
    # 从 model_hint 前缀提取: 'deepseek-momo/deepseek-v4-flash' → 'momo'
    m = (model_hint or "").lower()
    if "/" in m:
        prefix = m.split("/")[0]
        for agent in AGENT_KEYS:
            if prefix.endswith(agent):
                return agent
    return ""

def _provider_client(provider: str, agent: str = ""):
    """按 provider 返回 OpenAI client(动态 base_url + key·支持按 Agent 分发独立 key)。

    命中率/成本治理(Stella 派单 2026-08-15):
    - max_retries=1: 防失败重试风暴(thinking 失败时一次任务扣 3 次费)
    - timeout=300 保持(复杂任务需要)
    """
    cfg = PROVIDER_CONFIG.get(provider) or PROVIDER_CONFIG["deepseek"]
    # 若是 deepseek 且识别到 Agent → 用该 Agent 独立 key(治本·后台可归因)
    if provider in ("deepseek", "") and agent in AGENT_KEYS and AGENT_KEYS[agent]:
        return OpenAI(api_key=AGENT_KEYS[agent], base_url=cfg["base_url"], timeout=300, max_retries=1)
    if not cfg["api_key"]:
        cfg = PROVIDER_CONFIG["deepseek"]
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=300, max_retries=1)

# 每日预算($USD·成本红线·Private Policy 可调)
DAILY_BUDGET = float(os.environ.get("LAO_DAILY_BUDGET_USD", "5.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lao-router")

app = FastAPI(title="lao-router", version="1.0.0")
router = ModelRouter()
# M6: LAO 节省证据链(SavingsEngine·Dashboard Impact Report 数据源)
savings_engine = SavingsEngine()

# ── B2: LAO→RIS 反向桥(LAO 路由/降级/成本信号 → lao-signal.json·RIS 消费) ──
LAO_SIGNAL_FILE = "/home/agentuser/shared/state/lao-signal.json"
_signal_lock = threading.Lock()
_signal_window: Dict[str, deque] = {}   # provider → 滚动窗口(最近 50 次转发结果)
SIGNAL_WINDOW_SIZE = 50

# ── B2/B5: RIS 健康门(LAO 消费 RIS 桥·阻断被隔离/掉线的 provider) ──
ris_gate = RISHealthGate()

# ── 三层Loop(2026-08-16 创始人令): L2经验工厂→L3确权→反哺L1 命中率/免疫 ──
# ExperienceLoop 持久化锚点库+反馈总线+确权链, route 结果回流(错误复利),
# 确权经验约束反哺 route()。任何初始化失败不阻塞路由(fail-open)。
try:
    from lao.effect_anchored.experience_loop import ExperienceLoop
    LOOP = ExperienceLoop()
    LOOP.attach_router(router)
    # Loop④(2026-08-16): 启动即反哺 RIS 恢复经验(成功→锚点/免疫·失败→错误复利)
    try:
        _ris_fb = LOOP.ingest_ris_recovery()
        logger.info(f"ExperienceLoop RIS 反哺: {_ris_fb}")
    except Exception as _e:
        logger.warning(f"ExperienceLoop RIS 反哺失败: {_e}")
except Exception as _loop_e:  # pragma: no cover - 环境缺件时路由照常
    LOOP = None
    logger.warning(f"ExperienceLoop 未启用: {_loop_e}")


def _loop_record(provider: str, model: str, ok: bool, error: str = "") -> None:
    """路由结果回流 FeedbackBus(L1→L2/L3·错误复利/经验复利)。失败不阻塞。"""
    if LOOP is None:
        return
    try:
        LOOP.record_route_result(provider, model, ok, error)
    except Exception as e:
        logger.warning(f"loop record fail: {e}")


# ── 会话粘性(2026-08-16 L1命中率·最有效手段): 同一会话粘住同 provider+model ──
# DeepSeek KVCache 按前缀匹配: 同会话每轮换 model/provider = 每轮全量 miss。
# 会话指纹 = 首条 system/首条 user 消息前缀哈希(跨轮稳定), 粘性表持久化+TTL+LRU。
STICKY_FILE = os.path.join(LOG_DIR, "lao-session-sticky.json")
STICKY_MAX_ENTRIES = 500
STICKY_TTL_SEC = 6 * 3600
_sticky_lock = threading.Lock()
_sticky_cache: Dict[str, Dict] = {}


def _sticky_load() -> Dict[str, Dict]:
    if _sticky_cache:
        return _sticky_cache
    try:
        with open(STICKY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _sticky_cache.update(data)
    except Exception:
        pass
    return _sticky_cache


def _sticky_save_locked() -> None:
    try:
        # LRU 收敛: 只保留最近 STICKY_MAX_ENTRIES 条(按 ts)
        items = sorted(_sticky_cache.items(), key=lambda kv: kv[1].get("ts", 0))
        for k, _ in items[:-STICKY_MAX_ENTRIES]:
            _sticky_cache.pop(k, None)
        tmp = STICKY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sticky_cache, f, ensure_ascii=False)
        os.replace(tmp, STICKY_FILE)
    except Exception as e:
        logger.warning(f"sticky save fail: {e}")


def _session_fingerprint(messages: List[Dict]) -> str:
    """会话指纹: 首条 system + 首条 user 消息前 512 字符的 sha1(跨轮稳定)。"""
    import hashlib as _hl
    parts = []
    for want in ("system", "user"):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == want:
                c = m.get("content", "")
                if not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False, default=str)
                parts.append(c[:512])
                break
    raw = "\x1f".join(parts)
    return _hl.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _sticky_get(session_fp: str) -> Optional[Dict]:
    if not session_fp:
        return None
    with _sticky_lock:
        entry = _sticky_load().get(session_fp)
    if not entry or time.time() - entry.get("ts", 0) > STICKY_TTL_SEC:
        return None
    return entry


def _sticky_put(session_fp: str, provider: str, model: str, agent: str) -> None:
    if not session_fp:
        return
    with _sticky_lock:
        _sticky_load()[session_fp] = {
            "provider": provider, "model": model,
            "agent": agent or "unknown", "ts": time.time(),
        }
        _sticky_save_locked()


def _sticky_usable(entry: Dict, tier: str, agent: str, cur_provider: str) -> bool:
    """粘性条目是否可用: provider 有 key + agent 归属一致 + 质量不破底线。"""
    p = entry.get("provider", "")
    cfg = PROVIDER_CONFIG.get(p)
    if not cfg or not cfg.get("api_key"):
        return False
    if (entry.get("agent") or "unknown") != (agent or "unknown"):
        return False  # 跨 agent 复用 = key/前缀隔离被破坏
    if agent and p != cur_provider:
        return False  # agent 绑定 provider 优先于粘性
    # 质量底线: 粘性 model 必须过该 tier 的 SAFETY_GATE
    gate = router.SAFETY_GATE.get(tier, 0.50)
    for e in router.MODEL_POOL.get(tier, []):
        if e.get("model") == entry.get("model"):
            return float(e.get("quality", 0)) >= gate
    return False


# ── 命中率反馈进路由(2026-08-16 L1): 实测 cache_hit_rate 低的 provider 让位 ──
HITRATE_MIN_SAMPLES = 10
HITRATE_LOW_BAR = 0.60
HITRATE_SWAP_GAP = 0.15


def _provider_cache_hit_rate(provider: str) -> Optional[float]:
    """滚动窗口内 provider 的实测缓存命中率(无样本=None)。"""
    with _signal_lock:
        dq = _signal_window.get(provider)
        if not dq or len(dq) < HITRATE_MIN_SAMPLES:
            return None
        hit = sum(e["hit"] for e in dq)
        miss = sum(e["miss"] for e in dq)
    return round(hit / (hit + miss), 4) if hit + miss else None


def _prefer_hitrate_provider(sel: RouteSelection) -> RouteSelection:
    """实测命中率反馈: 首选 provider 命中率显著低且 fallback 有明显更优者 → 切换。

    只在无会话粘性时生效(粘性优先); 切换是收敛的: 高命中 provider 持续胜出。
    """
    cur_rate = _provider_cache_hit_rate(sel.provider)
    if cur_rate is not None and cur_rate >= HITRATE_LOW_BAR:
        return sel
    for fc in sel.fallback_chain:
        try:
            prov, model = fc.split("/", 1)
        except ValueError:
            continue
        cand_rate = _provider_cache_hit_rate(prov)
        if cand_rate is None:
            continue
        if cand_rate - (cur_rate if cur_rate is not None else 0.0) >= HITRATE_SWAP_GAP:
            sel.provider, sel.model = prov, model
            _log_event({"type": "hitrate_feedback_switch", "from": cur_rate,
                        "to": cand_rate, "provider": prov, "model": model})
            break
    return sel


def _update_lao_signal(provider: str, ok: bool, cache_hit: int = 0, cache_miss: int = 0,
                       cost_usd: float = 0.0, degraded: bool = False) -> None:
    """B2 反向桥写入: 每次转发结算后更新 lao-signal.json(原子写·RIS 每 30s 消费)。

    RIS 消费端(ris.lao_signal.LAOSignalMonitor)用错误率产出 provider 退化事件 →
    隔离指令回写 ris-bridge → 本服务 ris_gate 阻断 → 双向数据飞轮闭环。
    """
    try:
        with _signal_lock:
            dq = _signal_window.setdefault(provider, deque(maxlen=SIGNAL_WINDOW_SIZE))
            dq.append({"ok": bool(ok), "hit": cache_hit, "miss": cache_miss,
                       "cost": cost_usd, "degraded": degraded, "ts": time.time()})
            providers = {}
            for pname, entries in _signal_window.items():
                n = len(entries)
                errs = sum(1 for e in entries if not e["ok"])
                hit = sum(e["hit"] for e in entries)
                miss = sum(e["miss"] for e in entries)
                providers[pname] = {
                    "requests": n, "errors": errs,
                    "error_rate": round(errs / n, 4) if n else 0.0,
                    "cache_hit_tokens": hit, "cache_miss_tokens": miss,
                    "cache_hit_rate": round(hit / (hit + miss), 4) if hit + miss else None,
                    "cost_usd": round(sum(e["cost"] for e in entries), 6),
                    "degraded_count": sum(1 for e in entries if e["degraded"]),
                    "last_ts": entries[-1]["ts"],
                }
            signal = {
                "layer": "lao",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "window": {"providers": providers},
                "daily_cost_usd": round(_daily_cost["total_usd"], 6),
                "budget_usd": DAILY_BUDGET,
            }
            os.makedirs(os.path.dirname(LAO_SIGNAL_FILE), exist_ok=True)
            tmp = LAO_SIGNAL_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(signal, f, ensure_ascii=False)
            os.replace(tmp, LAO_SIGNAL_FILE)
    except Exception as e:
        logger.warning(f"lao-signal update fail: {e}")


def _ris_guard_provider(chosen_provider: str, request_id: str = "") -> tuple:
    """B2/B5: RIS 健康门——被 RIS 判定 down/isolated 的 provider 真实阻断。

    - 阻断后降级切换到第一个健康且有 key 的 provider(事件留痕 ris_provider_block)
    - 全部候选被阻断 → (None, ev)·调用方显式 503(禁止静默 fallback)
    - RIS 桥陈旧/不可读 → fail-open 不阻断(RIS 故障不放大为 LAO 全停)
    Returns: (provider 或 None, block_event 或 None)
    """
    try:
        snap = ris_gate.read()
    except Exception:
        return chosen_provider, None
    if not snap["fresh"] or not snap["blocked"]:
        return chosen_provider, None
    if chosen_provider not in snap["blocked"]:
        return chosen_provider, None
    # 降级: 摘除被阻断 provider·按序选健康候选(有 key 才可用)
    for cand in ("deepseek", "token-plan", "novarouteai"):
        if cand == chosen_provider:
            continue
        cfg = PROVIDER_CONFIG.get(cand)
        if cfg and cfg.get("api_key") and cand not in snap["blocked"]:
            ev = {"request_id": request_id, "type": "ris_provider_block",
                  "blocked": chosen_provider, "fallback": cand,
                  "reason": "blocked by RIS (down/isolated)", "source": snap["source"]}
            _log_event(ev)
            return cand, ev
    ev = {"request_id": request_id, "type": "ris_provider_block",
          "blocked": chosen_provider, "fallback": None,
          "reason": "all candidate providers blocked by RIS", "source": snap["source"]}
    _log_event(ev)
    return None, ev

# ── Phase A/B: OpenAI 兼容参数过滤层 + Provider Capability Detection ──
# 防止未知参数(如 thinking)被直接透传到 OpenAI SDK → 400/TypeError
SUPPORTED_PARAMS = {
    "model", "messages", "temperature", "max_tokens", "top_p", "n",
    "stream", "tools", "tool_choice", "response_format", "stop", "frequency_penalty",
    "presence_penalty", "logprobs", "seed", "user", "stream_options",
}

# ProviderCapabilityRegistry: 各模型支持的能力(thinking/reasoning_content/tools/stream)
# 不支持的参数 → 自动 drop + CapabilityFallbackEvent(不报错)
ProviderCapabilityRegistry = {
    "deepseek-v4-flash": {"thinking": False, "reasoning_content": True, "tools": True, "stream": True},
    "deepseek-v4-pro":   {"thinking": False, "reasoning_content": True, "tools": True, "stream": True},
    "default":           {"thinking": False, "reasoning_content": True, "tools": True, "stream": True},
}


def _capability(model: str) -> dict:
    """获取模型能力(默认按 default 兜底)。"""
    for k in ("deepseek-v4-flash", "deepseek-v4-pro"):
        if k in model:
            return ProviderCapabilityRegistry[k]
    return ProviderCapabilityRegistry["default"]


# M4: cache hit/miss 价差计费(DeepSeek 官方口径: prompt = hit + miss·hit 价远低于 miss)
# ¥/1M tokens; miss 档与旧固定单价一致(无缓存时成本不变), hit 档按官方价差折算
# ⚠️ LEGACY 价表: 仅 PEAK_VALLEY_EFFECTIVE(20260817) 之前的事件使用(7.2汇率口径)
MODEL_PRICING_YUAN = {
    "deepseek-v4-pro":   {"hit": 0.6, "miss": 3.0, "output": 6.0},
    "deepseek-v4-flash": {"hit": 0.1, "miss": 1.0, "output": 2.0},
    "default":           {"hit": 0.6, "miss": 3.0, "output": 6.0},
}
CNY_PER_USD = 7.2  # legacy 汇率(20260817前事件); 峰谷口径用 FX_USD_CNY=7.1

# ── 峰谷计价(2026-08-17 00:00 CST 生效 · DeepSeek官方价 · Nova规格/Stella v2.1 ground truth) ──
# 官方USD价(per 1M tokens): 峰=UTC 01-04+06-10(=CST 09-12+14-18), 其余为谷
PEAK_VALLEY_EFFECTIVE = "20260817"  # 生效日(含)起用新价·按CST日期切换
FX_USD_CNY = 7.1  # 与Stella token-cost-model v2.1对齐(官方账单CSV到账后复核)
PEAK_VALLEY_PRICES_USD = {
    "deepseek-v4-flash": {"peak":   {"hit": 0.014, "miss": 0.44, "out": 1.32},
                          "valley": {"hit": 0.007, "miss": 0.22, "out": 0.66}},
    "deepseek-v4-pro":   {"peak":   {"hit": 0.044, "miss": 1.32, "out": 3.96},
                          "valley": {"hit": 0.022, "miss": 0.66, "out": 1.98}},
}
_CST = _tzone(_tdelta(hours=8))


def _pricing_now() -> tuple:
    """当前时刻(CST)的计价口径: 返回 (regime, window)。
    日期<生效日→legacy; 否则 CST 09-12/14-18=peak, 其余=valley。"""
    now = _dt.now(_CST)
    if now.strftime("%Y%m%d") < PEAK_VALLEY_EFFECTIVE:
        return "legacy", "valley"
    h = now.hour
    return "peak_valley", ("peak" if (9 <= h < 12 or 14 <= h < 18) else "valley")


def _compute_cost_yuan(model: str, cache_hit: int, cache_miss: int, out_tok: int):
    """按 cache hit/miss 分价计算一次调用成本(¥)。

    20260817起: 峰谷双表(USD)×7.1; 之前: legacy价表(¥)÷7.2口径不变。
    Returns: (cost_yuan, pricing_regime, window, fx)
    """
    regime, window = _pricing_now()
    if regime == "peak_valley":
        for k in ("deepseek-v4-pro", "deepseek-v4-flash"):
            if k in model:
                p = PEAK_VALLEY_PRICES_USD[k][window]
                break
        else:
            p = PEAK_VALLEY_PRICES_USD["deepseek-v4-pro"][window]
        cost_yuan = (cache_hit * p["hit"] + cache_miss * p["miss"] + out_tok * p["out"]) / 1e6 * FX_USD_CNY
        return cost_yuan, regime, window, FX_USD_CNY
    for k in ("deepseek-v4-pro", "deepseek-v4-flash"):
        if k in model:
            p = MODEL_PRICING_YUAN[k]
            break
    else:
        p = MODEL_PRICING_YUAN["default"]
    cost_yuan = (cache_hit * p["hit"] + cache_miss * p["miss"] + out_tok * p["output"]) / 1e6
    return cost_yuan, "legacy", "valley", CNY_PER_USD


def _safe_payload(body: dict, chosen_model: str) -> tuple[dict, list]:
    """过滤未知参数 + 基于能力的参数协商。

    C1/M2 修复(QODER 审计 2026-08-16): 转发层**只用 chosen_model**。
    - 旧逻辑保留 requested_model → route_with_budget 的 pro→flash 成本红线
      降级被架空(决策 flash·实际按 pro 计费·¥400 事故根因)。
    - 缓存稳定不再靠转发 model 参数, 而由三层保障:
      ① provider/api_key 按 agent 固定(C4); ② payload["user"] 按 agent 隔离;
      ③ _stabilize_messages 稳定前缀。转发 model = 路由决策, 与缓存解耦。

    Returns:
        (payload: 仅含支持的参数, fallback_events: CapabilityFallbackEvent 列表)
    """
    cap = _capability(chosen_model)
    payload = {k: v for k, v in body.items() if k in SUPPORTED_PARAMS}
    # 命中率数据(Stella派单): stream 时强制 include_usage·否则 chunk 无 usage·cache 字段永远 0
    if payload.get("stream") and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    # C1: 转发 model 必须等于路由决策(成本红线真实生效)
    payload["model"] = chosen_model
    # P1 命中率优化(Shuyu派单 2026-08-15): 稳定前缀 + 历史剪枝
    payload["messages"] = _stabilize_messages(payload.get("messages", []))
    events = []
    # 能力协商: body 中存在的参数但 provider 不支持 → drop + 记录事件
    thinking_enabled = bool(cap.get("thinking", False)) and body.get("thinking") not in (None, False)
    for param, supported in (("thinking", cap.get("thinking", False)),):
        if param in body and not supported:
            payload.pop(param, None)
            events.append({
                "type": "CapabilityFallbackEvent",
                "reason": f"{param} unsupported by {chosen_model}",
                "model": chosen_model, "param": param,
            })
    # 根治(成本事故复盘·成功率门禁): 只要未以 thinking 模式转发，就 strip 所有
    # assistant 消息的 reasoning_content。
    # 旧版只在 `thinking in body` 时 strip → 漏掉两种 400:
    #   ① client 不发 thinking·但历史带 reasoning_content(上一个 thinking turn 残留)
    #      → DeepSeek 400 "reasoning_content in thinking mode must be passed back"
    #   ② thinking 被 drop 时 pop tool_calls → 工具链断裂 → 另两类 400
    # 我们所有模型 thinking=False → 永不转发 thinking 模式 → reasoning_content 必须全清。
    if not thinking_enabled:
        _strip_reasoning_content(payload.get("messages", []))
    return payload, events


# P1 命中率优化(Shuyu派单 2026-08-15): 稳定前缀 + 历史剪枝
import re as _re

# OpenClaw 注入的动态时间戳(每请求不同→前缀不稳定→缓存 miss)
_TIMESTAMP_PAT = _re.compile(
    r"Current time: [^\n]*\([^\n]*\)\nReference UTC: [^\n]*\n?")


def _stabilize_messages(messages: list, max_history: int = 30) -> list:
    """稳定前缀 = 缓存命中(架构红线落地)。

    ① 归一化动态时间戳: OpenClaw 每请求注入 'Current time: ...' 不同 →
       替换为固定占位符 → system 前缀稳定 → 缓存可命中。
    ② 历史剪枝: 多轮对话历史过长 → 保留早期稳定前缀 + 截断变化历史
       (过长历史=更多 miss·剪到 max_history 条·保留 system+早期)。
    """
    if not messages:
        return messages
    out = []
    for m in messages:
        c = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(c, str) and "Current time:" in c:
            m = dict(m)
            m["content"] = _TIMESTAMP_PAT.sub("", c)  # 移除动态时间戳行
        out.append(m)
    # 历史剪枝: 保留 system + 前 max_history-1 条早期 + 最后 1 条(当前请求)
    if len(out) > max_history:
        # 修复(2026-08-16 三层审计): 旧实现 out[:max_history] 会丢掉最末尾的
        # 当前用户消息 → 请求语义被改变 + 前缀与客户端预期错位(伤命中率)。
        out = out[:max_history - 1] + out[-1:]
    return out


def _strip_reasoning_content(messages: list):
    """P0修复(根治·LAO 成本事故复盘·成功率门禁): 当 thinking 被 drop 时，
    只 strip assistant 消息的 reasoning_content，**绝不删 tool_calls**。

    背景(两次 P0 事故根因·2026-08-15 Ethan 实测):
    - 旧版 `m.pop("tool_calls", None)` 破坏了 tool-call 对话链 →
      残留 role='tool' 消息失去前置 tool_calls →
      DeepSeek 400: "Messages with role 'tool' must be a response to a
      preceding message with 'tool_calls'".
    - 旧版同时 pop reasoning_content + tool_calls → 若 assistant 消息原本
      只有 reasoning_content / tool_calls 而无 content → 变成空消息 →
      DeepSeek 400: "Invalid assistant message: content or tool_calls must be set".

    根治原则:
    ① 只删 reasoning_content(thinking 模式下必须传回的字段)·tool_calls 是
       function-calling 链的合法部分·v4-flash 支持 tools → 必须保留。
    ② 删掉 reasoning_content 后若 assistant 消息既无 content 又无 tool_calls
       (空消息)→ 补一个空串占位 content·否则触发 400 空消息。
    ③ 保留 tool_calls 时·其后续 role='tool' 消息天然合法·不再孤儿。
    """
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            m.pop("reasoning_content", None)
            # 根治: 删 reasoning_content 后若消息为空(content/tool_calls 均无)→补占位
            # 增强: content 可能是空 list 或 [{'type':'text','text':''}] (OpenClaw 多模态残留)
            #       → DeepSeek 400 空消息·统一归一为空串
            content = m.get("content")
            if isinstance(content, list):
                if not content or all(
                    isinstance(c, dict) and not (c.get("text") or c.get("image_url") or c.get("input") or c.get("tool_code_execution"))
                    for c in content
                ):
                    m["content"] = ""  # 空/纯占位 list → 空串(避免 DeepSeek 400 空消息)
            if not m.get("content") and not m.get("tool_calls"):
                m["content"] = ""


# 每日成本累计(成本红线)
_lock = threading.Lock()
_daily_cost = {"date": time.strftime("%Y-%m-%d"), "total_usd": 0.0}


def _reset_daily_if_needed():
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if _daily_cost["date"] != today:
            _daily_cost["date"] = today
            _daily_cost["total_usd"] = 0.0


# ── 任务分层(tier)启发式 ──────────────────────────
def _infer_tier(messages: List[Dict], model_hint: str = "") -> str:
    """从请求推断任务层级(供 route_with_budget 选模型)。

    规则: 显式 header → model名 → 内容长度/复杂度启发式。
    """
    # model 名可直接映射(优先·避免 model 名被内容启发式误判)
    m = (model_hint or "").lower()
    if "ultra" in m or "tiny" in m: return "ultra_light"
    if "flash" in m:
        # v4-flash → 低成本 tier(用户显式要 flash = 便宜优先·不因内容误判升 pro)
        if "reason" in m or "code" in m:
            return "code"
        return "light"
    if "reason" in m: return "reasoning"
    if "pro" in m:
        # v4-pro → 重活(用户显式要 pro·质量优先)
        if "reason" in m: return "reasoning"
        if "code" in m or "coder" in m: return "code"
        if "heavy" in m: return "heavy"
        return "medium"
    if "code" in m or "coder" in m: return "code"
    # 内容启发式
    text = " ".join(str(x.get("content", "")) for x in messages)[:2000]
    L = len(text)
    kw = text.lower()
    if any(k in kw for k in ("architecture", "design review", "refactor", "审计", "架构", "设计")) and L > 1500:
        return "reasoning"
    if any(k in kw for k in ("code", "bug", "function", "实现", "代码", "报错")) :
        return "code"
    if L > 2000: return "heavy"
    if L > 600: return "medium"
    return "light"


# ── 成本红线检查 ──────────────────────────────────
def _remaining_budget() -> float:
    _reset_daily_if_needed()
    with _lock:
        return max(0.0, DAILY_BUDGET - _daily_cost["total_usd"])


def _record_cost(usd: float):
    _reset_daily_if_needed()
    with _lock:
        _daily_cost["total_usd"] += usd


def _log_event(ev: Dict[str, Any]):
    ev["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    # M3: agent 归因规范化 — 空值统一 unknown(聚合/成本归因不再出现空串)
    if not ev.get("agent"):
        ev["agent"] = "unknown"
    with open(EVENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")


def _model_quality(model: str, tier: str) -> float:
    """从 MODEL_POOL 查所选模型质量分(供 SavingsEngine 证据链)。"""
    for e in router.MODEL_POOL.get(tier, []):
        if e.get("model") == model:
            return float(e.get("quality", 0.0))
    return 0.0


def _settle_and_log(*, request_id: str, tier: str, agent: str, model_hint: str,
                    chosen_model: str, provider: str, sel: RouteSelection, budget: float,
                    in_tok: int, out_tok: int, cache_hit: int, cache_miss: int,
                    stream: bool, latency_ms: int, cap_events: list,
                    status: str = "ok", error: str = "",
                    session_fp: str = ""):
    """成本结算 + SavingsEngine + 事件落日志(流式/非流式统一入口)。

    C2: 流式/非流式都经此记账(_record_cost)→ 预算红线覆盖全部流量。
    M4: cache hit/miss 分价计费。M5: 流式不再硬编码 cost_yuan=0。
    M6: 每次成功响应产出 CostSavingsEvent。m3: request_id 贯穿。
    Loop(2026-08-16): 成功→会话粘性+经验复利回流; 失败→错误复利回流。
    """
    degraded = "flash" in chosen_model and "pro" in str(model_hint).lower()
    cost_yuan, pricing_regime, pricing_window, fx = _compute_cost_yuan(chosen_model, cache_hit, cache_miss, out_tok)
    _record_cost(cost_yuan / fx)
    baseline_model = (model_hint.split("/")[-1] if model_hint else "") or "deepseek-v4-pro"
    saving_ev = savings_engine.compute_saving(
        agent_id=agent or "unknown", task_type=tier,
        original_model=baseline_model, selected_model=chosen_model,
        in_tok=in_tok, out_tok=out_tok, cache_hit=cache_hit, cache_miss=cache_miss,
        quality_score=_model_quality(chosen_model, sel.tier),
        switch_reason="budget_redline_degrade" if degraded else "tier_match",
    )
    _log_event({
        "request_id": request_id,
        "tier": tier, "chosen_model": chosen_model, "forwarded_model": chosen_model,
        "provider": provider,
        "agent": agent, "requested_model": model_hint, "budget_remaining": round(budget, 4),
        "degraded": degraded,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_hit_tokens": cache_hit, "cache_miss_tokens": cache_miss,
        "task_type": tier,
        "cost_yuan": round(cost_yuan, 6), "pricing_regime": pricing_regime, "window": pricing_window,
        "latency_ms": latency_ms, "stream": stream,
        "status": status, "error": error or "",
        "saving_usd": round(saving_ev.saving_amount, 6),
        "fallback_chain": sel.fallback_chain,
        "capability_events": cap_events,   # Phase A/B: 参数过滤事件(TrustEvent 链)
        "session_fp": session_fp,
    })
    # B2 反向桥: 路由/降级/成本/缓存结果 → lao-signal.json(RIS 消费·双向飞轮)
    _update_lao_signal(provider, ok=(status == "ok"),
                       cache_hit=cache_hit, cache_miss=cache_miss,
                       cost_usd=cost_yuan / fx, degraded=degraded)
    # 三层Loop回流(2026-08-16): L1结果→L2经验工厂(错误复利/经验复利)
    _loop_record(provider, chosen_model, ok=(status == "ok"), error=error)
    # 会话粘性: 成功 → 记住本次 provider+model(下轮同会话复用→前缀命中)
    if status == "ok":
        _sticky_put(session_fp, provider, chosen_model, agent)


# ── OpenAI 兼容端点 ──────────────────────────────
@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [
        {"id": "deepseek-v4-pro", "object": "model"},
        {"id": "deepseek-v4-flash", "object": "model"},
    ]}


@app.get("/v1/savings")
def savings_report():
    """M6: LAO Impact Report(供 Nova/Stella/Dashboard 消费)。"""
    return savings_engine.impact_report()


@app.get("/v1/loop/status")
def loop_status():
    """三层Loop状态(L1命中率↔L2经验工厂↔L3确权·审计/Dashboard 消费)。"""
    if LOOP is None:
        return {"enabled": False}
    try:
        return {"enabled": True, **LOOP.status()}
    except Exception as e:
        return {"enabled": True, "error": str(e)}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    # m3: request_id 贯穿该请求的全部日志事件(优先透传 x-request-id)
    request_id = (request.headers.get("x-request-id") or "").strip() or uuid.uuid4().hex[:12]
    messages = body.get("messages", [])
    model_hint = body.get("model", "")
    stream = body.get("stream", False)

    # ① 任务分层
    tier = request.headers.get("x-lao-tier", "") or _infer_tier(messages, model_hint)

    # 按 Agent 分发独立 key(治本·B1)·创始人 B 阶段: 先提取 agent 供路由绑定 provider
    agent = _extract_agent(model_hint, dict(request.headers))

    # L1 命中率修复(2026-08-16): 真实任务文本+上下文规模 → 缓存感知路由激活
    # (旧实现只传 tier 名 → 缓存感知分支生产恒不激活·死代码)
    def _msg_text(ml: List[Dict]) -> str:
        for m in reversed(ml):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, list):
                    c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
                return str(c)[:500]
        return ""
    task_text = _msg_text(messages)
    _total_chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    context_tokens = int(_total_chars * 0.75)  # CJK≈1字1token·ASCII≈4字1token 的折中估算
    session_fp = _session_fingerprint(messages)

    # ② 成本红线路由(用已算好的 tier 而非把 model 名当 task·根治 Nova 根因1)
    budget = _remaining_budget()
    try:
        # 关键修复: 不把 model_hint(如 deepseek-momo/deepseek-v4-flash)当 task 传给 classify
        # → 用 _infer_tier 已算出的 tier, 避免 model 名走 classify default=medium → 误判 pro
        # 创始人 B 阶段: 传 agent 实现按 Agent 绑定 provider(baron/ethan/momo→token-plan)
        # v3.4: 附带 task_text/context_tokens 激活缓存感知选品(T2 真实生效)
        sel: RouteSelection = router.route_with_budget(
            task=tier or model_hint, budget=budget, agent=agent,
            task_text=task_text, context_tokens=context_tokens)
    except Exception as e:
        logger.error(f"route_with_budget失败({e}), 使用默认")
        sel = router.route("light")

    chosen_model = sel.model
    chosen_provider = sel.provider

    # C4: provider 绑定与缓存隔离统一 —— agent 一旦识别, provider 固定为其绑定值
    # (未绑定 agent 默认 deepseek·route() 已过滤池), 不再按请求前缀切换:
    # provider/api_key 变 = DeepSeek 前缀缓存全失效(miss 价是 hit 价数十倍)。
    # 仅无 agent 的裸请求尊重 provider 前缀(deepseek/ token-plan/ novarouteai/)。
    req_provider = model_hint.split("/")[0] if "/" in model_hint else ""
    if not agent and req_provider in PROVIDER_CONFIG:
        chosen_provider = req_provider

    # 会话粘性(2026-08-16 L1命中率): 同会话复用上次成功的 provider+model
    # → KVCache 前缀最大复用。粘性优先于命中率反馈(稳定即命中)。
    sticky_entry = _sticky_get(session_fp)
    if sticky_entry and _sticky_usable(sticky_entry, sel.tier, agent, chosen_provider):
        chosen_provider = sticky_entry["provider"]
        chosen_model = sticky_entry["model"]
    else:
        # 命中率反馈(2026-08-16 L1): 实测命中率显著低的 provider 让位给 fallback
        sel_c = RouteSelection(task=sel.task, model=chosen_model, provider=chosen_provider,
                               tier=sel.tier, cost=sel.cost,
                               credit_based=sel.credit_based,
                               fallback_chain=sel.fallback_chain)
        sel_c = _prefer_hitrate_provider(sel_c)
        chosen_model, chosen_provider = sel_c.model, sel_c.provider

    # B2/B5: RIS 健康门——被 RIS 阻断(down/isolated)的 provider 摘出候选·降级切换;
    # 全部候选被阻断 → 显式 503(禁止静默 fallback·ProviderHealthGate 哲学)
    chosen_provider, _block_ev = _ris_guard_provider(chosen_provider, request_id)
    if chosen_provider is None:
        return JSONResponse(
            {"error": {"message": "all upstream providers blocked by RIS isolation",
                       "type": "lao_router_ris_gate"}},
            status_code=503)

    # ③ 转发真实 provider(按 chosen_provider 动态选 base_url + key·白名单过滤+能力协商)
    client = _provider_client(chosen_provider, agent)
    payload, cap_events = _safe_payload(body, chosen_model)
    # P0-2 命中率99.9%: 传独立 user_id(DeepSeek 官方 KVCache 隔离机制)
    # 每个 agent 独立 user → 缓存按 agent 隔离·前缀更稳定·miss 降(官方CSV: miss价是hit价120倍)
    # v3.4(2026-08-16): 无 agent 的裸请求按会话指纹隔离(不再共享池 → 跨会话互相冲刷缓存)
    if agent:
        payload["user"] = f"lao-{agent}"
    elif session_fp:
        payload["user"] = f"lao-s-{session_fp}"
    started = time.time()
    try:
        # C3: 同步 OpenAI 调用放线程池执行, 不阻塞 uvicorn 事件循环
        # (旧逻辑直接同步调用 → 首 token 前整个 router 卡死 → 重试风暴放大成本)
        resp = await asyncio.to_thread(client.chat.completions.create, **payload)
    except Exception as e:
        await asyncio.to_thread(_log_event, {
            "request_id": request_id,
            "tier": tier, "chosen_model": chosen_model, "forwarded_model": chosen_model,
            "provider": chosen_provider,
            "agent": agent, "budget": budget, "status": "error", "error": str(e)[:200],
            "capability_events": cap_events})
        # B2 反向桥: 转发失败也要进滚动窗口(错误率是 RIS 判定退化的核心信号)
        await asyncio.to_thread(_update_lao_signal, chosen_provider, False)
        # 三层Loop(2026-08-16): 转发失败 → 错误复利回流(≥2次同类→锚点→路由约束)
        await asyncio.to_thread(_loop_record, chosen_provider, chosen_model, False, str(e)[:200])
        return JSONResponse({"error": {"message": str(e), "type": "lao_router_forward"}}, status_code=502)

    latency_ms = int((time.time() - started) * 1000)

    # ③.5 流式响应: 必须流式转发(OpenClaw 用 streaming)·否则 SSE 序列化失败
    if stream:
        # 命中率(Stella派单): 从流末尾 chunk 提取 usage(含 cache 字段)·stream 也要记录真实命中率
        stream_usage = {"input": 0, "output": 0, "hit": 0, "miss": 0}

        def _sse_gen():
            nonlocal stream_usage
            status, err = "ok", ""
            try:
                for chunk in resp:   # OpenAI Stream 迭代(Starlette 在线程池中迭代本生成器)
                    # 保留 OpenAI SSE 格式
                    yield "data: " + chunk.model_dump_json() + "\n\n"
                    # 流末尾 chunk 带 usage(含 cache)·提取真实命中率数据
                    cu = getattr(chunk, "usage", None)
                    if cu is not None:
                        stream_usage["input"] = getattr(cu, "prompt_tokens", 0) or 0
                        stream_usage["output"] = getattr(cu, "completion_tokens", 0) or 0
                        stream_usage["hit"] = getattr(cu, "prompt_cache_hit_tokens", 0) or 0
                        stream_usage["miss"] = getattr(cu, "prompt_cache_miss_tokens", 0) or 0
                yield "data: [DONE]\n\n"
            except Exception as e:
                status, err = "error", str(e)[:200]
                logger.error(f"stream error: {e}")
                yield f"data: {{\"error\":{{\"message\":\"{e}\",\"type\":\"lao_router_stream\"}}}}\n\n"
            finally:
                # M1/C2/M5: 成功/异常/客户端断开都必经 finally —
                # 流式成本计入每日预算 + 真实 cost_yuan + 事件日志(含 error·A/B 成功率不再失真)
                _settle_and_log(
                    request_id=request_id, tier=tier, agent=agent, model_hint=model_hint,
                    chosen_model=chosen_model, provider=chosen_provider, sel=sel, budget=budget,
                    in_tok=stream_usage["input"], out_tok=stream_usage["output"],
                    cache_hit=stream_usage["hit"], cache_miss=stream_usage["miss"],
                    stream=True, status=status, error=err,
                    latency_ms=int((time.time() - started) * 1000), cap_events=cap_events,
                    session_fp=session_fp,
                )

        return StreamingResponse(_sse_gen(), media_type="text/event-stream")

    # ④ 成本记录(非流式) + SavingsEngine + 事件日志(与流式共用 _settle_and_log 统一入口)
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    # 命中率 99.9%(Stella/创始人令 2026-08-15): 提取 cache 字段
    # DeepSeek usage: prompt_cache_hit_tokens(命中) / prompt_cache_miss_tokens(未命中)
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) if usage else 0
    cache_miss = getattr(usage, "prompt_cache_miss_tokens", 0) if usage else 0
    await asyncio.to_thread(
        _settle_and_log,
        request_id=request_id, tier=tier, agent=agent, model_hint=model_hint,
        chosen_model=chosen_model, provider=chosen_provider, sel=sel, budget=budget,
        in_tok=in_tok, out_tok=out_tok, cache_hit=cache_hit, cache_miss=cache_miss,
        stream=stream, latency_ms=latency_ms, cap_events=cap_events,
        session_fp=session_fp,
    )

    return resp


if __name__ == "__main__":
    logger.info(f"lao-router 启动: :{PORT} | DeepSeek基址={DEEPSEEK_BASE} | key={'$'*8 if DEEPSEEK_KEY else 'MISSING'} | 每日预算=${DAILY_BUDGET}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
