"""P1-2 RIS config治理参照零信任测试（2026-08-19 Shuyu审定·外部案例E）。

验证: 非法改 bind/providers/models → 告警/拦截(越权改config检测)。
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.config_drift import ConfigDriftWatcher


def _make_watcher():
    tmp = tempfile.mkdtemp()
    return ConfigDriftWatcher(
        manifest=[],
        baseline_file=os.path.join(tmp, "baseline.json"),
        state_file=os.path.join(tmp, "state.json"),
    )


class TestDetectOpenclawCritical:
    """非法改 bind → 告警"""

    def test_first_run_baseline_no_alert(self):
        """首轮建立基线 → 不告警"""
        w = _make_watcher()
        events = w.detect_openclaw_critical()
        assert events == []

    def test_bind_change_triggers_critical(self):
        """bind 变更 → config_drift critical 事件(双失联根因④)"""
        w = _make_watcher()
        w.detect_openclaw_critical()  # 建基线

        # 模拟 bind 被改(改 openclaw.json 的 gateway.bind → tailnet)
        orig = w.OPENCLAW_CONFIG
        tmp_cfg = os.path.join(tempfile.mkdtemp(), "openclaw.json")
        with open(orig, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["gateway"]["bind"] = "tailnet"  # 越权变更
        with open(tmp_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        w.OPENCLAW_CONFIG = tmp_cfg  # 重定向

        events = w.detect_openclaw_critical()
        bind_events = [e for e in events if e.detail.get("field") == "bind"]
        assert len(bind_events) == 1, f"应检测到 bind 漂移: {events}"
        assert bind_events[0].severity == "critical"
        assert bind_events[0].detail["who"] == "unauthorized_change"

    def test_providers_change_triggers_warning(self):
        """providers 变更 → warning 事件"""
        w = _make_watcher()
        w.detect_openclaw_critical()

        tmp_cfg = os.path.join(tempfile.mkdtemp(), "openclaw.json")
        with open(w.OPENCLAW_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["models"]["providers"]["fake-new-provider"] = {"models": []}
        with open(tmp_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        w.OPENCLAW_CONFIG = tmp_cfg

        events = w.detect_openclaw_critical()
        prov_events = [e for e in events if e.detail.get("field") == "providers"]
        assert len(prov_events) >= 1
        assert prov_events[0].severity == "warning"

    def test_no_change_no_alert(self):
        """配置未变 → 无告警"""
        w = _make_watcher()
        w.detect_openclaw_critical()
        events = w.detect_openclaw_critical()
        assert events == []

    def test_authorized_change_auto_approved(self):
        """护栏4: 白名单(合法变更)自动放行·不误拦"""
        w = _make_watcher()
        w.detect_openclaw_critical()  # 建基线

        # 记录某合法变更的指纹为已授权
        tmp_cfg = os.path.join(tempfile.mkdtemp(), "openclaw.json")
        with open(w.OPENCLAW_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["gateway"]["bind"] = "tailnet"  # 合法变更(经审批)
        with open(tmp_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        w.OPENCLAW_CONFIG = tmp_cfg

        # 先检测一次(应告警) → 拿到指纹后授权
        events = w.detect_openclaw_critical()
        assert len([e for e in events if e.detail.get("field") == "bind"]) == 1

        # 手动授权该变更(白名单)
        state = w._load_json(w.state_file)
        import hashlib
        bind_fp = hashlib.sha256(b"tailnet").hexdigest()[:16]
        state["openclaw_authorized"] = {"bind": [bind_fp]}
        with open(w.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)
        # 更新基线到当前?(授权后重新检测不应告警)
        w.detect_openclaw_critical()
        events2 = w.detect_openclaw_critical()
        assert len([e for e in events2 if e.detail.get("field") == "bind"]) == 0

    def test_fail_open(self):
        """读取失败 → 空列表不误报"""
        w = _make_watcher()
        w.OPENCLAW_CONFIG = "/nonexistent/path.json"
        assert w.detect_openclaw_critical() == []
