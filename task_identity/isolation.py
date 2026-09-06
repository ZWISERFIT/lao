"""T2 主侧隔离管理器。

P1-A T2 规格落地（143-T2-Design 第五节）：
    - scope 判定：有 parent_task_id → side，无 → main
    - 成本隔离：side 任务 token 消耗不计入主任务 R3 护栏
    - 记忆隔离：side 任务不检索主任务私有记忆
    - 结果写入：side 任务不得直接写入主结果集
    - 取消传播：主任务取消 → 关联 side 任务自动取消（规格定义，P1-A 不实现运行时）

约束：仅标准库 · 零外部依赖 · 纯逻辑层（不持有任何运行时状态）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .schema import TaskIdentity


class IsolationManager:
    """主侧隔离管理器。

    职责：
    1. 根据 TaskIdentity.scope 决定记忆/成本/结果的隔离边界
    2. side 任务的 token 消耗不计入 parent 的 R3 护栏
    3. side 任务的结果需显式 merge 才写入主任务

    本类为纯逻辑层，不持有任何运行时状态（无文件 I/O、无网络）。
    """

    def __init__(self) -> None:
        # side_task_id → 独立成本账（P1-A 阶段仅定义接口，不实现持久化）
        self._side_ledgers: Dict[str, Dict] = {}

    # ── scope 判定 ────────────────────────────────────────────────

    @staticmethod
    def resolve_scope(parent_task_id: Optional[str]) -> str:
        """判定 scope：有 parent → side，无 parent → main。

        Args:
            parent_task_id: 父任务 ID（None 表示无父任务）。

        Returns:
            "main" 或 "side"。
        """
        return "side" if parent_task_id is not None else "main"

    # ── 隔离查询 ──────────────────────────────────────────────────

    @staticmethod
    def should_isolate_cost(identity: TaskIdentity) -> bool:
        """side 任务成本隔离：不计入主任务 R3 护栏。

        Args:
            identity: 待查询的 TaskIdentity。

        Returns:
            True=需要隔离（side 任务），False=不隔离（main 任务）。
        """
        return identity.scope == "side"

    @staticmethod
    def should_isolate_memory(identity: TaskIdentity) -> bool:
        """side 任务记忆隔离：不检索主任务私有记忆。

        Args:
            identity: 待查询的 TaskIdentity。

        Returns:
            True=需要隔离（side 任务），False=不隔离（main 任务）。
        """
        return identity.scope == "side"

    @staticmethod
    def can_write_result(identity: TaskIdentity) -> bool:
        """side 任务不得直接写入主结果集。

        Args:
            identity: 待查询的 TaskIdentity。

        Returns:
            True=可以写入（main 任务），False=不可直接写入（side 任务）。
        """
        return identity.scope == "main"

    # ── 成本归因键 ────────────────────────────────────────────────

    @staticmethod
    def get_cost_key(identity: TaskIdentity) -> str:
        """成本归因键：main 用 task_id，side 用自身 task_id（隔离）。

        无论 scope 为何，均返回 identity.task_id 作为独立归因键。
        side 任务与 main 任务的 task_id 天然不同，因此成本自动隔离。

        Args:
            identity: 待查询的 TaskIdentity。

        Returns:
            用于成本归因的字符串键。
        """
        return identity.task_id

    # ── side 账本管理（P1-A 阶段仅定义接口） ──────────────────────

    def register_side_ledger(self, side_task_id: str) -> None:
        """为 side 任务注册独立成本账。

        Args:
            side_task_id: 侧任务 task_id。
        """
        if side_task_id not in self._side_ledgers:
            self._side_ledgers[side_task_id] = {
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
            }

    def add_side_tokens(
        self, side_task_id: str, tokens_in: int, tokens_out: int
    ) -> None:
        """累计 side 任务 token（不触发主任务 R3 护栏）。

        Args:
            side_task_id: 侧任务 task_id。
            tokens_in: 输入 token 数。
            tokens_out: 输出 token 数。
        """
        self.register_side_ledger(side_task_id)
        ledger = self._side_ledgers[side_task_id]
        ledger["tokens_in"] += max(0, tokens_in)
        ledger["tokens_out"] += max(0, tokens_out)

    def get_side_ledger(self, side_task_id: str) -> Optional[Dict]:
        """查询 side 任务成本账。

        Args:
            side_task_id: 侧任务 task_id。

        Returns:
            成本账字典，不存在返回 None。
        """
        return self._side_ledgers.get(side_task_id)
