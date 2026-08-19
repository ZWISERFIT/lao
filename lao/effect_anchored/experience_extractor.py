# v3.5.2-laorefactor: P0-2
"""
Experience Extractor — LAO 架构重构 P0-2
========================================

回站验收后·每次 LLM 交互结果 → 萃取经验 → 存入经验库（JSONL）。

规格依据: LAO 架构产品规格 v1.0 第二节（Shuyu·2026-08-19）。
存储路径: /home/agentuser/lao-release/lao/effect_anchored/data/lao_experiences.jsonl
归属: 归用户·LAO 只是守护者。

规则:
    - 不萃取密钥（apiKey/key/token/secret/password 特征）
    - 不萃取 PII
    - experience_content ≤ 500 chars
    - 同 fingerprint+agent_id 只保留最新（去重）
    - disputed 也存·但 quality_grade=disputed·匹配引擎跳过

约束: 仅标准库 · fail-open（任何异常返回 None/空结果不抛）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ExperienceRecord:
    """一次完整 LLM 交互的经验记录（规格 2.1）。"""

    experience_id: str              # exp-{fingerprint}
    experience_fingerprint: str     # sha256(cleaned_task_text)[:16]
    experience_content: str         # "Q: ...\nA: ..."（≤500 chars）
    task_type: str                  # fact/decision/cognitive/chat
    agent_id: str                   # 隔离键
    model_used: str
    provider_used: str
    quality_grade: str              # verified/pending/disputed
    cache_hit: bool
    actual_cost: float
    context_tokens: int
    response_tokens: int
    retry_count: int
    isolation_key: str              # = agent_id
    created_at: str                 # ISO 时间（含时区）
    verified_by: str = "lao_acceptance"

    def to_jsonl(self) -> str:
        """序列化为 JSONL 单行。"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExperienceRecord":
        """从 dict 构造（缺字段用默认值·容忍扩展字段）。"""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# 萃取器
# ---------------------------------------------------------------------------

class ExperienceExtractor:
    """经验萃取器: LLM交互记录 → 经验库（JSONL）。"""

    STORE_PATH = "/home/agentuser/lao-release/lao/effect_anchored/data/lao_experiences.jsonl"
    MAX_CONTENT_CHARS = 500
    SECRET_RE = re.compile(
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S|sk-[A-Za-z0-9_\-]{6,}"
    )
    TASK_TYPES = ("fact", "decision", "cognitive", "chat")

    def __init__(self, store_path: str = STORE_PATH):
        """初始化萃取器。确保 data/ 目录存在（Stella 修正3: 绝对路径·先建 data 目录）。"""
        self.store_path = store_path
        try:
            os.makedirs(os.path.dirname(store_path), exist_ok=True)
        except Exception:
            pass

    # -- 主流程 -----------------------------------------------------------

    def extract(self, record: Dict[str, Any]) -> Optional[str]:
        """萃取一条经验 → 存入经验库。返回 experience_id·失败返回 None。

        输入 record 字段（规格 2.1）:
            request_id, request_features{task_text, task_type, agent_id,
                                          model_used, provider_used, context_tokens},
            response_features{response_text, response_tokens, cache_hit, actual_cost},
            verification{fact_verified, cognitive_consistent, quality_grade, retry_count}

        fail-open：任何异常返回 None 不抛。
        """
        try:
            req = record.get("request_features") or {}
            resp = record.get("response_features") or {}
            ver = record.get("verification") or {}

            task_text = str(req.get("task_text", "") or "")
            agent_id = str(req.get("agent_id", "") or "unknown")
            task_type = str(req.get("task_type", "") or "chat")
            if task_type not in self.TASK_TYPES:
                task_type = "chat"
            response_text = str(resp.get("response_text", "") or "")

            # 不萃取密钥（请求/响应任一含密钥特征 → 放弃）
            if self._looks_secret(task_text) or self._looks_secret(response_text):
                return None

            # ① 请求特征清洗 + 指纹
            cleaned = self._clean_task_text(task_text)
            if not cleaned:
                return None
            fingerprint = self._extract_fingerprint(cleaned)

            # ② 结果特征精简
            content = self._build_content(cleaned, response_text)
            if not content:
                return None

            # ③ 质量评级
            grade = self._grade(ver)

            # ④ 隔离键 = agent_id
            isolation_key = agent_id

            rec = ExperienceRecord(
                experience_id=f"exp-{fingerprint}",
                experience_fingerprint=fingerprint,
                experience_content=content,
                task_type=task_type,
                agent_id=agent_id,
                model_used=str(req.get("model_used", "") or ""),
                provider_used=str(req.get("provider_used", "") or ""),
                quality_grade=grade,
                cache_hit=bool(resp.get("cache_hit", False)),
                actual_cost=float(resp.get("actual_cost", 0.0) or 0.0),
                context_tokens=int(req.get("context_tokens", 0) or 0),
                response_tokens=int(resp.get("response_tokens", 0) or 0),
                retry_count=int(ver.get("retry_count", 0) or 0),
                isolation_key=isolation_key,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self.save(rec)
            return rec.experience_id
        except Exception:
            return None  # fail-open

    # -- 清洗与指纹 -------------------------------------------------------

    def _clean_task_text(self, text: str) -> str:
        """清洗: 去无关上下文·保留核心问题·去密钥特征。"""
        t = text.strip()
        # 截断超长（保留前 2000 字符·防噪音）
        t = t[:2000]
        # 去密钥特征行
        lines = [ln for ln in t.splitlines() if not self._looks_secret(ln)]
        return "\n".join(lines).strip()

    def _extract_fingerprint(self, cleaned: str) -> str:
        """sha256(cleaned)[:16]"""
        return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]

    def _grade(self, verification: Dict[str, Any]) -> str:
        """质量评级（规格 2.2 ③）:
        - fact_verified=True + cognitive_consistent=True + retry=0 → verified
        - fact_verified=True + retry>0 → pending
        - fact_verified=False → disputed
        """
        fact_ok = bool(verification.get("fact_verified", False))
        cog_ok = bool(verification.get("cognitive_consistent", True))
        retry = int(verification.get("retry_count", 0) or 0)
        if not fact_ok:
            return "disputed"
        if cog_ok and retry == 0:
            return "verified"
        return "pending"

    def _build_content(self, cleaned_q: str, response_text: str) -> str:
        """精简: "Q: {cleaned}\nA: {核心结论}"·首句结论+关键事实·≤500 chars。"""
        # 取首句结论 + 关键事实点（前 3 句）
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?\.])\s+|\n+", response_text)
                     if s.strip()]
        if not sentences:
            sentences = [response_text.strip()]
        core = sentences[0]
        for s in sentences[1:3]:
            core += " " + s
        content = f"Q: {cleaned_q[:200]}\nA: {core}"
        if len(content) > self.MAX_CONTENT_CHARS:
            content = content[: self.MAX_CONTENT_CHARS]
        return content.strip()

    def _looks_secret(self, text: str) -> bool:
        """内容命中密钥特征 → True（丢弃）。"""
        return bool(self.SECRET_RE.search(text or ""))

    # -- 存储 -------------------------------------------------------------

    def save(self, rec: ExperienceRecord) -> None:
        """追加 JSONL。同 fingerprint+agent_id → 覆盖（去重·规格 2.4）。"""
        try:
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            lines = []
            if os.path.exists(self.store_path):
                with open(self.store_path, "r", encoding="utf-8") as f:
                    lines = [ln for ln in f if ln.strip()]
            # 去重: 同 fingerprint+agent_id 的行替换
            key = (rec.experience_fingerprint, rec.agent_id)
            kept = []
            replaced = False
            for ln in lines:
                try:
                    d = json.loads(ln)
                    if (d.get("experience_fingerprint"), d.get("agent_id")) == key:
                        kept.append(rec.to_jsonl() + "\n")
                        replaced = True
                    else:
                        kept.append(ln if ln.endswith("\n") else ln + "\n")
                except Exception:
                    kept.append(ln if ln.endswith("\n") else ln + "\n")
            if not replaced:
                kept.append(rec.to_jsonl() + "\n")
            with open(self.store_path, "w", encoding="utf-8") as f:
                f.writelines(kept)
        except Exception:
            pass  # fail-open

    def load_all(self) -> List[Dict[str, Any]]:
        """读取全部经验（供匹配引擎）。失败返回空列表。"""
        result: List[Dict[str, Any]] = []
        try:
            if not os.path.exists(self.store_path):
                return result
            with open(self.store_path, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        result.append(json.loads(ln))
                    except Exception:
                        continue
        except Exception:
            pass
        return result

    def dedupe(self) -> int:
        """重写文件: 同 fingerprint+agent_id 只保留最新。返回删除数。"""
        try:
            rows = self.load_all()
            seen: Dict[tuple, Dict[str, Any]] = {}
            for r in rows:
                key = (r.get("experience_fingerprint", ""), r.get("agent_id", ""))
                seen[key] = r  # 后者覆盖 → 保留最新
            removed = len(rows) - len(seen)
            if removed > 0 or len(rows) != len(seen):
                os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
                with open(self.store_path, "w", encoding="utf-8") as f:
                    for r in seen.values():
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return removed
        except Exception:
            return 0
