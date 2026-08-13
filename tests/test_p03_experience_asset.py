"""Phase2 P0-3 测试: ExperienceAsset MVP(开发者贡献→可验证资产·Web5入口)。

创始人 v3.4 P0-3 提前: 外部开发者贡献必须生成 ExperienceAsset, 不只是 issue/PR。
Asset = Asset ID + Creator DID + Problem + Solution + Verification% + Attestation。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.experience_asset import ExperienceAssetRegistry


def test_create_asset_with_external_trust_event_hash():
    """外部 TrustEvent 溯源 hash → 资产可验证(外部 provenance)。"""
    reg = ExperienceAssetRegistry()
    a = reg.create(creator_did="did:zwf:dev-alice", problem="Gateway Failure",
                   solution="port_probe+http+synthetic", domain="gateway",
                   verification_pct=98, trust_event_hash="sha256:abc123def456")
    assert a.asset_id == "EXP-00001"
    assert a.attestation == "sha256:abc123def456"
    assert reg.verify(a.asset_id) is True


def test_create_asset_with_local_fingerprint():
    """无外部 hash → 本地指纹可验证。"""
    reg = ExperienceAssetRegistry()
    a = reg.create(creator_did="did:zwf:dev-bob", problem="Context Compaction",
                   solution="bootstrap prevention")
    # 本地指纹 = 16 位十六进制
    assert len(a.attestation) == 16
    assert all(c in "0123456789abcdef" for c in a.attestation)
    assert reg.verify(a.asset_id) is True


def test_verify_rejects_incomplete_asset():
    """缺 problem/solution → verify=False(不信任不完整自报)。"""
    reg = ExperienceAssetRegistry()
    a = reg.create(creator_did="did:zwf:bad", problem="", solution="",
                   verification_pct=100)
    assert reg.verify(a.asset_id) is False


def test_trust_event_ownership():
    """资产上链 → TrustEvent(OwnershipEvent)。"""
    reg = ExperienceAssetRegistry()
    a = reg.create(creator_did="did:zwf:dev", problem="P", solution="S",
                   trust_event_hash="sha256:xyz")
    te = reg.trust_event(a)
    assert te["event"] == "AssetAttested"
    assert te["subtype"] == "OwnershipEvent"
    assert te["asset_id"] == a.asset_id
    assert te["creator_did"] == "did:zwf:dev"


def test_unique_asset_ids_and_count():
    """多资产 → 唯一 ID + count。"""
    reg = ExperienceAssetRegistry()
    for i in range(3):
        reg.create(creator_did=f"did:zwf:dev{i}", problem=f"P{i}", solution=f"S{i}")
    assert reg.count() == 3
    ids = [a.asset_id for a in reg.all()]
    assert len(set(ids)) == 3  # 唯一
