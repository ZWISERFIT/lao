"""T7 状态机与止损规则 — 单元测试（全部合成数据）。

P1-A T7 规格落地（147-T7-Design）：
    - 状态机 8 种合法转换 + 非法转换拒绝
    - 终态不可转换
    - Checkpoint 创建/校验/序列化
    - 止损配置与 router_r3.py 阈值对齐
    - 止损监控：token 累计/告警/熔断/重试

运行方式：
    python -m unittest task_state_machine.test_task_state_machine -v
"""

from __future__ import annotations

import json
import unittest

from task_state_machine.state_machine import (
    TaskState,
    TaskStateMachine,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
)
from task_state_machine.checkpoint import CheckpointData
from task_state_machine.stop_loss import (
    StopLossConfig,
    StopLossMonitor,
    DEFAULT_ALERT_TOKENS,
    DEFAULT_HARD_TOKENS,
    DEFAULT_MAX_RETRIES,
)

SYNTH_TASK_ID = "synth-task-001"


# ── 状态机测试 ───────────────────────────────────────────────────────

class TestStateMachineValidTransitions(unittest.TestCase):
    """状态机：8 种合法转换。"""

    def test_pending_to_running(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start("t1")
        self.assertEqual(sm.state, TaskState.RUNNING)

    def test_running_to_checkpoint(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.save_checkpoint("t2")
        self.assertEqual(sm.state, TaskState.CHECKPOINT)

    def test_checkpoint_to_running(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.save_checkpoint()
        sm.resume("t3")
        self.assertEqual(sm.state, TaskState.RUNNING)

    def test_running_to_cancelled(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.cancel("t4")
        self.assertEqual(sm.state, TaskState.CANCELLED)
        self.assertTrue(sm.is_terminal)

    def test_checkpoint_to_cancelled(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.save_checkpoint()
        sm.cancel("t5")
        self.assertEqual(sm.state, TaskState.CANCELLED)

    def test_running_to_completed(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.complete("t6")
        self.assertEqual(sm.state, TaskState.COMPLETED)
        self.assertTrue(sm.is_terminal)

    def test_running_to_failed(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.fail("t7")
        self.assertEqual(sm.state, TaskState.FAILED)
        self.assertTrue(sm.is_terminal)

    def test_checkpoint_to_failed(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.save_checkpoint()
        sm.fail("t8")
        self.assertEqual(sm.state, TaskState.FAILED)


class TestStateMachineInvalidTransitions(unittest.TestCase):
    """状态机：非法转换拒绝。"""

    def test_pending_to_completed(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        with self.assertRaises(ValueError):
            sm.complete()

    def test_pending_to_cancelled(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        with self.assertRaises(ValueError):
            sm.cancel()

    def test_terminal_no_transition(self):
        for terminal in TERMINAL_STATES:
            for target in TaskState:
                sm = TaskStateMachine(SYNTH_TASK_ID)
                # 手动设置到终态（绕过转换校验）
                sm._state = terminal
                if target != terminal:
                    with self.assertRaises(ValueError):
                        sm.transition(target)

    def test_checkpoint_to_completed(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start()
        sm.save_checkpoint()
        with self.assertRaises(ValueError):
            sm.complete()  # CHECKPOINT 不能直接→COMPLETED


class TestStateMachineFeatures(unittest.TestCase):
    """状态机：intent_locked / history / 序列化。"""

    def test_intent_locked(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        self.assertFalse(sm.intent_locked)
        sm.intent_locked = True
        self.assertTrue(sm.intent_locked)

    def test_history_tracking(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.start("t1")
        sm.save_checkpoint("t2")
        sm.resume("t3")
        sm.complete("t4")
        self.assertEqual(len(sm.history), 4)
        self.assertEqual(sm.history[0], ("t1", TaskState.PENDING, TaskState.RUNNING))
        self.assertEqual(sm.history[-1], ("t4", TaskState.RUNNING, TaskState.COMPLETED))

    def test_can_transition(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        self.assertTrue(sm.can_transition(TaskState.RUNNING))
        self.assertFalse(sm.can_transition(TaskState.COMPLETED))

    def test_serialization(self):
        sm = TaskStateMachine(SYNTH_TASK_ID)
        sm.intent_locked = True
        sm.start("t1")
        sm.complete("t2")
        d = sm.to_dict()
        self.assertEqual(d["task_id"], SYNTH_TASK_ID)
        self.assertEqual(d["state"], "COMPLETED")
        self.assertTrue(d["is_terminal"])
        self.assertTrue(d["intent_locked"])
        self.assertEqual(len(d["history"]), 2)
        # JSON 往返
        j = sm.to_json()
        parsed = json.loads(j)
        self.assertEqual(parsed["state"], "COMPLETED")


class TestTransitionTable(unittest.TestCase):
    """状态转换表完整性。"""

    def test_six_states(self):
        self.assertEqual(len(TaskState), 6)

    def test_three_terminal_states(self):
        self.assertEqual(len(TERMINAL_STATES), 3)

    def test_eight_valid_transitions(self):
        total = sum(len(targets) for targets in VALID_TRANSITIONS.values())
        self.assertEqual(total, 8)


# ── Checkpoint 测试 ──────────────────────────────────────────────────

class TestCheckpoint(unittest.TestCase):
    """Checkpoint 创建/校验/序列化。"""

    def test_create(self):
        cp = CheckpointData.create(
            task_id=SYNTH_TASK_ID,
            intent="查询门店数据",
            intent_locked=True,
            tokens_consumed=5000,
            messages_snapshot=[{"role": "user", "content": "合成消息"}],
        )
        self.assertEqual(cp.task_id, SYNTH_TASK_ID)
        self.assertEqual(cp.state, "CHECKPOINT")
        self.assertTrue(cp.intent_locked)
        self.assertEqual(cp.tokens_consumed, 5000)
        errors = cp.validate()
        self.assertEqual(errors, [])

    def test_empty_task_id_rejected(self):
        cp = CheckpointData.create(task_id="")
        errors = cp.validate()
        self.assertTrue(any("task_id" in e for e in errors))

    def test_serialization_round_trip(self):
        cp = CheckpointData.create(
            task_id=SYNTH_TASK_ID, intent="合成意图", tokens_consumed=1000,
        )
        j = cp.to_json()
        restored = CheckpointData.from_dict(json.loads(j))
        self.assertEqual(restored.task_id, SYNTH_TASK_ID)
        self.assertEqual(restored.tokens_consumed, 1000)


# ── 止损配置测试 ─────────────────────────────────────────────────────

class TestStopLossConfig(unittest.TestCase):
    """止损配置：默认值与 router_r3.py 对齐。"""

    def test_defaults_match_router(self):
        cfg = StopLossConfig()
        self.assertEqual(cfg.alert_tokens, 30_000_000)
        self.assertEqual(cfg.hard_tokens, 50_000_000)
        self.assertEqual(cfg.max_retries, 5)
        self.assertEqual(cfg.retry_window_sec, 600)

    def test_validate_ok(self):
        cfg = StopLossConfig()
        self.assertEqual(cfg.validate(), [])

    def test_validate_invalid(self):
        cfg = StopLossConfig(alert_tokens=-1, hard_tokens=-1)
        errors = cfg.validate()
        self.assertTrue(len(errors) >= 2)

    def test_validate_hard_lt_alert(self):
        cfg = StopLossConfig(alert_tokens=100, hard_tokens=50)
        errors = cfg.validate()
        self.assertTrue(any("hard_tokens" in e for e in errors))


# ── 止损监控测试 ─────────────────────────────────────────────────────

class TestStopLossMonitor(unittest.TestCase):
    """止损监控：token 累计/告警/熔断/重试。"""

    def setUp(self):
        # 使用小阈值方便测试
        self.cfg = StopLossConfig(
            alert_tokens=100, hard_tokens=200,
            max_retries=3, retry_window_sec=60,
        )
        self.mon = StopLossMonitor(config=self.cfg)

    def test_add_tokens_below_alert(self):
        result = self.mon.add_tokens("t1", 50)
        self.assertEqual(result["tokens"], 50)
        self.assertFalse(result["alerted"])
        self.assertFalse(result["broken"])

    def test_alert_triggered(self):
        result = self.mon.add_tokens("t1", 100)
        self.assertTrue(result["alerted"])
        self.assertTrue(result["new_alert"])
        self.assertFalse(result["broken"])

    def test_circuit_break(self):
        self.mon.add_tokens("t1", 100)
        result = self.mon.add_tokens("t1", 100)
        self.assertTrue(result["broken"])
        self.assertTrue(self.mon.check_blocked("t1"))

    def test_unknown_task_not_blocked(self):
        self.assertFalse(self.mon.check_blocked("unknown"))

    def test_retry_within_limit(self):
        result = self.mon.record_retry("t1", 1000.0)
        self.assertEqual(result["retry_count"], 1)
        self.assertFalse(result["exceeded"])

    def test_retry_exceeded(self):
        for i in range(4):
            self.mon.record_retry("t1", 1000.0 + i)
        self.assertTrue(self.mon.check_retry_blocked("t1"))

    def test_retry_window_reset(self):
        # 窗口内累计
        for i in range(3):
            self.mon.record_retry("t1", 1000.0 + i)
        self.assertFalse(self.mon.check_retry_blocked("t1"))
        # 窗口过期后重置
        result = self.mon.record_retry("t1", 2000.0)  # > 60s 后
        self.assertEqual(result["retry_count"], 1)
        self.assertFalse(self.mon.check_retry_blocked("t1"))

    def test_zero_tokens_ignored(self):
        result = self.mon.add_tokens("t1", 0)
        self.assertEqual(result["tokens"], 0)

    def test_task_state_query(self):
        state = self.mon.get_task_state("unknown")
        self.assertEqual(state["tokens"], 0)
        self.mon.add_tokens("t1", 50)
        state = self.mon.get_task_state("t1")
        self.assertEqual(state["tokens"], 50)


if __name__ == "__main__":
    unittest.main()
