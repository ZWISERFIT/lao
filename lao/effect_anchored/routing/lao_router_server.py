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
from lao.effect_anchored.routing.model_router import ModelRouter, RouteSelection

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
# 每日预算($USD·成本红线·Private Policy 可调)
DAILY_BUDGET = float(os.environ.get("LAO_DAILY_BUDGET_USD", "5.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lao-router")

app = FastAPI(title="lao-router", version="1.0.0")
router = ModelRouter()

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
    # model 名可直接映射
    m = (model_hint or "").lower()
    if "ultra" in m or "tiny" in m: return "ultra_light"
    if "flash" in m and ("reason" in m or "code" in m): return "code"
    if "reason" in m: return "reasoning"
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

    # ② 成本红线路由
    budget = _remaining_budget()
    try:
        sel: RouteSelection = router.route_with_budget(task=model_hint, budget=budget)
    except Exception as e:
        logger.error(f"route_with_budget失败({e}), 使用默认")
        sel = router.route("light")

    chosen_model = sel.model
    chosen_provider = sel.provider

    # ③ 转发真实 DeepSeek(route()保证 provider/model 端点可用·防400)
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE, timeout=300)
    payload = {**body, "model": chosen_model}
    started = time.time()
    try:
        if stream:
            resp = client.chat.completions.create(**payload)
        else:
            resp = client.chat.completions.create(**payload)
    except Exception as e:
        _log_event({"tier": tier, "chosen_model": chosen_model, "provider": chosen_provider,
                    "budget": budget, "status": "error", "error": str(e)[:200]})
        return JSONResponse({"error": {"message": str(e), "type": "lao_router_forward"}}, status_code=502)

    latency_ms = int((time.time() - started) * 1000)

    # ④ 成本记录(按官方单价·¥/1M)
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    # 单价(¥/1M): pro 3/6, flash 1/2
    ci = 3 if "pro" in chosen_model else 1
    co = 6 if "pro" in chosen_model else 2
    cost_yuan = (in_tok * ci + out_tok * co) / 1e6
    cost_usd = cost_yuan / 7.2
    _record_cost(cost_usd)

    _log_event({
        "tier": tier, "chosen_model": chosen_model, "provider": chosen_provider,
        "requested_model": model_hint, "budget_remaining": round(budget, 4),
        "degraded": "flash" in chosen_model and "pro" in str(model_hint).lower(),
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cost_yuan": round(cost_yuan, 6), "latency_ms": latency_ms, "stream": stream,
        "fallback_chain": sel.fallback_chain,
    })

    return resp


if __name__ == "__main__":
    logger.info(f"lao-router 启动: :{PORT} | DeepSeek基址={DEEPSEEK_BASE} | key={'$'*8 if DEEPSEEK_KEY else 'MISSING'} | 每日预算=${DAILY_BUDGET}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
