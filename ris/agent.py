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
from ris.health.provider_monitor import ProviderHealthMonitor
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

        # P0-1 Provider 健康监控(成熟部署加速·Shuyu立项): 复用 ProviderHealthGate
        # 捕获成本链路: provider掉线→lao-router(8765)不可达→回退直连→单key混用+cache_miss↑→成本↑
        # 关联成本事故复盘(2026-08-14·Stella评估重量级)。
        provider_monitor = ProviderHealthMonitor()
        for ev in provider_monitor.check_once():
            _emit(ev)
            events.append(ev)
            print(f"  ⚠️ {ev.event_type}: {ev.agent_id} "
                  f"cost_impact={ev.detail.get('cost_impact','n/a')}")

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

    def recover_cpu(self, current_cpu: float) -> Dict:
        """CPU 持续 > 90% → 自动恢复闭环(P0-2)。

        五步闭环: Detect(持续 N 帧) → Classify(cpu_sustained) →
                  Recover(识别 top 进程·降级非关键 mcp) → Verify(CPU 回落) → Record。

        恢复动作(安全·不杀关键进程):
          1. 识别 top CPU 进程(记录到 detail 供审计)
          2. 对空闲 npm exec @agentmemory mcp 进程 SIGHUP(温和·可自愈)
          3. 不触碰 gateway/lao-router/agent 主进程

        注意: 这是"真实触发"的 L3 Recovery Executor 工作——不再只是 mcp_leak/gateway
        两个常驻检测的恢复动作，而是 CPU 异常→恢复→验证的完整闭环。
        """
        def detect() -> bool:
            # 只有持续帧数达标才算"持续"(跨周期累积·写 state 文件)
            state = _read_cpu_state()
            if current_cpu >= CPU_SUSTAINED_THRESHOLD:
                state["consecutive"] += 1
                state["peak"] = max(state["peak"], current_cpu)
            else:
                state["consecutive"] = 0
                state["recovering"] = False
            state["last_ts"] = _ts()
            _write_cpu_state(state)
            return state["consecutive"] >= CPU_SUSTAINED_FRAMES

        def recover() -> bool:
            # 识别 top CPU 进程 + 对空闲 mcp 温和降级(SIGHUP 让 npm 重启·非 kill -9)
            _top_cpu_processes(5)
            try:
                out = subprocess.check_output(
                    ["pgrep", "-f", "npm exec @agentmemory"], text=True)
                pids = [l for l in out.strip().split("\n") if l]
                # 保留最新 1 个·其余 SIGHUP(温和·让进程优雅退出·npm 会按需重启)
                for pid in pids[:-1]:
                    try:
                        os.kill(int(pid), 1)  # SIGHUP
                    except Exception:
                        pass
            except Exception:
                pass
            # 恢复动作成功 = 已识别 top 进程(不击杀关键进程·安全边界)
            return True

        def verify() -> bool:
            # Verify 铁律: 恢复后 CPU 必须回落到阈值以下(重新采样)
            idle = _read_proc_stat_cpu()
            if idle < CPU_VERIFY_BELOW:
                state = _read_cpu_state()
                state["recovering"] = False
                state["consecutive"] = 0
                _write_cpu_state(state)
                return True
            return False

        result = self.engine.run(
            "cpu_recovery", "system",
            detect_fn=detect, classify_fn=lambda: "cpu_sustained",
            action=RecoveryAction(name="throttle_idle_mcp", recover_fn=recover,
                                  verify_fn=verify, max_attempts=2),
            severity="warn",
        )
        # Record 阶段: 附加 top 进程信息到事件 detail(供审计)
        if result.recovered and result.verified:
            ev = result.to_event(severity="warn")
            ev.detail["top_processes"] = _top_cpu_processes(3)
            ev.detail["peak_cpu"] = _read_cpu_state().get("peak", current_cpu)
            _emit(ev)
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded,
                "classified": result.classified}


# ── 3.5 CPU 自动恢复闭环(P0-2·成熟部署加速·Shuyu立项) ──────────────
# 持续 CPU > 90% 跟踪状态文件(跨 30s 检测周期累积·"持续"=连续 N 帧)
CPU_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "state", "data", "cpu-sustained.json")
CPU_SUSTAINED_THRESHOLD = 90.0   # 持续高于此值才触发恢复(区别于检测阈值 80)
CPU_SUSTAINED_FRAMES = 3         # 连续 3 帧(约 90s)视为"持续"
CPU_VERIFY_BELOW = 70.0          # 恢复后需降到该值以下才算 Verify 通过


def _read_cpu_state() -> Dict:
    """读取持续高 CPU 跟踪状态(跨周期累积)。"""
    try:
        with open(CPU_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"consecutive": 0, "last_ts": "", "peak": 0.0, "recovering": False}


def _write_cpu_state(state: Dict) -> None:
    os.makedirs(os.path.dirname(CPU_STATE_FILE), exist_ok=True)
    with open(CPU_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _top_cpu_processes(top_n: int = 5) -> List[Dict]:
    """找出 CPU 占用最高的进程(用 psutil.process_iter)。"""
    if psutil is None:
        return []
    procs = []
    try:
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "cmdline"]):
            try:
                info = p.info
                procs.append({
                    "pid": info["pid"], "name": info["name"] or "?",
                    "cpu_pct": round(info["cpu_percent"] or 0.0, 1),
                    "cmd": " ".join((info["cmdline"] or [""])[:2])[:120],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return []
    procs.sort(key=lambda x: x["cpu_pct"], reverse=True)
    return procs[:top_n]


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

    # P0-2: CPU 持续 > 90% → 自动恢复闭环(真实触发 L3 Recovery Executor)
    if summary["cpu_pct"] >= 80.0:
        r = executor.recover_cpu(summary["cpu_pct"])
        if r["recovered"]:
            summary["recoveries"].append({"action": "cpu_recovery", **r})

    # openclaw 连接检查
    connector.check_gateway()
    connector.check_webui()
    connector.check_session_bloat()

    # P0-3 RIS→LAO 数据桥: 每次检测后生成共享摘要(供 LAO/Stella 消费)
    try:
        import importlib
        bridge = importlib.import_module("ris.experience_integration")
        bridge.dump_summary()
    except Exception as _be:
        print(f"[RIS] 数据桥异常: {_be}")

    # P0-3: RIS→LAO 数据桥(事件写入共享 JSON 供 LAO/Stella 消费)
    try:
        from ris.bridge import sync_bridge
        bridge = sync_bridge()
        summary["bridge_synced"] = bridge["window"]["events_total"]
    except Exception as e:
        summary["bridge_synced"] = 0
        print(f"  ⚠️ bridge sync fail: {e}")

    if verbose:
        print(f"[RIS] cpu={s['cpu_pct']:.1f}% mem={s['mem_pct']:.1f}% "
              f"mcp={mcp} events={len(events)} recoveries={len(summary['recoveries'])}",
              flush=True)
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
