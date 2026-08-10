"""
Experience Graph Lite — LAO 2.7 P1-③
====================================

在 Experience Atom 之间建立关系网络（Lite 版，非完整用户图谱）:

    三种关系 (Zeus 指令 2026-08-10):
        similar_to     — 同类模式(指纹/pattern 相似) → 合并/聚类
        caused_by      — 因果(G 由 E 触发) → 错误溯源
        derived_from   — 派生(A 锚点由 E 经验提炼) → 经验→锚点链路

范围限定:
    - 只做 Atom 间关系，不做完整用户/实体图谱(那是 Melody 的事)
    - Experience Matrix 的轻量前置: 关系可持久化到 anchors.db

关系数据写入 anchors.db 的辅助表 or 独立 graph 表。
这里设计成独立 experience_graph 表(轻量, 不污染 anchors 主表)，
但保留与 anchors 的关联字段。
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import json

# 关系类型
REL_TYPES = ("similar_to", "caused_by", "derived_from")


@dataclass
class GraphEdge:
    """一条关系边。"""
    source_id: str          # 源节点(atom_id / anchor_id)
    target_id: str          # 目标节点
    relation: str           # similar_to | caused_by | derived_from
    weight: float = 1.0     # 关系强度(类似发生次数/置信度)
    reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExperienceGraph:
    """
    Lightweight Atom 关系图。

    add_edge          — 添加关系
    neighbors         — 节点的邻接关系
    similar_cluster   — 聚类相似节点(similar_to 连通分量)
    causal_chain      — 因果链(caused_by 反向追溯)
    derive_chain      — 派生链(derived_from)
    to_edges_for_db   — 导出边数据(供写入 anchors.db / ERGE)
    """

    def __init__(self):
        self._edges: List[GraphEdge] = []
        # 索引
        self._out: Dict[str, List[GraphEdge]] = {}   # source_id -> edges
        self._in: Dict[str, List[GraphEdge]] = {}    # target_id -> edges

    # -- 关系维护 -----------------------------------------------------------

    def add_edge(self, source_id: str, target_id: str, relation: str,
                 weight: float = 1.0, reason: Optional[str] = None) -> GraphEdge:
        """添加/更新一条关系边。"""
        if relation not in REL_TYPES:
            raise ValueError(f"relation 必须 ∈ {REL_TYPES}, got {relation}")
        edge = GraphEdge(source_id, target_id, relation, weight, reason)
        self._edges.append(edge)
        self._out.setdefault(source_id, []).append(edge)
        self._in.setdefault(target_id, []).append(edge)
        return edge

    def neighbors(self, node_id: str) -> List[Dict[str, Any]]:
        """节点的所有邻接关系(出+入)。"""
        result = []
        for e in self._out.get(node_id, []):
            result.append({"direction": "out", **e.to_dict()})
        for e in self._in.get(node_id, []):
            result.append({"direction": "in", **e.to_dict()})
        return result

    # -- 聚类: similar_to 连通分量 -----------------------------------------

    def similar_cluster(self, node_id: str) -> List[str]:
        """返回与 node_id 通过 similar_to 形成连通分量的所有节点(含自己)。"""
        visited = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for e in self._out.get(cur, []):
                if e.relation == "similar_to" and e.target_id not in visited:
                    stack.append(e.target_id)
            for e in self._in.get(cur, []):
                if e.relation == "similar_to" and e.source_id not in visited:
                    stack.append(e.source_id)
        return list(visited)

    # -- 因果链: caused_by --------------------------------------------------

    def causal_chain(self, node_id: str, max_depth: int = 10) -> List[Dict[str, Any]]:
        """沿 caused_by 反向追溯因果链(从后果 node_id 到根因)。

        边语义: source --caused_by--> target = source 是 target 的后果,target 是根因。
        因此 node 的 caused_by 是它的出边(target=根因)。
        """
        chain = []
        cur = node_id
        seen = set()
        for _ in range(max_depth):
            if cur in seen:
                break
            seen.add(cur)
            # cur 的出边中 caused_by 的 target 就是根因
            causes = [e for e in self._out.get(cur, []) if e.relation == "caused_by"]
            if not causes:
                break
            cause = max(causes, key=lambda e: e.weight)
            chain.append({"cause": cause.target_id, "effect": cur,
                          "weight": cause.weight, "reason": cause.reason})
            cur = cause.target_id
        return chain

    # -- 派生链: derived_from -------------------------------------------------

    def derive_chain(self, node_id: str) -> List[Dict[str, Any]]:
        """沿 derived_from 追溯派生来源(经验→锚点链路)。

        边语义: source --derived_from--> target = target 由 source 派生。
        因此 node 的 derived_from 是它的出边(target=来源)。
        """
        chain = []
        cur = node_id
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            derived = [e for e in self._out.get(cur, []) if e.relation == "derived_from"]
            if not derived:
                break
            d = max(derived, key=lambda e: e.weight)
            chain.append({"derived_from": d.target_id, "derived_into": cur,
                          "reason": d.reason})
            cur = d.target_id
        return chain

    # -- 自动关系推断(Lite) ---------------------------------------------------

    def auto_link_similar(self, atoms: List[Dict[str, Any]], threshold: float = 0.7) -> int:
        """
        按 pattern/lesson 相似度自动建立 similar_to 关系(Lite)。
        atoms: ExperienceAtom 的 to_dict() 列表。
        返回新建边数。
        """
        added = 0
        seen_pairs = set()
        for i in range(len(atoms)):
            for j in range(i + 1, len(atoms)):
                a, b = atoms[i], atoms[j]
                if a.get("pattern") and a.get("pattern") == b.get("pattern"):
                    pair = tuple(sorted([a["atom_id"], b["atom_id"]]))
                    if pair not in seen_pairs:
                        self.add_edge(a["atom_id"], b["atom_id"], "similar_to",
                                      weight=0.9, reason="same pattern auto-linked")
                        seen_pairs.add(pair)
                        added += 1
        return added

    # -- 数据导出(供写入 anchors.db) ----------------------------------------

    def to_edges_for_db(self) -> List[Dict[str, Any]]:
        """导出所有边为可写入 anchors.db 的字典列表。"""
        return [e.to_dict() for e in self._edges]

    def stats(self) -> Dict[str, Any]:
        """图统计。"""
        n_edges = len(self._edges)
        n_nodes_src = len(self._out)
        n_nodes_in = len(self._in)
        by_rel = {}
        for e in self._edges:
            by_rel[e.relation] = by_rel.get(e.relation, 0) + 1
        return {"edges": n_edges, "source_nodes": n_nodes_src,
                "target_nodes": n_nodes_in, "by_relation": by_rel}


def build_relation_graph(edges_data: List[Dict[str, Any]]) -> ExperienceGraph:
    """从边数据重建图(用于从 anchors.db 加载)。"""
    g = ExperienceGraph()
    for e in edges_data:
        g.add_edge(e["source_id"], e["target_id"], e["relation"],
                   weight=e.get("weight", 1.0), reason=e.get("reason"))
    return g
