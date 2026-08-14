"""RIS Health Monitor — 主动健康检测模块 (Phase 2·创始人 23:37 令)

检测三块:
  1. Process Health   : gateway/agent 进程状态·PID 变化检测
  2. Resource Health  : CPU / Memory / Disk / Network
  3. Runtime Availability: HTTP 健康 / Session 响应 / WebUI 连接

输出: RuntimeHealthEvent (复用 ris/events.py 定义·独立顶层事件)
"""
from __future__ import annotations
import os, time
from typing import Callable, Dict, List, Optional

from ris.events import RuntimeHealthEvent


class ProcessHealth:
    """进程健康: 检查 gateway/agent 进程是否存活 + PID 是否变化(重启检测)。"""

    def __init__(self, pid_file: str = ""):
        self._pid_file = pid_file or "/tmp/ris-last-pids.json"
        self._last_pids: Dict[str, int] = {}

    def check_process(self, name: str, pid: Optional[int] = None,
                      is_alive: Optional[bool] = None) -> RuntimeHealthEvent:
        """检查一个进程。is_alive=None 时自动探测。"""
        alive = is_alive if is_alive is not None else (pid is not None)
        if not alive:
            return RuntimeHealthEvent(
                event_type="process_down", agent_id=name, status="detected",
                severity="error", detail={"pid": pid, "alive": False})
        # PID 变化检测(重启)
        changed = self._last_pids.get(name) is not None and self._last_pids[name] != pid
        self._last_pids[name] = pid or 0
        if changed:
            return RuntimeHealthEvent(
                event_type="process_restart", agent_id=name, status="detected",
                severity="warn", detail={"old_pid": self._last_pids.get(name), "new_pid": pid})
        return RuntimeHealthEvent(
            event_type="process_ok", agent_id=name, status="recovered",
            severity="info", detail={"pid": pid, "alive": True})


class ResourceHealth:
    """资源健康: CPU / Memory / Disk / Network 阈值检测。"""

    def __init__(self, cpu_threshold: float = 80.0, mem_threshold: float = 85.0,
                 disk_threshold: float = 90.0):
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.disk_threshold = disk_threshold

    def check_cpu(self, cpu_pct: float, agent: str = "system") -> RuntimeHealthEvent:
        """CPU 使用率检测(超阈值 → cpu_anomaly)。"""
        if cpu_pct >= self.cpu_threshold:
            return RuntimeHealthEvent(
                event_type="cpu_anomaly", agent_id=agent, status="detected",
                severity="error" if cpu_pct >= self.cpu_threshold + 10 else "warn",
                detail={"cpu_pct": cpu_pct, "threshold": self.cpu_threshold})
        return RuntimeHealthEvent(
            event_type="cpu_ok", agent_id=agent, status="recovered",
            severity="info", detail={"cpu_pct": cpu_pct})

    def check_memory(self, mem_pct: float, agent: str = "system") -> RuntimeHealthEvent:
        if mem_pct >= self.mem_threshold:
            return RuntimeHealthEvent(
                event_type="memory_anomaly", agent_id=agent, status="detected",
                severity="warn", detail={"mem_pct": mem_pct, "threshold": self.mem_threshold})
        return RuntimeHealthEvent(
            event_type="memory_ok", agent_id=agent, status="recovered",
            severity="info", detail={"mem_pct": mem_pct})

    def check_disk(self, disk_pct: float, agent: str = "system") -> RuntimeHealthEvent:
        if disk_pct >= self.disk_threshold:
            return RuntimeHealthEvent(
                event_type="disk_anomaly", agent_id=agent, status="detected",
                severity="warn", detail={"disk_pct": disk_pct, "threshold": self.disk_threshold})
        return RuntimeHealthEvent(
            event_type="disk_ok", agent_id=agent, status="recovered",
            severity="info", detail={"disk_pct": disk_pct})

    def check_network(self, ok: bool, latency_ms: float = 0.0, agent: str = "system") -> RuntimeHealthEvent:
        if not ok:
            return RuntimeHealthEvent(
                event_type="network_anomaly", agent_id=agent, status="detected",
                severity="error", detail={"latency_ms": latency_ms, "ok": False})
        return RuntimeHealthEvent(
            event_type="network_ok", agent_id=agent, status="recovered",
            severity="info", detail={"latency_ms": latency_ms})


class RuntimeAvailability:
    """运行可用性: HTTP 健康 / Session 响应 / WebUI 连接。"""

    def __init__(self):
        self._probes: Dict[str, Callable[[], bool]] = {}

    def register_probe(self, name: str, fn: Callable[[], bool]):
        self._probes[name] = fn

    def check_http(self, ok: bool, agent: str = "gateway") -> RuntimeHealthEvent:
        if not ok:
            return RuntimeHealthEvent(
                event_type="http_unavailable", agent_id=agent, status="detected",
                severity="error", detail={"http": "down"})
        return RuntimeHealthEvent(
            event_type="http_ok", agent_id=agent, status="recovered",
            severity="info", detail={"http": "up"})

    def check_session(self, ok: bool, agent: str = "") -> RuntimeHealthEvent:
        if not ok:
            return RuntimeHealthEvent(
                event_type="session_unresponsive", agent_id=agent or "session", status="detected",
                severity="error", detail={"session": "unresponsive"})
        return RuntimeHealthEvent(
            event_type="session_ok", agent_id=agent or "session", status="recovered",
            severity="info", detail={"session": "responsive"})

    def check_webui(self, ok: bool, agent: str = "webui") -> RuntimeHealthEvent:
        if not ok:
            return RuntimeHealthEvent(
                event_type="webui_unavailable", agent_id=agent, status="detected",
                severity="error", detail={"webui": "down"})
        return RuntimeHealthEvent(
            event_type="webui_ok", agent_id=agent, status="recovered",
            severity="info", detail={"webui": "up"})
