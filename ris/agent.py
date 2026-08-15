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
import gzip, json, os, shutil, time, subprocess, sys
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
from ris.provider import ProviderIsolator          # B5: 真实隔离/熔断
from ris.lao_signal import LAOSignalMonitor        # B2: LAO→RIS 反向桥消费
from ris.config_drift import ConfigDriftWatcher    # B3: 配置漂移运行时接线
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


def _mcp_pids() -> List[str]:
    try:
        out = subprocess.check_output(["pgrep", "-f", "npm exec @agentmemory"], text=True)
        return [l for l in out.strip().split("\n") if l]
    except Exception:
        return []


def _count_mcp() -> int:
    return len(_mcp_pids())


def _read_proc_stat_cpu() -> float:
    """开机以来平均 CPU(仅供 psutil 缺失时的检测兜底)。

    ⚠️ B1 教训: 该值是开机累计均值, 本机长期远低于恢复阈值 →
    任何 Verify 逻辑禁止使用本函数(恒真缺陷根源), 恢复后验必须用
    _instant_cpu_percent() 独立瞬时重采样。
    """
    try:
        with open("/proc/stat") as f:
            line = f.readline().split()
        total = sum(int(x) for x in line[1:])
        idle = int(line[4])
        return round((1 - idle / total) * 100, 1) if total else 0.0
    except Exception:
        return 0.0


def _instant_cpu_percent(interval: float = 1.0) -> float:
    """独立瞬时 CPU 采样(B1 修复): psutil 实时采样优先, /proc 双读差分兜底。

    与 _read_proc_stat_cpu 的本质区别:
      - 单次读 /proc/stat = 开机以来平均(本机≈26%·恒低于阈值 → verify 恒真)
      - 本函数 = interval 秒窗口内的真实增量(psutil.cpu_percent 语义)
    """
    if psutil is not None:
        return round(psutil.cpu_percent(interval=interval), 1)

    def _snap():
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)

    i0, t0 = _snap()
    time.sleep(max(interval, 0.05))
    i1, t1 = _snap()
    dt = t1 - t0
    return round((1 - (i1 - i0) / dt) * 100, 1) if dt > 0 else 0.0


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
            # 进程事件不再静默丢弃: 只落异常(process_restart/process_down)·ok 不刷屏
            pev = self.process.check_process("gateway", pid=gw_pid)
            if pev.event_type != "process_ok":
                _emit(pev)
                events.append(pev)
                print(f"  ⚠️ {pev.event_type}: gateway old={pev.detail.get('old_pid')} new={gw_pid}")
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

        # P0-1 Provider 健康监控 + B5 真实隔离(连续失败确认 → 熔断 → 冷却/释放)
        # 捕获成本链路: provider掉线→lao-router(8765)不可达→回退直连→单key混用+cache_miss↑→成本↑
        # 关联成本事故复盘(2026-08-14·Stella评估重量级)。
        # B5 修复: provider_unavailable 不再是死胡同——连续失败 → ProviderIsolator
        # 隔离(状态落盘) → 隔离指令进 ris-bridge → LAO 真实摘除该 provider。
        provider_monitor = ProviderHealthMonitor()
        isolator = ProviderIsolator()
        for ev in provider_monitor.check_once(emit_ok=True):
            provider = (ev.detail.get("provider") or ev.agent_id or "").lower()
            if ev.event_type == "provider_unavailable":
                _emit(ev)
                events.append(ev)
                print(f"  ⚠️ provider_unavailable: {provider} "
                      f"cost_impact={ev.detail.get('cost_impact', 'n/a')}")
                iso = isolator.record_failure(
                    provider, ev.detail.get("error") or ev.detail.get("reason", ""))
            else:
                # 探活成功 → 失败计数复位; 隔离中的 provider 提前释放(灰度回归)
                # (provider_ok 事件本身不落日志·避免每 30s 刷屏)
                iso = isolator.record_success(provider)
            if iso is not None:
                _emit(iso)
                events.append(iso)
                print(f"  🚧 provider_isolation: {iso.detail.get('provider')} → {iso.status}")

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
    """检测到异常 → 执行恢复 → Verify → Record。

    B7 修复: 每次恢复尝试(attempts>0)自动经 _distill 沉淀恢复经验
    (recovery_experience.jsonl·替代手工种子)。
    """

    def __init__(self, experience_extractor=None):
        self.engine = RecoveryEngine()
        self._experience = experience_extractor  # None → 首次使用时懒加载默认提取器

    def _distill(self, result, recovery_method: str):
        """B7: 恢复经验自动沉淀(接线 ris.experience.RiskExperienceExtractor)。"""
        if result.attempts == 0 and not result.recovered:
            return None  # 未实际尝试恢复·不沉淀(防 30s 周期刷噪声)
        try:
            if self._experience is None:
                from ris.experience.risk_experience_extractor import RiskExperienceExtractor
                self._experience = RiskExperienceExtractor()
            return self._experience.extract_from_recovery(result, recovery_method)
        except Exception as e:
            print(f"  ⚠️ 恢复经验沉淀异常: {e}")
            return None

    @staticmethod
    def _emit_result(result, severity: str = "warn", **detail_extra) -> Optional[RuntimeHealthEvent]:
        """恢复结果统一落事件(治审计 B9: 不再只有 cpu_recovery 落盘)。"""
        if result.attempts == 0 and not result.recovered:
            return None
        ev = result.to_event(severity=severity)
        ev.detail.update(detail_extra)
        _emit(ev)
        return ev

    def recover_mcp_leak(self) -> Dict:
        """mcp 泄漏清理(安全·已验证·首个真实恢复动作)。"""
        def detect() -> bool:
            return len(_mcp_pids()) > 4

        def recover() -> bool:
            # 清掉空闲 mcp(保留最新1个)
            pids = _mcp_pids()
            for pid in pids[:-1]:
                subprocess.run(["kill", pid], timeout=5)
            return True

        def verify() -> bool:
            return len(_mcp_pids()) <= 4

        result = self.engine.run(
            "mcp_leak", "mcp",
            detect_fn=detect, classify_fn=lambda: "mcp_leak",
            action=RecoveryAction(name="cleanup_mcp", recover_fn=recover,
                                  verify_fn=verify, max_attempts=2),
        )
        self._emit_result(result, verify_method="count-threshold")
        self._distill(result, "cleanup_mcp")
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
        self._emit_result(result, severity="error", verify_method="pid-alive")
        self._distill(result, "restart_lao_router")
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded}

    def recover_cpu(self, current_cpu: float,
                    settle_s: Optional[float] = None,
                    sample_interval: Optional[float] = None) -> Dict:
        """CPU 持续 > 90% → 自动恢复闭环(P0-2 + B1 修复)。

        五步闭环: Detect(持续 N 帧) → Classify(cpu_sustained) →
                  Recover(识别 top 进程·降级非关键 mcp) → Verify(独立瞬时重采样) → Record。

        B1 修复(审计最严重缺陷): 旧 verify 用 _read_proc_stat_cpu() 的
        开机累计均值(本机≈26% < 阈值70%) → 恒真 → 27 条"verified:true"全部无效。
        现 verify = 回落观察窗(settle_s) + _instant_cpu_percent() 独立瞬时重采样,
        并在事件 detail 记录 verify_method / cpu_after_recover 供审计。

        恢复动作(安全·不杀关键进程):
          1. 识别 top CPU 进程(记录到 detail 供审计)
          2. 对空闲 npm exec @agentmemory mcp 进程 SIGHUP(温和·可自愈)
          3. 不触碰 gateway/lao-router/agent 主进程
        """
        settle_s = CPU_VERIFY_SETTLE_S if settle_s is None else settle_s
        sample_interval = CPU_VERIFY_SAMPLE_S if sample_interval is None else sample_interval
        evidence: Dict = {}

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
            try:
                pids = _mcp_pids()
                # 保留最新 1 个·其余 SIGHUP(温和·让进程优雅退出·npm 会按需重启)
                for pid in pids[:-1]:
                    try:
                        os.kill(int(pid), 1)  # SIGHUP
                    except Exception:
                        pass
            except Exception:
                pass
            return True

        def verify() -> bool:
            # B1: 独立后验——回落观察窗 + 瞬时重采样(禁止 /proc 开机均值恒真)
            if settle_s > 0:
                time.sleep(settle_s)
            cpu_after = _instant_cpu_percent(sample_interval)
            evidence["verify_method"] = "resample-instant"
            evidence["cpu_after_recover"] = cpu_after
            if cpu_after < CPU_VERIFY_BELOW:
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
        result.detail.update(evidence)
        # Record 阶段: 附加 top 进程/峰值/verify 证据到事件 detail(供审计)
        self._emit_result(result,
                          severity="warn" if result.verified else "error",
                          top_processes=_top_cpu_processes(3),
                          peak_cpu=_read_cpu_state().get("peak", current_cpu))
        self._distill(result, "throttle_idle_mcp")
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded,
                "classified": result.classified,
                "verify_method": evidence.get("verify_method"),
                "cpu_after_recover": evidence.get("cpu_after_recover")}

    def recover_session_bloat(self, big: Optional[List[Dict]] = None) -> Dict:
        """B4: session 膨胀真实处置——超龄(≥SESSION_ARCHIVE_AGE_DAYS)大文件 gzip 冷归档。

        五步闭环: Detect(有可归档文件) → Classify(session_bloat_stale) →
        Recover(gzip→archive 目录·删原件·真实回收磁盘) → Verify(重扫确认已消失) →
        Record(session_recovery 事件 + 经验沉淀)。

        安全边界: 只动 mtime 超过 30 天的大文件(活跃会话不动·避免破坏进行中对话);
        活跃膨胀文件走 _session_bloat_alert() 降噪告警。
        """
        if big is None:
            big = scan_session_bloat()
        archivable = [f for f in big if f["age_days"] >= SESSION_ARCHIVE_AGE_DAYS]
        archived: List[Dict] = []

        def detect() -> bool:
            return bool(archivable)

        def recover() -> bool:
            for f in archivable:
                try:
                    dest = _archive_session_file(f["file"])
                    archived.append({"file": f["file"], "mb": f["mb"], "archived_to": dest})
                except Exception:
                    pass
            return bool(archived)

        def verify() -> bool:
            return all(not os.path.exists(f["file"]) for f in archivable)

        result = self.engine.run(
            "session_recovery", "sessions",
            detect_fn=detect, classify_fn=lambda: "session_bloat_stale",
            action=RecoveryAction(name="archive_bloat", recover_fn=recover,
                                  verify_fn=verify, max_attempts=1),
            severity="warn",
        )
        self._emit_result(result,
                          severity="warn" if result.verified else "error",
                          verify_method="rescan-gone",
                          archived=archived,
                          reclaimed_mb=round(sum(a["mb"] for a in archived), 1))
        self._distill(result, "archive_bloat")
        return {"recovered": result.recovered, "verified": result.verified,
                "attempts": result.attempts, "recorded": result.recorded,
                "archived": len(archived)}


# ── 3.5 CPU 自动恢复闭环(P0-2·成熟部署加速·Shuyu立项) ──────────────
# 持续 CPU > 90% 跟踪状态文件(跨 30s 检测周期累积·"持续"=连续 N 帧)
CPU_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "state", "data", "cpu-sustained.json")
CPU_SUSTAINED_THRESHOLD = 90.0   # 持续高于此值才触发恢复(区别于检测阈值 80)
CPU_SUSTAINED_FRAMES = 3         # 连续 3 帧(约 90s)视为"持续"
CPU_VERIFY_BELOW = 70.0          # 恢复后需降到该值以下才算 Verify 通过
CPU_VERIFY_SETTLE_S = 5.0        # B1: 恢复后回落观察窗(毫秒级 verify 反映不了 SIGHUP 效果)
CPU_VERIFY_SAMPLE_S = 1.0        # B1: 独立瞬时采样窗口(psutil.cpu_percent interval)

# ── B4: Session 膨胀真实处置 + 降噪(治 1533 次刷屏 / 0 处置) ────────
SESSION_DIR = "/home/agentuser/.openclaw/agents"
SESSION_ARCHIVE_DIR = "/home/agentuser/.openclaw/agents-archive"  # 归档目录(监控树外·不会重扫)
SESSION_BLOAT_STATE_FILE = os.path.join(RIS_DIR, "state", "data", "session-bloat-state.json")
SESSION_BLOAT_MIN_MB = 8.0        # 膨胀检测阈值(与既有 8MB 一致)
SESSION_ARCHIVE_AGE_DAYS = 30     # 只归档超龄文件(活跃会话不动·安全边界)
SESSION_ALERT_COOLDOWN_S = 1800   # 同一膨胀文件集合 30 分钟只告警一次(降噪)


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


# ── B4: session 膨胀扫描 / 冷归档 / 降噪告警 ─────────────────────────
def scan_session_bloat(max_mb: float = SESSION_BLOAT_MIN_MB) -> List[Dict]:
    """扫描 session 目录·返回超过 max_mb 的 .jsonl 文件(含大小/龄期)。"""
    now = time.time()
    big: List[Dict] = []
    for root, _, files in os.walk(SESSION_DIR):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(root, fn)
            try:
                sz_mb = os.path.getsize(fp) / 1024 / 1024
                if sz_mb > max_mb:
                    age_days = (now - os.path.getmtime(fp)) / 86400
                    big.append({"file": fp, "mb": round(sz_mb, 1),
                                "age_days": round(age_days, 1)})
            except OSError:
                continue
    return big


def _archive_session_file(fp: str) -> str:
    """gzip 冷归档: 原件压缩到 SESSION_ARCHIVE_DIR(保子路径)后删除·真实回收磁盘。"""
    rel = os.path.relpath(fp, SESSION_DIR)
    dest = os.path.join(SESSION_ARCHIVE_DIR, rel + ".gz")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(fp, "rb") as fin, gzip.open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    os.remove(fp)
    return dest


def _session_bloat_alert(big: List[Dict]) -> Optional[RuntimeHealthEvent]:
    """B4 降噪告警: 同一膨胀文件集合在冷却期内只告警一次(治 30s 重复刷屏)。"""
    if not big:
        return None
    sig = sorted(f["file"] for f in big)
    state: Dict = {}
    try:
        with open(SESSION_BLOAT_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    now = time.time()
    if state.get("last_sig") == sig and \
       now - state.get("last_ts", 0.0) < SESSION_ALERT_COOLDOWN_S:
        return None
    os.makedirs(os.path.dirname(SESSION_BLOAT_STATE_FILE), exist_ok=True)
    with open(SESSION_BLOAT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_sig": sig, "last_ts": now}, f)
    return _emit(RuntimeHealthEvent(
        event_type="session_bloat", agent_id="sessions", status="detected",
        severity="warn",
        detail={"files": [{"file": b["file"], "mb": b["mb"], "age_days": b["age_days"]}
                          for b in big[:5]],
                "count": len(big),
                "cooldown_s": SESSION_ALERT_COOLDOWN_S}))


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


# ── 主循环(常驻·systemd 调用) ─────────────────────────────────────
def _recent_event_dicts(n: int = 200) -> List[Dict]:
    """读事件日志尾部 n 条(供 config drift 与故障做时间窗关联)。"""
    try:
        with open(EVENT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _check_lao_signal(events: List[RuntimeHealthEvent]) -> None:
    """B2: 消费 LAO→RIS 反向桥(lao-signal.json)。

    LAO 转发错误率超阈 → provider 退化事件 → ProviderIsolator 隔离记账 →
    隔离指令随 ris-bridge 同步给 LAO → 双向数据飞轮闭环。
    """
    try:
        isolator = ProviderIsolator()
        for ev in LAOSignalMonitor().check_once():
            _emit(ev)
            events.append(ev)
            print(f"  ⚠️ provider_unavailable(lao-signal): {ev.agent_id} "
                  f"{ev.detail.get('reason', '')}")
            iso = isolator.record_failure(ev.agent_id, ev.detail.get("reason", ""))
            if iso is not None:
                _emit(iso)
                events.append(iso)
                print(f"  🚧 provider_isolation: {iso.detail.get('provider')} → {iso.status}")
    except Exception as e:
        print(f"  ⚠️ lao-signal consume fail: {e}")


def _check_config_drift(events: List[RuntimeHealthEvent]) -> None:
    """B3: 配置漂移检测(基线 diff) + 与故障事件时间窗关联(可靠性影响评估)。"""
    try:
        watcher = ConfigDriftWatcher()
        drift_events = watcher.check_once()
        if drift_events:
            drift_events = watcher.correlate(drift_events, _recent_event_dicts(200))
            for ev in drift_events:
                _emit(ev)
                events.append(ev)
                print(f"  ⚠️ config_drift: {ev.detail.get('field')} "
                      f"who={ev.detail.get('who')} impact={ev.detail.get('impact')}")
    except Exception as e:
        print(f"  ⚠️ config drift check fail: {e}")


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

    # P0-2 + B1: CPU 持续高载恢复闭环(每轮驱动帧状态机·Verify=独立瞬时重采样)
    r = executor.recover_cpu(summary["cpu_pct"])
    if r["attempts"] > 0:
        summary["recoveries"].append({"action": "cpu_recovery", **r})

    # B4: session 膨胀——超龄大文件真实处置(gzip 冷归档)+ 剩余降噪告警
    big = scan_session_bloat()
    if big:
        r = executor.recover_session_bloat(big)
        if r["attempts"] > 0:
            summary["recoveries"].append({"action": "session_recovery", **r})
        remaining = scan_session_bloat()
        if remaining:
            _session_bloat_alert(remaining)

    # openclaw 连接检查
    connector.check_gateway()
    connector.check_webui()

    # B2: LAO→RIS 反向桥消费(错误率退化 → 事件 → 隔离)
    _check_lao_signal(events)

    # B3: 配置漂移检测 + 可靠性影响评估
    _check_config_drift(events)
    summary["events"] = len(events)

    # P0-3 RIS→LAO 数据桥: 每次检测后生成共享摘要(供 LAO/Stella 消费)
    try:
        import importlib
        bridge = importlib.import_module("ris.experience_integration")
        bridge.dump_summary()
    except Exception as _be:
        print(f"[RIS] 数据桥异常: {_be}")

    # P0-3 + B5: RIS→LAO 数据桥(含 provider_status + isolated_providers 隔离指令)
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
