# v3.5.2-laorefactor: P0-2
"""
Loop Guard — LAO key_anchor 循环边界哨兵 (2026-08-19 Shuyu审定·外部案例B)
============================================================================

外部案例B(OpenFang loop_guard)映射: 文本重复哈希检测·
同一文本连续 N 次触发 → 判定循环 → 拦截(超阈值)。

对应 waxell $47k 死循环教训[E·仅设计参考]: per-agent 循环保护。

双保险联动:
    本模块(LAO侧)检测文本循环 → 拦截重复请求
    RIS 循环检测(ris/agent.py)检测进程/事件循环 → 恢复动作
    两者独立·互不依赖(禁止跨包import)·通过共享状态文件联动。

约束: 仅标准库 · docstring · fail-open(异常不抛·放行) · 进程内存(不持久化)。
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, List, Optional


class LoopGuard:
    """key_anchor 循环边界哨兵: 同一文本连续触发超阈值 → 拦截。"""

    DEFAULT_THRESHOLD = 3      # 同一文本连续触发 N 次 → 判定循环
    DEFAULT_WINDOW_SECONDS = 300  # 时间窗(5分钟·窗内计数)

    def __init__(self, threshold: int = DEFAULT_THRESHOLD,
                 window_seconds: int = DEFAULT_WINDOW_SECONDS,
                 agent_id: str = "default"):
        """初始化循环哨兵(进程内存·per-agent 隔离)。

        Args:
            threshold: 连续触发阈值(默认3·可配)。
            window_seconds: 时间窗(默认300s·窗内连续计数)。
            agent_id: 归属 agent(隔离键·per-agent 循环保护)。
        """
        self.threshold = max(threshold, 1)
        self.window_seconds = max(window_seconds, 1)
        self.agent_id = agent_id
        self._counts: Dict[str, List[float]] = {}   # text_hash -> [timestamps]
        self._lock = threading.Lock()

    def _fingerprint(self, text: str) -> str:
        """文本指纹(sha256[:16]·与经验萃取一致)。"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def check(self, text: str) -> Dict[str, Any]:
        """检查文本是否触发循环(计数+1·超阈值→拦截)。

        Args:
            text: 请求/触发文本。

        Returns:
            {"looping": bool, "count": int, "threshold": int,
             "fingerprint": str, "intercepted": bool}

        looping=True → 调用方应拦截(不执行该请求/不重试)。
        fail-open: 异常返回 {"looping": False}(放行不抛)。
        """
        try:
            if not text or not text.strip():
                return {"looping": False, "count": 0,
                        "threshold": self.threshold, "fingerprint": "", "intercepted": False}
            fp = self._fingerprint(text.strip())
            now = time.time()
            with self._lock:
                ts_list = self._counts.get(fp, [])
                # 窗内保留(去旧)
                ts_list = [t for t in ts_list if now - t <= self.window_seconds]
                ts_list.append(now)
                self._counts[fp] = ts_list
                count = len(ts_list)
            looping = count >= self.threshold
            return {
                "looping": looping,
                "count": count,
                "threshold": self.threshold,
                "fingerprint": fp,
                "intercepted": looping,
            }
        except Exception:
            return {"looping": False, "count": 0,
                    "threshold": self.threshold, "fingerprint": "", "intercepted": False}

    def reset(self, text: str) -> None:
        """重置指定文本的计数(正常处理后调用·防误伤)。"""
        try:
            if not text:
                return
            fp = self._fingerprint(text.strip())
            with self._lock:
                self._counts.pop(fp, None)
        except Exception:
            pass

    def summary(self) -> Dict[str, int]:
        """当前活跃计数摘要。"""
        with self._lock:
            return {"active_fingerprints": len(self._counts),
                    "total_events": sum(len(v) for v in self._counts.values())}
