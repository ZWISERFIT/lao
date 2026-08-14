"""RIS Recovery Engine — 自动恢复闭环 (Phase 2·创始人 23:37 令)

五步闭环: Detect → Classify → Recover → Verify → Record

铁律: **禁止只 restart 不验证**
  - Recover 后必须 Verify(检查端口/HTTP/session 响应) → Verify 通过才 Record
  - Verify 失败 → 回到 Recover 重试(有 budget 上限·不无限循环)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ris.events import RuntimeHealthEvent


@dataclass
class RecoveryAction:
    """一次恢复动作(可注入·由调用方实现具体恢复)。"""
    name: str
    recover_fn: Optional[Callable[[], bool]] = None      # 执行恢复→成功?
    verify_fn: Optional[Callable[[], bool]] = None       # 验证恢复成功?
    max_attempts: int = 3                                # 恢复 budget(不无限循环)


@dataclass
class RecoveryResult:
    """恢复结果(Record 阶段产物)。"""
    event_type: str
    agent_id: str
    detected_at: str = ""
    classified: str = ""          # classify 结论(如 session_down / cpu_anomaly)
    attempts: int = 0
    recovered: bool = False
    verified: bool = False
    recorded: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_event(self, severity: str = "info") -> RuntimeHealthEvent:
        status = "recovered" if (self.recovered and self.verified) else "failed"
        return RuntimeHealthEvent(
            event_type=self.event_type, agent_id=self.agent_id, status=status,
            severity=severity, detail={
                "classified": self.classified, "attempts": self.attempts,
                "verified": self.verified, "recorded": self.recorded, **self.detail})


class RecoveryEngine:
    """恢复引擎: Detect → Classify → Recover → Verify → Record。

    - detect_fn : 检测异常(返回 bool·或直接传异常事件)
    - classify  : 判定异常类型
    - recover   : 执行恢复动作(可多次·有 budget)
    - verify    : 验证恢复成功(禁止只 restart 不验证)
    - record    : 记录 RecoveryEvent
    """

    def __init__(self):
        self._records: List[RuntimeHealthEvent] = []

    def run(self, event_type: str, agent_id: str,
            detect_fn: Callable[[], bool],
            classify_fn: Optional[Callable[[], str]] = None,
            action: Optional[RecoveryAction] = None,
            severity: str = "warn") -> RecoveryResult:
        """执行五步闭环。"""
        result = RecoveryResult(event_type=event_type, agent_id=agent_id)
        result.detected_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        # ① Detect
        if not detect_fn():
            result.detail["detect"] = "ok"   # 无异常·无需恢复
            return result

        # ② Classify
        result.classified = classify_fn() if classify_fn else event_type
        result.detail["detect"] = f"{result.classified} detected"

        # ③ Recover + ④ Verify(循环·有 budget)
        if action is None:
            # 无恢复动作 → 标记未恢复(不 Record 为 recovered)
            result.detail["reason"] = "no_recovery_action"
            return result

        for attempt in range(1, action.max_attempts + 1):
            result.attempts = attempt
            # Recover
            recovered = action.recover_fn() if action.recover_fn else True
            if not recovered:
                result.detail["recover_fail"] = f"attempt {attempt}"
                continue
            # Verify(铁律: 必须验证)
            verified = action.verify_fn() if action.verify_fn else False
            if verified:
                result.recovered = True
                result.verified = True
                break
            result.detail["verify_fail"] = f"attempt {attempt}"

        # ⑤ Record(仅 recovered+verified 才 Record 为 recovered)
        if result.recovered and result.verified:
            result.recorded = True
            ev = result.to_event(severity)
        else:
            ev = result.to_event(severity="error" if result.attempts else "info")
        self._records.append(ev)
        return result

    def records(self) -> List[RuntimeHealthEvent]:
        return self._records
