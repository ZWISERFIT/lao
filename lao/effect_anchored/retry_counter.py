# v3.5.2-laorefactor: P0-2
"""
Retry Counter — LAO 架构重构 P0-2
=================================

退回重做计数器（进程内存·request_id → count·上限 3）。

规格依据: LAO 架构产品规格 v1.0 1.3 节（Shuyu·2026-08-19）。
生命周期: 请求到达初始化为 0 · 每次退回 +1 · 交付后清除。
上限: 3 次 · 第 3 次后调用方降级 Flash 交付。

存储位置: LAO 进程内存（Dict[request_id, int]）· 不持久化。
约束: 仅标准库 · 线程安全（threading.Lock）· fail-open。
"""

from __future__ import annotations

import threading
from typing import Dict


class RetryCounter:
    """退回重做计数器（进程内存·不持久化）。"""

    MAX_RETRIES = 3

    def __init__(self):
        self._counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def init(self, request_id: str) -> int:
        """初始化计数器为 0·返回 0。"""
        with self._lock:
            self._counts[request_id] = 0
            return 0

    def increment(self, request_id: str) -> int:
        """退回 +1·返回当前计数（超上限返回 MAX_RETRIES·不无限增长）。"""
        with self._lock:
            cur = self._counts.get(request_id, 0) + 1
            cur = min(cur, self.MAX_RETRIES)
            self._counts[request_id] = cur
            return cur

    def get(self, request_id: str) -> int:
        """当前计数（未初始化返回 0）。"""
        with self._lock:
            return self._counts.get(request_id, 0)

    def should_retry(self, request_id: str) -> bool:
        """get < MAX_RETRIES → True（可重试）。"""
        return self.get(request_id) < self.MAX_RETRIES

    def clear(self, request_id: str) -> None:
        """交付后清除（不存在的 id 静默忽略）。"""
        with self._lock:
            self._counts.pop(request_id, None)

    def summary(self) -> Dict[str, int]:
        """统计: {active_requests: n, total_retries: n}"""
        with self._lock:
            return {
                "active_requests": len(self._counts),
                "total_retries": sum(self._counts.values()),
            }
