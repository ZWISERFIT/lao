from __future__ import annotations

import hashlib
import json
import os
import pwd
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ris.events import RuntimeHealthEvent

# 运行时状态目录(基线/去重状态·与 cpu-sustained.json 同约定)
_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "data")
DEFAULT_BASELINE_FILE = os.path.join(_STATE_DIR, "config-baseline.json")
DEFAULT_DRIFT_STATE_FILE = os.path.join(_STATE_DIR, "config-drift-state.json")

# 同一漂移集合的去重冷却(治 session_bloat 式 30s 刷屏·审计 §2.2/§3.2-6)
DRIFT_ALERT_COOLDOWN_S = 1800

# 配置变化 → Agent 可靠性影响评估的关联故障类型(时间窗内 join)
FAULT_EVENT_TYPES = (
    "lao_router_down", "gateway_down", "webui_unavailable",
    "provider_unavailable", "cpu_anomaly", "memory_anomaly",
    "session_unresponsive", "http_unavailable",
)
FAULT_CORRELATION_WINDOW_S = 900  # 漂移后 15 分钟内的故障视为疑似相关


@dataclass
class ConfigDriftEvent:
    field: str
    expected: Any
    actual: Any
    who: str = "unknown"
    when: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    why: str = "config drift detected"
    severity: str = "warn"
    impact: str = ""          # 影响面(routing / provider_keys / gateway / ris)

    def to_health_event(self, agent_id: str = "system") -> RuntimeHealthEvent:
        detail = {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "who": self.who,
            "why": self.why,
        }
        if self.impact:
            detail["impact"] = self.impact
        return RuntimeHealthEvent(
            event_type="config_drift",
            agent_id=agent_id,
            status="detected",
            severity=self.severity,
            detail=detail,
        )


class ConfigDriftGuard:
    def detect(
        self,
        expected_config: Dict[str, Any],
        actual_config: Dict[str, Any],
        *,
        who: str = "unknown",
    ) -> List[ConfigDriftEvent]:
        drifts: List[ConfigDriftEvent] = []
        for key in set(expected_config) | set(actual_config):
            expected = expected_config.get(key)
            actual = actual_config.get(key)
            if expected != actual:
                drifts.append(
                    ConfigDriftEvent(
                        field=key,
                        expected=expected,
                        actual=actual,
                        who=who,
                    )
                )
        return drifts


# ── B3 修复: 运行时配置漂移监控(主循环接线·基线存储·影响评估) ──────────
# 审计缺陷 RIS-Audit-Report-20260816 §2.5/B4: ConfigDriftGuard 是零调用点的纯函数,
# 无"实际配置"来源、无基线、无影响关联。以下 Watcher 补齐三件事:
#   1. 快照: 真实配置源(systemd unit / secrets.env / 关键 env)指纹化(sha256)
#   2. 基线: 首轮自动落盘, 之后逐轮 diff(密钥只存指纹·不落明文)
#   3. 评估: 漂移 → config_drift 事件(带 who 归因/impact) + 与故障事件时间窗关联
DEFAULT_MANIFEST = [
    {"name": "lao-router-unit", "path": "~/.config/systemd/user/lao-router.service",
     "affects": "routing"},
    {"name": "ris-monitor-unit", "path": "~/.config/systemd/user/ris-monitor.service",
     "affects": "ris"},
    {"name": "openclaw-gateway-unit", "path": "~/.config/systemd/user/openclaw-gateway.service",
     "affects": "gateway"},
    {"name": "openclaw-secrets", "path": "~/.openclaw/secrets.env",
     "affects": "provider_keys", "secret": True},
    {"name": "lao-router-env", "affects": "routing_cost",
     "env": ["LAO_ROUTER_PORT", "LAO_DAILY_BUDGET_USD", "DEEPSEEK_BASE_URL"]},
]


class ConfigDriftWatcher:
    """主循环每轮调用: 快照真实配置 → 对比基线 → 产出 config_drift 事件。

    - 基线缺失时首轮自动建立(不告警), 变更后持续告警直至 rebaseline()
    - 同一漂移集合在 DRIFT_ALERT_COOLDOWN_S 内去重(不重复告警)
    - secret 文件只存内容 sha256 指纹(不落明文/不进事件正文)
    """

    def __init__(self, manifest: Optional[List[Dict]] = None,
                 baseline_file: Optional[str] = None,
                 state_file: Optional[str] = None,
                 alert_cooldown_s: float = DRIFT_ALERT_COOLDOWN_S):
        self.manifest = manifest if manifest is not None else DEFAULT_MANIFEST
        # None → 运行时解析模块常量(测试可重定向·不写生产 state 目录)
        self.baseline_file = baseline_file or DEFAULT_BASELINE_FILE
        self.state_file = state_file or DEFAULT_DRIFT_STATE_FILE
        self.alert_cooldown_s = alert_cooldown_s

    # ── 快照 ──────────────────────────────────────────────
    def _file_fingerprint(self, path: str, secret: bool) -> Dict[str, Any]:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            return {"exists": False}
        with open(p, "rb") as f:
            content = f.read()
        fp: Dict[str, Any] = {
            "exists": True,
            "sha256": hashlib.sha256(content).hexdigest()[:16],
        }
        if secret:
            return fp  # 密钥文件: 只留指纹, 不带 owner/mtime 细节
        try:
            fp["owner"] = pwd.getpwuid(os.stat(p).st_uid).pw_name
        except Exception:
            fp["owner"] = str(os.stat(p).st_uid)
        return fp

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """对 manifest 全部条目取当前指纹(= detect 的 actual 来源)。"""
        snap: Dict[str, Dict[str, Any]] = {}
        for item in self.manifest:
            name = item["name"]
            if "path" in item:
                snap[name] = self._file_fingerprint(item["path"], item.get("secret", False))
            elif "env" in item:
                snap[name] = {"values": {k: os.environ.get(k, "") for k in item["env"]}}
        return snap

    # ── 基线 ──────────────────────────────────────────────
    def _load_json(self, path: str) -> Optional[Dict]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_json(self, path: str, data: Dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def ensure_baseline(self) -> bool:
        """基线不存在时以当前快照建立; 返回是否新建。"""
        if self._load_json(self.baseline_file) is not None:
            return False
        self._save_json(self.baseline_file, {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": self.snapshot(),
        })
        return True

    def rebaseline(self) -> None:
        """接受当前配置为新基线(人工确认漂移后调用·停止告警)。"""
        self._save_json(self.baseline_file, {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": self.snapshot(),
        })
        self._save_json(self.state_file, {"last_sig": [], "last_ts": 0.0})

    # ── 检测(主入口·agent 主循环每轮调用) ─────────────────
    def check_once(self) -> List[RuntimeHealthEvent]:
        self.ensure_baseline()
        baseline = (self._load_json(self.baseline_file) or {}).get("snapshot", {})
        current = self.snapshot()
        guard = ConfigDriftGuard()
        drifts: List[tuple] = []  # (item, ConfigDriftEvent)
        for item in self.manifest:
            name = item["name"]
            exp = baseline.get(name, {})
            act = current.get(name, {})
            for d in guard.detect(exp, act):
                d.field = f"{name}.{d.field}"
                d.who = act.get("owner", "runtime-env")
                d.impact = item.get("affects", "")
                d.severity = "error" if item.get("affects") in ("routing", "provider_keys", "gateway") else "warn"
                drifts.append((item, d))
        if not drifts:
            return []
        # 去重: 同一漂移集合冷却期内不重复告警(治 30s 刷屏)
        sig = sorted(f"{d.field}" for _, d in drifts)
        state = self._load_json(self.state_file) or {"last_sig": [], "last_ts": 0.0}
        if state.get("last_sig") == sig and \
           time.time() - state.get("last_ts", 0.0) < self.alert_cooldown_s:
            return []
        self._save_json(self.state_file, {"last_sig": sig, "last_ts": time.time()})
        return [d.to_health_event(agent_id="config") for _, d in drifts]

    # ── 影响评估: 漂移 ↔ 故障时间窗关联(回答"配置变化是否影响可靠性") ──
    def correlate(self, drift_events: List[RuntimeHealthEvent],
                  recent_events: List[Dict],
                  window_s: float = FAULT_CORRELATION_WINDOW_S) -> List[RuntimeHealthEvent]:
        """漂移后 window_s 内出现 FAULT_EVENT_TYPES → 关联标注 + 升级 critical。

        recent_events: RuntimeHealthEvent.to_dict() 列表(如 ris-events.jsonl 尾部)。
        """
        faults_by_ts = []
        for r in recent_events:
            if r.get("event_type") in FAULT_EVENT_TYPES:
                ts = _parse_ts(r.get("ts", ""))
                if ts is not None:
                    faults_by_ts.append((ts, r.get("event_type")))
        for ev in drift_events:
            drift_ts = _parse_ts(ev.ts)
            related = [ft for fts, ft in faults_by_ts
                       if drift_ts is not None and 0 <= (fts - drift_ts).total_seconds() <= window_s]
            if related:
                ev.detail["fault_correlation"] = {
                    "count": len(related),
                    "types": sorted(set(related)),
                }
                ev.detail["impact_assessment"] = (
                    f"drift 后 {int(window_s)}s 内出现 {len(related)} 个故障事件·疑似影响 Agent 可靠性")
                ev.severity = "critical"
            else:
                ev.detail["impact_assessment"] = (
                    f"drift 后 {int(window_s)}s 内无关联故障事件·暂无可靠性影响证据")
        return drift_events


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


__all__ = ["ConfigDriftGuard", "ConfigDriftEvent", "ConfigDriftWatcher",
           "DEFAULT_MANIFEST", "DEFAULT_BASELINE_FILE", "FAULT_EVENT_TYPES"]
