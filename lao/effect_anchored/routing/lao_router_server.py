#!/usr/bin/env python3
# v3.5.1-fix: R5
# v3.5.1-glm: R5
# v3.5.1-wiring: W1-W9 (2026-08-19 创始人令·LAO三环节接线: 入站萃取W1/W2/W2.5 + 经验直答W3 + 出站验证W4/W5/W6/W7 + 全链路W8 + L3经验闭环W9)
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
from lao.effect_anchored.context_rebuilder import ContextRebuilder, Event as CRB_Event  # v3.5.1-wiring: W1
from lao.effect_anchored.memory_anchor import MemoryAnchor  # v3.5.1-wiring: W2
from lao.effect_anchored.cognitive_system import DeterministicCognitiveSystem  # v3.5.1-wiring: W2.5
from lao.effect_anchored.hallucination_gate import HallucinationGate  # v3.5.1-wiring: W4
from lao.effect_anchored.reality_check import RealityCheckEngine  # v3.5.1-wiring: W5
from lao.effect_anchored.user_fact_base import UserFactBase  # v3.5.1-wiring: W5

# ── 配置 ─────────────────────────────────────────────
PORT = int(os.environ.get("LAO_ROUTER_PORT", "8765"))
LOG_DIR = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs"
os.makedirs(LOG_DIR, exist_ok=True)
EVENT_LOG = os.path.join(LOG_DIR, "lao-router-events.jsonl")

# ── r3(2026-08-25 创始人命令·有条件解冻) 任务级成本护栏 ─────────────
# C22 单任务 token 上限告警+熔断 / 盲点3 重试计数上限。
# 阈值为暂定值(A11 基线出具后校准·可用环境变量覆盖):
#   告警 30M tokens/任务 · 熔断 50M tokens/任务 · 同 payload 重试上限 5 次
R3_TASK_ALERT_TOKENS = int(os.environ.get("R3_TASK_ALERT_TOKENS", "30000000"))
R3_TASK_HARD_TOKENS = int(os.environ.get("R3_TASK_HARD_TOKENS", "50000000"))
R3_MAX_RETRIES = int(os.environ.get("R3_MAX_RETRIES", "5"))
R3_LEDGER = os.path.join(LOG_DIR, "r3_task_token_ledger.json")

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
    "qwen": {
        # C9(2026-08-23): ModelRouter 从 openclaw.json 构建的池含 provider="qwen"
        # (dashscope 直连·qwen3.7-flash 等)·但本表缺失 → _provider_client 回退
        # deepseek 配置·DeepSeek API 收到 qwen 模型名 → 400 invalid_request_error。
        "base_url": os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": _read_secret("OC_QWEN_API_KEY") or os.environ.get("OC_QWEN_API_KEY", ""),
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

# C23(2026-08-25): token-plan 系模型(阿里云MaaS预付包·创始人令切换) — 放行+配额制计价
_TOKEN_PLAN_MODELS = frozenset({"qwen3.8-max", "qwen3.7-plus", "qwen3.6-flash", "qwen3.7-max", "glm-5.2"})

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
    # C16(2026-08-23): key 反查 — Authorization Bearer 里的 per-agent key 即身份证明。
    # 网关(OpenClaw)发往 LAO 的请求会剥掉 model 的 provider 前缀, 但 Authorization
    # 保留 provider 条目各自的 key(deepseek-<agent> 条目 → <agent> 独立 key)。
    _auth = (headers.get("authorization") or "").strip()
    if _auth.lower().startswith("bearer "):
        _k = _auth[7:].strip()
        for _a, _ak in AGENT_KEYS.items():
            if _ak and _k == _ak:
                return _a
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

# ── W1: 入站萃取·ContextRebuilder 接线(2026-08-19 创始人令·LAO接线) ──
# 记录每次请求事件 → 上下文重建证据链。初始化失败不阻塞路由(fail-open)。
try:
    CONTEXT_REBUILDER = ContextRebuilder(session_id="lao-router")
except Exception as _crb_e:
    CONTEXT_REBUILDER = None
    logger.warning(f"ContextRebuilder 未启用: {_crb_e}")

# ── W2: 入站萃取·MemoryAnchor 认知锚定(2026-08-19 创始人令·LAO接线) ──
try:
    ANCHOR_MEMORY = MemoryAnchor()
except Exception as _anch_e:
    ANCHOR_MEMORY = None
    logger.warning(f"MemoryAnchor 未启用: {_anch_e}")

# ── W2.5: 入站萃取·CognitiveSystem 认知模式匹配(2026-08-19 Shuyu追加) ──
try:
    COGNITIVE = DeterministicCognitiveSystem()
except Exception as _cog_e:
    COGNITIVE = None
    logger.warning(f"CognitiveSystem 未启用: {_cog_e}")

# ── W4: 出站验证·HallucinationGate(2026-08-19 创始人令·LAO接线) ──
try:
    HALL_GATE = HallucinationGate()
except Exception as _hg_e:
    HALL_GATE = None
    logger.warning(f"HallucinationGate 未启用: {_hg_e}")

# ── W5: 出站验证·RealityCheck + UserFactBase 组合(2026-08-19 创始人令·LAO接线) ──
try:
    REALITY = RealityCheckEngine()
except Exception as _rl_e:
    REALITY = None
try:
    FACTS = UserFactBase()
except Exception as _ft_e:
    FACTS = None

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
    # R4.2(2026-08-19 PRD v1.1): LAO→RIS 反哺 — 路由结果写入共享桥
    # ris-bridge.json 的 lao_feedback 段(不 import ris 包·fail-open 不阻塞路由)
    try:
        LOOP.l3_feedback_to_bridge()
    except Exception as e:
        logger.warning(f"loop bridge feedback fail: {e}")


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
HITRATE_WINDOW_SAMPLES = 10  # L-06: 窗口长度=MIN_SAMPLES(创始人裁定2026-08-25: 10样本触发告警)
_hitrate_low_state = {}      # L-06两级状态: provider -> {alerted_count}


def _provider_cache_hit_rate(provider: str) -> Optional[float]:
    """滚动窗口内 provider 的实测缓存命中率(无样本=None)。"""
    with _signal_lock:
        dq = _signal_window.get(provider)
        if not dq or len(dq) < HITRATE_MIN_SAMPLES:
            return None
        hit = sum(e["hit"] for e in dq)
        miss = sum(e["miss"] for e in dq)
    return round(hit / (hit + miss), 4) if hit + miss else None


def _hitrate_escalate(provider: str, cur_rate: float) -> bool:
    """L-06 两级机制(创始人裁定2026-08-25): 维持10样本门槛——
    首窗口(10样本)低于LOW_BAR仅告警不动作; 连续第二个窗口(再10样本)仍低才升级熔断(允许换路)。"""
    with _signal_lock:
        dq = _signal_window.get(provider)
        n = len(dq) if dq else 0
    st = _hitrate_low_state.get(provider)
    if st is None:
        _hitrate_low_state[provider] = {"alerted_count": n}
        _log_event({"type": "hitrate_low_alert", "provider": provider,
                    "rate": cur_rate, "samples": n, "tier": "alert_only"})
        return False
    if n - st["alerted_count"] >= HITRATE_WINDOW_SAMPLES:
        _log_event({"type": "hitrate_low_alert", "provider": provider,
                    "rate": cur_rate, "samples": n, "tier": "escalate_swap"})
        _hitrate_low_state.pop(provider, None)
        return True
    return False


def _prefer_hitrate_provider(sel: RouteSelection) -> RouteSelection:
    """实测命中率反馈: 首选 provider 命中率显著低且 fallback 有明显更优者 → 切换。

    只在无会话粘性时生效(粘性优先); 切换是收敛的: 高命中 provider 持续胜出。
    """
    cur_rate = _provider_cache_hit_rate(sel.provider)
    if cur_rate is not None and cur_rate >= HITRATE_LOW_BAR:
        _hitrate_low_state.pop(sel.provider, None)  # L-06: 命中率恢复, 重置升级状态
        return sel
    if cur_rate is None:
        return sel  # L-06: 样本不足10个, 不判定不动作(维持10样本门槛)
    if not _hitrate_escalate(sel.provider, cur_rate):
        return sel  # L-06两级: 仅告警档, 未连续两窗口, 不升级熔断
    for fc in sel.fallback_chain:
        try:
            prov, model = fc.split("/", 1)
        except ValueError:
            continue
        cand_rate = _provider_cache_hit_rate(prov)
        if cand_rate is None:
            continue
        if cand_rate - (cur_rate if cur_rate is not None else 0.0) >= HITRATE_SWAP_GAP:
            old_provider, old_model = sel.provider, sel.model
            sel.provider, sel.model = prov, model
            _log_event({"type": "hitrate_feedback_switch", "from": cur_rate,
                        "to": cand_rate, "provider": prov, "model": model})
            # R5: 记录命中率驱动的provider切换
            try:
                from lao.effect_anchored.routing.switch_audit import SwitchAuditor as _SA, SwitchAuditEntry as _SAE
                _SA().record(_SAE(
                    task_type=sel.tier,
                    from_provider=old_provider, from_model=old_model,
                    to_provider=prov, to_model=model,
                    reason="hitrate_feedback",
                ))
            except Exception:
                pass
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
            # R5: 记录RIS驱动的provider切换
            try:
                from lao.effect_anchored.routing.switch_audit import SwitchAuditor as _SA, SwitchAuditEntry as _SAE
                _SA().record(_SAE(
                    from_provider=chosen_provider, to_provider=cand,
                    reason="ris_block",
                ))
            except Exception:
                pass
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
    """获取模型能力(默认按 default 兜底)。plain名与-0731后缀都能命中flash键。"""
    for k in ("deepseek-v4-flash", "deepseek-v4-pro"):
        if k in model:
            return ProviderCapabilityRegistry[k]
    return ProviderCapabilityRegistry["default"]


# ── 官方CNY直读计价(官方账单CSV price列 = 单一source of truth · 事故#001行动项#2) ──
# 2026-08-18 Stella派单/Nova方案: 禁止硬编码价表·禁止USD价×汇率换算
# (旧USD×7.1路径偏差[V·6-cell]: miss +56.2% / out +134.3% / pro hit +524.8%)
# 统一价 [V·8/10-16官方amount.csv三源交叉]: flash hit¥0.02/M·miss¥1/M·out¥2/M; pro hit¥0.025/M·miss¥3/M·out¥6/M
# 峰价(2026-08-17生效): 不预先硬编码 — 8/17官方CSV到账后由price列按(日期+CST小时)自动解析
PEAK_VALLEY_EFFECTIVE = "20260817"  # 峰谷制生效日(含)·按CST日期切换
FX_USD_CNY = 7.1  # 仅用于USD预算口径换算(_record_cost)·不再参与计价
_CST = _tzone(_tdelta(hours=8))
_CST_PEAK_HOURS = frozenset(("09","10","11","14","15","16","17"))  # 峰窗口 CST 09-12,14-18
_CSV_PRICE_DIRS = (
    "/home/agentuser/shared/tkee/ledger/extracted",  # Nova official-csv-reconcile管道产出(权威)
    "/home/agentuser/shared/state/usage-csv-0812",
)
_CSV_UNIFIED_FALLBACK = {  # 官方8/16 CSV冻结副本[V]·仅CSV管道不可读时兜底(log WARNING)·非常规价源
    # 键用官方CSV的plain模型名(计费口径)·API模型ID(deepseek-v4-flash-0731)在_lookup_key归一
    "deepseek-v4-flash": {"hit": 0.02, "miss": 1.0, "output": 2.0},
    "deepseek-v4-pro":   {"hit": 0.025, "miss": 3.0, "output": 6.0},
}
_csv_price_cache: Dict[str, Any] = {"loaded_at": 0.0, "unified": None, "peak": None}
_csv_price_lock = threading.Lock()


def _classify_price_type(ptype: str):
    if "cache_hit" in ptype: return "hit"
    if "cache_miss" in ptype or ptype == "input_tokens": return "miss"
    if "output" in ptype: return "output"
    return None


def _pricing_model_key(model: str) -> str:
    """归一到官方CSV计费口径的plain模型名(账单CSV只有plain名·无-0731后缀)."""
    m = (model or "").lower()
    return "deepseek-v4-pro" if "pro" in m else "deepseek-v4-flash"


def _pricing_model_key_is_deepseek(model: str) -> bool:
    """官方CSV model列匹配: plain名或-0731后缀都归入deepseek计费口径."""
    m = (model or "").lower()
    return m.startswith("deepseek-v4")


def _load_official_csv_prices(force: bool = False):
    """从官方账单CSV的price列解析(model→{hit,miss,output})¥/M价表; 返回(unified, peak). 缓存10分钟.
    date>=2026-08-17的行按CST小时窗口分峰/谷; 其余行入统一价表. CSV不可读→(None, {})."""
    import csv as _csv, glob as _glob
    with _csv_price_lock:
        now = time.time()
        if not force and _csv_price_cache["unified"] and now - _csv_price_cache["loaded_at"] < 600:
            return _csv_price_cache["unified"], _csv_price_cache["peak"]
        unified: Dict[str, Dict[str, float]] = {}
        peak: Dict[str, Dict[str, float]] = {}
        for d in _CSV_PRICE_DIRS:
            for path in sorted(_glob.glob(os.path.join(d, "amount-*.csv"))):
                try:
                    with open(path, newline="", encoding="utf-8") as f:
                        for row in _csv.DictReader(f):
                            model = (row.get("model") or "").strip()
                            cat = _classify_price_type(row.get("type") or "")
                            date = (row.get("start_time_iso") or "")[:10]
                            if not _pricing_model_key_is_deepseek(model) or not cat or not date:
                                continue
                            try:
                                yuan_per_m = float(row.get("price") or 0) * 1e6
                            except ValueError:
                                continue
                            if yuan_per_m <= 0:
                                continue
                            key = _pricing_model_key(model)  # plain名·与fallback/lookup键一致
                            if date >= "2026-08-17" and (row.get("start_time_iso") or "")[11:13] in _CST_PEAK_HOURS:  # FIX(2026-08-26): "time"列不存在，小时位取自start_time_iso
                                peak.setdefault(key, {})[cat] = yuan_per_m
                            else:
                                unified.setdefault(key, {})[cat] = yuan_per_m
                except OSError:
                    continue
        if unified:
            _csv_price_cache.update({"loaded_at": now, "unified": unified, "peak": peak})
        return unified or None, peak


def _pricing_now() -> tuple:
    """当前时刻(CST)的计价窗口: 返回 (regime, window)。
    日期<生效日→legacy; 否则 CST 09-12/14-18=peak, 其余=valley。"""
    now = _dt.now(_CST)
    if now.strftime("%Y%m%d") < PEAK_VALLEY_EFFECTIVE:
        return "legacy", "valley"
    h = now.hour
    return "peak_valley", ("peak" if (9 <= h < 12 or 14 <= h < 18) else "valley")


def _compute_cost_yuan(model: str, cache_hit: int, cache_miss: int, out_tok: int):
    """官方CNY直读计价: 直接从官方账单CSV price列读¥/M(单一source of truth)。

    hit/miss/out分档计价(hit不再与miss同价). Returns: (cost_yuan, pricing_regime, window, fx);
    fx=FX_USD_CNY仅用于USD预算口径换算(_record_cost).
    CSV管道不可读→8/16冻结官方价兜底(regime=official-csv-fallback·log WARNING).
    """
    regime, window = _pricing_now()
    k = _pricing_model_key(model)  # plain名: 官方CSV计费口径
    # C23(2026-08-25): token-plan 配额制(预付包) — 边际成本按0记账(配额消耗以token量留痕)
    if (model or "").lower() in _TOKEN_PLAN_MODELS:
        return (0.0, "token-plan-quota", window, FX_USD_CNY)
    try:
        unified, peak = _load_official_csv_prices()
    except Exception:
        unified, peak = None, {}
    table = None
    if regime == "peak_valley" and window == "peak" and peak.get(k):
        table = peak[k]
    if not table and unified and unified.get(k):
        table = unified[k]
    if not table:
        table = _CSV_UNIFIED_FALLBACK[k]
        logger.warning("official CSV prices unavailable; using frozen 8/16 prices for %s", k)
        return ((cache_hit * table.get("hit", 0.0) + cache_miss * table.get("miss", 0.0)
                 + out_tok * table.get("output", 0.0)) / 1e6, "official-csv-fallback", window, FX_USD_CNY)
    cost_yuan = (cache_hit * table.get("hit", 0.0) + cache_miss * table.get("miss", 0.0)
                 + out_tok * table.get("output", 0.0)) / 1e6
    return cost_yuan, "official-csv", window, FX_USD_CNY


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
    # C19(2026-08-23): thinking 块规范化(content数组 → reasoning_content 字段)
    _normalize_thinking_blocks(payload.get("messages", []))
    # C19: DeepSeek v4 系为思考模式(reasoning_content 必须随历史回传) → 保留字段;
    #      其他上游(qwen/token-plan 降级通道)照旧全清, 避免 unknown-field 400。
    if not thinking_enabled and not str(chosen_model).startswith("deepseek-"):
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
    # 历史剪枝: 保留 system + 前段稳定历史 + 尾段当前请求
    if len(out) > max_history:
        # 修复(2026-08-16 三层审计): 旧实现 out[:max_history] 会丢掉最末尾的
        # 当前用户消息 → 请求语义被改变 + 前缀与客户端预期错位(伤命中率)。
        # C18(2026-08-23): 配对感知剪枝 — 旧版 out[:max_history-1]+out[-1:] 的两个
        # 切点若落在 assistant(tool_calls)/tool 配对中间 → 孤儿消息 → DeepSeek 400
        # "Messages with role 'tool' must be a response to a preceding message with
        # 'tool_calls'"(shuyu 记忆工具链实测触发, 09:52 三连 400)。切点必须落在
        # 回合边界: 切点前一条不能是未闭合的 tool_calls-assistant, 切点后一条
        # 不能是 tool。正确性优先: 极端长工具链下允许剪枝退化(保整段)。
        def _opens_pair(m):
            return isinstance(m, dict) and m.get("role") == "assistant" and bool(m.get("tool_calls"))

        def _is_tool(m):
            return isinstance(m, dict) and m.get("role") == "tool"

        # 尾段: 从末条回溯, 把未闭合的 tool 结果与其配对 assistant 整段带上
        tail = len(out) - 1
        while tail > 0 and _is_tool(out[tail]):
            tail -= 1
        # 尾段起点前一条若也是开启配对的 assistant(连续工具轮) → 继续向前扩展
        while tail > 1 and _opens_pair(out[tail - 1]):
            tail -= 1
        # 头段: 预算 max_history-1 内回退到安全切点
        cut = min(max_history - 1, tail)
        while cut > 1 and (_is_tool(out[cut]) or _opens_pair(out[cut - 1])):
            cut -= 1
        out = out[:cut] + out[tail:]
    return out


def _normalize_thinking_blocks(messages: list):
    """C19(2026-08-23): assistant content 数组里的 thinking 块规范化。

    背景(ral-b 400 死循环根因):
    - DeepSeek v4 思考模式输出 reasoning_content → OpenClaw 存为 content 数组
      里的 thinking 块(thinkingSignature=reasoning_content)回传。
    - DeepSeek API 不认 content 数组里的 thinking 变体
      → 400 "unknown variant `thinking`, expected one of `text`,`image_url`..."
      (或思考痕迹被剥后) 400 "reasoning_content ... must be passed back"。
    根治: thinking 块文本 → 消息顶层 reasoning_content 字段(DeepSeek 标准回传格式),
          content 数组只留 text 等标准块; DeepSeek 通道保留该字段不 strip。
    """
    for m in messages:
        if not (isinstance(m, dict) and m.get("role") == "assistant"):
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        thinks = [b for b in c if isinstance(b, dict) and b.get("type") == "thinking"]
        if not thinks:
            continue
        others = [b for b in c if not (isinstance(b, dict) and b.get("type") == "thinking")]
        rc = "\n".join(t.get("thinking") or "" for t in thinks if t.get("thinking"))
        if rc:
            prev = m.get("reasoning_content") or ""
            m["reasoning_content"] = (prev + "\n" + rc).strip() if prev else rc
        m["content"] = others if others else ""
        if not m.get("content") and not m.get("tool_calls"):
            m["content"] = ""
    return messages

def _ensure_toolcall_rc(payload):
    """C22v2-20260823: deepseek thinking 模式在 tool 续传轮要求**最后一条
    user 之后的全部 assistant**回传 reasoning_content (含纯文本 assistant·
    diagnose61 X3 实测: 只补 [28] 纯文本 assistant 即 200)·
    OpenClaw 出站全部不带 → 统一注入占位 (X5 全量注入 200·X9 user结尾安全)"""
    msgs = payload.get("messages") or []
    n = 0
    for m in msgs:
        if (m.get("role") == "assistant"
                and not m.get("reasoning_content")):
            m["reasoning_content"] = "(elided)"
            n += 1
    return n


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
    # r3: 跨天同步重置任务级台账(新的一天·任务 token 从零计)
    try:
        with _r3_lock:
            _r3_rollover_locked()
    except Exception:
        pass


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


# ── r3 任务级成本护栏(盲点1: token上限告警+熔断 · 盲点3: 重试计数上限) ──
_r3_lock = threading.Lock()
_r3_state: Dict[str, Any] = {"tasks": {}, "date": time.strftime("%Y-%m-%d")}


def _r3_load_ledger():
    """启动时恢复当日台账(跨重启不丢熔断状态)。"""
    global _r3_state
    try:
        with open(R3_LEDGER, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("date") == time.strftime("%Y-%m-%d"):
            _r3_state = d
    except Exception:
        pass


def _r3_save_ledger_locked():
    try:
        with open(R3_LEDGER, "w", encoding="utf-8") as f:
            json.dump(_r3_state, f, ensure_ascii=False)
    except Exception:
        pass


def _r3_rollover_locked():
    today = time.strftime("%Y-%m-%d")
    if _r3_state.get("date") != today:
        _r3_state["date"] = today
        _r3_state["tasks"] = {}
        _r3_save_ledger_locked()


def _r3_task_key(agent: str, session_fp: str) -> str:
    return f"{agent or 'unknown'}|{session_fp or 'nofp'}"


def _r3_add_tokens(agent: str, session_fp: str, tokens: int):
    """结算时累计任务 token · 越限即打告警/熔断标记(下一次请求入口拦截)。"""
    if tokens <= 0:
        return
    key = _r3_task_key(agent, session_fp)
    with _r3_lock:
        _r3_rollover_locked()
        t = _r3_state["tasks"].setdefault(
            key, {"tokens": 0, "alerted": False, "broken": False})
        t["tokens"] += int(tokens)
        crossed_alert = (not t["alerted"]) and t["tokens"] >= R3_TASK_ALERT_TOKENS
        crossed_hard = (not t["broken"]) and t["tokens"] >= R3_TASK_HARD_TOKENS
        if crossed_alert:
            t["alerted"] = True
        if crossed_hard:
            t["broken"] = True
        _r3_save_ledger_locked()
    if crossed_alert:
        _log_event({"type": "r3_token_alert", "agent": agent or "unknown",
                    "task_key": key, "tokens": t["tokens"],
                    "threshold": R3_TASK_ALERT_TOKENS})
    if crossed_hard:
        _log_event({"type": "r3_token_breaker", "agent": agent or "unknown",
                    "task_key": key, "tokens": t["tokens"],
                    "threshold": R3_TASK_HARD_TOKENS,
                    "action": "subsequent_requests_rejected_429"})


def _r3_task_guard(key: str, agent: str, request_id: str):
    """入口拦截: 已熔断的任务立即 429(熔断到停止计费即时生效)。"""
    with _r3_lock:
        _r3_rollover_locked()
        t = _r3_state["tasks"].get(key)
        tokens = t["tokens"] if t else 0
        broken = bool(t and t.get("broken"))
    if broken:
        _log_event({"type": "r3_task_rejected", "request_id": request_id,
                    "agent": agent or "unknown", "task_key": key,
                    "tokens": tokens, "threshold": R3_TASK_HARD_TOKENS})
        return JSONResponse(
            {"error": {"message": (
                f"task token budget exhausted ({tokens} >= {R3_TASK_HARD_TOKENS}); "
                "task circuit-broken by LAO r3 guard; "
                "founder order 2026-08-25: stop billing immediately"),
                "type": "lao_router_task_breaker",
                "lao_task_tokens": tokens}},
            status_code=429)
    return None


def _r3_retry_check(key: str, agent: str, request_id: str,
                    messages: List[Dict], model_hint: str):
    """盲点3: 同会话同 payload 10 分钟内重发 = 重试 · 计数上报 · 超限熔断。"""
    import hashlib
    try:
        _canon = json.dumps({"m": messages, "model": model_hint},
                            ensure_ascii=False, sort_keys=True, default=str)
        ph = hashlib.sha1(_canon.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None
    now = time.time()
    with _r3_lock:
        _r3_rollover_locked()
        t = _r3_state["tasks"].setdefault(
            key, {"tokens": 0, "alerted": False, "broken": False})
        rp = t.get("retry_payload", "")
        rts = float(t.get("retry_ts", 0.0))
        cnt = int(t.get("retry_count", 0))
        if rp == ph and (now - rts) <= 600:
            cnt += 1
        else:
            cnt = 0
        t["retry_payload"], t["retry_ts"], t["retry_count"] = ph, now, cnt
        _r3_save_ledger_locked()
    if cnt > 0:
        _log_event({"type": "r3_retry", "request_id": request_id,
                    "agent": agent or "unknown", "task_key": key,
                    "retry_seq": cnt, "max": R3_MAX_RETRIES})
    if cnt > R3_MAX_RETRIES:
        _log_event({"type": "r3_retry_breaker", "request_id": request_id,
                    "agent": agent or "unknown", "task_key": key,
                    "retry_seq": cnt, "max": R3_MAX_RETRIES})
        return JSONResponse(
            {"error": {"message": (
                f"identical request retried {cnt} times within 10min "
                f"(limit {R3_MAX_RETRIES}); blocked by LAO r3 retry guard"),
                "type": "lao_router_retry_breaker",
                "lao_retry_count": cnt}},
            status_code=429)
    return None


def _r3_status():
    with _r3_lock:
        tasks = {k: dict(v) for k, v in _r3_state.get("tasks", {}).items()}
        date = _r3_state.get("date")
    top = sorted(tasks.items(), key=lambda kv: -kv[1].get("tokens", 0))[:10]
    return {"date": date,
            "thresholds": {"alert": R3_TASK_ALERT_TOKENS,
                           "hard": R3_TASK_HARD_TOKENS,
                           "max_retries": R3_MAX_RETRIES},
            "top_tasks": [{"task_key": k, "tokens": v.get("tokens", 0),
                           "alerted": v.get("alerted", False),
                           "broken": v.get("broken", False)} for k, v in top]}


def _model_quality(model: str, tier: str) -> float:
    """从 MODEL_POOL 查所选模型质量分(供 SavingsEngine 证据链)。"""
    for e in router.MODEL_POOL.get(tier, []):
        if e.get("model") == model:
            return float(e.get("quality", 0.0))
    return 0.0




# ── W6: 退回 LLM 重推理机制(2026-08-19 创始人令·LAO接线·max 2次) ─────────
async def _rethink_and_revalidate(client, payload, resp, task_text, tier, agent,
                                  max_retries=2):
    """出站验证失败 → 追加修正指令重新请求 LLM（最多 max_retries 次）。

    Args:
        client: OpenAI client 实例。
        payload: 原始请求 payload(dict)。
        task_text: 任务文本。
        tier: 任务层级。
        agent: agent 标识。
        max_retries: 最大重推理次数(默认 2)。

    Returns:
        (resp, validation_result): (最终响应, 验证标记 dict)。
        validation_result: {"lao_validation": "passed"|"repaired_after_N"|"failed_after_retries",
                            "retries": int, "violations": list}
    """
    import copy as _copy
    retries = 0
    violations = []
    _retry_usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}  # P1-9: 重试usage累计
    _validation_result = {"lao_validation": "passed", "retries": 0, "violations": [], "retry_usage": _retry_usage_total}
    while True:
        # 验证当前内容
        _content = ""
        try:
            _content = resp.choices[0].message.content or ""
        except Exception:
            _content = str(resp)
        _passed = True
        _vios = []
        if HALL_GATE is not None:
            try:
                _hr = HALL_GATE.check(_content, context={"task": task_text, "tier": tier, "agent": agent or ""})
                if _hr is not None and not getattr(_hr, "passed", True):
                    _passed = False
                    _vios = list(getattr(_hr, "anchors_violated", []) or [])
            except Exception:
                pass
        if REALITY is not None:
            try:
                _ev_cnt = 0
                _kw = 0
                if FACTS is not None:
                    _facts = FACTS.query_facts(agent or "unknown")
                    _ft = " ".join(str(f) for f in _facts)
                    _kw = sum(1 for _w in _ft.split() if _w and _w in _content)
                    _ev_cnt = min(len(_facts), 5)
                _rev = REALITY.evaluate(answer_id="rethink", evidence_count=_ev_cnt,
                                        trusted_sources=1, unknown_assumptions=0,
                                        experience_keys=[], keyword_matches=_kw)
                if getattr(_rev, "verification_state", None) == "unverified" \
                        and float(getattr(_rev, "confidence_score", 0) or 0) < 50:
                    _passed = False
            except Exception:
                pass
        if _passed:
            _validation_result = {"lao_validation": ("repaired_after_%d_retries" % retries) if retries else "passed",
                                  "retries": retries, "violations": _vios,
                                  "retry_usage": _retry_usage_total}
            return resp, _validation_result
        if retries >= max_retries:
            _validation_result = {"lao_validation": "failed_after_retries",
                                  "retries": retries, "violations": _vios,
                                  "retry_usage": _retry_usage_total}
            return resp, _validation_result
        # 追加修正指令 → 重新请求
        retries += 1
        violations = _vios
        _fix = {"role": "user",
                "content": "【LAO验证反馈】上轮回复存在事实偏差: %s。请修正后重新回答。" % ("; ".join(_vios) if _vios else "内容与已知事实不符")}
        _payload2 = _copy.deepcopy(payload)
        _msgs = list(_payload2.get("messages", [])) + [_fix]
        _payload2["messages"] = _msgs
        resp = await asyncio.to_thread(client.chat.completions.create, **_payload2)
        # P1-9: 累计重试 usage(真实花费入账)
        try:
            _ru = getattr(resp, "usage", None)
            if _ru is not None:
                _retry_usage_total["prompt_tokens"] += int(getattr(_ru, "prompt_tokens", 0) or 0)
                _retry_usage_total["completion_tokens"] += int(getattr(_ru, "completion_tokens", 0) or 0)
                _retry_usage_total["total_tokens"] += int(getattr(_ru, "total_tokens", 0) or 0)
        except Exception:
            pass

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
    # r3 盲点1: 任务级 token 累计(越限告警/熔断由下一次请求入口拦截·fail-open 不阻塞结算)
    try:
        _r3_add_tokens(agent, session_fp, int(in_tok or 0) + int(out_tok or 0))
    except Exception:
        pass
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
        # "saving_usd" 已全线停用(2026-08-26 创始人裁定): 口径证伪·非汇率换算值; 对外一律 ¥ 口径
        "fallback_chain": sel.fallback_chain,
        "capability_events": cap_events,   # Phase A/B: 参数过滤事件(TrustEvent 链)
        "session_fp": session_fp,
    })
    # B2 反向桥: 路由/降级/成本/缓存结果 → lao-signal.json(RIS 消费·双向飞轮)
    # FIX(2026-08-26 C23全量接入): W6验证失败(status=retry)转发已成功, 不计provider错误, 防RIS误熔断
    _sig_ok = (status == "ok") or (status == "retry")
    _update_lao_signal(provider, ok=_sig_ok,
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
    # 口径标注(2026-08-26 创始人裁定): 纯内存估算，仅覆盖最近一次进程重启后的窗口；
    # original_cost 为硬编码反事实基线，非官方价目；不得作为省钱证据对外引用。
    """M6: LAO Impact Report(供 Nova/Stella/Dashboard 消费)。"""
    rep = dict(savings_engine.impact_report())
    rep["disclaimer"] = ("in-memory estimate; covers ONLY the window since the last process restart; "
                         "original_cost is a hardcoded counterfactual baseline, NOT official pricing; "
                         "not an audited cost-saving figure / 仅覆盖重启后窗口，内存估算，非官方对账口径")
    return rep


@app.get("/v1/loop/status")
def loop_status():
    """三层Loop状态(L1命中率↔L2经验工厂↔L3确权·审计/Dashboard 消费)。"""
    if LOOP is None:
        return {"enabled": False}
    try:
        return {"enabled": True, **LOOP.status()}
    except Exception as e:
        return {"enabled": True, "error": str(e)}


@app.get("/v1/r3/status")
def r3_status():
    """r3(2026-08-25 创始人命令): 任务级 token 台账与熔断状态(监控消费)。"""
    try:
        return _r3_status()
    except Exception as e:
        return {"error": str(e)}


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

    # v3.5.1-wiring: W1 入站萃取·请求事件记录(上下文重建证据链)
    if CONTEXT_REBUILDER is not None:
        try:
            CONTEXT_REBUILDER.record(CRB_Event(
                event_id=uuid.uuid4().hex[:12],
                timestamp=_dt.now(_tzone.utc).isoformat(),
                speaker=agent or "unknown",
                event_type="request",
                subject=tier or model_hint or "chat",
                summary=str(messages[-1].get("content", ""))[:5000] if messages else "",
                anchor_keys=[tier] if tier else [],
            ))
        except Exception:
            pass  # fail-open·不阻塞路由

    # v3.5.1-wiring: W2 认知锚定·查询历史认知注入 context
    if ANCHOR_MEMORY is not None:
        try:
            _task_text = ""
            if messages:
                _last = messages[-1].get("content", "")
                _task_text = _last if isinstance(_last, str) else str(_last)
            _anchor_key = None
            for _kw in ("创始人", "门店", "预算", "用户", "基础设施"):
                if _kw in _task_text:
                    _anchor_key = _kw
                    break
            if _anchor_key:
                _mres = ANCHOR_MEMORY.lookup(_anchor_key)
                if _mres.found:
                    context = locals().get("context", {})
                    context["memory_anchor"] = {"found": True, "value": _mres.value, "anchor_key": _anchor_key}
        except Exception:
            pass  # fail-open·不阻塞路由

    # v3.5.1-wiring: W2.5 认知模式匹配·影响 W3 经验直答双重确认
    _cognitive_match = None
    if COGNITIVE is not None:
        try:
            _task_text = ""
            if messages:
                _last = messages[-1].get("content", "")
                _task_text = _last if isinstance(_last, str) else str(_last)
            _cognitive_match = COGNITIVE.match_cognitive_pattern(agent or "unknown", _task_text)
        except Exception:
            _cognitive_match = None  # fail-open

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

    # r3 盲点1/3(2026-08-25 创始人命令): 任务级 token 熔断 + 重试上限
    # 前置拦截 — 已熔断任务立即 429(熔断到停止计费即时生效·不产生任何 token 花费)
    _r3_key = _r3_task_key(agent, session_fp)
    try:
        _r3_blk = _r3_task_guard(_r3_key, agent, request_id)
        if _r3_blk is not None:
            return _r3_blk
        _r3_rblk = _r3_retry_check(_r3_key, agent, request_id, messages, model_hint)
        if _r3_rblk is not None:
            return _r3_rblk
    except Exception:
        pass  # fail-open: 护栏自身故障不得阻塞路由(但会在日志缺失护栏事件·监控侧可查)

    # v3.5.1-wiring: W3 经验直答·双重确认(经验匹配 + 认知匹配 才全短路·省100%成本)
    _exp_match = None
    if LOOP is not None and task_text:
        try:
            _exp_match = LOOP.match_experience(task_text, tier=tier, agent=agent or "")
        except Exception:
            _exp_match = None  # fail-open
    if _exp_match is not None and _exp_match.get("confidence", 0) >= 0.8:
        if _cognitive_match is not None:
            # 双重确认 → 全短路·直接返回经验答案(不请求 LLM·省 100% 成本)
            _answer = _exp_match.get("answer", "")
            # P1-10 修复(2026-08-19): 短路落观察事件(证据链可观测)+认知层经验复利
            try:
                await asyncio.to_thread(_log_event, {
                    "type": "experience_shortcircuit",
                    "request_id": request_id, "agent": agent or "unknown",
                    "tier": tier, "model": model_hint or "lao-experience",
                    "experience_key": _exp_match.get("experience_key", ""),
                    "confidence": _exp_match.get("confidence", 0),
                    "cost_saved": "100%", "latency_ms": int((time.time() - started) * 1000),
                })
            except Exception:
                pass
            try:
                if LOOP is not None and hasattr(LOOP.bus, "cognitive"):
                    LOOP.bus.cognitive.L1.on_success(
                        _exp_match.get("experience_key", ""), delta=0.3)
            except Exception:
                pass
            return JSONResponse({
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_hint or "lao-experience",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": _answer},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "lao_validation": {"source": "experience_shortcircuit",
                                    "experience_key": _exp_match.get("experience_key", ""),
                                    "confidence": _exp_match.get("confidence", 0)},
            })
        # 半短路: 经验命中但无认知匹配 → 附加到 messages 提精度(继续请求 LLM)
        try:
            _exp_hint = (f"【LAO经验参考】历史确权经验: {_exp_match.get('answer', '')}"
                         f" (confidence={_exp_match.get('confidence', 0):.2f})")
            if messages and isinstance(messages[-1], dict):
                _c = messages[-1].get("content", "")
                if isinstance(_c, str):
                    messages[-1]["content"] = _c + "\n\n" + _exp_hint
        except Exception:
            pass

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

    # C14(2026-08-23): 显式模型直通(修正版·置于粘性之后) — 请求 model 的模型名
    # 在动态池中存在时锁定 (provider, model), 覆盖效果锚定与粘性的自主改写。
    # 优先 provider=deepseek 条目(官方通道对 deepseek-* 模型名最可靠)。
    # 此前: 自主改写(deepseek-v4-flash→qwen-plus)+RIS摘除qwen后仅换provider不换
    # 模型名 → qwen系模型名发阿里云/官方 → 400/401 连环失败。
    _hint_model = model_hint.split("/")[-1] if model_hint else ""
    if _hint_model:
        _cands = []
        for _pool in router.MODEL_POOL.values():
            for _pe in _pool:
                if _pe.get("model") == _hint_model:
                    _cands.append(_pe)
        if _cands:
            _hit = next((_pe for _pe in _cands if _pe.get("provider") == "deepseek"), _cands[0])
            chosen_model = _hit["model"]
            chosen_provider = _hit.get("provider", chosen_provider)

    # C23(2026-08-25): 大上下文守门(成本-效率平衡点) — token-plan 窗口131072/maxOut8192,
    # 预估输入 context_tokens>100k 的请求自动回 deepseek-v4-flash(1M窗口/384k输出)。
    # 实测2026-08-24: 0.4%请求(stella/tristan/baron大会话, 最大262k)超131k会跑不通。
    if chosen_provider == "token-plan" and context_tokens > 100000:
        chosen_provider = "deepseek"
        chosen_model = "deepseek-v4-flash"

    # B2/B5: RIS 健康门——被 RIS 阻断(down/isolated)的 provider 摘出候选·降级切换;
    # 全部候选被阻断 → 显式 503(禁止静默 fallback·ProviderHealthGate 哲学)
    chosen_provider, _block_ev = _ris_guard_provider(chosen_provider, request_id)
    if chosen_provider is None:
        return JSONResponse(
            {"error": {"message": "all upstream providers blocked by RIS isolation",
                       "type": "lao_router_ris_gate"}},
            status_code=503)

    # C16(2026-08-23): per-agent key 边界 — 识别到 Agent 且有独立 key 时锁定 DeepSeek
    # 官方通道, 覆盖效果锚定/粘性/RIS 降级的 provider 逃逸(novarouteai/qwen), 保证
    # DeepSeek 后台按 key 归因每个 Agent 的真实调用; 模型名同步锁 deepseek 系防错配。
    if agent and AGENT_KEYS.get(agent):
        # C23(2026-08-25): token-plan 放行口(创始人令切换) — 归因不受影响:
        # 身份已在 LAO 入口按 per-agent key 反查完成; 上游用 PROVIDER_CONFIG 的 token-plan key。
        _c23_tp = (chosen_provider == "token-plan" and chosen_model in _TOKEN_PLAN_MODELS)
        if not ((chosen_provider == "deepseek" and chosen_model.startswith("deepseek-")) or _c23_tp):
            chosen_provider = "deepseek"
            chosen_model = _hint_model if (_hint_model or "").startswith("deepseek-") else "deepseek-v4-flash"

    # ③ 转发真实 provider(按 chosen_provider 动态选 base_url + key·白名单过滤+能力协商)
    client = _provider_client(chosen_provider, agent)
    payload, cap_events = _safe_payload(body, chosen_model)
    # C22v2-20260823: deepseek thinking 强制 assistant 回传 rc → 注入占位
    if chosen_provider == "deepseek" or str(chosen_model).startswith("deepseek-"):
        _ensure_toolcall_rc(payload)
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
        # C18(2026-08-23): 上游 4xx(请求本身无效)透传真实状态码。旧版统一 502 →
        # OpenClaw failover 归类 timeout → provider 进 cooldown → 后续请求全拒
        # (FallbackSummaryError "in cooldown")。4xx 透传让网关判定 invalid_request
        # 不触发 cooldown; 网络/服务类错误仍按 502。
        _status = 502
        try:
            _sc = int(getattr(e, "status_code", 0) or 0)
            if 400 <= _sc < 500:
                _status = _sc
        except Exception:
            pass
        return JSONResponse({"error": {"message": str(e), "type": "lao_router_forward"}}, status_code=_status)

    latency_ms = int((time.time() - started) * 1000)

    # ③.5 流式响应: 必须流式转发(OpenClaw 用 streaming)·否则 SSE 序列化失败
    if stream:
        # 命中率(Stella派单): 从流末尾 chunk 提取 usage(含 cache 字段)·stream 也要记录真实命中率
        stream_usage = {"input": 0, "output": 0, "hit": 0, "miss": 0}

        def _sse_gen():
            nonlocal stream_usage
            status, err = "ok", ""
            _acc_content = []  # v3.5.1-wiring: W7 累积流式内容供出站验证
            try:
                for chunk in resp:   # OpenAI Stream 迭代(Starlette 在线程池中迭代本生成器)
                    # 保留 OpenAI SSE 格式
                    yield "data: " + chunk.model_dump_json() + "\n\n"
                    # 累积内容(W7 验证用)
                    try:
                        _d = chunk.choices[0].delta.content
                        if _d:
                            _acc_content.append(str(_d))
                    except Exception:
                        pass
                    # 流末尾 chunk 带 usage(含 cache)·提取真实命中率数据
                    cu = getattr(chunk, "usage", None)
                    if cu is not None:
                        stream_usage["input"] = getattr(cu, "prompt_tokens", 0) or 0
                        stream_usage["output"] = getattr(cu, "completion_tokens", 0) or 0
                        stream_usage["hit"] = getattr(cu, "prompt_cache_hit_tokens", 0) or 0
                        stream_usage["miss"] = getattr(cu, "prompt_cache_miss_tokens", 0) or 0
                # v3.5.1-wiring: W7 流式出站验证(累积内容·只标记不重推理)
                _full = "".join(_acc_content)
                _vios = []
                if HALL_GATE is not None and _full:
                    try:
                        _hr = HALL_GATE.check(_full, context={"task": task_text, "tier": tier, "agent": agent or ""})
                        if _hr is not None and not getattr(_hr, "passed", True):
                            _vios = list(getattr(_hr, "anchors_violated", []) or [])
                    except Exception:
                        pass
                if _vios:
                    import json as _json
                    yield f"data: {_json.dumps({'lao_validation': {'passed': False, 'violations': _vios}})}" + "\n\n"
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

    # v3.5.1-wiring: W4 出站验证·非流式(转发后·返回前)
    _validation = None
    _validation_failed = False  # P0-2: 降级环境安全初始化(防 NameError)
    _content = ""  # P0-2: 降级环境安全初始化
    if HALL_GATE is not None:
        try:
            _content = ""
            try:
                _content = resp.choices[0].message.content or ""
            except Exception:
                _content = str(resp)
            _validation = HALL_GATE.check(
                _content, context={"task": task_text, "tier": tier, "agent": agent or ""})
            if _validation is not None and not getattr(_validation, "passed", True):
                # 验证失败 → 标记(重推理在 W6 补全)
                resp = resp  # 保留原始响应·W6 将在此处接入重推理
                _validation_failed = True
            else:
                _validation_failed = False
        except Exception:
            _validation_failed = False  # fail-open

    # v3.5.1-wiring: W5 出站验证·RealityCheck+UserFactBase(第二层)
    _reality_state = None
    if REALITY is not None and _validation_failed is False:
        try:
            _ev_cnt = 0
            _kw_matches = 0
            if FACTS is not None:
                _facts = FACTS.query_facts(agent or "unknown")
                _facts_text = " ".join(str(f) for f in _facts)
                if _facts_text:
                    _kw_matches = sum(1 for _w in _facts_text.split() if _w and _w in _content)
                _ev_cnt = min(len(_facts), 5)
            _rev = REALITY.evaluate(
                answer_id=request_id, evidence_count=_ev_cnt, trusted_sources=1,
                unknown_assumptions=0, experience_keys=[], keyword_matches=_kw_matches)
            _reality_state = getattr(_rev, "verification_state", None)
            _conf = float(getattr(_rev, "confidence_score", 0) or 0)
            if _reality_state == "unverified" and _conf < 50:
                _validation_failed = True  # 无事实支撑 → 标记(重推理 W6)
        except Exception:
            pass  # fail-open

    # v3.5.1-wiring: W6 验证失败 → 退回 LLM 重推理(max 2 次·非流式)
    if _validation_failed:
        try:
            _rr = await _rethink_and_revalidate(
                client, payload, resp, task_text, tier, agent or "", max_retries=2)
            resp, _val_mark = _rr
            # P1-9 修复(2026-08-19): 重试真实花费入账(预算红线+证据链)
            _ru = _val_mark.get("retry_usage") or {}
            if _ru.get("total_tokens"):
                try:
                    await asyncio.to_thread(
                        _settle_and_log,
                        request_id=request_id, tier=tier, agent=agent, model_hint=model_hint,
                        chosen_model=chosen_model, provider=chosen_provider, sel=sel, budget=budget,
                        in_tok=_ru.get("prompt_tokens", 0), out_tok=_ru.get("completion_tokens", 0),
                        cache_hit=0, cache_miss=0, stream=False, status="retry",
                        error=_val_mark.get("lao_validation", ""),
                        latency_ms=0, cap_events=cap_events, session_fp=session_fp)
                except Exception as _se:
                    logger.warning(f"W6 重试记账失败: {_se}")
        except Exception as _rr_e:
            logger.warning(f"W6 重推理失败: {_rr_e}")

    return resp


if __name__ == "__main__":
    _r3_load_ledger()  # r3: 恢复当日任务级 token 台账(跨重启不丢熔断状态)
    logger.info(f"lao-router 启动: :{PORT} | DeepSeek基址={DEEPSEEK_BASE} | key={'$'*8 if DEEPSEEK_KEY else 'MISSING'} | 每日预算=${DAILY_BUDGET}")
    logger.info(f"r3 护栏: 任务告警={R3_TASK_ALERT_TOKENS} tokens · 熔断={R3_TASK_HARD_TOKENS} tokens · 重试上限={R3_MAX_RETRIES} 次(创始人命令2026-08-25)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
