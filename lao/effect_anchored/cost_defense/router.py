# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""LAO 成本防线 — S3 Ethan 行为建模双路由 (N1-c 扩展)。

规格锚点 (77号 _rev2):
    - S3-1 分类规则: axis=cost→Nova; axis=behavior→Ethan; axis=both→Nova 与 Ethan 同送。
    - S3-2 送达机制: 写 share/inbox/cost-alert-outbox/<alert_id>.json(原子写: 临时文件+rename),
      出箱记录保留 ≥7 天; 落盘即记 delivered_to; 消费方读走+ACK 文件闭环;
      哨兵不做外部消息推送(不越权调通讯渠道)。
    - S3-3 完整治理阶梯: 触发 → Nova+Ethan 自动排查 → Stella → (数据基建维稳类)Tristan
      → 智囊团红蓝对抗 → 创始人; 出箱消息体必须携带 escalation_level 与 escalated_to, 每级留痕。
    - S3-4 Nova 接账: cost_basis != official_ledger 时先官方对账再回账(N-01)。

分类补充口径(施工期, 已在阶段证据中标注待统筹席确认):
    - axis=switch(S1 切换异常轴) 按 S3-1"违规切换"归**行为类** → Ethan。
    - axis 未识别时**双送** Nova+Ethan(宁紧勿松, 不因分类不确定而漏送)。

约束: 仅标准库 · 只写自家出箱目录(不碰真实账本/不改选路) · fail-open。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .model import COST_BASIS_OFFICIAL, UTC, Alert, iso_utc, parse_ts

# -- 收件人与分类 ---------------------------------------------------------

RECIPIENT_NOVA = "nova"
RECIPIENT_ETHAN = "ethan"

AXIS_COST = "cost"
AXIS_BEHAVIOR = "behavior"
AXIS_BOTH = "both"
AXIS_SWITCH = "switch"

AXIS_ROUTING: Dict[str, Tuple[str, ...]] = {
    AXIS_COST: (RECIPIENT_NOVA,),
    AXIS_BEHAVIOR: (RECIPIENT_ETHAN,),
    AXIS_BOTH: (RECIPIENT_NOVA, RECIPIENT_ETHAN),
    AXIS_SWITCH: (RECIPIENT_ETHAN,),
}
FALLBACK_RECIPIENTS: Tuple[str, ...] = (RECIPIENT_NOVA, RECIPIENT_ETHAN)

# -- 五级治理阶梯 (S3-3) --------------------------------------------------

E1_UNIT_AGENTS = "E1_unit_agents"
E2_UNIT_LEAD = "E2_unit_lead"
E3_INFRA_OPS = "E3_infra_ops"
E4_THINK_TANK = "E4_think_tank"
E5_FOUNDER = "E5_founder"

ESCALATION_LADDER: Tuple[str, ...] = (
    E1_UNIT_AGENTS,
    E2_UNIT_LEAD,
    E3_INFRA_OPS,
    E4_THINK_TANK,
    E5_FOUNDER,
)
ESCALATION_OWNERS: Dict[str, Tuple[str, ...]] = {
    E2_UNIT_LEAD: ("stella",),
    E3_INFRA_OPS: ("tristan",),
    E4_THINK_TANK: ("operating_system",),
    E5_FOUNDER: ("founder",),
}

# 数据基建维稳类触发规则(命中即至少 E3 转 Tristan)
INFRA_RULE_MARKERS: Tuple[str, ...] = (
    "no_event_for",
    "whitelist_missing",
    "whitelist_expired",
    "whitelist_malformed",
    "whitelist_empty",
)

ACK_TIMEOUT_HOURS = 4  # 出箱后未 ACK 视为处置未闭环, 上抬一级
STALE_ESCALATION_HOURS = 24  # 未闭环满 24h 至少进智囊团红蓝对抗
REPEAT_ESCALATION_COUNT = 3  # 同键反复触发达此次数至少进智囊团
OUTBOX_RETENTION_DAYS = 7  # S3-2 出箱记录保留 ≥7 天

OUTBOX_SUBPATH = os.path.join("inbox", "cost-alert-outbox")
_ACK_SUFFIX = ".ack."


def classify(alert: Alert) -> List[str]:
    """S3-1 分类: 按 axis 决定送达对象(未识别 axis 双送)。"""
    return list(AXIS_ROUTING.get((alert.axis or "").strip().lower(), FALLBACK_RECIPIENTS))


def is_infra_alert(alert: Alert) -> bool:
    """判定是否属数据基建维稳类(S3-3 中转 Tristan 的那一档)。"""
    rule = (alert.trigger_rule or "").lower()
    return any(marker in rule for marker in INFRA_RULE_MARKERS)


def _escalate_one_step(index: int, is_infra: bool) -> int:
    """未闭环上抬一级。

    E3(Tristan) 是按**类型**转办的分支档(数据基建维稳类), 不是严重度上的一档:
    非基建类告警从 E2 上抬时跳过 E3, 直接进 E4 智囊团, 避免把成本类问题错派给基建。
    """
    if index == 1 and not is_infra:
        return 3
    return min(index + 1, len(ESCALATION_LADDER) - 1)


def decide_escalation(
    alert: Alert, unacked_hours: float = 0.0, repeat_count: int = 1
) -> Tuple[str, List[str]]:
    """S3-3 定级: 返回 (escalation_level, 责任人列表)。

    定级表(施工期口径, 严重度定起点、未闭环驱动上抬):
        L1                     → E1 单元内自动排查(Nova/Ethan)
        L2 / L3                → E2 组长 Stella 处置闭环
        命中基建维稳规则        → 至少 E3 Tristan(按类型转办的分支档)
        未 ACK ≥ 4h            → 上抬一级(非基建类从 E2 直接跳到 E4, 不错派 Tristan)
        未 ACK ≥ 24h 或同键≥3次 → 至少 E4 智囊团红蓝对抗
        L3 且未 ACK ≥ 24h      → E5 创始人(超出军团自主治理范围)
    """
    infra = is_infra_alert(alert)
    index = 1 if (alert.level or "").upper() in {"L2", "L3"} else 0
    if infra:
        index = max(index, 2)
    if unacked_hours >= ACK_TIMEOUT_HOURS:
        index = _escalate_one_step(index, infra)
    if unacked_hours >= STALE_ESCALATION_HOURS or repeat_count >= REPEAT_ESCALATION_COUNT:
        index = max(index, 3)
    if (alert.level or "").upper() == "L3" and unacked_hours >= STALE_ESCALATION_HOURS:
        index = len(ESCALATION_LADDER) - 1
    level = ESCALATION_LADDER[index]
    owners = list(ESCALATION_OWNERS.get(level, ())) or classify(alert)
    return level, owners


def needs_reconciliation(alert: Alert) -> bool:
    """S3-4: 成本类且 cost_basis != official_ledger → Nova 须先官方对账再回账。"""
    if RECIPIENT_NOVA not in classify(alert):
        return False
    return (alert.cost_basis or "") != COST_BASIS_OFFICIAL


@dataclass
class DeliveryStatus:
    """单条告警的送达/回执状态。"""

    alert_id: str
    delivered_to: List[str]
    acked_by: List[str]
    closed: bool
    age_hours: Optional[float] = None

    def missing(self) -> List[str]:
        """尚未回执的收件人。"""
        return [r for r in self.delivered_to if r not in self.acked_by]


class AlertOutbox:
    """S3-2 出箱: 原子写 + ACK 回执闭环, 只写自家出箱目录。"""

    def __init__(self, root: str):
        """初始化出箱。

        Args:
            root: 出箱目录(生产为 share/inbox/cost-alert-outbox; 复演用隔离目录)。
        """
        self.root = root

    # -- 路径 -------------------------------------------------------------

    def message_path(self, alert_id: str) -> str:
        """出箱消息路径 <root>/<alert_id>.json。"""
        return os.path.join(self.root, f"{alert_id}.json")

    def ack_path(self, alert_id: str, consumer: str) -> str:
        """回执路径 <root>/<alert_id>.ack.<consumer>.json。"""
        return os.path.join(self.root, f"{alert_id}{_ACK_SUFFIX}{consumer}.json")

    # -- 写入 -------------------------------------------------------------

    def _atomic_write(self, path: str, payload: Dict[str, Any]) -> bool:
        """原子写(同目录临时文件 + os.replace); 失败返回 False(fail-open)。"""
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except OSError:
            return False
        return True

    def dispatch(
        self,
        alert: Alert,
        now: Optional[datetime] = None,
        unacked_hours: float = 0.0,
        repeat_count: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """分类 → 定级 → 原子写出箱; 返回落盘的消息体(写失败返回 None)。

        落盘即视为送达(S3-2): delivered_to 写入消息体, 由 Nova 线/Ethan 线各自消费。
        """
        stamp = now or parse_ts(alert.ts) or datetime.now(tz=UTC)
        recipients = classify(alert)
        level, owners = decide_escalation(alert, unacked_hours, repeat_count)

        alert.delivered_to = recipients
        alert.escalation_level = level
        alert.escalated_to = ",".join(owners)
        if level != E1_UNIT_AGENTS:
            alert.status = "escalated"

        record = alert.to_record()
        record["dispatched_at"] = iso_utc(stamp)
        record["recipients"] = recipients
        record["escalation_owners"] = owners
        record["escalation_path"] = list(
            ESCALATION_LADDER[: ESCALATION_LADDER.index(level) + 1]
        )
        record["requires_official_reconciliation"] = needs_reconciliation(alert)
        record["ack_required_from"] = recipients

        if not self._atomic_write(self.message_path(alert.alert_id), record):
            return None
        return record

    def dispatch_many(
        self, alerts: Sequence[Alert], now: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """批量出箱; 逐条独立失败不影响其余(fail-open)。"""
        records: List[Dict[str, Any]] = []
        for alert in alerts:
            record = self.dispatch(alert, now)
            if record is not None:
                records.append(record)
        return records

    # -- 回执 -------------------------------------------------------------

    def ack(
        self, alert_id: str, consumer: str, now: Optional[datetime] = None
    ) -> bool:
        """消费方回执(读走 + ACK), 原子写 ACK 文件。

        生产由 Nova 线/Ethan 线各自调用; 复演/测试用于闭环验证。
        """
        stamp = now or datetime.now(tz=UTC)
        payload = {
            "alert_id": alert_id,
            "consumer": consumer,
            "acked_at": iso_utc(stamp),
            "message": os.path.basename(self.message_path(alert_id)),
        }
        return self._atomic_write(self.ack_path(alert_id, consumer), payload)

    def read_message(self, alert_id: str) -> Optional[Dict[str, Any]]:
        """只读取回出箱消息体; 缺失或损坏返回 None。"""
        path = self.message_path(alert_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def delivery_status(
        self, alert_id: str, now: Optional[datetime] = None
    ) -> Optional[DeliveryStatus]:
        """汇总单条告警的送达与回执闭环状态。"""
        record = self.read_message(alert_id)
        if record is None:
            return None
        delivered = [str(r) for r in record.get("delivered_to", []) or []]
        acked = [
            consumer
            for consumer in delivered
            if os.path.isfile(self.ack_path(alert_id, consumer))
        ]
        age: Optional[float] = None
        dispatched = parse_ts(record.get("dispatched_at"))
        if dispatched is not None and now is not None:
            age = round((now - dispatched).total_seconds() / 3600.0, 4)
        return DeliveryStatus(
            alert_id=alert_id,
            delivered_to=delivered,
            acked_by=acked,
            closed=bool(delivered) and len(acked) == len(delivered),
            age_hours=age,
        )

    def list_alert_ids(self) -> List[str]:
        """列出出箱内的告警 ID(排除 ACK 文件与临时文件)。"""
        if not os.path.isdir(self.root):
            return []
        ids: List[str] = []
        for name in sorted(os.listdir(self.root)):
            if name.startswith(".tmp-") or _ACK_SUFFIX in name or not name.endswith(".json"):
                continue
            ids.append(name[: -len(".json")])
        return ids

    def pending(
        self, now: datetime, timeout_hours: float = ACK_TIMEOUT_HOURS
    ) -> List[DeliveryStatus]:
        """超时未闭环清单(供下一轮定级上抬用)。"""
        stale: List[DeliveryStatus] = []
        for alert_id in self.list_alert_ids():
            status = self.delivery_status(alert_id, now=now)
            if status is None or status.closed:
                continue
            if status.age_hours is not None and status.age_hours >= timeout_hours:
                stale.append(status)
        return stale

    def prune(
        self,
        now: datetime,
        retention_days: int = OUTBOX_RETENTION_DAYS,
        dry_run: bool = True,
    ) -> List[str]:
        """列出(默认不删)超过保留期的出箱文件。

        S3-2 要求保留 ≥7 天。默认 dry_run=True 只报不删;
        真正清理需显式 dry_run=False, 且不在任何自动任务中调用。
        """
        if not os.path.isdir(self.root):
            return []
        cutoff = now - timedelta(days=retention_days)
        expired: List[str] = []
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if not os.path.isfile(path):
                continue
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=UTC)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            expired.append(path)
            if not dry_run:
                try:
                    os.unlink(path)
                except OSError:
                    continue
        return expired


def outbox_root(share_dir: str) -> str:
    """生产出箱根目录: <share>/inbox/cost-alert-outbox。"""
    return os.path.join(share_dir, OUTBOX_SUBPATH)
