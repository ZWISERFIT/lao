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
    # ── v3.5 L3 确权流程(飞轮引擎: 授权→DID 签名→上链交易) ──────────────
    authorization_status: str = "PENDING"   # PENDING / AUTHORIZED / REVOKED
    did_signature: Optional[str] = None     # DID 签名(所有方对经验哈希的签名)
    transaction_ready: bool = False         # 是否准备好上链交易
    royalty_config: Dict[str, Any] = field(default_factory=dict)  # 版税配置

    # ── v3.5 确权流程方法 ────────────────────────────────────────────────

    def authorize(self, did_signature: str) -> "ExperienceContract":
        """确权：登记 DID 签名并置为已授权(交易就绪)。

        Args:
            did_signature: 所有方 DID 对经验内容哈希的签名字符串(非空)。
        """
        if not did_signature or not str(did_signature).strip():
            raise ValueError("DID 签名不能为空")
        self.did_signature = str(did_signature).strip()
        self.authorization_status = "AUTHORIZED"
        self.transaction_ready = True
        return self

    def revoke(self) -> "ExperienceContract":
        """撤销授权：不可再上链交易(签名保留作审计痕迹)。"""
        self.authorization_status = "REVOKED"
        self.transaction_ready = False
        return self

    @property
    def is_transaction_ready(self) -> bool:
        """确权完备性：已授权 + 有 DID 签名 + 交易标记。"""
        return (
            self.authorization_status == "AUTHORIZED"
            and bool(self.did_signature)
            and self.transaction_ready
        )

    def content_hash(self) -> str:
        """经验内容哈希(DID 签名的签名对象)。"""
        payload = {
            "owner": self.owner,
            "domain": self.domain,
            "confidence": self.confidence,
            "anchor_type": self.anchor_type,
            "allowed_agents": self.allowed_agents,
            "forbidden_domains": self.forbidden_domains,
        }
        return hashlib.sha256(
            _json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

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
        # v3.5 确权流程校验
        if self.authorization_status not in ("PENDING", "AUTHORIZED", "REVOKED"):
            issues.append("authorization_status 必须为 PENDING/AUTHORIZED/REVOKED")
        if self.authorization_status == "AUTHORIZED" and not self.did_signature:
            issues.append("AUTHORIZED 状态必须携带 did_signature")
        if self.transaction_ready and not self.is_transaction_ready:
            issues.append("transaction_ready 需要 AUTHORIZED 状态 + did_signature")
        shares = [float(v) for v in self.royalty_config.values()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if shares and sum(shares) > 1.0 + 1e-9:
            issues.append("royalty_config 分成比例之和不能超过 1.0")
        return issues


class ExperienceContractRegistry:
    """经验契约注册表：按 owner 管理契约，提供共享判定 + L3 确权存证。"""

    def __init__(self, store_path: Optional[str] = None,
                 consent_gate: Optional[Any] = None):
        self._contracts: Dict[str, ExperienceContract] = {}  # 按 owner+domain 键
        self._path = store_path
        # P0-2: Consent Gate（确权时授权检查·非安装时）
        if consent_gate is not None:
            self._consent_gate = consent_gate
        else:
            from lao.effect_anchored.consent_gate import ConsentGate
            consent_path = None
            if self._path:
                consent_path = os.path.join(
                    os.path.dirname(self._path) or ".",
                    os.path.splitext(os.path.basename(self._path))[0] + "_consent.json",
                )
            self._consent_gate = ConsentGate(store_path=consent_path)
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
        - **先经 Consent Gate 授权检查（P0-2）：未授权返回 None（确权时触发，非安装时）**
        - 对契约 to_dict() 计算 sha256
        - POST 到 Ethan /attest（P0-① 新增接口）
        - 返回 attestation_id；Ethan 不可达/失败返回 None
        """
        contract = self.get_for_owner_domain(owner, domain)
        if contract is None:
            return None
        # P0-2: Consent Gate 授权检查（确权时·非安装时）
        if not self._consent_gate.is_granted(owner, domain):
            return None  # 未授权不确权（安全：需用户先授权共享哈希元数据）
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
