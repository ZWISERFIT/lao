# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""LAO 成本防线 — S1 烧钱哨兵 (N1-a)。

规格锚点 (77号 _rev2):
    - S1-3 阈值体系: 军团全局日预算 7.0 USD(env LAO_DAILY_BUDGET), 三级窗口 + 切换异常轴。
    - S1-4 去重规则: 同键 4h 内 1 次; 级别升级不受抑制; 恢复通知 24h 内 1 次。
    - S1-5 告警字段: 落 share/state/lao-cost-sentinel-YYYYMMDD.jsonl。
    - S1-6 UTC 归窗; S1-7 数据活性检测(零成本≠安全)。

约束: 仅标准库 · 只读旁路(不改 model_router.py 选路逻辑, 止血基线不回退) · fail-open。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .model import (
    AGENT_UNATTRIBUTED,
    COST_BASIS_LOCAL,
    COST_BASIS_UNRECONCILED,
    UTC,
    Alert,
    CostEvent,
    SwitchEvent,
    day_key,
    hour_key,
    iso_utc,
)

# S1-3 军团全局日预算(共享总额, 非每 agent 独立额度)
DEFAULT_DAILY_BUDGET_USD = 7.0

# S1-3 阈值(占日预算百分比)
L1_DAY_PCT, L1_HOUR_PCT, L1_TASK_PCT = 70.0, 25.0, 10.0
L2_DAY_PCT, L2_HOUR_PCT = 90.0, 50.0
L3_DAY_PCT, L3_HOUR_PCT = 100.0, 80.0

# S1-3 切换异常轴: 单小时切入同一非白名单 provider 笔数阈值
SWITCH_ANOMALY_HOURLY = 20

# S1-4 去重与恢复窗口
DEDUP_WINDOW_HOURS = 4
RECOVERY_WINDOW_HOURS = 24

# S1-5 明细上限
SAMPLE_DETAIL_LIMIT = 20

# S1-7 数据活性: 连续无事件小时数
DATA_GAP_HOURS = 2

# 级别序(用于"升级不受去重抑制"判定)
_LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3}


def resolve_daily_budget(env: Optional[Dict[str, str]] = None) -> float:
    """解析军团全局日预算(env LAO_DAILY_BUDGET, 默认 7.0 USD)。

    非法值回落默认值(fail-open), 不抛异常。
    """
    source = env if env is not None else os.environ
    raw = source.get("LAO_DAILY_BUDGET")
    if raw is None:
        return DEFAULT_DAILY_BUDGET_USD
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_BUDGET_USD
    return value if value > 0 else DEFAULT_DAILY_BUDGET_USD


def _alert_id(dedup_key: str, ts: datetime) -> str:
    """生成稳定 alert_id(去重键 + 时间戳哈希前12位)。"""
    digest = hashlib.sha256(f"{dedup_key}|{iso_utc(ts)}".encode("utf-8")).hexdigest()
    return f"lcs-{digest[:12]}"


def _billable(events: Iterable[CostEvent]) -> List[CostEvent]:
    """过滤出可计费事件: 排除合成/测试记录(R-02 封存口径)与无时间戳记录。"""
    return [e for e in events if not e.synthetic and e.ts is not None]


def aggregate_cost(
    events: Sequence[CostEvent], now: datetime
) -> Dict[str, Any]:
    """按 UTC 日窗/小时窗/任务聚合成本, 并给出 agent 维度分解(S1-3/S1-5)。

    Args:
        events: 成本事件(含合成记录, 本函数内部过滤)。
        now: 评估时刻(UTC)。

    Returns:
        含 day_cost / hour_cost / task_cost / agent_breakdown / billable 的字典。
    """
    billable = _billable(events)
    dkey, hkey = day_key(now), hour_key(now)
    day_events = [e for e in billable if day_key(e.ts) == dkey]
    hour_events = [e for e in day_events if hour_key(e.ts) == hkey]

    agent_breakdown: Dict[str, float] = {}
    task_cost: Dict[str, float] = {}
    task_unattributed = 0
    for event in day_events:
        agent = event.agent or AGENT_UNATTRIBUTED
        agent_breakdown[agent] = round(agent_breakdown.get(agent, 0.0) + event.cost_usd, 6)
        task_key = event.task_id_key()
        if not task_key:
            # 归因缺口: 无 task_id 不参与单任务轴判定, 禁止用任务类型冒充
            task_unattributed += 1
            continue
        task_cost[task_key] = round(task_cost.get(task_key, 0.0) + event.cost_usd, 6)

    return {
        "day_key": dkey,
        "hour_key": hkey,
        "day_cost": round(sum(e.cost_usd for e in day_events), 6),
        "hour_cost": round(sum(e.cost_usd for e in hour_events), 6),
        "task_cost": task_cost,
        "task_unattributed": task_unattributed,
        "agent_breakdown": agent_breakdown,
        "day_events": day_events,
        "hour_events": hour_events,
        "synthetic_excluded": len(events) - len(billable),
    }


def _sample_details(events: Sequence[CostEvent]) -> List[Dict[str, Any]]:
    """取最近 ≤20 笔明细(S1-5)。"""
    ordered = sorted([e for e in events if e.ts is not None], key=lambda e: e.ts)
    tail = ordered[-SAMPLE_DETAIL_LIMIT:]
    return [
        {
            "ts": iso_utc(e.ts),
            "provider": e.provider,
            "model": e.model,
            "task": e.task,
            "agent": e.agent,
            "cost_usd": round(e.cost_usd, 6),
        }
        for e in tail
    ]


class DedupStore:
    """去重与恢复状态存储(S1-4)。

    状态文件: <state_dir>/lao-cost-sentinel-dedup.json
    结构: {dedup_key: {"level": "L2", "last_fired": ISO, "last_recovered": ISO}}
    """

    FILENAME = "lao-cost-sentinel-dedup.json"

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.path = os.path.join(state_dir, self.FILENAME)
        self._state: Dict[str, Dict[str, str]] = self._load()

    def _load(self) -> Dict[str, Dict[str, str]]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def should_fire(self, dedup_key: str, level: str, now: datetime) -> bool:
        """判定是否放行本次告警(升级放行 / 超出 4h 去重窗放行)。"""
        entry = self._state.get(dedup_key)
        if not entry:
            return True
        last_level = entry.get("level", "")
        if _LEVEL_ORDER.get(level, 0) > _LEVEL_ORDER.get(last_level, 0):
            return True  # S1-4: 级别升级不受去重抑制
        last_fired = entry.get("last_fired")
        try:
            fired_at = datetime.fromisoformat(last_fired) if last_fired else None
        except (TypeError, ValueError):
            fired_at = None
        if fired_at is None:
            return True
        return now - fired_at >= timedelta(hours=DEDUP_WINDOW_HOURS)

    def should_notify_recovery(self, dedup_key: str, now: datetime) -> bool:
        """判定是否发恢复通知(曾告警过, 且 24h 内未发过恢复)。"""
        entry = self._state.get(dedup_key)
        if not entry or not entry.get("last_fired"):
            return False
        last_recovered = entry.get("last_recovered")
        if not last_recovered:
            return True
        try:
            recovered_at = datetime.fromisoformat(last_recovered)
        except ValueError:
            return True
        return now - recovered_at >= timedelta(hours=RECOVERY_WINDOW_HOURS)

    def mark_fired(self, dedup_key: str, level: str, now: datetime) -> None:
        """记录本次告警放行。"""
        entry = self._state.setdefault(dedup_key, {})
        entry["level"] = level
        entry["last_fired"] = iso_utc(now)

    def mark_recovered(self, dedup_key: str, now: datetime) -> None:
        """记录恢复通知已发。"""
        entry = self._state.setdefault(dedup_key, {})
        entry["last_recovered"] = iso_utc(now)
        entry["level"] = ""

    def flush(self) -> None:
        """原子落盘(临时文件 + rename); 失败不抛(fail-open)。"""
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".dedup-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            return


class CostSentinel:
    """S1 烧钱哨兵: 只读旁路评估成本与切换异常, 产出告警记录。"""

    def __init__(
        self,
        state_dir: str,
        daily_budget_usd: Optional[float] = None,
        cost_basis: str = COST_BASIS_UNRECONCILED,
    ):
        """初始化哨兵。

        Args:
            state_dir: 哨兵日志与去重状态目录(share/state)。
            daily_budget_usd: 军团全局日预算; None 时读 env LAO_DAILY_BUDGET(默认 7.0)。
            cost_basis: 成本口径标注; 未与官方后台对账前为 unreconciled(N-01)。
        """
        self.state_dir = state_dir
        self.budget = daily_budget_usd if daily_budget_usd else resolve_daily_budget()
        self.cost_basis = cost_basis
        self.dedup = DedupStore(state_dir)

    # -- 阈值判定 ---------------------------------------------------------

    def _cost_alerts(self, agg: Dict[str, Any], now: datetime) -> List[Alert]:
        """成本轴三级判定(取命中的最高级别, 升级链由去重层放行)。"""
        budget = self.budget
        day_cost, hour_cost = agg["day_cost"], agg["hour_cost"]
        day_pct = day_cost / budget * 100.0 if budget else 0.0
        hour_pct = hour_cost / budget * 100.0 if budget else 0.0

        level: Optional[str] = None
        rules: List[str] = []
        if day_pct >= L3_DAY_PCT or hour_pct >= L3_HOUR_PCT:
            level = "L3"
            if day_pct >= L3_DAY_PCT:
                rules.append(f"day_cost>={L3_DAY_PCT}%budget")
            if hour_pct >= L3_HOUR_PCT:
                rules.append(f"hour_cost>={L3_HOUR_PCT}%budget")
        elif day_pct >= L2_DAY_PCT or hour_pct >= L2_HOUR_PCT:
            level = "L2"
            if day_pct >= L2_DAY_PCT:
                rules.append(f"day_cost>={L2_DAY_PCT}%budget")
            if hour_pct >= L2_HOUR_PCT:
                rules.append(f"hour_cost>={L2_HOUR_PCT}%budget")
        elif day_pct >= L1_DAY_PCT or hour_pct >= L1_HOUR_PCT:
            level = "L1"
            if day_pct >= L1_DAY_PCT:
                rules.append(f"day_cost>={L1_DAY_PCT}%budget")
            if hour_pct >= L1_HOUR_PCT:
                rules.append(f"hour_cost>={L1_HOUR_PCT}%budget")

        alerts: List[Alert] = []
        if level is not None:
            dedup_key = f"({level},cost,{agg['day_key']})"
            alerts.append(
                Alert(
                    alert_id=_alert_id(dedup_key, now),
                    ts=iso_utc(now),
                    level=level,
                    axis="cost",
                    trigger_rule="+".join(rules),
                    window=agg["day_key"],
                    dedup_key=dedup_key,
                    agent_cost_breakdown=agg["agent_breakdown"],
                    sample_details=_sample_details(agg["day_events"]),
                    cost_usd=day_cost,
                    cost_basis=self.cost_basis,
                    budget_usd=budget,
                )
            )

        # 单任务成本轴(L1): 单任务 >= 日预算10%; 仅对可归因(有 task_id)的笔判定
        for task, cost in sorted(agg["task_cost"].items(), key=lambda kv: -kv[1]):
            if budget and cost / budget * 100.0 >= L1_TASK_PCT:
                dedup_key = f"(L1,task_cost,{task}@{agg['day_key']})"
                alerts.append(
                    Alert(
                        alert_id=_alert_id(dedup_key, now),
                        ts=iso_utc(now),
                        level="L1",
                        axis="cost",
                        trigger_rule=f"task_cost>={L1_TASK_PCT}%budget",
                        window=agg["day_key"],
                        dedup_key=dedup_key,
                        task_id=task,
                        agent_cost_breakdown=agg["agent_breakdown"],
                        sample_details=_sample_details(
                            [e for e in agg["day_events"] if e.task_id_key() == task]
                        ),
                        cost_usd=cost,
                        cost_basis=self.cost_basis,
                        budget_usd=budget,
                    )
                )
        return alerts

    def _switch_anomaly_alerts(
        self,
        switch_events: Sequence[SwitchEvent],
        now: datetime,
        allowed_providers: Optional[Sequence[str]] = None,
    ) -> List[Alert]:
        """切换异常轴: 单小时切入同一非白名单 provider ≥20 笔(S1-3)。"""
        allowed = {p.lower() for p in (allowed_providers or [])}
        hkey = hour_key(now)
        counter: Dict[str, List[SwitchEvent]] = {}
        for event in switch_events:
            if event.ts is None or hour_key(event.ts) != hkey:
                continue
            provider = (event.to_provider or "").lower()
            if allowed and provider in allowed:
                continue
            counter.setdefault(event.to_provider, []).append(event)

        alerts: List[Alert] = []
        for provider, events in counter.items():
            if len(events) < SWITCH_ANOMALY_HOURLY:
                continue
            dedup_key = f"(SWITCH_ANOMALY,provider={provider},{hkey})"
            alerts.append(
                Alert(
                    alert_id=_alert_id(dedup_key, now),
                    ts=iso_utc(now),
                    level="L3",
                    axis="switch",
                    trigger_rule=f"hourly_switch_in>={SWITCH_ANOMALY_HOURLY}",
                    window=hkey,
                    dedup_key=dedup_key,
                    provider=provider,
                    model=events[-1].to_model,
                    sample_details=[
                        {
                            "ts": iso_utc(e.ts),
                            "from_provider": e.from_provider,
                            "to_provider": e.to_provider,
                            "to_model": e.to_model,
                            "reason": e.reason,
                            "triggered_by": e.triggered_by,
                        }
                        for e in events[-SAMPLE_DETAIL_LIMIT:]
                    ],
                    cost_basis=self.cost_basis,
                    budget_usd=self.budget,
                )
            )
        return alerts

    def _data_gap_alert(
        self,
        cost_events: Sequence[CostEvent],
        switch_events: Sequence[SwitchEvent],
        now: datetime,
        session_active: bool,
    ) -> Optional[Alert]:
        """S1-7 数据活性检测: 连续 2h 无事件且网关有活跃会话 → DATA_GAP。"""
        if not session_active:
            return None
        cutoff = now - timedelta(hours=DATA_GAP_HOURS)
        latest: Optional[datetime] = None
        for ts in [e.ts for e in _billable(cost_events)] + [e.ts for e in switch_events]:
            if ts is not None and (latest is None or ts > latest):
                latest = ts
        if latest is not None and latest >= cutoff:
            return None
        dedup_key = f"(DATA_GAP,liveness,{hour_key(now)})"
        return Alert(
            alert_id=_alert_id(dedup_key, now),
            ts=iso_utc(now),
            level="L1",
            axis="behavior",
            trigger_rule=f"no_event_for>={DATA_GAP_HOURS}h_with_active_session",
            window=hour_key(now),
            dedup_key=dedup_key,
            sample_details=[{"last_event_ts": iso_utc(latest) if latest else None}],
            cost_basis=self.cost_basis,
            budget_usd=self.budget,
        )

    # -- 主评估 -----------------------------------------------------------

    def evaluate(
        self,
        cost_events: Sequence[CostEvent],
        switch_events: Sequence[SwitchEvent],
        now: datetime,
        allowed_providers: Optional[Sequence[str]] = None,
        session_active: bool = False,
    ) -> List[Alert]:
        """评估一次, 返回经去重放行的告警(含恢复通知)。

        只读: 不修改任何真实账本; 仅写哨兵自有日志与去重状态。
        """
        agg = aggregate_cost(cost_events, now)
        candidates = self._cost_alerts(agg, now)
        candidates.extend(self._switch_anomaly_alerts(switch_events, now, allowed_providers))
        gap = self._data_gap_alert(cost_events, switch_events, now, session_active)
        if gap is not None:
            candidates.append(gap)

        fired: List[Alert] = []
        fired_keys = set()
        for alert in candidates:
            if not self.dedup.should_fire(alert.dedup_key, alert.level, now):
                continue
            self.dedup.mark_fired(alert.dedup_key, alert.level, now)
            fired.append(alert)
            fired_keys.add(alert.dedup_key)

        fired.extend(self._recovery_alerts(fired_keys, now))
        self.dedup.flush()
        return fired

    def _recovery_alerts(self, fired_keys: set, now: datetime) -> List[Alert]:
        """对已回落的告警键发恢复通知(S1-4, 24h 内 1 次)。"""
        recoveries: List[Alert] = []
        for dedup_key, entry in list(self.dedup._state.items()):
            if dedup_key in fired_keys or not entry.get("level"):
                continue
            if not self.dedup.should_notify_recovery(dedup_key, now):
                continue
            recoveries.append(
                Alert(
                    alert_id=_alert_id(f"recovered|{dedup_key}", now),
                    ts=iso_utc(now),
                    level=entry.get("level", "L1"),
                    axis="cost",
                    trigger_rule="threshold_recovered",
                    window=day_key(now),
                    dedup_key=dedup_key,
                    cost_basis=self.cost_basis,
                    budget_usd=self.budget,
                    status="recovered",
                )
            )
            self.dedup.mark_recovered(dedup_key, now)
        return recoveries

    # -- 落盘 -------------------------------------------------------------

    def log_path(self, now: datetime) -> str:
        """哨兵日志路径 share/state/lao-cost-sentinel-YYYYMMDD.jsonl(S1-4)。"""
        return os.path.join(
            self.state_dir, f"lao-cost-sentinel-{now.astimezone(UTC).strftime('%Y%m%d')}.jsonl"
        )

    def write_alerts(self, alerts: Sequence[Alert], now: datetime) -> Optional[str]:
        """追加落盘告警记录; 失败返回 None(fail-open)。"""
        if not alerts:
            return None
        path = self.log_path(now)
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                for alert in alerts:
                    fh.write(json.dumps(alert.to_record(), ensure_ascii=False) + "\n")
        except OSError:
            return None
        return path
