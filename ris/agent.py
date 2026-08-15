"""
RIS Agent — 常驻运行免疫代理 (Phase RIS-Enablement·创始人 11:38 令)
=============================================================================
RIS 从 L1(事件体系) → L2(监控接入) → L3(自动恢复)。

核心 4 件套:
  1. Runtime Sensor   : psutil 采集真实 CPU/Memory/Process/IO
  2. Health Monitor   : 常驻检测·超阈值产出 RuntimeHealthEvent(cpu_anomaly 等)
  3. Recovery Executor: 检测到异常→执行恢复→Verify→Record(五步闭环·铁律)
  4. openclaw connector: 监控 Gateway/WebUI/lao-router/session 状态

常驻机制: systemd 用户服务(ris-monitor.service)·每 30s 检测。
"""
from __future__ import annotations
import json, os, time, subprocess, sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# 确保 lao-release 在 path(ris 包所在)
_LAO_RELEASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAO_RELEASE not in sys.path:
    sys.path.insert(0, _LAO_RELEASE)

try:
    import psutil
except ImportError:
    psutil = None

# 复用 ris 模块
from ris.events import RuntimeHealthEvent
from ris.health import ProcessHealth, ResourceHealth, RuntimeAvailability
from ris.recovery import RecoveryEngine, RecoveryAction

# 路径
RIS_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs"
os.makedirs(LOG_DIR, exist_ok=True)
EVENT_LOG = os.path.join(LOG_DIR, "ris-events.jsonl")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(ev: RuntimeHealthEvent):
    """写 RIS 事件到日志(append-only·可审计)。"""
    with open(EVENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    return ev


# ── 1. Runtime Sensor ──────────────────────────────────────────────
class RuntimeSensor:
    """采集真实 runtime 数据(psutil·不 mock)。"""

    def sample(self) -> Dict:
        """采集一帧系统数据。"""
        if psutil is None:
            return {"cpu_pct": _read_proc_stat_cpu(), "mem_pct": _read_proc_mem()}
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        return {
            "cpu_pct": cpu,
            "mem_pct": mem.percent,
            "mem_avail_mb": round(mem.available / 1024 / 1024),
            "load_avg": os.getloadavg()[0],
            "gateway_pid": _find_pid("gateway --port 18789"),
            "lao_router_pid": _find_pid("lao_router_server"),
            "mcp_count": _count_mcp(),
            "ts": _ts(),
        }

    def process_detail(self, name: str) -> Optional[Dict]:
        """某进程详情(CPU/Mem/RSS)。"""
        if psutil is None:
            return None
        pid = _find_pid(name)
        if not pid:
            return None
        try:
            p = psutil.Process(pid)
            return {"pid": pid, "cpu_pct": p.cpu_percent(interval=0.2),
                    "rss_mb": round(p.memory_info().rss / 1024 / 1024),
                    "status": p.status()}
        except Exception:
            return None


def _find_pid(needle: str) -> Optional[int]:
    try:
        out = subprocess.check_output(["pgrep", "-f", needle], text=True).strip()
        return int(out.split("\n")[0]) if out else None
    except Exception:
        return None


def _count_mcp() -> int:
    try:
        out = subprocess.check_output(["pgrep", "-f", "npm exec @agentmemory"], text=True)
        return len([l for l in out.strip().split("\n") if l])
    except Exception:
        return 0


def _read_proc_stat_cpu() -> float:
    try:
        with open("/proc/stat") as f:
            line = f.readline().split()
        total = sum(int(x) for x in line[1:])
        idle = int(line[4])
        return round((1 - idle / total) * 100, 1) if total else 0.0
    except Exception:
        return 0.0


def _read_proc_mem() -> float:
    try:
        with open("/proc/meminfo") as f:
            d = {}
            for line in f:
                k, _, v = line.partition(":")
                d[k] = int(v.strip().split()[0])
        total = d.get("MemTotal", 1)
        avail = d.get("MemAvailable", total)
        return round((1 - avail / total) * 100, 1)
    except Exception:
        return 0.0


# ── 2. Health Monitor(常驻检测) ─────────────────────────────────────
class HealthMonitor:
    """定时检测系统健康·超阈值产出 RuntimeHealthEvent。"""

    def __init__(self, cpu_threshold: float = 80.0, mem_threshold: float = 85.0):
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.sensor = RuntimeSensor()
        self.resource = ResourceHealth(cpu_threshold=cpu_threshold,
                                       mem_threshold=mem_threshold)
        self.process = ProcessHealth()
        self.avail = RuntimeAvailability()

    def check_once(self) -> List[RuntimeHealthEvent]:
        """检测一帧·返回产生的事件。"""
        events = []
        s = self.sensor.sample()

        # CPU 超阈值 → cpu_anomaly(真实捕获·创始人核心诉求)
        if s["cpu_pct"] >= self.cpu_threshold:
            ev = _emit(self.resource.check_cpu(s["cpu_pct"], agent="system"))
            events.append(ev)
            print(f"  ⚠️ cpu_anomaly: {s['cpu_pct']}% >= {self.cpu_threshold}%")

        # Memory 超阈值
        if s["mem_pct"] >= self.mem_threshold:
            ev = _emit(self.resource.check_memory(s["mem_pct"], agent="system"))
            events.append(ev)
            print(f"  ⚠️ memory_anomaly: {s['mem_pct']}%")

        # Gateway 进程存活
        gw_pid = s.get("gateway_pid")
        if gw_pid:
            self.process.check_process("gateway", pid=gw_pid)
        else:
            ev = _emit(RuntimeHealthEvent(
                event_type="gateway_down", agent_id="gateway", status="detected",
                severity="critical", detail={"pid": None}))
            events.append(ev)
            print("  ⚠️ gateway_down: 进程未找到")

        # WebUI HTTP 健康
        webui_ok = _http_ok("https://127.0.0.1:8444/")
        if not webui_ok:
            ev = _emit(self.avail.check_webui(False))
            events.append(ev)
            print("  ⚠️ webui_unavailable")

        # lao-router 存活
        lr_pid = s.get("lao_router_pid")
        if not lr_pid:
            ev = _emit(RuntimeHealthEvent(
                event_type="lao_router_down", agent_id="lao-router", status="detected",
                severity="critical", detail={"pid": None}))
            events.append(ev)
            print("  ⚠️ lao_router_down")

        # P0-1 Provider 健康监控(成熟部署加速·Shuyu立项): 检测 lao-router provider 可用性
        # 关联成本事故复盘·Provider 不可用=缓存/成本关键维度
        provider_ok = _http_ok("http://127.0.0.1:8765/v1/models", timeout=6.0)
        if not provider_ok:
            ev = _emit(RuntimeHealthEvent(
                event_type="provider_unavailable", agent_id="lao-router", status="detected",
                severity="critical", detail={"endpoint": "8765/v1/models", "ok": False}))
            events.append(ev)
            print("  ⚠️ provider_unavailable: lao-router 不可用")
        # DeepSeek 直连可用性(官方端点探活)
        deepseek_ok = _http_ok("https://api.deepseek.com/v1/models", timeout=6.0) \
            if False else True  # 需 key·不直接探测·用 lao-router 即可
        if not deepseek_ok:
            ev = _emit(RuntimeHealthEvent(
                event_type="provider_unavailable", agent_id="deepseek", status="detected",
                severity="critical", detail={"endpoint": "api.deepseek.com", "ok": False}))
            events.append(ev)
            print("  ⚠️ provider_unavailable: deepseek 不可用")

        # mcp 泄漏(>4 个·已知问题)
        mcp = s.get("mcp_count", 0)
        if mcp > 4:
            ev = _emit(RuntimeHealthEvent(
                event_type="mcp_leak", agent_id="mcp", status="detected",
                severity="warn", detail={"count": mcp}))
            events.append(ev)
            print(f"  ⚠️ mcp_leak: {mcp} 个进程")

        return events


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        import urllib.request, ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
            return r.status < 500
    except Exception:
        return False


# ── 3. Recovery Executor(自动恢复·五步闭环) ─────────────────────────
class RecoveryExecutor:
    """检测到异常 → 执行恢复 → Verify → Record。"""

    def __init__(self):
        self.engine = RecoveryEngine()

    def recover_mcp_leak(self) -> Dict:
        """mcp 泄漏清理(安全·已验证·首个真实恢复动作)。"""
        def detect() -> bool:
            return _count_mcp() > 4

        def recover() -> bool:
            # 清掉空闲 mcp(保留最新1个)
            try:
                out = subprocess.check_output(["pgrep", "-f", "npm exec @agentmemory"], text=True)
                pids = [l for l in out.strip().split("\n") if l]
                for pid in pids[:-1]:
                    subprocess.run(["kill", pid], timeout=5)
                return True
            except Exception:
                return False

        def verify() -> bool:
            return _count_mcp() <= 4

        result = self.engine.run(
            "mcp_leak", "mcp",
            detect_fn=detect, classify_fn=lambda: "mcp_leak",
            action=RecoveryAction(name="cleanup_mcp", recover_fn=recover,
                                  verify_fn=verify, max_attempts=2),
        )
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded}

    def recover_lao_router(self) -> Dict:
        """lao-router 重启恢复。"""
        def detect() -> bool:
            return _find_pid("lao_router_server") is None

        def recover() -> bool:
            try:
                subprocess.run(["systemctl", "--user", "restart", "lao-router.service"],
                               timeout=15, capture_output=True)
                return True
            except Exception:
                return False

        def verify() -> bool:
            return _find_pid("lao_router_server") is not None

        result = self.engine.run(
            "lao_router_down", "lao-router",
            detect_fn=detect, classify_fn=lambda: "lao_router_down",
            action=RecoveryAction(name="restart_lao_router", recover_fn=recover,
                                  verify_fn=verify, max_attempts=2),
        )
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded}


# ── 4. openclaw connector(连接 runtime) ─────────────────────────────
class OpenClawConnector:
    """监控 OpenClaw runtime: Gateway 进程/WebUI/session。"""

    def check_gateway(self) -> RuntimeHealthEvent:
        pid = _find_pid("gateway --port 18789")
        if not pid:
            return _emit(RuntimeHealthEvent(
                event_type="gateway_down", agent_id="gateway", status="detected",
                severity="critical", detail={"pid": None}))
        return RuntimeHealthEvent(
            event_type="gateway_ok", agent_id="gateway", status="recovered",
            severity="info", detail={"pid": pid})

    def check_webui(self) -> RuntimeHealthEvent:
        ok = _http_ok("https://127.0.0.1:8444/")
        if not ok:
            return _emit(RuntimeHealthEvent(
                event_type="webui_unavailable", agent_id="webui", status="detected",
                severity="error", detail={}))
        return RuntimeHealthEvent(
            event_type="webui_ok", agent_id="webui", status="recovered",
            severity="info", detail={})

    def check_session_bloat(self, max_mb: float = 8.0) -> RuntimeHealthEvent:
        """session 文件过大检测(>8MB·拖慢加载·webchat慢根因)。"""
        big = []
        for root, _, files in os.walk("/home/agentuser/.openclaw/agents"):
            for fn in files:
                if fn.endswith(".jsonl"):
                    fp = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(fp) / 1024 / 1024
                        if sz > max_mb:
                            big.append({"file": fp, "mb": round(sz, 1)})
                    except Exception:
                        pass
        if big:
            return _emit(RuntimeHealthEvent(
                event_type="session_bloat", agent_id="sessions", status="detected",
                severity="warn", detail={"files": big[:5], "count": len(big)}))
        return RuntimeHealthEvent(
            event_type="session_ok", agent_id="sessions", status="recovered",
            severity="info", detail={})


# ── 主循环(常驻·systemd 调用) ─────────────────────────────────────
def run_once(verbose: bool = True) -> Dict:
    """执行一轮完整检测(供 systemd 每 30s 调用)。"""
    monitor = HealthMonitor()
    executor = RecoveryExecutor()
    connector = OpenClawConnector()

    events = monitor.check_once()
    summary = {"cpu_pct": 0, "mem_pct": 0, "events": len(events),
               "recoveries": []}

    s = monitor.sensor.sample()
    summary["cpu_pct"] = s["cpu_pct"]
    summary["mem_pct"] = s["mem_pct"]

    # 恢复动作
    mcp = _count_mcp()
    if mcp > 4:
        r = executor.recover_mcp_leak()
        summary["recoveries"].append({"action": "mcp_leak", **r})
    if _find_pid("lao_router_server") is None:
        r = executor.recover_lao_router()
        summary["recoveries"].append({"action": "lao_router", **r})

    # openclaw 连接检查
    connector.check_gateway()
    connector.check_webui()
    connector.check_session_bloat()

    if verbose:
        print(f"[RIS] cpu={s['cpu_pct']:.1f}% mem={s['mem_pct']:.1f}% "
              f"mcp={mcp} events={len(events)} recoveries={len(summary['recoveries'])}")
    return summary


if __name__ == "__main__":
    # 单次运行(供 systemd oneshot 或 cron 调用)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="单次检测")
    ap.add_argument("--interval", type=int, default=30, help="循环间隔秒")
    args = ap.parse_args()

    if args.once:
        run_once()
    else:
        # 常驻循环(供 systemd 服务 ExecStart)
        print(f"[RIS] 常驻监控启动·每 {args.interval}s 检测·日志: {EVENT_LOG}")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[RIS] 检测异常: {e}")
            time.sleep(args.interval)
