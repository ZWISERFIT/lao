"""T2 统一任务身份模块。

P1-A T2 规格落地（143-T2-Design）：
    - TaskIdentity: 统一任务身份数据类
    - IsolationManager: 主侧隔离管理器
    - compute_session_fingerprint: SHA-256 会话指纹

导出核心接口，供 router_r3.py 集成时 import。
"""

from .schema import TaskIdentity, TASK_IDENTITY_SCHEMA
from .fingerprint import compute_session_fingerprint
from .isolation import IsolationManager

__all__ = [
    "TaskIdentity",
    "TASK_IDENTITY_SCHEMA",
    "compute_session_fingerprint",
    "IsolationManager",
]
