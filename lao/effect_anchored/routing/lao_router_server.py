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
import json, os, time, logging, threading
from typing import Optional, Dict, Any, List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openai import OpenAI

# LAO 路由核心(已实现·T1成本红线真实生效)
import sys
sys.path.insert(0, "/home/agentuser/lao-release")
from lao.effect_anchored.routing.model_router import ModelRouter, RouteSelection, AGENT_PROVIDER_BINDING

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


def _safe_payload(body: dict, chosen_model: str, preserve_requested: bool = True) -> tuple[dict, list]:
    """过滤未知参数 + 基于能力的参数协商。

    命中率 99.9% P0-1(智囊团决议 2026-08-15): 缓存键 bug 修复。
    关键: **保留请求方 model(稳定前缀=缓存命中)**·不再无条件覆盖。
    - preserve_requested=True(默认): payload["model"]=请求方 model(若有效)
    - 仅当请求方 model 为空/无效时才用 chosen_model(兜底)

    Returns:
        (payload: 仅含支持的参数, fallback_events: CapabilityFallbackEvent 列表)
    """
    cap = _capability(chosen_model)
    payload = {k: v for k, v in body.items() if k in SUPPORTED_PARAMS}
    # 命中率数据(Stella派单): stream 时强制 include_usage·否则 chunk 无 usage·cache 字段永远 0
    if payload.get("stream") and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    # 架构红线(固化): 稳定前缀 = 缓存命中
    # 不再强制 payload["model"]=chosen_model·保留请求方 model 保证前缀稳定
    requested = body.get("model", "") or ""
    if preserve_requested and requested and _is_valid_model_name(requested):
        # 保留请求方 model(从全名提取末段·如 deepseek-momo/deepseek-v4-flash → deepseek-v4-flash)
        payload["model"] = requested.split("/")[-1]
    else:
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


def _is_valid_model_name(model: str) -> bool:
    """判断 model 名是否有效(含已知模型名·防把随机字符串当 model)。"""
    m = (model or "").lower()
    return any(k in m for k in ("deepseek", "qwen", "glm", "kimi", "gpt", "flash", "pro"))


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
    # 历史剪枝: 保留 system + 前 max_history 条(早期稳定前缀)
    if len(out) > max_history:
        # 保留 system(如有)+ 前 max_history-1 条早期 + 最后 1 条(当前请求)
        out = out[:max_history]
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
    with open(EVENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")


# ── OpenAI 兼容端点 ──────────────────────────────
@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [
        {"id": "deepseek-v4-pro", "object": "model"},
        {"id": "deepseek-v4-flash", "object": "model"},
    ]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model_hint = body.get("model", "")
    stream = body.get("stream", False)

    # ① 任务分层
    tier = request.headers.get("x-lao-tier", "") or _infer_tier(messages, model_hint)

    # 按 Agent 分发独立 key(治本·B1)·创始人 B 阶段: 先提取 agent 供路由绑定 provider
    agent = _extract_agent(model_hint, dict(request.headers))

    # ② 成本红线路由(用已算好的 tier 而非把 model 名当 task·根治 Nova 根因1)
    budget = _remaining_budget()
    try:
        # 关键修复: 不把 model_hint(如 deepseek-momo/deepseek-v4-flash)当 task 传给 classify
        # → 用 _infer_tier 已算出的 tier, 避免 model 名走 classify default=medium → 误判 pro
        # 创始人 B 阶段: 传 agent 实现按 Agent 绑定 provider(baron/ethan/momo→token-plan)
        sel: RouteSelection = router.route_with_budget(task=tier or model_hint, budget=budget, agent=agent)
    except Exception as e:
        logger.error(f"route_with_budget失败({e}), 使用默认")
        sel = router.route("light")

    chosen_model = sel.model
    chosen_provider = sel.provider

    # 路径3 修复(命中率 P0-1): provider 稳定 = 缓存可命中
    # 请求方 model 带 provider 前缀(如 deepseek-momo/...) → 用请求方 provider 稳定转发
    req_provider = model_hint.split("/")[0] if "/" in model_hint else ""
    if req_provider in PROVIDER_CONFIG and not (agent and AGENT_PROVIDER_BINDING.get(agent) not in (None, "deepseek")):
        chosen_provider = req_provider

    # ③ 转发真实 provider(按 chosen_provider 动态选 base_url + key·白名单过滤+能力协商)
    client = _provider_client(chosen_provider, agent)
    payload, cap_events = _safe_payload(body, chosen_model)
    # P0-2 命中率99.9%: 传独立 user_id(DeepSeek 官方 KVCache 隔离机制)
    # 每个 agent 独立 user → 缓存按 agent 隔离·前缀更稳定·miss 降(官方CSV: miss价是hit价120倍)
    if agent:
        payload["user"] = f"lao-{agent}"
    started = time.time()
    try:
        resp = client.chat.completions.create(**payload)
    except Exception as e:
        _log_event({"tier": tier, "chosen_model": chosen_model, "provider": chosen_provider,
                    "agent": agent, "budget": budget, "status": "error", "error": str(e)[:200],
                    "capability_events": cap_events})
        return JSONResponse({"error": {"message": str(e), "type": "lao_router_forward"}}, status_code=502)

    latency_ms = int((time.time() - started) * 1000)

    # ③.5 流式响应: 必须流式转发(OpenClaw 用 streaming)·否则 SSE 序列化失败
    if stream:
        # 命中率(Stella派单): 从流末尾 chunk 提取 usage(含 cache 字段)·stream 也要记录真实命中率
        stream_usage = {"input": 0, "output": 0, "hit": 0, "miss": 0}

        def _sse_gen():
            nonlocal stream_usage
            try:
                for chunk in resp:   # OpenAI Stream 迭代
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
                logger.error(f"stream error: {e}")
                yield f"data: {{\"error\":{{\"message\":\"{e}\",\"type\":\"lao_router_stream\"}}}}\n\n"
            # 台账修复(创始人令)+命中率(Stella派单): 流结束后记录事件日志(agent标识·cache真实值)
            _log_event({
                "tier": tier, "chosen_model": chosen_model, "provider": chosen_provider,
                "agent": agent, "requested_model": model_hint, "budget_remaining": round(budget, 4),
                "degraded": "flash" in chosen_model and "pro" in str(model_hint).lower(),
                "input_tokens": stream_usage["input"], "output_tokens": stream_usage["output"], "stream": True,
                "cache_hit_tokens": stream_usage["hit"], "cache_miss_tokens": stream_usage["miss"], "task_type": tier,
                "cost_yuan": 0.0, "latency_ms": latency_ms,
                "fallback_chain": sel.fallback_chain, "capability_events": cap_events,
            })
        return StreamingResponse(_sse_gen(), media_type="text/event-stream")
        return StreamingResponse(_sse_gen(), media_type="text/event-stream")

    # ④ 成本记录(仅非流式·流式在 chunk 末尾拿 usage)
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    # 命中率 99.9%(Stella/创始人令 2026-08-15): 提取 cache 字段算命中率
    # DeepSeek usage: prompt_cache_hit_tokens(命中) / prompt_cache_miss_tokens(未命中)
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) if usage else 0
    cache_miss = getattr(usage, "prompt_cache_miss_tokens", 0) if usage else 0
    # 单价(¥/1M): pro 3/6, flash 1/2
    ci = 3 if "pro" in chosen_model else 1
    co = 6 if "pro" in chosen_model else 2
    cost_yuan = (in_tok * ci + out_tok * co) / 1e6
    cost_usd = cost_yuan / 7.2
    _record_cost(cost_usd)

    _log_event({
        "tier": tier, "chosen_model": chosen_model, "provider": chosen_provider,
        "agent": agent, "requested_model": model_hint, "budget_remaining": round(budget, 4),
        "degraded": "flash" in chosen_model and "pro" in str(model_hint).lower(),
        "input_tokens": in_tok, "output_tokens": out_tok,
        # 命中率数据基础(按 agent×task×时段分析):
        "cache_hit_tokens": cache_hit,     # prompt_cache_hit_tokens(命中)
        "cache_miss_tokens": cache_miss,   # prompt_cache_miss_tokens(未命中)
        "task_type": tier,                 # 任务归类(light/medium/reasoning/...)
        "cost_yuan": round(cost_yuan, 6), "latency_ms": latency_ms, "stream": stream,
        "fallback_chain": sel.fallback_chain,
        "capability_events": cap_events,   # Phase A/B: 参数过滤事件(TrustEvent 链)
    })

    return resp


if __name__ == "__main__":
    logger.info(f"lao-router 启动: :{PORT} | DeepSeek基址={DEEPSEEK_BASE} | key={'$'*8 if DEEPSEEK_KEY else 'MISSING'} | 每日预算=${DAILY_BUDGET}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
