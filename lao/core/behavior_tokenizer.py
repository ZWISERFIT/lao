"""
行为Tokenizer — LAO v2 懂人性层核心组件
=======================================

把"用户的行为"拆成token，就像LLM把"一句话"拆成token一样。

行为token标准化类别（5~7个预定义类别）：

| 类别 | 示例 | 特征维度 |
|:-----|:-----|:---------|
| action_register | 注册 | 来源、时间、邀请码 |
| action_checkin | 到店训练 | 时长、项目、教练 |
| action_dialog | 与AI对话 | 原文、情绪值、主题分类 |
| action_purchase | 购买 | 金额、品类、支付方式 |
| action_silence | 无行为持续 | 沉默天数 |
| action_cancel | 取消/退费 | 原因、阶段 |

LLM Tokenizer的对称类比：
- 词 → token_id → embedding
- 行为 → behavior_type_code + metadata → behavior_vector
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import hashlib


class ActionType(str, Enum):
    """预定义行为类别（6类）"""
    REGISTER = "action_register"      # 注册
    CHECKIN = "action_checkin"        # 到店训练
    DIALOG = "action_dialog"          # 与AI对话
    PURCHASE = "action_purchase"      # 购买
    SILENCE = "action_silence"        # 无行为/沉默
    CANCEL = "action_cancel"          # 取消/退费


@dataclass
class BehaviorToken:
    """
    行为token —— 类似LLM的一个词token
    
    行为空间中的原子单位：
    一个人在一个时间点做的一件事
    """
    user_id: str
    action_type: ActionType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 行为embedding（可选·LLM对称中这一步相当于词嵌入）
    behavior_vector: Optional[List[float]] = None
    
    def __post_init__(self):
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)
    
    @property
    def behavior_code(self) -> str:
        """
        行为类型代码——类似token_id
        
        格式: "{action_type}_{date}_{user_hash}"
        作用：全局唯一标识一个行为实例
        """
        date_str = self.timestamp.strftime("%Y%m%d")
        user_hash = hashlib.md5(self.user_id.encode()).hexdigest()[:8]
        return f"{self.action_type.value}_{date_str}_{user_hash}"
    
    @property
    def behavior_code_type_only(self) -> str:
        """仅行为类型——类似词表ID，用于构建转移矩阵"""
        return f"{self.action_type.value}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "action_type": self.action_type.value,
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp,
            "metadata": self.metadata,
            "behavior_code": self.behavior_code,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorToken":
        return cls(
            user_id=data["user_id"],
            action_type=ActionType(data["action_type"]),
            timestamp=data["timestamp"],
            metadata=data.get("metadata", {}),
        )


class BehaviorTokenSequence:
    """
    行为token序列 —— 类似LLM的句子
    
    就是一个人的完整行为流：按时间排序的BehaviorToken列表
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.tokens: List[BehaviorToken] = []
    
    def add(self, token: BehaviorToken):
        self.tokens.append(token)
    
    def add_from_dict(self, data: Dict[str, Any]):
        self.tokens.append(BehaviorToken.from_dict(data))
    
    def __len__(self):
        return len(self.tokens)
    
    def __getitem__(self, idx):
        return self.tokens[idx]
    
    def last(self) -> Optional[BehaviorToken]:
        return self.tokens[-1] if self.tokens else None
    
    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.tokens]
    
    def action_type_sequence(self) -> List[str]:
        """仅提取行为类型序列 —— 用于构建马尔可夫链"""
        return [t.behavior_code_type_only for t in self.tokens]


def behavior_silence_token(user_id: str, days_since_last: int) -> BehaviorToken:
    """
    生成沉默token —— 当用户X天没有行为时，生成一个沉默标记
    
    创始人说："系统知道用户沉默了"
    这是实现方式：每次update用户状态时，如果发现无新一轮行为→生成沉默token
    """
    return BehaviorToken(
        user_id=user_id,
        action_type=ActionType.SILENCE,
        timestamp=datetime.now(timezone.utc),
        metadata={"days_since_last": days_since_last},
    )


# 行为embedding维度的参考定义（后续Phase 2实现完整embedding）
BEHAVIOR_EMBEDDING_DIMENSIONS = {
    "time_bucket": [0, 1, 2, 3],        # 0=凌晨 1=上午 2=下午 3=晚上
    "day_of_week": list(range(7)),        # 0=Mon ... 6=Sun
    "days_since_last": list(range(0, 31)), # 距上次活动天数
    "is_weekend": [0, 1],
    "preceding_actions": [],              # 前X个行为类型（动态填充）
}
