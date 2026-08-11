"""
CostTracker — 路由调用成本日志 + Nova 成本同步
==============================================

记录每次模型调用成本(含 latency)并汇总。
① 授权后自动匿名回传 model+tokens+latency+cost 到 Nova(L1 总消耗成本优化)。
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Dict, List, Optional


# Nova 成本接收 URL(可经环境变量覆盖; 默认走 Agent-Bus 事件文件回传)
NOVA_SYNC_URL = os.environ.get("NOVA_SYNC_URL", "")


class CostTracker:
    """追踪并持久化模型调用成本, 支持匿名回传 Nova。"""

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "routing_cost_log.json"
        )
        self.records: List[dict] = []
        self._load()

    def record(
        self,
        task: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_ms: float = 0.0,
    ) -> dict:
        """记录一次模型调用成本。

        Args:
            task: 任务描述。
            model: 使用的模型名称。
            tokens_in: 输入 token 数。
            tokens_out: 输出 token 数。
            cost_usd: 美元成本。
            latency_ms: 延迟毫秒(LAO v3.1 新增, 回传 Nova 用)。

        Returns:
            刚记录的条目 dict。
        """
        entry = {
            "task": task,
            "model": model,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "cost_usd": float(cost_usd),
            "latency_ms": float(latency_ms),
            "timestamp": datetime.now().isoformat(),
        }
        self.records.append(entry)
        self._save()
        return entry

    def total_cost(self) -> float:
        """返回所有记录的累计美元成本。"""
        return sum(r.get("cost_usd", 0) for r in self.records)

    # -- Nova 成本同步 (P0-4) -------------------------------------------------

    def sync_to_nova(self, anon: bool = True) -> Dict[str, Any]:
        """① 授权后自动匿名回传 model+tokens+latency+cost 到 Nova。

        Args:
            anon: 匿名化(去除 task 等可辨识字段, 只回传聚合统计)。

        Returns:
            同步结果的摘要 dict; 失败返回 {"ok": False, "reason": ...}。
        """
        if not self.records:
            return {"ok": True, "synced": 0, "reason": "无待同步记录"}

        # 聚合(按 model)
        agg: Dict[str, dict] = {}
        for r in self.records:
            m = r["model"]
            a = agg.setdefault(m, {
                "model": m,
                "calls": 0, "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "latency_ms_sum": 0.0,
            })
            a["calls"] += 1
            a["tokens_in"] += r["tokens_in"]
            a["tokens_out"] += r["tokens_out"]
            a["cost_usd"] += r["cost_usd"]
            a["latency_ms_sum"] += r["latency_ms"]

        payload = {
            "source": "lao-cost-tracker",
            "type": "cost-sync",
            "aggregate": list(agg.values()),
            "total_cost_usd": round(self.total_cost(), 6),
            "total_calls": len(self.records),
            "anonymized": anon,
            "timestamp": datetime.now().isoformat(),
        }
        if anon:
            # 匿名化: 剔除 task/来源等可辨识字段, 只保留 model+聚合统计
            for a in payload["aggregate"]:
                a.pop("task", None)

        # 回传
        ok = False
        if NOVA_SYNC_URL:
            ok = self._post_nova(payload)
        else:
            # 默认落盘到 Nova 读取的成本档案(本地, 供 Nova/Stella 聚合)
            ok = self._write_sync_file(payload)

        return {
            "ok": ok,
            "synced": len(self.records),
            "total_cost_usd": payload["total_cost_usd"],
            "target": "nova",
        }

    def _post_nova(self, payload: dict) -> bool:
        try:
            req = urllib.request.Request(
                NOVA_SYNC_URL, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _write_sync_file(self, payload: dict) -> bool:
        """写本地成本档案(供 Nova 读取), 原子写。"""
        out = os.path.join(os.path.dirname(self.log_path), "nova_cost_sync.json")
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except OSError:
            return False

    # -- 持久化 -------------------------------------------------------------

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w") as f:
            json.dump({"records": self.records, "total_cost": self.total_cost()}, f, indent=2)

    def _load(self) -> None:
        try:
            with open(self.log_path) as f:
                data = json.load(f)
                self.records = data.get("records", [])
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []
