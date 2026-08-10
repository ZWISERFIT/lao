"""
Experience Contract — LAO 2.7 P0-②
==================================

经验共享的安全契约。防止 Agent 经验跨域污染。

结构 (对齐 Zeus 指令 2026-08-10):
    {
      owner:             经验所有方(Agent id 或 user id)
      domain:            经验所属领域
      allowed_agents:    允许共享的 Agent
      forbidden_domains: 禁止被该经验影响的领域
      confidence:        经验置信度 0.0-1.0
      source:            经验来源
    }

关联 ERGE v2:
    - owner            → anchors.source (来源)
    - allowed_agents   → ERGE permissions 表
    - domain           → anchors.category
    - confidence       → anchors.confidence_score / trust_weight
    - 契约本质 = ERGE 的 permissions + tags 层

用途:
    - Experience Network 的安全基础(未来 P2-⑤)
    - 保证经验"正确的人/正确的域/正确的时机"被共享，不污染
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import hashlib
import json as _json
import os
import urllib.request
import urllib.error

# Ethan Attestation API 地址（P0-①，可经环境变量覆盖）
ETHAN_ATTEST_URL = os.environ.get(
    "ETHAN_ATTEST_URL", "http://localhost:17800/attest"
)


@dataclass
class ExperienceContract:
    """经验共享契约。"""
    owner: str                          # 经验所有方
    domain: str                         # 所属领域
    allowed_agents: List[str] = field(default_factory=list)    # 允许共享的Agent(空=仅owner)
    forbidden_domains: List[str] = field(default_factory=list) # 禁止影响的领域
    confidence: float = 0.5             # 0.0-1.0
    source: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # anchor_type 兼容(对齐 ERGE 三元组映射): fact/decision/cognitive
    anchor_type: str = "fact"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # -- 访问判定 -----------------------------------------------------------

    def can_share(self, agent_id: str) -> bool:
        """该经验能否共享给指定 Agent。"""
        # owner 本人总是可以
        if agent_id == self.owner:
            return True
        # 显式 allowed_agents 列表
        if self.allowed_agents and agent_id in self.allowed_agents:
            return True
        return False

    def can_apply(self, domain: str, agent_id: Optional[str] = None) -> bool:
        """该经验能否应用到指定领域(防跨域污染)。"""
        # 禁止域
        if domain in self.forbidden_domains:
            return False
        # Agent 权限
        if agent_id is not None and not self.can_share(agent_id):
            return False
        return True

    def validate(self) -> List[str]:
        """契约自检，返回违规项列表(空=合法)。"""
        issues = []
        if not self.owner:
            issues.append("owner 不能为空")
        if not self.domain:
            issues.append("domain 不能为空")
        if not (0.0 <= self.confidence <= 1.0):
            issues.append("confidence 必须在 0-1")
        if not self.can_share(self.owner):
            issues.append("owner 必须能访问自身经验")
        return issues


class ExperienceContractRegistry:
    """经验契约注册表：按 owner 管理契约，提供共享判定。"""

    def __init__(self, store_path: Optional[str] = None):
        self._contracts: Dict[str, ExperienceContract] = {}  # 按 owner+domain 键
        self._path = store_path
        if store_path:
            self._load()

    def register(self, contract: ExperienceContract) -> str:
        """注册契约，返回契约键。"""
        key = f"{contract.owner}:{contract.domain}"
        self._contracts[key] = contract
        if self._path:
            self._save()
        return key

    def get_for_owner_domain(self, owner: str, domain: str) -> Optional[ExperienceContract]:
        """取 owner+domain 的契约。"""
        return self._contracts.get(f"{owner}:{domain}")

    def can_agent_use(self, agent_id: str, owner: str, domain: str) -> bool:
        """Agent 能否使用某个 owner 的经验(未注册契约默认拒绝,安全优先)。"""
        c = self.get_for_owner_domain(owner, domain)
        if c is None:
            return False  # 无契约=不共享(安全锁定)
        return c.can_apply(domain, agent_id)

    def list_by_owner(self, owner: str) -> List[Dict[str, Any]]:
        """列出某 owner 的所有契约。"""
        return [c.to_dict() for k, c in self._contracts.items() if c.owner == owner]

    # -- L3 确权存证 (P0-②) ------------------------------------------------

    def attest_experience(self, owner: str, domain: str) -> Optional[str]:
        """对经验合约做 Ethan 存证，返回存证 ID 或 None。

        - 取 owner+domain 的契约；无契约返回 None
        - 对契约 to_dict() 计算 sha256
        - POST 到 Ethan /attest（P0-① 新增接口）
        - 返回 attestation_id；Ethan 不可达/失败返回 None
        """
        contract = self.get_for_owner_domain(owner, domain)
        if contract is None:
            return None
        payload = contract.to_dict()
        content_hash = hashlib.sha256(
            _json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        body = _json.dumps({
            "content_hash": f"sha256:{content_hash}",
            "content_type": "experience_contract",
            "owner": owner,
            "metadata": payload,
        }).encode("utf-8")
        req = urllib.request.Request(
            ETHAN_ATTEST_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = _json.loads(resp.read().decode("utf-8"))
                return data.get("attestation_id")
        except (urllib.error.URLError, OSError, _json.JSONDecodeError):
            return None

    # -- 持久化 -------------------------------------------------------------

    def _load(self) -> None:
        import os, json as _json
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    raw = _json.load(f)
                for k, d in raw.items():
                    self._contracts[k] = ExperienceContract(**d)
            except (_json.JSONDecodeError, OSError, TypeError):
                self._contracts = {}

    def _save(self) -> None:
        import os, json as _json
        if os.path.dirname(self._path):
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            _json.dump({k: c.to_dict() for k, c in self._contracts.items()},
                       f, ensure_ascii=False, indent=2)
