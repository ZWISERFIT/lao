"""T3 意图保持率计算。

P1-A T3 规格落地（149-T3-Design）：
    - IntentKeeper: 意图保持率计算引擎
    - 公式：保持率 = 保留意图数 / 原始意图数 × 100%
    - 验收标准：≥95%（待P1-A评测实测验证）

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class IntentItem:
    """单个意图条目。"""

    intent_id: str
    text: str
    preserved: bool = True  # 剪枝后是否保留

    def to_dict(self) -> Dict:
        return {
            "intent_id": self.intent_id,
            "text": self.text,
            "preserved": self.preserved,
        }


class IntentKeeper:
    """意图保持率计算引擎。

    用法：
        1. 创建 IntentKeeper 实例
        2. 注册压缩前的所有意图（register_intents）
        3. 剪枝后检查每个意图是否保留（check_preservation）
        4. 计算保持率（keep_rate）

    合成验证说明：
        本类仅管理意图列表和计算逻辑，不涉及任何真实数据。
    """

    def __init__(self) -> None:
        self._intents: List[IntentItem] = []

    def register_intents(self, intents: List[Dict]) -> None:
        """注册压缩前的意图列表。

        Args:
            intents: [{"intent_id": "...", "text": "..."}, ...]
        """
        self._intents = []
        for item in intents:
            self._intents.append(IntentItem(
                intent_id=item["intent_id"],
                text=item["text"],
                preserved=True,
            ))

    def check_preservation(self, pruned_content: str) -> int:
        """检查剪枝后保留了多少意图。

        Args:
            pruned_content: 剪枝后的完整文本内容。

        Returns:
            保留的意图数量。
        """
        count = 0
        for intent in self._intents:
            if intent.text in pruned_content:
                intent.preserved = True
                count += 1
            else:
                intent.preserved = False
        return count

    @property
    def total_intents(self) -> int:
        """注册的意图总数。"""
        return len(self._intents)

    @property
    def preserved_count(self) -> int:
        """当前保留的意图数。"""
        return sum(1 for i in self._intents if i.preserved)

    def keep_rate(self) -> Optional[float]:
        """计算意图保持率。

        Returns:
            保持率 [0, 1]；无意图时返回 None。
        """
        if self.total_intents == 0:
            return None
        return self.preserved_count / self.total_intents

    def meets_threshold(self, threshold: float = 0.95) -> bool:
        """是否达到验收标准。

        Args:
            threshold: 验收阈值（默认 0.95 = 95%）。

        Returns:
            True=达标，False=未达标或无数据。
        """
        rate = self.keep_rate()
        if rate is None:
            return False
        return rate >= threshold

    def report(self) -> Dict:
        """生成意图保持率报告。"""
        return {
            "total_intents": self.total_intents,
            "preserved_count": self.preserved_count,
            "keep_rate": self.keep_rate(),
            "meets_threshold_95": self.meets_threshold(0.95),
            "intents": [i.to_dict() for i in self._intents],
        }
