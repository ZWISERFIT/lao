"""
RIS Provider Health Monitor — Provider 健康监控接入 (P0-1 · 成熟部署加速 · Shuyu 立项)
======================================================================================
关联成本事故复盘（2026-08-14 lao-router 成本暴增·Stella 评估为重量级）。

为什么 Provider 健康监控是 P0（最高价值）：
    成本链路（四段，RIS 必须能捕获整条链路，而不只是"端口通不通"）：

        Provider 掉线/degraded
              │  RIS 此处产出 provider_unavailable 事件
              ▼
        lao-router(8765) 不可达 → OpenClaw 回退直连 DeepSeek(deepseek-{agent})
              │  路由态变更（RoutingStateGuard 审计）· 丢失 KVCache 隔离 → cache miss ↑
              ▼
        直连 = 单 key 混用 + 无 budget 红线 + 无 pro→flash 降级 → 成本暴涨
              │  cost 维度漂移（原成本事故主根因之一）
              ▼
        RIS 关联告警：provider_unavailable 事件上带 cost_impact 字段

设计要点：
    1. 复用 ProviderHealthGate（lao.effect_anchored.provider_health_gate）做健康判定，
       不自己造"端口通=健康"的判断 → 与 LAO 出口稳定阶段（禁止静默 fallback）对齐。
    2. 输出 RuntimeHealthEvent(event_type="provider_unavailable")，进入 ris-events.jsonl 可审计。
    3. detail.cost_impact 显式标注成本链路影响，供财务/智囊团复盘时直接关联。
"""
from __future__ import annotations

import os
import ssl
import time
import urllib.request
from typing import Dict, List, Optional

import sys
_LAO_RELEASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _LAO_RELEASE not in sys.path:
    sys.path.insert(0, _LAO_RELEASE)

from lao.effect_anchored.provider_health_gate import ProviderHealthGate
from ris.events import RuntimeHealthEvent

# ── Provider 拓扑 ──────────────────────────────────────────────────
# lao-router: LAO 成本优化 OpenAI 兼容代理(方案A·9Agent共用·8765)
LAO_ROUTER_ENDPOINT = "http://127.0.0.1:8765/v1/models"
# 直连 DeepSeek: lao-router 掉线后 OpenClaw 的回退目标(独立 key·flash 可用)
DEEPSEEK_DIRECT_ENDPOINT = "https://api.deepseek.com/v1/models"

# 成本链路注解(原 2026-08-14 成本事故的主根因，RIS 捕获链路时直接标注)
COST_CHAIN_NOTE = (
    "provider→lao-router(8765)→回退直连deepseek→单key混用+cache_miss↑+无budget红线→成本↑"
)


class ProviderHealthMonitor:
    """Provider 健康监控：复用 ProviderHealthGate + 捕获成本链路。

    比 agent.py 内联的 `_http_ok` 强在三点：
      1. 用 ProviderHealthGate.check() 产出结构化 ProviderHealthEvent(可进 TrustEvent)
      2. detail 带上 cost_impact / fallback_target / cost_chain(成本事故复盘必需)
      3. 可同时探 lao-router(8765) 与直连 deepseek，区分"链路哪一段断"
    """

    def __init__(self, gate: Optional[ProviderHealthGate] = None,
                 router_endpoint: str = LAO_ROUTER_ENDPOINT,
                 direct_endpoint: str = DEEPSEEK_DIRECT_ENDPOINT):
        self.gate = gate or ProviderHealthGate()
        self.router_endpoint = router_endpoint
        self.direct_endpoint = direct_endpoint

    # ── 探测 ──────────────────────────────────────────────
    def _probe(self, url: str, timeout: float = 6.0) -> Dict:
        """HTTP 探活，返回 {ok, status, latency_ms, error}。

        ok 语义 = "端点可达"（endpoint reachable）：
          - 2xx/401/403/404 都视为端点可达（服务在响应，只是需要认证或路径不存在）
          - 连接拒绝/超时/DNS 失败才视为不可达（真正的 provider 掉线）
        这样不会把"需 key 的直连端点"误判为 provider 掉线（成本事故复盘里的关键误报源）。
        """
        t0 = time.time()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                return {"ok": True, "status": r.status,
                        "latency_ms": round((time.time() - t0) * 1000, 1), "error": None}
        except urllib.error.HTTPError as e:
            # 401/403 = 需要认证(端点可达)；404 = 路径不存在(端点可达)；仅 >=500 视为不可达
            reachable = e.code < 500
            return {"ok": reachable, "status": e.code,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "error": None if reachable else f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            # 连接拒绝/超时/DNS失败 = 真正的不可达
            return {"ok": False, "status": None,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "error": type(e).__name__}

    # ── lao-router 健康 ───────────────────────────────────
    def check_lao_router(self) -> RuntimeHealthEvent:
        """探 lao-router(8765)：不可达 → provider_unavailable(critical) + cost_impact。"""
        r = self._probe(self.router_endpoint)
        ev = self.gate.check(
            provider="lao-router",
            endpoint_available=r["ok"],
            latency_ms=r["latency_ms"],
            reason="" if r["ok"] else f"{r['error']}: {self.router_endpoint}",
        )
        if ev.status != "healthy":
            return RuntimeHealthEvent(
                event_type="provider_unavailable",
                agent_id="lao-router",
                status="detected",
                severity="critical",
                detail={
                    "provider": "lao-router",
                    "endpoint": self.router_endpoint,
                    "ok": r["ok"],
                    "latency_ms": r["latency_ms"],
                    "error": r["error"],
                    "reason": ev.reason,
                    # 成本链路(关键·P0-1 核心)
                    "fallback_target": "deepseek-direct",
                    "cost_impact": "high",
                    "cost_chain": COST_CHAIN_NOTE,
                },
            )
        return RuntimeHealthEvent(
            event_type="provider_ok", agent_id="lao-router", status="recovered",
            severity="info",
            detail={"provider": "lao-router", "endpoint": self.router_endpoint,
                    "latency_ms": r["latency_ms"]})

    # ── 直连 deepseek 健康 ─────────────────────────────────
    def check_deepseek_direct(self) -> RuntimeHealthEvent:
        """探直连 deepseek：若 lao-router 已断且直连也断 → 完全无 provider(最严重)。"""
        r = self._probe(self.direct_endpoint)
        ev = self.gate.check(
            provider="deepseek",
            endpoint_available=r["ok"],
            latency_ms=r["latency_ms"],
            reason="" if r["ok"] else f"{r['error']}: {self.direct_endpoint}",
        )
        if ev.status != "healthy":
            return RuntimeHealthEvent(
                event_type="provider_unavailable",
                agent_id="deepseek",
                status="detected",
                severity="critical",
                detail={
                    "provider": "deepseek",
                    "endpoint": self.direct_endpoint,
                    "ok": r["ok"],
                    "latency_ms": r["latency_ms"],
                    "error": r["error"],
                    "reason": ev.reason,
                    "fallback_target": None,     # 直连也断 = 无兜底
                    "cost_impact": "critical",   # 完全不可用 → 所有 Agent 停摆
                    "cost_chain": COST_CHAIN_NOTE,
                },
            )
        return RuntimeHealthEvent(
            event_type="provider_ok", agent_id="deepseek", status="recovered",
            severity="info",
            detail={"provider": "deepseek", "endpoint": self.direct_endpoint,
                    "latency_ms": r["latency_ms"]})

    # ── 一次完整检测 ─────────────────────────────────────
    def check_once(self, emit_ok: bool = False) -> List[RuntimeHealthEvent]:
        """检测 lao-router + 直连 deepseek，返回产生的事件。

        emit_ok=False 时仅返回异常(provider_unavailable)事件，避免噪声事件刷日志；
        异常事件才是 P0-1 关心的高价值信号。
        """
        out: List[RuntimeHealthEvent] = []
        lao = self.check_lao_router()
        if lao.event_type == "provider_unavailable":
            out.append(lao)
        elif emit_ok:
            out.append(lao)

        ds = self.check_deepseek_direct()
        if ds.event_type == "provider_unavailable":
            out.append(ds)
        elif emit_ok:
            out.append(ds)
        return out


__all__ = ["ProviderHealthMonitor", "COST_CHAIN_NOTE",
           "LAO_ROUTER_ENDPOINT", "DEEPSEEK_DIRECT_ENDPOINT"]
