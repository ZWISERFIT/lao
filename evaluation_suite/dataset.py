"""T9 合成评测数据集。

P1-A T9 规格落地（155-T9-Design）：
    - 30+ 合成评测样本
    - 覆盖 4 家 Provider
    - 评测维度：路由准确性/成本效率/响应质量/延迟

约束：仅标准库 · 零外部依赖 · 全部合成数据
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvalItem:
    """单个评测样本。"""

    item_id: str
    task_description: str
    expected_provider: str
    difficulty: str = "medium"   # easy / medium / hard
    category: str = "general"    # general / code / analysis / creative
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "task_description": self.task_description,
            "expected_provider": self.expected_provider,
            "difficulty": self.difficulty,
            "category": self.category,
            "metadata": self.metadata,
        }


@dataclass
class EvalDataset:
    """评测数据集。"""

    items: List[EvalItem] = field(default_factory=list)
    name: str = "synthetic_eval_v1"
    version: str = "1.0.0"

    def __len__(self) -> int:
        return len(self.items)

    def add_item(self, item: EvalItem) -> None:
        self.items.append(item)

    def get_by_category(self, category: str) -> List[EvalItem]:
        return [i for i in self.items if i.category == category]

    def get_by_difficulty(self, difficulty: str) -> List[EvalItem]:
        return [i for i in self.items if i.difficulty == difficulty]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "item_count": len(self.items),
            "items": [i.to_dict() for i in self.items],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def generate_synthetic_dataset(count: int = 32) -> EvalDataset:
    """生成合成评测数据集。

    Args:
        count: 样本数量（默认 32，满足 30+ 要求）。

    Returns:
        合成评测数据集。
    """
    providers = ["deepseek", "qwen", "token-plan", "novarouteai"]
    categories = ["general", "code", "analysis", "creative"]
    difficulties = ["easy", "medium", "hard"]

    # 合成任务模板
    task_templates = [
        ("general", "查询门店 {store_id} 的出勤数据"),
        ("general", "统计 {month} 月的销售总额"),
        ("code", "编写 Python 函数计算斐波那契数列"),
        ("code", "优化 SQL 查询性能"),
        ("analysis", "分析用户行为数据趋势"),
        ("analysis", "生成月度运营报告"),
        ("creative", "撰写产品推广文案"),
        ("creative", "设计用户引导流程"),
    ]

    dataset = EvalDataset()
    random.seed(42)  # 可复现

    for i in range(count):
        cat = categories[i % len(categories)]
        diff = difficulties[i % len(difficulties)]
        provider = providers[i % len(providers)]
        template = task_templates[i % len(task_templates)]

        task_desc = template[1].format(
            store_id=f"store-{i+1:03d}",
            month=f"2026-{(i % 12) + 1:02d}",
        )

        dataset.add_item(EvalItem(
            item_id=f"eval-{i+1:03d}",
            task_description=task_desc,
            expected_provider=provider,
            difficulty=diff,
            category=cat,
            metadata={"round": (i // 10) + 1},  # 分三轮
        ))

    return dataset


# 默认合成评测集
DEFAULT_EVAL_DATASET = generate_synthetic_dataset()
