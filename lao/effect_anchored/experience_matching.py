# v3.5.2-laorefactor: P0
"""
Experience Matching Prototype — LAO 2.7 P2-⑤
=============================================

给 Melody 的检索接口（Experience Matching Prototype）：
    retrieve_verified_experience()

作用:
    从已验证经验库检索最相关的经验组合，返回给上层(Melody/Agent)消费。
    只提供检索能力，不实现匹配/适配逻辑 —— 那是 Melody 的域(LAO Kernel 边界切割 2026-08-10)。

对齐 LAO Kernel 边界:
    LAO Kernel (本文件):  Storage + Verification + Retrieval
    Melody (未来, 不实现): Identity + Preference + Matching + Personal Adaptation

数据源 (三层融合):
    1. CognitiveAnchorStore      — 三层认知锚点(Fact/Decision/Cognitive) + 决策查询
    2. ERGE anchors.db           — 已验证经验(verified/permanent), 含 facts/规则
    3. ExperienceGraph           — 经验关系(similar_to/caused_by/derived_from)

核心接口:
    retrieve_verified_experience(agent, query, limit, context) -> Dict
        - 从 ERGE + 认知锚点检索已验证经验
        - 按状态优先级(permanent > verified) + 信任度排序
        - 附带契约合规检查(Agent 是否被授权使用该经验)
        - 返回结构化结果(经验 + 来源 + 可信度 + 关系)

--- P0 扩展 (2026-08-19 LAO 架构重构) ---
    match(request_features) -> MatchResult
        - 主动经验匹配引擎(产品规格 v1.0 第三节)
        - 精确指纹 → 语义 tf-idf → 隔离键检查 → 三段式决策(Zeus LAO-C3)
        - 保留 retrieve_verified_experience 不动(向后兼容·Stella 修正2)
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
import json
import re
import sqlite3


@dataclass
class ExperienceHit:
    """一次检索命中(已验证经验)。"""
    anchor_id: str
    content: str                # 规则/原理内容
    anchor_type: str            # fact | decision | cognitive
    status: str                 # permanent | verified
    source: str                 # 来源
    trust: float                # 信任度 0-1
    relation_context: List[str] = field(default_factory=list)  # 相关经验关系

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MatchResult:
    """主动匹配结果（产品规格 v1.0 3.1·LAO-C3 双阈值灰区）。"""

    confidence: float                                  # 0.0-1.0
    matched_experience: Optional[Dict[str, Any]] = None  # None=未匹配
    action: str = "proceed_to_llm"   # direct_return / flag_for_comparison / proceed_to_llm
    isolation_violation: bool = False                  # 隔离键违规
    matched_via: str = "none"                          # exact / semantic / none

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（供上层消费/日志）。"""
        return asdict(self)


class ExperienceMatcher:
    """
    已验证经验检索器(接口层, 不实现匹配逻辑)。

    retrieve_verified_experience() 是给 Melody 的稳定 API。
    """

    def __init__(
        self,
        erge_db: str = "/home/agentuser/.openclaw/workspace/data/ZWISERFIT/cognitive-os/anchors.db",
        anchor_store: Optional[Any] = None,
        graph: Any = None,
    ):
        self.erge_db = erge_db
        self.anchor_store = anchor_store  # CognitiveAnchorStore 实例(可选)
        self.graph = graph                # ExperienceGraph 实例(可选)

    # -- 核心 API -----------------------------------------------------------

    def retrieve_verified_experience(
        self,
        agent: str = "suzanne",
        query: str = "",
        limit: int = 5,
        anchor_type: Optional[str] = None,
        category: Optional[str] = None,
        permissioned_only: bool = True,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        检索已验证经验(permanent/verified)给上层消费。

        Args:
            agent: 请求方(用于权限校验)
            query: 检索文本
            limit: 返回条数上限
            anchor_type: 过滤 fact/decision/cognitive
            category: 过滤 category
            permissioned_only: 是否只看该 agent 有权限的经验
            context: Melody 传入的上下文(本接口不消费, 仅透传标注)

        Returns:
            {"hits": [...], "count": n, "engine": "LAO-ERGE", "permissioned": bool}
        """
        # 1) 从 ERGE 检索已验证锚点
        rows = self._query_erge(
            agent=agent, query=query, limit=limit,
            anchor_type=anchor_type, category=category,
            permissioned=permissioned_only,
        )
        hits = []
        for r in rows:
            rel = self._relation_context(r["id"])
            hits.append(ExperienceHit(
                anchor_id=r["id"], content=self._content_of(r),
                anchor_type=self._rget(r, "anchor_type", None) or self._infer_type(r["status"]),
                status=r["status"], source=self._rget(r, "source_type", "agent_derived"),
                trust=self._rget(r, "trust_weight", 0.5), relation_context=rel,
            ))
        # 2) 若配了 CognitiveAnchorStore, 补充决策查询命中
        if self.anchor_store and query:
            human_id = (context or {}).get("human_id")
            for a in self.anchor_store.query(query):
                # Same-Agent-Different-Human: 当请求带 human_id 时,
                # LAO 检索只返回该 human 有权看到的契约经验(Verification/权限职责)。
                # 差异来自 Storage 层 contract 数据; 这里不做偏好推断(那是 Melody 域)。
                if human_id:
                    owner = a.get("value", {}).get("human") if isinstance(a.get("value"), dict) else None
                    if owner and owner != human_id:
                        continue
                if all(h.anchor_id != a["anchor_id"] for h in hits):
                    hits.append(ExperienceHit(
                        anchor_id=a["anchor_id"],
                        content=str(a.get("value", {}).get("rule") or a.get("value", {})),
                        anchor_type=a.get("anchor_type", "decision"),
                        status="verified", source=a.get("source", "anchor_store"),
                        trust=a.get("trust_weight", 0.8),
                    ))
        # 3) 排序: permanent > verified, trust 降序
        hits.sort(key=lambda h: (h.status == "permanent", h.trust), reverse=True)
        hits = hits[:limit]

        return {
            "hits": [h.to_dict() for h in hits],
            "count": len(hits),
            "engine": "LAO-ERGE-v2",
            "permissioned": permissioned_only,
            "context_passthrough": bool(context),
        }

    # -- 内部: ERGE 查询 -----------------------------------------------------

    def _query_erge(self, agent, query, limit, anchor_type, category, permissioned):
        conn = sqlite3.connect(self.erge_db)
        conn.row_factory = sqlite3.Row
        try:
            where = ["a.status IN ('permanent','verified')"]
            params = []
            # 权限过滤
            if permissioned:
                where.append("EXISTS (SELECT 1 FROM permissions p WHERE p.anchor_id=a.id AND p.agent_id=?)")
                params.append(agent)
            if anchor_type:
                where.append("a.anchor_type = ?"); params.append(anchor_type)
            if category:
                where.append("a.category = ?"); params.append(category)
            # query: 匹配 rule 或 tag 或 id 子串
            if query:
                where.append("(a.rule LIKE ? OR a.id LIKE ? OR a.rationale LIKE ?)")
                like = f"%{query}%"
                params += [like, like, like]
            sql = f"""SELECT a.* FROM anchors a
                      WHERE {' AND '.join(where)}
                      ORDER BY a.status='permanent' DESC, a.trust_weight DESC
                      LIMIT ?"""
            params.append(limit * 3)  # 多取供后续过滤
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _relation_context(self, anchor_id):
        """从 ExperienceGraph 提取锚点的关系上下文(similar/caused/derived)。"""
        if not self.graph:
            return []
        rel = []
        for n in self.graph.neighbors(anchor_id):
            rel.append(f"{n['relation']}->{n.get('target_id') or n.get('source_id')}")
        return rel[:3]


    @staticmethod
    def _rget(row, key, default=None):
        try:
            v = row[key]
            return v if v is not None else default
        except (KeyError, IndexError, TypeError):
            return default

    def _content_of(self, row):
        try:
            return row["rule"] or row["rationale"] or row["id"]
        except (KeyError, IndexError):
            return row["id"]

    @staticmethod
    def _infer_type(status):
        return "cognitive" if status == "permanent" else "decision"

    # ------------------------------------------------------------------
    # P0 主动匹配引擎 (2026-08-19 · 产品规格 v1.0 第三节 · Zeus LAO-C3)
    # ------------------------------------------------------------------

    EXPERIENCE_STORE = (
        "/home/agentuser/lao-release/lao/effect_anchored/data/"
        "lao_experiences.jsonl"
    )
    HIGH_CONFIDENCE = 0.85   # 直返上阈值 (LAO-C3)
    GRAY_ZONE = 0.50         # 灰区下限 (0.5-0.85 → flag_for_comparison)

    def match(self, request_features: Dict[str, Any]) -> MatchResult:
        """主动经验匹配（规格 3.2·LAO-C3 双阈值灰区）。

        Args:
            request_features: 含 task_text, agent_id, task_type。

        流程:
            ① 清洗 task_text + fingerprint(sha256[:16]) — 与萃取一致
            ② 精确匹配: 经验库 fingerprint == query → verified=1.0/pending=0.6/disputed跳过
            ③ 语义匹配: 同 agent+task_type 的 verified 经验 tf-idf cosine
            ④ 隔离键检查: matched.agent_id != request.agent_id → isolation_violation
            ⑤ 三段式: ≥0.85 direct_return · 0.5-0.85 flag_for_comparison · <0.5 proceed_to_llm

        fail-open: 任何异常返回 proceed_to_llm 不抛。
        """
        try:
            task_text = str(request_features.get("task_text") or "").strip()
            agent_id = str(request_features.get("agent_id") or "").strip()
            task_type = str(request_features.get("task_type") or "chat").strip()
            if not task_text:
                return MatchResult(confidence=0.0)

            # ① 指纹(与萃取一致: 清洗+sha256[:16])
            import hashlib
            cleaned = task_text[:2000]
            fp = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]

            # ② 精确匹配
            for exp in self._load_experience_store():
                if exp.get("experience_fingerprint") != fp:
                    continue
                grade = exp.get("quality_grade", "disputed")
                if grade == "disputed":
                    continue  # disputed 不参与匹配
                if exp.get("agent_id") != agent_id:
                    return MatchResult(
                        confidence=1.0 if grade == "verified" else 0.6,
                        matched_experience=exp,
                        action="proceed_to_llm",
                        isolation_violation=True,
                        matched_via="exact",
                    )
                conf = 1.0 if grade == "verified" else 0.6
                return MatchResult(
                    confidence=conf,
                    matched_experience=exp,
                    action=self._decide(conf),
                    isolation_violation=False,
                    matched_via="exact",
                )

            # ③ 语义匹配(同 agent+task_type 的 verified 经验)
            candidates = [
                e for e in self._load_experience_store()
                if e.get("agent_id") == agent_id
                and e.get("task_type") == task_type
                and e.get("quality_grade") == "verified"
            ]
            if candidates:
                texts = [str(e.get("experience_content", "")) for e in candidates]
                sims = self._tfidf_cosine(cleaned, texts)
                best = max(sims) if sims else 0.0
                if best >= self.GRAY_ZONE:
                    best_exp = candidates[sims.index(best)]
                    return MatchResult(
                        confidence=round(best, 4),
                        matched_experience=best_exp,
                        action=self._decide(best),
                        isolation_violation=False,
                        matched_via="semantic",
                    )

            # ⑤ 无匹配 → 交 LLM
            return MatchResult(confidence=0.0)
        except Exception:
            return MatchResult(confidence=0.0)  # fail-open

    def _decide(self, confidence: float) -> str:
        """三段式决策 (LAO-C3 双阈值灰区):
        ≥0.85 → direct_return · 0.5-0.85 → flag_for_comparison · <0.5 → proceed_to_llm
        """
        if confidence >= self.HIGH_CONFIDENCE:
            return "direct_return"
        if confidence >= self.GRAY_ZONE:
            return "flag_for_comparison"
        return "proceed_to_llm"

    def _load_experience_store(self) -> List[Dict[str, Any]]:
        """读经验库(绝对路径·P0-2 建)。不存在返回 []。fail-open。"""
        import os
        try:
            if not os.path.exists(self.EXPERIENCE_STORE):
                return []
            rows = []
            with open(self.EXPERIENCE_STORE, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        continue
            return rows
        except Exception:
            return []

    def _tfidf_cosine(self, query: str, texts: List[str]) -> List[float]:
        """词频-逆文档频率 cosine 相似度（纯标准库·规格 3.2 ③）。

        中文按单字+双字切分(无外部分词)·英文按空格。返回每个 text 的相似度。
        """
        try:
            def tokenize(s: str):
                s = s.lower()
                tokens = []
                # 英文/数字词
                for w in re.findall(r"[a-z0-9_]{2,}", s):
                    tokens.append(w)
                # 中文: 单字 + 相邻双字
                cjk = re.findall(r"[\u4e00-\u9fff]", s)
                tokens.extend(cjk)
                if len(cjk) >= 2:
                    pairs = ["".join(cjk[i:i+2]) for i in range(len(cjk)-1)]
                    tokens.extend(pairs)
                return tokens

            import math
            from collections import Counter

            q_tokens = tokenize(query)
            if not q_tokens:
                return [0.0] * len(texts)
            q_tf = Counter(q_tokens)
            corpus = [tokenize(t) for t in texts]
            # df: 含该词的文档数
            df: Dict[str, int] = {}
            for doc in corpus:
                for tok in set(doc):
                    df[tok] = df.get(tok, 0) + 1
            n_docs = max(len(corpus), 1)
            # 查询 tf-idf 向量
            q_vec: Dict[str, float] = {}
            for tok, cnt in q_tf.items():
                idf = math.log((n_docs + 1) / (df.get(tok, 0) + 1)) + 1.0
                q_vec[tok] = cnt * idf
            q_norm = math.sqrt(sum(v*v for v in q_vec.values())) or 1.0

            sims = []
            for doc in corpus:
                d_tf = Counter(doc)
                d_vec = {}
                for tok, cnt in d_tf.items():
                    idf = math.log((n_docs + 1) / (df.get(tok, 0) + 1)) + 1.0
                    d_vec[tok] = cnt * idf
                d_norm = math.sqrt(sum(v*v for v in d_vec.values())) or 1.0
                dot = sum(q_vec.get(tok, 0.0) * v for tok, v in d_vec.items())
                sims.append(dot / (q_norm * d_norm))
            return sims
        except Exception:
            return [0.0] * len(texts)
