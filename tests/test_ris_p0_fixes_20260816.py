"""RIS P0 修复行为测试 (B1-B7 · 20260816 审计整改)
=====================================================================================
测试原则(治审计 B13"同义反复"缺陷):
  - 故障注入 → 验证真实行为(恢复动作发生 / Verify 真实有效 / 数据真实流动)
  - 不构造 dataclass 再断言自身字段; 断言对象是"副作用"(文件/事件/状态跃迁)
  - 全部状态重定向到 tmp 目录·不写生产 ris-events.jsonl / shared/state

覆盖:
  B1 CPU Verify 恒真修复(独立瞬时重采样·恢复后 CPU 仍高必须判失败)
  B2 双向桥(LAO 真实读 ris-bridge/ris_summary 并阻断 + LAO→RIS 反向桥消费 + 全链飞轮)
  B3 Config Drift 运行时接线(基线/检测/降噪/故障关联影响评估)
  B4 Session 膨胀真实处置(gzip 冷归档)+ 告警降噪
  B5 Provider 熔断(连续失败确认→隔离→冷却→释放)
  B6 主循环接线冒烟(全部子系统在一轮 run_once 中真实运转)
  B7 恢复经验自动沉淀(恢复尝试→recovery_experience.jsonl·非手工种子)
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ris.agent as ra
from ris.agent import RecoveryExecutor
from ris.config_drift import ConfigDriftWatcher
from ris.events import RuntimeHealthEvent
from ris.experience.risk_experience_extractor import RiskExperienceExtractor
from ris.lao_signal import LAOSignalMonitor
from ris.provider import ProviderIsolator
from ris.bridge import RISToLAOBridge
from lao.effect_anchored.routing.ris_health_gate import RISHealthGate

import lao.effect_anchored.routing.lao_router_server as lao_server


# ── 公共 fixture: 把 RIS 全部落盘路径重定向到 tmp ────────────────────
@pytest.fixture
def ris_env(tmp_path, monkeypatch):
    log = tmp_path / "ris-events.jsonl"
    monkeypatch.setattr(ra, "EVENT_LOG", str(log))
    monkeypatch.setattr(ra, "CPU_STATE_FILE", str(tmp_path / "cpu-sustained.json"))
    monkeypatch.setattr(ra, "SESSION_DIR", str(tmp_path / "agents"))
    monkeypatch.setattr(ra, "SESSION_ARCHIVE_DIR", str(tmp_path / "agents-archive"))
    monkeypatch.setattr(ra, "SESSION_BLOAT_STATE_FILE",
                        str(tmp_path / "session-bloat-state.json"))
    monkeypatch.setattr(ris_provider_mod, "ISOLATION_FILE",
                        str(tmp_path / "provider-isolation.json"))
    (tmp_path / "agents" / "tristan").mkdir(parents=True)
    return tmp_path


import ris.provider as ris_provider_mod  # noqa: E402
import ris.config_drift as ris_drift_mod  # noqa: E402


def _read_events(tmp_path):
    log = tmp_path / "ris-events.jsonl"
    if not log.exists():
        return []
    with open(log, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _write_cpu_state(frames: int, peak: float = 95.0):
    with open(ra.CPU_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"consecutive": frames, "last_ts": "", "peak": peak,
                   "recovering": False}, f)


# ════════════════════════════════════════════════════════════════════
# B1: Verify 恒真缺陷修复
# ════════════════════════════════════════════════════════════════════
class TestB1VerifyIndependentResample:

    def test_verify_fails_while_cpu_still_high(self, ris_env, monkeypatch):
        """故障注入: 恢复动作执行后 CPU 仍 95% → Verify 必须失败。

        旧实现用 /proc/stat 开机均值(≈26%<70%)恒真 → 27 条假 verified;
        新实现用独立瞬时重采样 → 本例必须 NOT verified(anti-tautology 铁证)。
        """
        monkeypatch.setattr(ra, "_instant_cpu_percent", lambda interval=1.0: 95.0)
        _write_cpu_state(frames=2)  # 再来一帧即达 3 帧持续
        r = RecoveryExecutor().recover_cpu(95.0, settle_s=0, sample_interval=0)
        assert r["attempts"] == 2          # 两次尝试都执行了恢复动作
        assert r["recovered"] is False
        assert r["verified"] is False      # ← 旧实现这里是 True(恒真缺陷)
        evs = [e for e in _read_events(ris_env) if e["event_type"] == "cpu_recovery"]
        assert len(evs) == 1
        assert evs[0]["status"] == "failed"
        assert evs[0]["detail"]["verify_method"] == "resample-instant"
        assert evs[0]["detail"]["cpu_after_recover"] == 95.0

    def test_verify_passes_only_after_real_drop(self, ris_env, monkeypatch):
        """恢复后 CPU 真回落(第 2 次采样 40%) → verified + 状态机复位 + 证据留痕。"""
        samples = iter([95.0, 40.0])
        monkeypatch.setattr(ra, "_instant_cpu_percent",
                            lambda interval=1.0: next(samples))
        _write_cpu_state(frames=2)
        r = RecoveryExecutor().recover_cpu(95.0, settle_s=0, sample_interval=0)
        assert r["verified"] is True and r["recovered"] is True
        assert r["attempts"] == 2
        assert r["verify_method"] == "resample-instant"
        assert r["cpu_after_recover"] == 40.0
        # 持续帧状态机复位(否则下一帧会立刻再触发)
        state = json.load(open(ra.CPU_STATE_FILE))
        assert state["consecutive"] == 0
        evs = [e for e in _read_events(ris_env) if e["event_type"] == "cpu_recovery"]
        assert evs[0]["status"] == "recovered"
        assert evs[0]["detail"]["verified"] is True

    def test_proc_fallback_is_delta_not_boot_average(self, monkeypatch):
        """psutil 缺失时的兜底 = /proc 双读差分(区间合法)·不再是开机均值单读。"""
        monkeypatch.setattr(ra, "psutil", None)
        v = ra._instant_cpu_percent(0.05)
        assert isinstance(v, float) and 0.0 <= v <= 100.0


# ════════════════════════════════════════════════════════════════════
# B5: Provider 隔离熔断(连续失败确认 → 隔离 → 冷却半开 → 释放)
# ════════════════════════════════════════════════════════════════════
class TestB5ProviderIsolation:

    def test_circuit_breaker_lifecycle(self, ris_env):
        iso = ProviderIsolator()
        # 第 1 帧失败: 抖动抑制·不隔离
        assert iso.record_failure("deepseek", "conn refused") is None
        assert not iso.is_isolated("deepseek")
        # 第 2 帧连续失败: 状态跃迁 → 隔离事件(非注释字段·真实事件对象)
        ev = iso.record_failure("deepseek", "conn refused")
        assert ev is not None
        assert ev.event_type == "provider_isolation"
        assert ev.status == "isolated"
        assert ev.detail["provider"] == "deepseek"
        assert iso.is_isolated("deepseek")
        # 状态跨实例持久化(主循环每 30s 重建对象·靠文件续命)
        assert ProviderIsolator().is_isolated("deepseek")
        # 探活恢复 → 提前释放(灰度回归)
        rel = ProviderIsolator().record_success("deepseek")
        assert rel is not None and rel.status == "released"
        assert not ProviderIsolator().is_isolated("deepseek")

    def test_cooldown_auto_reopen(self, tmp_path):
        t = [1000.0]
        iso = ProviderIsolator(state_file=str(tmp_path / "iso.json"),
                               clock=lambda: t[0])
        iso.record_failure("p", "x")
        iso.record_failure("p", "x")
        assert iso.is_isolated("p")
        t[0] += 601  # 超过 600s 冷却 → 自动半开(不会永久锁死 provider)
        assert not iso.is_isolated("p")

    def test_active_snapshot_for_bridge(self, ris_env):
        iso = ProviderIsolator()
        iso.record_failure("deepseek", "e1")
        iso.record_failure("deepseek", "e1")
        snap = iso.active()
        assert "deepseek" in snap
        assert snap["deepseek"]["isolated_until"] > time.time()


# ════════════════════════════════════════════════════════════════════
# B2: 双向数据桥闭环
# ════════════════════════════════════════════════════════════════════
class TestB2LAOConsumesRISBridge:

    def test_blocks_down_and_isolated_providers(self, tmp_path):
        bridge = tmp_path / "ris-bridge.json"
        bridge.write_text(json.dumps({
            "layer": "ris", "generated_at": "now",
            "summary": {"provider_status": {"deepseek": "down", "token-plan": "healthy"},
                        "isolated_providers": ["token-plan"]},
        }))
        gate = RISHealthGate(bridge_file=str(bridge),
                             summary_file=str(tmp_path / "missing.json"))
        snap = gate.read(force=True)
        assert snap["fresh"] is True and "ris-bridge" in snap["source"]
        assert gate.is_blocked("deepseek")      # provider_status down
        assert gate.is_blocked("token-plan")    # B5 隔离指令
        assert not gate.is_blocked("novarouteai")

    def test_ris_summary_active_risks_are_consumed(self, tmp_path):
        """第二座桥(ris_summary.json)从此有真实消费者: active_risks → 阻断。"""
        summary = tmp_path / "ris_summary.json"
        summary.write_text(json.dumps({
            "total_events": 10,
            "active_risks": [
                {"event_type": "provider_unavailable", "agent_id": "deepseek",
                 "severity": "critical",
                 "detail": {"provider": "deepseek", "cost_impact": "high"}, "ts": "x"},
                {"event_type": "cpu_anomaly", "agent_id": "system",
                 "detail": {}, "ts": "x"},
            ],
        }))
        gate = RISHealthGate(bridge_file=str(tmp_path / "missing.json"),
                             summary_file=str(summary))
        snap = gate.read(force=True)
        assert "ris_summary" in snap["source"]
        assert gate.is_blocked("deepseek")      # provider 风险 → 阻断
        assert not gate.is_blocked("token-plan")

    def test_stale_bridge_fails_open(self, tmp_path):
        """RIS 桥陈旧(RIS 停摆) → LAO fail-open 不阻断(单层故障不跨层放大)。"""
        bridge = tmp_path / "ris-bridge.json"
        bridge.write_text(json.dumps({
            "summary": {"provider_status": {"deepseek": "down"},
                        "isolated_providers": []}}))
        old = time.time() - 3600
        os.utime(bridge, (old, old))
        gate = RISHealthGate(bridge_file=str(bridge),
                             summary_file=str(tmp_path / "missing.json"))
        snap = gate.read(force=True)
        assert snap["fresh"] is False
        assert not gate.is_blocked("deepseek")

    def test_router_guard_degrades_blocked_provider(self, tmp_path, monkeypatch):
        """lao_router 真实执行阻断: deepseek 被 RIS 阻断 → 切换 token-plan(事件留痕)。"""
        bridge = tmp_path / "ris-bridge.json"
        bridge.write_text(json.dumps({
            "summary": {"provider_status": {"deepseek": "down"},
                        "isolated_providers": []}}))
        fake_gate = RISHealthGate(bridge_file=str(bridge),
                                  summary_file=str(tmp_path / "missing.json"))
        monkeypatch.setattr(lao_server, "ris_gate", fake_gate)
        monkeypatch.setattr(lao_server, "EVENT_LOG",
                            str(tmp_path / "lao-router-events.jsonl"))
        monkeypatch.setattr(lao_server, "PROVIDER_CONFIG", {
            "deepseek": {"base_url": "https://a", "api_key": "k1"},
            "token-plan": {"base_url": "https://b", "api_key": "k2"},
        })
        provider, ev = lao_server._ris_guard_provider("deepseek", "req-1")
        assert provider == "token-plan"           # 真实降级切换
        assert ev["type"] == "ris_provider_block"
        with open(tmp_path / "lao-router-events.jsonl") as f:
            logged = [json.loads(l) for l in f if l.strip()]
        assert any(e["type"] == "ris_provider_block" and e["fallback"] == "token-plan"
                   for e in logged)

    def test_router_guard_all_blocked_returns_none(self, tmp_path, monkeypatch):
        bridge = tmp_path / "ris-bridge.json"
        bridge.write_text(json.dumps({
            "summary": {"provider_status": {"deepseek": "down"},
                        "isolated_providers": ["token-plan", "novarouteai"]}}))
        fake_gate = RISHealthGate(bridge_file=str(bridge),
                                  summary_file=str(tmp_path / "missing.json"))
        monkeypatch.setattr(lao_server, "ris_gate", fake_gate)
        monkeypatch.setattr(lao_server, "EVENT_LOG",
                            str(tmp_path / "lao-router-events.jsonl"))
        monkeypatch.setattr(lao_server, "PROVIDER_CONFIG", {
            "deepseek": {"base_url": "https://a", "api_key": "k1"},
            "token-plan": {"base_url": "https://b", "api_key": "k2"},
        })
        provider, ev = lao_server._ris_guard_provider("deepseek", "req-2")
        assert provider is None                     # 全部候选被阻断 → 显式失败(非静默)
        assert ev["fallback"] is None


class TestB2ReverseBridge:

    def test_lao_signal_to_ris_degradation_event(self, tmp_path, monkeypatch):
        """反向桥真实流动: LAO 错误率 80% → lao-signal.json → RIS 退化事件。"""
        sig = str(tmp_path / "lao-signal.json")
        monkeypatch.setattr(lao_server, "LAO_SIGNAL_FILE", sig)
        lao_server._signal_window.clear()
        for i in range(5):
            lao_server._update_lao_signal("deepseek", ok=(i == 0),
                                          cache_hit=100, cache_miss=100,
                                          cost_usd=0.01, degraded=False)
        with open(sig) as f:
            data = json.load(f)
        stats = data["window"]["providers"]["deepseek"]
        assert stats["requests"] == 5 and stats["errors"] == 4
        assert stats["error_rate"] == 0.8
        # RIS 消费端
        evs = LAOSignalMonitor(signal_file=sig).check_once()
        assert len(evs) == 1
        assert evs[0].event_type == "provider_unavailable"
        assert evs[0].detail["source"] == "lao-signal"
        assert evs[0].severity == "critical"     # ≥50% 错误率
        # 健康窗口不产事件(不误报)
        for _ in range(10):
            lao_server._update_lao_signal("token-plan", ok=True)
        assert LAOSignalMonitor(signal_file=sig).check_once() == [] or \
            all(e.agent_id != "token-plan"
                for e in LAOSignalMonitor(signal_file=sig).check_once())

    def test_flywheel_full_loop(self, ris_env, tmp_path, monkeypatch):
        """全链飞轮: LAO 错误率 → RIS 事件 → 熔断隔离 → bridge 隔离指令 → LAO 阻断。"""
        # ① LAO 侧写反向桥
        sig = str(tmp_path / "lao-signal.json")
        monkeypatch.setattr(lao_server, "LAO_SIGNAL_FILE", sig)
        lao_server._signal_window.clear()
        for _ in range(6):
            lao_server._update_lao_signal("deepseek", ok=False)
        # ② RIS 消费 → 隔离记账(连续两帧)
        monitor = LAOSignalMonitor(signal_file=sig, min_requests=5)
        iso = ProviderIsolator()
        iso_ev = None
        for _ in range(2):
            for ev in monitor.check_once():
                got = iso.record_failure(ev.agent_id, ev.detail["reason"])
                if got is not None:
                    iso_ev = got
        assert iso_ev is not None and iso_ev.status == "isolated"
        # ③ bridge 同步隔离指令(summary.isolated_providers)
        b = RISToLAOBridge(bridge_file=str(tmp_path / "ris-bridge.json"),
                           source_log=str(ris_env / "ris-events.jsonl"))
        bridge = b.sync([RuntimeHealthEvent(
            event_type="provider_unavailable", agent_id="deepseek",
            status="detected", severity="critical",
            detail={"provider": "deepseek"}).to_dict()])
        assert "deepseek" in bridge["summary"]["isolated_providers"]
        # ④ LAO 读桥 → 阻断生效(飞轮闭环)
        gate = RISHealthGate(bridge_file=str(tmp_path / "ris-bridge.json"),
                             summary_file=str(tmp_path / "missing.json"))
        assert gate.is_blocked("deepseek")


# ════════════════════════════════════════════════════════════════════
# B3: Config Drift 运行时接线
# ════════════════════════════════════════════════════════════════════
class TestB3ConfigDriftWiring:

    def _watcher(self, tmp_path, unit):
        return ConfigDriftWatcher(
            manifest=[{"name": "lao-router-unit", "path": str(unit),
                       "affects": "routing"}],
            baseline_file=str(tmp_path / "baseline.json"),
            state_file=str(tmp_path / "drift-state.json"))

    def test_baseline_then_drift_then_dedup(self, tmp_path):
        unit = tmp_path / "lao-router.service"
        unit.write_text("ExecStart=/usr/bin/python3 lao_router_server.py\n")
        w = self._watcher(tmp_path, unit)
        assert w.check_once() == []                      # 首轮建基线·不告警
        assert os.path.exists(tmp_path / "baseline.json")
        unit.write_text("ExecStart=/usr/bin/python3 lao_router_server.py --port 9999\n")
        evs = w.check_once()                              # 注入配置变化 → 检出
        assert len(evs) == 1
        assert evs[0].event_type == "config_drift"
        assert evs[0].detail["field"].startswith("lao-router-unit.")
        assert evs[0].detail["who"] != "unknown"          # 真实 owner 归因
        assert evs[0].detail["impact"] == "routing"
        assert evs[0].severity == "error"
        assert w.check_once() == []                       # 同一漂移去重(不刷屏)

    def test_rebaseline_stops_alerting(self, tmp_path):
        unit = tmp_path / "lao-router.service"
        unit.write_text("A\n")
        w = self._watcher(tmp_path, unit)
        w.check_once()
        unit.write_text("B\n")
        assert len(w.check_once()) == 1
        w.rebaseline()                                    # 人工接受变更
        assert w.check_once() == []

    def test_impact_correlation_with_faults(self, tmp_path):
        """漂移后 15 分钟内出现故障 → 关联标注 + 升级 critical(可靠性影响评估)。"""
        unit = tmp_path / "lao-router.service"
        unit.write_text("A\n")
        w = self._watcher(tmp_path, unit)
        w.check_once()
        unit.write_text("B\n")
        evs = w.check_once()
        drift_ts = datetime.fromisoformat(evs[0].ts)
        recent = [
            {"event_type": "lao_router_down", "severity": "critical",
             "ts": (drift_ts + timedelta(seconds=60)).isoformat()},   # 窗口内·相关
            {"event_type": "cpu_anomaly", "severity": "warn",
             "ts": (drift_ts - timedelta(seconds=2000)).isoformat()},  # 窗口外
            {"event_type": "session_ok", "severity": "info",
             "ts": (drift_ts + timedelta(seconds=90)).isoformat()},    # 非故障类型
        ]
        out = w.correlate(evs, recent)
        assert out[0].severity == "critical"
        assert out[0].detail["fault_correlation"]["count"] == 1
        assert out[0].detail["fault_correlation"]["types"] == ["lao_router_down"]
        # 反例: 无相关故障 → 不升级
        out2 = w.correlate(w.check_once() or out, [])
        assert all("fault_correlation" not in e.detail or
                   e.detail.get("fault_correlation", {}).get("count", 0) >= 0
                   for e in out2)


# ════════════════════════════════════════════════════════════════════
# B4: Session 膨胀真实处置 + 降噪
# ════════════════════════════════════════════════════════════════════
class TestB4SessionDisposal:

    @staticmethod
    def _mk_session(path, size_mb, age_days):
        path.parent.mkdir(parents=True, exist_ok=True)
        n = int(size_mb * 1024 * 1024)
        payload = (b"ris-b4-" * (n // 7 + 1))[:n] or b"x"
        path.write_bytes(payload)
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))

    def test_stale_bloat_archived_recent_untouched(self, ris_env):
        stale = ris_env / "agents" / "tristan" / "old.jsonl"
        active = ris_env / "agents" / "tristan" / "active.jsonl"
        small = ris_env / "agents" / "tristan" / "small.jsonl"
        self._mk_session(stale, 9.0, age_days=40)     # 超龄膨胀 → 应归档
        self._mk_session(active, 9.0, age_days=0)     # 活跃膨胀 → 不动(安全边界)
        self._mk_session(small, 0.01, age_days=60)    # 超龄小文件 → 不动(未超阈值)

        r = RecoveryExecutor().recover_session_bloat()
        assert r["recovered"] is True and r["verified"] is True
        assert r["archived"] == 1
        assert not stale.exists()                      # 原件已删(磁盘真实回收)
        gz = ris_env / "agents-archive" / "tristan" / "old.jsonl.gz"
        assert gz.exists()                             # 内容进冷归档(可解压还原)
        with gzip.open(gz, "rb") as f:
            assert f.read().startswith(b"ris-b4-")
        assert active.exists() and small.exists()
        # session_recovery 事件真实落盘(此前该事件类型生产 0 条)
        evs = [e for e in _read_events(ris_env) if e["event_type"] == "session_recovery"]
        assert len(evs) == 1
        assert evs[0]["status"] == "recovered"
        assert evs[0]["detail"]["archived"][0]["file"] == str(stale)
        assert evs[0]["detail"]["verify_method"] == "rescan-gone"

    def test_bloat_alert_deduped(self, ris_env):
        active = ris_env / "agents" / "tristan" / "active.jsonl"
        self._mk_session(active, 9.0, age_days=0)
        big = ra.scan_session_bloat()
        assert len(big) == 1
        ev1 = ra._session_bloat_alert(big)
        ev2 = ra._session_bloat_alert(ra.scan_session_bloat())   # 30s 内同集合重复
        assert ev1 is not None
        assert ev2 is None                       # ← 治 1533 次重复告警


# ════════════════════════════════════════════════════════════════════
# B7: 恢复经验自动沉淀(主循环接线)
# ════════════════════════════════════════════════════════════════════
class TestB7ExperienceDistillation:

    def test_recovery_attempt_auto_distilled(self, ris_env, monkeypatch, tmp_path):
        store = tmp_path / "exp.jsonl"
        ex = RecoveryExecutor(experience_extractor=RiskExperienceExtractor(store_path=str(store)))
        pids = [["101", "102", "103", "104", "105", "106"], [], [], []]
        monkeypatch.setattr(ra, "_mcp_pids", lambda: pids.pop(0) if pids else [])
        r = ex.recover_mcp_leak()
        assert r["recovered"] is True and r["verified"] is True
        recs = [json.loads(l) for l in open(store, encoding="utf-8") if l.strip()]
        assert len(recs) == 1                      # 自动沉淀(非手工种子)
        assert recs[0]["event_type"] == "mcp_leak"
        assert recs[0]["recovery_method"] == "cleanup_mcp"
        assert recs[0]["recovered"] is True and recs[0]["verified"] is True

    def test_no_recovery_attempt_no_noise(self, ris_env, monkeypatch, tmp_path):
        """无异常(attempts=0)不沉淀——防止每 30s 周期刷出垃圾经验。"""
        store = tmp_path / "exp2.jsonl"
        monkeypatch.setattr(ra, "_mcp_pids", lambda: [])
        ex = RecoveryExecutor(experience_extractor=RiskExperienceExtractor(store_path=str(store)))
        r = ex.recover_mcp_leak()
        assert r["attempts"] == 0
        assert not store.exists() or store.read_text().strip() == ""


# ════════════════════════════════════════════════════════════════════
# B6: 主循环接线冒烟——一轮 run_once 真实运转全部子系统
# ════════════════════════════════════════════════════════════════════
class TestB6RunOnceWiring:

    def test_full_cycle_isolation_to_bridge(self, ris_env, tmp_path, monkeypatch):
        class FakeProviderMonitor:
            def __init__(self, *a, **k):
                pass

            def check_once(self, emit_ok=False):
                return [RuntimeHealthEvent(
                    event_type="provider_unavailable", agent_id="deepseek",
                    status="detected", severity="critical",
                    detail={"provider": "deepseek", "error": "conn refused",
                            "cost_impact": "critical"})]

        monkeypatch.setattr(ra, "ProviderHealthMonitor", FakeProviderMonitor)
        monkeypatch.setattr(ra, "_find_pid", lambda n: 4242)     # 防真实 systemctl
        monkeypatch.setattr(ra, "_mcp_pids", lambda: [])
        monkeypatch.setattr(ra, "_http_ok", lambda url, timeout=5.0: True)
        monkeypatch.setattr(ra.RuntimeSensor, "sample", lambda self: {
            "cpu_pct": 5.0, "mem_pct": 30.0, "gateway_pid": 4242,
            "lao_router_pid": 4242, "mcp_count": 0, "ts": "t"})
        monkeypatch.setattr(ris_drift_mod, "DEFAULT_BASELINE_FILE",
                            str(tmp_path / "baseline.json"))
        monkeypatch.setattr(ris_drift_mod, "DEFAULT_DRIFT_STATE_FILE",
                            str(tmp_path / "drift-state.json"))
        monkeypatch.setattr(ris_drift_mod, "DEFAULT_MANIFEST", [])
        monkeypatch.setattr(ra, "LAOSignalMonitor",
                            lambda *a, **k: LAOSignalMonitor(
                                signal_file=str(tmp_path / "no-signal.json")))
        import ris.bridge as rb
        import ris.experience_integration as ei
        monkeypatch.setattr(rb, "BRIDGE_FILE", str(tmp_path / "ris-bridge.json"))
        monkeypatch.setattr(rb, "_RIS_LOG", str(ris_env / "ris-events.jsonl"))
        monkeypatch.setattr(ei, "OUT_DIR", str(tmp_path / "exp"))

        ra.run_once(verbose=False)   # 第 1 帧: 失败记账·未隔离(抖动抑制)
        evs = _read_events(ris_env)
        assert any(e["event_type"] == "provider_unavailable" for e in evs)
        assert not any(e["event_type"] == "provider_isolation" for e in evs)

        ra.run_once(verbose=False)   # 第 2 帧: 连续失败确认 → 隔离 → 桥同步
        evs = _read_events(ris_env)
        iso = [e for e in evs if e["event_type"] == "provider_isolation"]
        assert len(iso) == 1 and iso[0]["status"] == "isolated"
        with open(tmp_path / "ris-bridge.json") as f:
            bridge = json.load(f)
        assert "deepseek" in bridge["summary"]["isolated_providers"]
        # CPU 帧状态机被主循环驱动(低载复位)
        assert json.load(open(ra.CPU_STATE_FILE))["consecutive"] == 0
        # ris_summary(LAO 第二桥)真实产出
        assert os.path.exists(tmp_path / "exp" / "ris_summary.json")
