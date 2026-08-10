"""
CostTracker — 路由调用成本日志
==============================

记录每次模型调用成本并汇总。
"""

import json
import os
from datetime import datetime


class CostTracker:
    """追踪并持久化模型调用成本。

    每次调用 record() 追加一条记录并写入磁盘。
    total_cost() 返回累计成本。
    """

    def __init__(self, log_path: str | None = None):
        """初始化成本追踪器。

        Args:
            log_path: 日志文件路径，默认为项目根目录的 routing_cost_log.json。
        """
        self.log_path = log_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "routing_cost_log.json"
        )
        self.records: list = []
        self._load()

    def record(
        self,
        task: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> dict:
        """记录一次模型调用成本。

        Args:
            task: 任务描述。
            model: 使用的模型名称。
            tokens_in: 输入 token 数。
            tokens_out: 输出 token 数。
            cost_usd: 美元成本。

        Returns:
            刚记录的条目 dict。
        """
        entry = {
            "task": task,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "timestamp": datetime.now().isoformat(),
        }
        self.records.append(entry)
        self._save()
        return entry

    def total_cost(self) -> float:
        """返回所有记录的累计美元成本。"""
        return sum(r.get("cost_usd", 0) for r in self.records)

    def _save(self) -> None:
        """将记录持久化到磁盘。"""
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w") as f:
            json.dump(
                {"records": self.records, "total_cost": self.total_cost()},
                f,
                indent=2,
            )

    def _load(self) -> None:
        """从磁盘加载历史记录。"""
        try:
            with open(self.log_path) as f:
                data = json.load(f)
                self.records = data.get("records", [])
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []
