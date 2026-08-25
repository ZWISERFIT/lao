"""
Runtime State Registry — Agent 运行状态台账（Phase 2 · Momo 负责）
=============================================================================
创始人 23:37 令：Phase 2 把 RIS 升级为主动检测 + 自动恢复系统。
本模块负责 **Runtime State Registry（Agent 运行状态台账）**：

目标：让系统知道「谁健康 / 谁异常 / 谁正在恢复」。

与既有 RuntimeRegistry（lao/effect_anchored/runtime_registry.py）的关系：
  既有 RuntimeRegistry = 内存态 + TrustEvent 追踪（Detect 阶段基础，不动它）
  本模块 = 在既有基础上叠加【持久化台账 + health_score + last_recovery +
           failure_history】的显式台账层，供「主动检测 + 自动恢复」系统查询决策。

设计约束（创始人令）:
  - 不新增架构、不重写 LAO，在现有 ris/ 基础上做 (方案 B 软分层)
  - 复用已有的 AgentRuntimeState dataclass，不重复定义核心状态
  - 现有 115 tests 保持不动；本模块新增独立测试

台账数据结构 (每 Agent 一条记录):
  {
    agent_id, runtime, provider,             # 身份
    status, health,                          # 当前态
    health_score,                            # 0~100 综合健康分(本模块新增计算)
    last_recovery,                           # 上次恢复时间 + 方式(ISO)
    failure_history,                         # 失败历史(环形·上限 N)
    trust_score                             # 复用既有 trust_score
  }
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用既有 AgentRuntimeState(不重复定义核心字段)
try:
    from ris import RuntimeRegistry
    from lao.effect_anchored.runtime_registry import AgentRuntimeState
except Exception:  # 保护性 import(独立运行/测试时兜底)
    from lao.effect_anchored.runtime_registry import AgentRuntimeState, RuntimeRegistry


# 台账默认落盘位置(ris/state/data/)
DEFAULT_STORE = Path(__file__).resolve().parent / "data" / "runtime_state_registry.jsonl"

# 失败历史环形窗口上限
MAX_FAILURE_HISTORY = 20

# 状态 → 基础健康分(未加权)
_STATUS_HEALTH = {
    "online": 100.0,
    "degraded": 60.0,
    "recovering": 40.0,
    "offline": 10.0,
    "unknown": 50.0,
}


@dataclass
class FailureRecord:
    """单条失败历史记录。"""
    ts: str
    domain: str = ""
    detail: str = ""


@dataclass
class RuntimeStateRecord:
    """每 Agent 的完整台账条目(在 AgentRuntimeState 之上的显式台账层)。"""
    agent_id: str
    runtime: str = "openclaw"          # openclaw / hermes
    provider: str = ""
    status: str = "unknown"            # online / recovering / offline / degraded
    health: str = "unknown"            # healthy / degraded / unhealthy
    health_score: float = 50.0         # 0~100 综合健康分(台账新增)
    last_recovery: str = ""            # ISO(上次恢复时间)
    last_recovery_method: str = ""     # 恢复方式(如 "L1_restart" / "manual")
    trust_score: float = 0.0           # 复用既有
    failure_history: List[FailureRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "provider": self.provider,
            "status": self.status,
            "health": self.health,
            "health_score": round(self.health_score, 1),
            "last_recovery": self.last_recovery,
            "last_recovery_method": self.last_recovery_method,
            "trust_score": round(self.trust_score, 1),
            "failure_history": [asdict(f) for f in self.failure_history],
        }


class RuntimeStateRegistry:
    """Agent 运行状态台账：登记 / 更新 / 查询 + 持久化。

    - register(): 登记 Agent(幂等)
    - update():   接收 RuntimeRegistry 状态信号 → 刷新台账 + 算 health_score
    - record_recovery() / record_failure(): 更新恢复/失败历史
    - get() / all() / summary(): 查询(台账视角: 谁健康/谁异常/谁恢复中)
    - persist() / load(): JSONL 持久化(崩溃后可重建台账)
    """

    def __init__(self, store_path: Optional[Path] = None):
        self._records: Dict[str, RuntimeStateRecord] = {}
        self._lock = threading.Lock()
        self.store_path = Path(store_path) if store_path else DEFAULT_STORE
        self._load()

    # ── 登记 ────────────────────────────────────────────────────────────
    def register(self, agent_id: str, runtime: str = "openclaw",
                 provider: str = "") -> RuntimeStateRecord:
        """登记一个 Agent(幂等)。

        ⚠️ 本方法会获取锁；若已在持锁上下文(update/record_*)中，
        请使用内部 _register_locked() 避免非重入死锁。
        """
        with self._lock:
            return self._register_locked(agent_id, runtime, provider)

    def _register_locked(self, agent_id: str, runtime: str = "openclaw",
                         provider: str = "") -> RuntimeStateRecord:
        """内部注册：调用方必须已持有 self._lock。用于避免锁内再取锁死锁。"""
        if agent_id not in self._records:
            self._records[agent_id] = RuntimeStateRecord(
                agent_id=agent_id, runtime=runtime, provider=provider)
        rec = self._records[agent_id]
        if runtime:
            rec.runtime = runtime
        if provider:
            rec.provider = provider
        return rec

    # ── 更新(从 RuntimeRegistry 信号同步) ──────────────────────────────
    def update_from_runtime(self, st: AgentRuntimeState) -> RuntimeStateRecord:
        """把既有 AgentRuntimeState 的状态同步进台账，并重算 health_score。"""
        with self._lock:
            rec = self._records.get(st.agent_id)
            if not rec:
                rec = self._register_locked(st.agent_id, provider=st.provider)
            # 状态复制
            rec.status = st.status
            rec.health = st.health
            rec.provider = st.provider or rec.provider
            rec.trust_score = st.trust_score
            # 重算综合健康分(基于 status 基础分 + trust 微调)
            rec.health_score = self._compute_health_score(rec, st)
            return rec

    def update(self, agent_id: str, status: str, runtime: str = "openclaw",
               provider: str = "", domain: str = "") -> RuntimeStateRecord:
        """直接更新台账(无 AgentRuntimeState 时的便捷入口)。"""
        with self._lock:
            rec = self._records.get(agent_id) or self._register_locked(agent_id, runtime, provider)
            rec.status = status
            rec.health = _health_from_status(status)
            if provider:
                rec.provider = provider
            rec.health_score = _score_from_status(status)
            if status == "recovering" and domain:
                rec.failure_history.append(
                    FailureRecord(ts=_now(), domain=domain, detail="recovery_triggered"))
                self._trim_history(rec)
            return rec

    # ── 恢复 / 失败记录 ────────────────────────────────────────────────
    def record_recovery(self, agent_id: str, method: str = "L1_restart") -> RuntimeStateRecord:
        """登记一次成功恢复(状态回 online + 更新 last_recovery)。"""
        with self._lock:
            rec = self._records.get(agent_id) or self._register_locked(agent_id)
            rec.status = "online"
            rec.health = "healthy"
            rec.health_score = 100.0
            rec.last_recovery = _now()
            rec.last_recovery_method = method
            return rec

    def record_failure(self, agent_id: str, domain: str = "",
                       detail: str = "") -> RuntimeStateRecord:
        """登记一次失败(追加进 failure_history + 降 health_score)。"""
        with self._lock:
            rec = self._records.get(agent_id) or self._register_locked(agent_id)
            rec.failure_history.append(FailureRecord(ts=_now(), domain=domain, detail=detail))
            self._trim_history(rec)
            rec.health_score = max(0.0, rec.health_score - 8.0)
            if rec.health_score < 40:
                rec.status = "recovering"
                rec.health = "unhealthy"
            return rec

    # ── 查询 ───────────────────────────────────────────────────────────
    def get(self, agent_id: str) -> Optional[RuntimeStateRecord]:
        with self._lock:
            return self._records.get(agent_id)

    def all(self) -> List[RuntimeStateRecord]:
        with self._lock:
            return list(self._records.values())

    def summary(self) -> Dict[str, Any]:
        """台账视角汇总：让系统知道谁健康/谁异常/谁恢复中。"""
        with self._lock:
            recs = list(self._records.values())
            return {
                "total": len(recs),
                "healthy": sum(1 for r in recs if r.status == "online"),
                "degraded": sum(1 for r in recs if r.status == "degraded"),
                "recovering": sum(1 for r in recs if r.status == "recovering"),
                "offline": sum(1 for r in recs if r.status == "offline"),
                "avg_health_score": round(
                    sum(r.health_score for r in recs) / len(recs), 1) if recs else 0.0,
            }

    # ── 持久化 ─────────────────────────────────────────────────────────
    def persist(self) -> int:
        """把台账全量写入 JSONL(崩溃后可重建)。返回写入条数。"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self.store_path.open("w", encoding="utf-8") as f:
            for rec in self._records.values():
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
                written += 1
        return written

    def _load(self) -> None:
        """启动时从 JSONL 恢复台账。"""
        if not self.store_path.exists():
            return
        try:
            with self.store_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    rec = RuntimeStateRecord(agent_id=data["agent_id"])
                    rec.runtime = data.get("runtime", "openclaw")
                    rec.provider = data.get("provider", "")
                    rec.status = data.get("status", "unknown")
                    rec.health = data.get("health", "unknown")
                    rec.health_score = data.get("health_score", 50.0)
                    rec.last_recovery = data.get("last_recovery", "")
                    rec.last_recovery_method = data.get("last_recovery_method", "")
                    rec.trust_score = data.get("trust_score", 0.0)
                    rec.failure_history = [
                        FailureRecord(ts=h.get("ts", ""), domain=h.get("domain", ""),
                                      detail=h.get("detail", ""))
                        for h in data.get("failure_history", [])
                    ]
                    self._records[rec.agent_id] = rec
        except Exception:
            pass  # 台账可重建，加载失败不致命

    # ── 内部 ───────────────────────────────────────────────────────────
    @staticmethod
    def _compute_health_score(rec: RuntimeStateRecord,
                              st: AgentRuntimeState) -> float:
        base = _score_from_status(st.status)
        # trust 微调(±10 封顶)
        trust_adj = max(-10.0, min(10.0, st.trust_score / 10.0))
        return max(0.0, min(100.0, base + trust_adj))

    def _trim_history(self, rec: RuntimeStateRecord) -> None:
        if len(rec.failure_history) > MAX_FAILURE_HISTORY:
            rec.failure_history = rec.failure_history[-MAX_FAILURE_HISTORY:]


# ── 工具函数 ────────────────────────────────────────────────────────────
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _health_from_status(status: str) -> str:
    if status == "online":
        return "healthy"
    if status == "degraded":
        return "degraded"
    if status in ("recovering", "offline"):
        return "unhealthy"
    return "unknown"


def _score_from_status(status: str) -> float:
    return _STATUS_HEALTH.get(status, 50.0)


__all__ = [
    "RuntimeStateRegistry",
    "RuntimeStateRecord",
    "FailureRecord",
    "MAX_FAILURE_HISTORY",
]
