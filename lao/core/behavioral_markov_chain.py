"""
行为马尔可夫链引擎 (BMC) — LAO v2 懂人性层核心
===============================================

数学原理：
P(下一个行为 | 所有历史行为) 
≈ P(下一个行为 | 最近1~N个行为)
= 基于行为token序列的马尔可夫链概率

对称映射：
  LLM: P(下一个词 | 前面所有词) — 词空间
  LAO: P(下一个行为 | 此人全部历史行为) — 行为空间

架构：
  1. 转移矩阵：action_from → action_to → count
  2. 多阶支持：1阶（默认）/ 2阶
  3. 预测函数：predict() 返回概率分布
  4. 序列更新：add_behavior() 自动更新矩阵
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import json
import math

from .behavior_tokenizer import ActionType, BehaviorToken, BehaviorTokenSequence


class BehavioralMarkovChain:
    """
    行为马尔可夫链引擎
    
    核心数据结构：行为状态转移矩阵
    - 1阶: P(next | current) = count(current→next) / count(current→*)
    - 2阶: P(next | current, prev) = count(prev→current→next) / count(prev→current→*)
    
    用法:
        bmc = BehavioralMarkovChain()
        bmc.add_behavior("user1", token1)
        bmc.add_behavior("user1", token2)
        ...
        bmc.predict("user1")  →  {"action_checkin": 0.5, "action_silence": 0.3, ...}
    """
    
    def __init__(self, order: int = 1):
        """
        Args:
            order: 马尔可夫链阶数
                - 1阶: 只看上一个行为（默认）
                - 2阶: 看上两个行为（小数据集慎用）
        """
        self.order = order
        
        # 行为状态转移矩阵
        # 1阶: matrix[from_type][to_type] = count
        # 2阶: matrix[(prev_type, current_type)][next_type] = count
        self.transition_matrix = defaultdict(lambda: defaultdict(int))
        
        # 每个用户的行为序列（按时间排序的action_type列表）
        self.sequences: Dict[str, List[str]] = {}
        
        # 每个用户的总行为计数
        self.total_counts: Dict[str, int] = {}
        
        # 全局统计（所有用户汇总）
        self.global_transitions = defaultdict(lambda: defaultdict(int))
        self.global_predictions: Dict[str, float] = {}
    
    def add_behavior(self, user_id: str, behavior: BehaviorToken) -> None:
        """
        添加一个行为 → 自动更新转移矩阵
        
        类似LLM的"添加一个词到序列"：
        LLM: 一个词后面的概率分布会改变
        BMC: 一个行为后面的概率分布会改变
        """
        seq = self.sequences.setdefault(user_id, [])
        action_type = behavior.behavior_code_type_only
        
        if seq:
            if self.order == 1:
                # 1阶：当前状态→新状态
                prev = seq[-1]
                self.transition_matrix[prev][action_type] += 1
                self.global_transitions[prev][action_type] += 1
            elif self.order == 2 and len(seq) >= 1:
                # 2阶：前两个状态→新状态
                if len(seq) >= 2:
                    key = (seq[-2], seq[-1])
                else:
                    key = ("<BOS>", seq[-1])
                self.transition_matrix[key][action_type] += 1
                self.global_transitions[key][action_type] += 1
        
        seq.append(action_type)
        self.total_counts[user_id] = len(seq)
    
    def add_behaviors(self, user_id: str, behaviors: List[BehaviorToken]) -> int:
        """
        批量添加行为序列
        
        Returns: 添加的行为数量
        """
        count = 0
        for b in behaviors:
            self.add_behavior(user_id, b)
            count += 1
        return count
    
    def predict(self, user_id: str, min_observations: int = 1) -> Dict[str, float]:
        """
        预测该用户的下一个行为概率分布
        
        Args:
            user_id: 用户ID
            min_observations: 最小观测数阈值（少于则返回全局概率）
            
        Returns:
            格式: {"action_checkin": 0.45, "action_silence": 0.30, ...}
            按概率降序排列
        """
        if user_id not in self.sequences or not self.sequences[user_id]:
            return self._global_predict()
        
        seq = self.sequences[user_id]
        total_user_behaviors = len(seq)
        
        if total_user_behaviors < min_observations:
            return self._global_predict()
        
        if self.order == 1:
            last_action = seq[-1]
            transitions = self.transition_matrix[last_action]
        else:
            if len(seq) >= 2:
                key = (seq[-2], seq[-1])
            else:
                key = ("<BOS>", seq[-1])
            transitions = self.transition_matrix.get(key, {})
        
        total = sum(transitions.values())
        if total == 0:
            return self._global_predict()
        
        # 计算概率
        result = {}
        for action, count in transitions.items():
            result[action] = count / total
        
        # 按概率降序排列
        return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))
    
    def predict_top_k(self, user_id: str, k: int = 3) -> List[Tuple[str, float]]:
        """返回前K个最可能的行为"""
        probs = self.predict(user_id)
        return list(probs.items())[:k]
    
    def _global_predict(self) -> Dict[str, float]:
        """全局概率（当用户数据不足时fallback）"""
        total = sum(
            sum(to_dict.values()) 
            for to_dict in self.global_transitions.values()
        )
        if total == 0:
            return {}
        
        # 汇总所有from→to的count
        agg = defaultdict(int)
        for from_action, to_dict in self.global_transitions.items():
            for to_action, count in to_dict.items():
                agg[to_action] += count
        
        result = {
            action: count / total
            for action, count in agg.items()
        }
        return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))
    
    def get_transition_from(self, action_type: str) -> Dict[str, float]:
        """从某个行为出发的概率分布（全局）"""
        transitions = self.transition_matrix.get(action_type, {})
        total = sum(transitions.values())
        if total == 0:
            return {}
        return {
            action: count / total
            for action, count in transitions.items()
        }
    
    def sequence_length(self, user_id: str) -> int:
        """获取用户的序列长度"""
        return len(self.sequences.get(user_id, []))
    
    def clear_user(self, user_id: str):
        """清空用户数据"""
        if user_id in self.sequences:
            # 从转移矩阵中移除该用户的数据
            seq = self.sequences[user_id]
            for i in range(1, len(seq)):
                if self.order == 1:
                    from_action = seq[i-1]
                    to_action = seq[i]
                    self.transition_matrix[from_action][to_action] -= 1
                    # 如果减到0则清除
                    if self.transition_matrix[from_action][to_action] <= 0:
                        del self.transition_matrix[from_action][to_action]
                elif self.order == 2 and i >= 2:
                    key = (seq[i-2], seq[i-1])
                    to_action = seq[i]
                    self.transition_matrix[key][to_action] -= 1
                    if self.transition_matrix[key][to_action] <= 0:
                        del self.transition_matrix[key][to_action]
            
            del self.sequences[user_id]
            del self.total_counts[user_id]
    
    def save(self, path: str):
        """保存到文件"""
        data = {
            "order": self.order,
            "sequences": self.sequences,
            "total_counts": self.total_counts,
            "global_transitions": {
                str(k): dict(v) if isinstance(k, tuple) else dict(v)
                for k, v in self.global_transitions.items()
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, path: str) -> "BehavioralMarkovChain":
        """从文件加载"""
        import json
        with open(path) as f:
            data = json.load(f)
        
        bmc = cls(order=data.get("order", 1))
        bmc.sequences = data.get("sequences", {})
        bmc.total_counts = data.get("total_counts", {})
        
        # 重建local transition_matrix
        for key_str, to_dict in data.get("global_transitions", {}).items():
            for to_action, count in to_dict.items():
                bmc.global_transitions[key_str][to_action] = count
                # 也重建local（简化处理——local会从sequences重建）
                bmc.transition_matrix[key_str][to_action] = count
        
        return bmc


class MultiOrderBMC:
    """
    多阶行为马尔可夫链混合引擎
    
    结合1阶/2阶预测，选最优：
    - 如果用户有足够的2阶数据 → 用2阶
    - 如果2阶数据稀疏 → 退回到1阶
    - 都没有 → 全局概率
    """
    
    def __init__(self):
        self.bmc_1 = BehavioralMarkovChain(order=1)
        self.bmc_2 = BehavioralMarkovChain(order=2)
        self.min_2nd_order_obs = 5  # 至少5次2阶观测才用2阶
    
    def add_behavior(self, user_id: str, behavior: BehaviorToken):
        self.bmc_1.add_behavior(user_id, behavior)
        self.bmc_2.add_behavior(user_id, behavior)
    
    def predict(self, user_id: str) -> Dict[str, float]:
        # 检查是否有足够2阶数据
        seq = self.bmc_2.sequences.get(user_id, [])
        if len(seq) >= self.min_2nd_order_obs + 2:
            p2 = self.bmc_2.predict(user_id, min_observations=2)
            if p2:
                return p2
        
        return self.bmc_1.predict(user_id)
