"""
Community Games — LAO v3.1 P1-7
=================================

Melody 社区玩法接口(6 种玩法·仅接口定义·不实现)。

玩法:
  1. 对决 (duel)   : 两套经验组合"对决", 由验证度/稀有度等分胜负
  2. 工坊 (workshop): 用户把多段经验"拼装"成新 Agent 画像
  3. 段位 (rank)   : 经验/用户的段位体系(青铜→王者)
  4. 盲盒 (blindbox): 随机抽取并解锁一段经验(可审核/可溯源)
  5. 连锁 (combo)  : 经验之间的因果/复利连锁链
  6. 趋势 (trend)  : 社区热门经验趋势(排行榜/热度)

边界:
  - 本文件只定义接口契约(方法签名 + 语义), **不实现逻辑**
  - 实现由 Melody(社区/交易域) 提供
  - LAO 侧只提供已验证经验的检索/存证基础
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GameContext:
    """社区玩法公共上下文。"""
    owner: str                     # 参与者
    agent_id: str = "customer_service"
    community_id: str = "zwiserfit"
    meta: Dict[str, Any] = field(default_factory=dict)


class CommunityGames:
    """6 种社区玩法接口(仅签名, 由 Melody 实现)。"""

    # ① 对决 — 两套经验组合按可信指标分胜负
    def duel(self, ctx: GameContext, left_ids: List[str], right_ids: List[str]) -> Dict[str, Any]:
        """两个经验组合对决。返回 {winner, score_left, score_right, verdict}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 duel")

    # ② 工坊 — 拼装多段经验成 Agent 画像
    def workshop(self, ctx: GameContext, experience_ids: List[str],
                 profile_name: str) -> Dict[str, Any]:
        """工坊拼装。返回 {profile_id, composition, status}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 workshop")

    # ③ 段位 — 经验/用户段位体系
    def rank(self, ctx: GameContext, target: str) -> Dict[str, Any]:
        """查询段位。返回 {target, tier, points, progress}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 rank")

    # ④ 盲盒 — 随机解锁一段可溯源经验
    def blindbox(self, ctx: GameContext, pool_ids: List[str]) -> Dict[str, Any]:
        """开盲盒。返回 {unlocked_id, attestation_ref, rarity}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 blindbox")

    # ⑤ 连锁 — 经验因果/复利连锁链
    def combo(self, ctx: GameContext, seed_id: str, max_depth: int = 3) -> Dict[str, Any]:
        """经验连锁链。返回 {chain: [...], total_effect}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 combo")

    # ⑥ 趋势 — 社区热门经验趋势
    def trend(self, ctx: GameContext, period: str = "week") -> Dict[str, Any]:
        """社区趋势。返回 {hot: [...], rankings}。"""
        raise NotImplementedError("Melody 域: 由 Melody 实现 trend")
