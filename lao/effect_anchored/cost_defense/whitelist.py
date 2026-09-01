# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""LAO 成本防线 — S2 当日指令白名单 (N1-b)。

规格锚点 (77号 _rev2):
    - S2-1 白名单数据结构: share/state/provider-daily-whitelist-YYYY-MM-DD.json,
      JSON 非自由文本(防 08-14 式默认值漂移); 日粒度自动过期(次日 00:00 UTC)。
    - S2-1 默认安全态: 文件缺失、过期、损坏、未生效、两列表皆空
      → 对一切切入非文件内 provider 的切换发 🟠 告警(宁紧勿松)。
    - S2-2 校验点: to_provider ∈ denied_providers(或 ∉ allowed 且 denied 非空) → 立即告警,
      axis=both; 检测延迟 ≤ 单条审计落盘间隔(即第1笔即触发)。
    - S2-3 与止血基线的关系: 旁路审计, 不修改 model_router.py 选路逻辑。

级别映射(施工期口径, 已在阶段证据中标注待统筹席确认):
    - 命中 denied_providers      → L3 🔴 (违反已签当日指令, 成本+行为双涉)
    - 白名单有效但 provider 未列 → L2 🟠 (未授权切入)
    - 白名单缺失/过期/损坏/空  → L2 🟠 (默认安全态, 宁紧勿松)

约束: 仅标准库 · 只读旁路(白名单文件只读不写) · fail-open(解析失败落默认安全态)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .model import (
    COST_BASIS_UNRECONCILED,
    Alert,
    SwitchEvent,
    day_key,
    hour_key,
    iso_utc,
    parse_ts,
)
from .sentinel import DedupStore, _alert_id, resolve_daily_budget

WHITELIST_FILENAME = "provider-daily-whitelist-{date}.json"

# 校验判定
VERDICT_DENIED = "denied"
VERDICT_ALLOWED = "allowed"
VERDICT_UNLISTED = "unlisted"

# 白名单加载状态
STATUS_VALID = "valid"
STATUS_MISSING = "missing"
STATUS_EXPIRED = "expired"
STATUS_MALFORMED = "malformed"
STATUS_NOT_YET_EFFECTIVE = "not_yet_effective"
STATUS_EMPTY = "empty"

# 默认安全态状态集合(一律按宁紧勿松处理)
_FALLBACK_STATUSES = frozenset(
    {
        STATUS_MISSING,
        STATUS_EXPIRED,
        STATUS_MALFORMED,
        STATUS_NOT_YET_EFFECTIVE,
        STATUS_EMPTY,
    }
)


@dataclass
class DailyWhitelist:
    """当日 provider 白名单(来源: 统筹席/创始人当日指令落盘文件, 只读)。"""

    date: str
    allowed_providers: List[str] = field(default_factory=list)
    denied_providers: List[str] = field(default_factory=list)
    signed_by: str = ""
    effective_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    notes: str = ""
    source_path: str = ""

    def is_active(self, now: datetime) -> bool:
        """判定当前时刻白名单是否在有效期内(缺失边界按宽松端处理)。"""
        if self.effective_from is not None and now < self.effective_from:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return True

    def verdict(self, provider: str) -> str:
        """对 to_provider 给出判定: denied / allowed / unlisted。"""
        name = (provider or "").strip().lower()
        if name in {p.strip().lower() for p in self.denied_providers}:
            return VERDICT_DENIED
        if name in {p.strip().lower() for p in self.allowed_providers}:
            return VERDICT_ALLOWED
        return VERDICT_UNLISTED


def whitelist_path(state_dir: str, day: str) -> str:
    """白名单文件路径 share/state/provider-daily-whitelist-YYYY-MM-DD.json。"""
    return os.path.join(state_dir, WHITELIST_FILENAME.format(date=day))


def load_whitelist(
    state_dir: str, now: datetime, day: Optional[str] = None
) -> Tuple[Optional[DailyWhitelist], str]:
    """只读加载当日白名单并给出状态。

    Args:
        state_dir: 白名单目录(share/state)。
        now: 评估时刻(UTC), 用于过期判定。
        day: 指定日期键(YYYY-MM-DD); 默认取 now 的 UTC 日窗。

    Returns:
        (白名单对象或 None, 状态)。状态属于 _FALLBACK_STATUSES 时按默认安全态处理。
    """
    target_day = day or day_key(now)
    path = whitelist_path(state_dir, target_day)
    if not os.path.isfile(path):
        return None, STATUS_MISSING
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None, STATUS_MALFORMED
    if not isinstance(payload, dict):
        return None, STATUS_MALFORMED

    allowed = payload.get("allowed_providers")
    denied = payload.get("denied_providers")
    if not isinstance(allowed, list) or not isinstance(denied, list):
        return None, STATUS_MALFORMED

    effective_from = parse_ts(payload.get("effective_from"))
    expires_at = parse_ts(payload.get("expires_at"))
    if expires_at is None:
        # 日粒度自动过期: 未写明则按次日 00:00 UTC 兜底(S2-1)
        parsed_day = parse_ts(f"{target_day}T00:00:00+00:00")
        expires_at = parsed_day + timedelta(days=1) if parsed_day else None

    whitelist = DailyWhitelist(
        date=str(payload.get("date", target_day) or target_day),
        allowed_providers=[str(p) for p in allowed],
        denied_providers=[str(p) for p in denied],
        signed_by=str(payload.get("signed_by", "") or ""),
        effective_from=effective_from,
        expires_at=expires_at,
        notes=str(payload.get("notes", "") or ""),
        source_path=path,
    )
    if not whitelist.is_active(now):
        if whitelist.effective_from is not None and now < whitelist.effective_from:
            return whitelist, STATUS_NOT_YET_EFFECTIVE
        return whitelist, STATUS_EXPIRED
    if not whitelist.allowed_providers and not whitelist.denied_providers:
        # 两列表皆空 = 无授权判定依据; 按宁紧勿松走默认安全态, 不得因此全静默
        return whitelist, STATUS_EMPTY
    return whitelist, STATUS_VALID


class WhitelistAuditor:
    """S2 白名单旁路审计器: 逐笔校验 switch_audit 的 to_provider。

    只读: 不改选路逻辑、不写审计账本; 仅产出告警记录(交 S3 分发)。
    """

    def __init__(
        self,
        daily_budget_usd: Optional[float] = None,
        cost_basis: str = COST_BASIS_UNRECONCILED,
        dedup_store: Optional[DedupStore] = None,
    ):
        """初始化审计器。

        Args:
            daily_budget_usd: 用于告警字段 budget_usd 标注; None 时读 env(默认 7.0)。
            cost_basis: 成本口径标注(N-01)。
            dedup_store: 可选去重存储; None 表示逐笔全量输出(用于"第1笔即告警"实测)。
        """
        self.budget = daily_budget_usd if daily_budget_usd else resolve_daily_budget()
        self.cost_basis = cost_basis
        self.dedup = dedup_store

    # -- 单笔判定 ---------------------------------------------------------

    def evaluate_event(
        self,
        event: SwitchEvent,
        whitelist: Optional[DailyWhitelist],
        status: str,
        now: Optional[datetime] = None,
    ) -> Optional[Alert]:
        """对单笔切换事件做白名单校验, 命中则返回告警(未命中返回 None)。

        默认安全态(status ∈ 缺失/过期/损坏/未生效): 一切切入"非文件内 provider"的切换
        均发 🟠; 文件不存在时视为文件内无任何 provider, 故所有切换均告警(宁紧勿松)。
        """
        ts = now or event.ts
        if ts is None:
            return None
        provider = (event.to_provider or "").strip()

        if status in _FALLBACK_STATUSES:
            # 默认安全态: 过期/损坏/缺失的文件不构成授权, 即便其中曾 allowed 也不放行
            level, axis, rule = "L2", "behavior", f"whitelist_{status}"
        else:
            verdict = whitelist.verdict(provider) if whitelist else VERDICT_UNLISTED
            if verdict == VERDICT_ALLOWED:
                return None
            if verdict == VERDICT_DENIED:
                level, axis, rule = "L3", "both", "to_provider_in_denied_providers"
            else:
                level, axis, rule = "L2", "behavior", "to_provider_not_in_allowed_providers"

        dedup_key = f"({rule},provider={provider},{hour_key(ts)})"
        if self.dedup is not None and not self.dedup.should_fire(dedup_key, level, ts):
            return None
        if self.dedup is not None:
            self.dedup.mark_fired(dedup_key, level, ts)

        return Alert(
            alert_id=_alert_id(dedup_key, ts),
            ts=iso_utc(ts),
            level=level,
            axis=axis,
            trigger_rule=rule,
            window=hour_key(ts),
            dedup_key=dedup_key,
            provider=provider,
            model=event.to_model,
            task_id=event.task_id,
            sample_details=[
                {
                    "ts": iso_utc(event.ts) if event.ts else None,
                    "from_provider": event.from_provider,
                    "from_model": event.from_model,
                    "to_provider": event.to_provider,
                    "to_model": event.to_model,
                    "reason": event.reason,
                    "triggered_by": event.triggered_by,
                    "whitelist_source": whitelist.source_path if whitelist else None,
                    "whitelist_signed_by": whitelist.signed_by if whitelist else None,
                }
            ],
            cost_basis=self.cost_basis,
            budget_usd=self.budget,
        )

    # -- 流式审计 ---------------------------------------------------------

    def audit_stream(
        self,
        events: Sequence[SwitchEvent],
        whitelist: Optional[DailyWhitelist],
        status: str,
    ) -> Dict[str, Any]:
        """按时间顺序逐笔审计, 报告首次告警的笔序(验证"第1笔即触发")。

        Returns:
            {"alerts": [...], "first_hit_index": int|None, "scanned": int}
            first_hit_index 为 1-based 笔序; 1 表示第1笔即告警。
        """
        ordered = sorted([e for e in events if e.ts is not None], key=lambda e: e.ts)
        alerts: List[Alert] = []
        first_hit_index: Optional[int] = None
        for index, event in enumerate(ordered, start=1):
            alert = self.evaluate_event(event, whitelist, status)
            if alert is None:
                continue
            if first_hit_index is None:
                first_hit_index = index
            alerts.append(alert)
        if self.dedup is not None:
            self.dedup.flush()
        return {"alerts": alerts, "first_hit_index": first_hit_index, "scanned": len(ordered)}
