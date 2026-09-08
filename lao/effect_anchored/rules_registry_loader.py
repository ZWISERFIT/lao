#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
治理规则注册表加载器 · rules_registry_loader
P0-4 施工令 · 2026-09-08 · chat-1

职责：
  1. 加载 221号《规则注册表 v0.1（静态版）》= 00_rules_registry.yaml
  2. 签章准入：以 221号签章确认件为**外部凭证**
  3. 基线锚定：首次加载落 sha256 基线，后续比对；变更即拒载
  4. 七门校验：调 verify_rules_registry.py，非 0 即拒载
  5. 写加载日志（rules_registry_load_log.jsonl），每次加载留痕

签章判定说明（重要工程决策）：
  v0.1 是 **baseline_immutable** 基线件（见 00_rules_registry_v0.2.yaml
  meta.supersession.baseline_immutable: true）。其签章效力由外部签章件
  《221号_规则注册表v0.1_签章确认_20260907.md》承载，**不由 YAML 内
  meta.status 字段承载** —— 该字段保留 chat-3 构建时原值 DRAFT_UNSIGNED，
  签章后按不可变纪律故意不改。
  故本加载器**不能**照抄 semantic_loader 的 meta.status 门（那会误拒
  已签章的 v0.1），改以「外部签章凭证 + sha256 基线锚定」双重判定。

铁律（承 221号签章件 + 剖析报告§五.2 "signed ≠ enforced"）：
  - 只接已签章的 v0.1；v0.2 状态 PENDING_FOUNDER_COUNTERSIGN，一律拒载
  - 签章只是准入前提，还必须过七门校验才允许生效
  - 任一门不过 → loaded=False 并留痕，不静默放行
"""
import os
import sys
import json
import glob
import hashlib
import subprocess
from datetime import datetime, timezone

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# ---- sha256 语义约定 --------------------------------------------------
# SIGNED_VERSIONS 中的 sha256 = 签章前原始内容哈希（pre-signature hash）
#   用途：版本溯源，确认“这份文件签章时内容是什么”
# baseline.json 中的 sha256 = 签章后文件哈希（post-signature hash）
#   用途：运行时锚定，确认“磁盘上的文件是否被篡改”
# endpoint /v1/rules/status sha256 = post-signature hash（与 baseline 一致）
# 两者共存是设计意图，不是 bug。
# -------------------------------------------------------------------------------

# ---- 唯一权威口径（随签章件更新）------------------------------------------
# 依据: 221号_规则注册表v0.1_签章确认_20260907.md（创始人 2026-09-07 签章）
SIGNED_VERSIONS = {
    "0.1": {
        "signed_by": "创始人",
        "signed_at": "2026-09-07",
        "signature_ref": "221号_规则注册表v0.1_签章确认_20260907.md",
        "archive_no": "221",
        "baseline_immutable": True,
    },
    "0.2": {
        "signed_by": "创始人",
        "signed_at": "2026-09-08",
        "sha256": "a81369dc119c606c63971479bf71150567436acf7a29389a09b494fc8891db16",
    },
}

# 显式拒载版本（未获创始人副署）
REJECTED_VERSIONS = {
}

# ---- 路径配置（均可用环境变量覆盖）----------------------------------------
REGISTRY_DIR = os.environ.get(
    "LAO_RULES_REGISTRY_DIR", os.path.expanduser("~/share/ZWISERFIT_OS")
)
REGISTRY_FILE = os.environ.get("LAO_RULES_REGISTRY_FILE", "00_rules_registry_v0.2.yaml")
# 签章件用 ASCII glob 匹配，避免中文文件名在跨平台管道中被破坏
SIGNATURE_GLOB = os.environ.get("LAO_RULES_SIGNATURE_GLOB", "221*.md")
VERIFIER = os.environ.get("LAO_RULES_VERIFIER", "verify_rules_registry.py")

BASELINE_PATH = os.environ.get(
    "LAO_RULES_BASELINE",
    os.path.expanduser("~/.lao/rules-registry/baseline.json"),
)
LOAD_LOG = os.environ.get(
    "LAO_RULES_LOAD_LOG",
    os.path.expanduser("~/.lao/rules-registry/rules_registry_load_log.jsonl"),
)

# 七门校验超时（秒）——防止启动被校验器卡死
VERIFY_TIMEOUT = int(os.environ.get("LAO_RULES_VERIFY_TIMEOUT", "60"))


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_log(record):
    """追加一条加载事件到 JSONL（fail-open，不因日志失败中断加载判定）。"""
    try:
        os.makedirs(os.path.dirname(LOAD_LOG), exist_ok=True)
        with open(LOAD_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _read_baseline():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _write_baseline(data):
    try:
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_signature_doc(registry_dir=None):
    """定位 221号签章确认件。返回路径或 None。"""
    d = registry_dir or REGISTRY_DIR
    hits = sorted(glob.glob(os.path.join(d, SIGNATURE_GLOB)))
    return hits[0] if hits else None


def check_signature(version, registry_dir=None):
    """签章准入判定。返回 (ok, reason, signature_meta)。"""
    v = str(version).strip() if version is not None else ""
    if v in REJECTED_VERSIONS:
        return False, "版本显式拒载: v%s —— %s" % (v, REJECTED_VERSIONS[v]), None
    if v not in SIGNED_VERSIONS:
        return False, "版本 v%s 不在已签章登记表内，拒载" % v, None
    sig_doc = find_signature_doc(registry_dir)
    if not sig_doc:
        return False, "未找到签章凭证件（%s），拒载" % SIGNATURE_GLOB, None
    meta = dict(SIGNED_VERSIONS[v])
    meta["signature_doc_path"] = sig_doc
    meta["signature_doc_sha256"] = _sha256_file(sig_doc)
    return True, "已签章版本 v%s（凭证: %s）" % (v, os.path.basename(sig_doc)), meta


def check_baseline_anchor(sha256, version):
    """基线锚定判定：首次落基线，后续比对。返回 (ok, reason, anchor_state)。"""
    key = "v%s" % version
    base = _read_baseline()
    rec = base.get(key)
    if rec is None:
        base[key] = {
            "sha256": sha256,
            "anchored_at": _now_iso(),
            "note": "首次加载落基线；后续加载比对，不一致即拒载",
        }
        saved = _write_baseline(base)
        return True, "首次加载已落 sha256 基线%s" % ("" if saved else "（基线落盘失败，仅本次放行）"), "anchored"
    if rec.get("sha256") == sha256:
        return True, "sha256 与基线一致（锚定于 %s）" % rec.get("anchored_at"), "matched"
    return False, (
        "sha256 与基线不一致 —— 签章后文件被改动，拒载。"
        "基线=%s 当前=%s（锚定于 %s）"
        % (str(rec.get("sha256"))[:16], sha256[:16], rec.get("anchored_at"))
    ), "mismatch"


def run_seven_gates(registry_dir=None):
    """跑七门校验器。返回 (ok, reason, detail)。"""
    d = registry_dir or REGISTRY_DIR
    verifier = os.path.join(d, VERIFIER)
    if not os.path.exists(verifier):
        return False, "七门校验器缺失: %s" % verifier, None
    try:
        proc = subprocess.run(
            [sys.executable, VERIFIER],
            cwd=d,
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "七门校验超时（>%ds），拒载" % VERIFY_TIMEOUT, None
    except Exception as e:  # noqa: BLE001
        return False, "七门校验执行失败: %s" % e, None
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-3:] if out.strip() else []
    detail = {"exit_code": proc.returncode, "tail": tail}
    if proc.returncode != 0:
        return False, "七门校验未全通过（exit=%d），拒载" % proc.returncode, detail
    return True, "七门校验全通过（exit=0）", detail


def load_registry(registry_dir=None, registry_file=None):
    """加载治理规则注册表。四门顺序：可解析 → 签章 → 基线锚定 → 七门校验。

    返回 dict，含 loaded / reason / version / rule_count / gates 等字段，
    并写一条加载日志。任一门不过即 loaded=False（不静默放行）。
    """
    d = registry_dir or REGISTRY_DIR
    fname = registry_file or REGISTRY_FILE
    path = os.path.join(d, fname)

    result = {
        "ts": _now_iso(),
        "file": fname,
        "path": path,
        "version": None,
        "meta_status": None,
        "loaded": False,
        "reason": None,
        "sha256": None,
        "rule_count": None,
        "gates": {},
    }

    if yaml is None:
        result["reason"] = "PyYAML 未安装，无法解析"
        _write_log(result)
        return result

    if not os.path.exists(path):
        result["reason"] = "注册表文件不存在: %s" % path
        _write_log(result)
        return result

    # 门① 可解析
    try:
        result["sha256"] = _sha256_file(path)
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        result["reason"] = "解析失败: %s" % e
        result["gates"]["parse"] = False
        _write_log(result)
        return result
    if not isinstance(doc, dict):
        result["reason"] = "注册表顶层不是映射结构"
        result["gates"]["parse"] = False
        _write_log(result)
        return result
    result["gates"]["parse"] = True

    meta = doc.get("meta") or {}
    result["version"] = meta.get("version")
    result["meta_status"] = meta.get("status")
    rules = doc.get("rules") or []
    result["rule_count"] = len(rules)

    # 门② 签章准入（外部凭证，非 meta.status）
    sig_ok, sig_reason, sig_meta = check_signature(result["version"], d)
    result["gates"]["signature"] = sig_ok
    result["signature_reason"] = sig_reason
    if sig_meta:
        result["signature"] = sig_meta
    if not sig_ok:
        result["reason"] = sig_reason
        _write_log(result)
        return result

    # 门③ 基线锚定
    anc_ok, anc_reason, anc_state = check_baseline_anchor(
        result["sha256"], result["version"]
    )
    result["gates"]["baseline_anchor"] = anc_ok
    result["baseline_reason"] = anc_reason
    result["baseline_state"] = anc_state
    if not anc_ok:
        result["reason"] = anc_reason
        _write_log(result)
        return result

    # 门④ 七门校验（signed ≠ enforced）
    ver_ok, ver_reason, ver_detail = run_seven_gates(d)
    result["gates"]["seven_gates"] = ver_ok
    result["seven_gates_reason"] = ver_reason
    if ver_detail:
        result["seven_gates_detail"] = ver_detail
    if not ver_ok:
        result["reason"] = ver_reason
        _write_log(result)
        return result

    # 四门全过 → 落数据
    result["loaded"] = True
    result["reason"] = "四门全过：%s；%s；%s" % (sig_reason, anc_reason, ver_reason)
    result["enums"] = list((doc.get("enums") or {}).keys())
    result["carrier_count"] = len(doc.get("carriers") or [])
    result["metric_count"] = len(doc.get("metrics") or [])
    by_lifecycle = {}
    by_binding = {}
    for r in rules:
        k = r.get("lifecycle_state")
        by_lifecycle[k] = by_lifecycle.get(k, 0) + 1
        b = r.get("binding_mode")
        by_binding[b] = by_binding.get(b, 0) + 1
    result["by_lifecycle_state"] = by_lifecycle
    result["by_binding_mode"] = by_binding
    # 生产执行路径上生效的规则（enforced）——供路由层按需引用
    result["enforced_keys"] = [
        r.get("registry_key") for r in rules if r.get("lifecycle_state") == "enforced"
    ]
    _write_log(result)
    # 完整规则体不进日志（体积），仅随返回值给调用方
    result["_rules"] = rules
    return result


def registry_status(loaded_result):
    """把 load_registry 结果压成适合状态接口暴露的摘要（不含规则全文）。"""
    if not loaded_result:
        return {"loaded": False, "reason": "未加载"}
    keys = (
        "file", "version", "meta_status", "loaded", "reason", "sha256",
        "rule_count", "carrier_count", "metric_count", "gates",
        "by_lifecycle_state", "by_binding_mode", "baseline_state",
    )
    out = {k: loaded_result.get(k) for k in keys if k in loaded_result}
    sig = loaded_result.get("signature") or {}
    if sig:
        out["signature"] = {
            "signed_by": sig.get("signed_by"),
            "signed_at": sig.get("signed_at"),
            "signature_ref": sig.get("signature_ref"),
            "archive_no": sig.get("archive_no"),
        }
    out["enforced_count"] = len(loaded_result.get("enforced_keys") or [])
    return out


def _main(argv):
    d = argv[1] if len(argv) > 1 else REGISTRY_DIR
    res = load_registry(registry_dir=d)
    flag = "OK " if res["loaded"] else "REJ"
    print("规则注册表加载: 目录=%s 文件=%s" % (d, res["file"]))
    print("  [%s] version=%s meta_status=%s rules=%s"
          % (flag, res["version"], res["meta_status"], res["rule_count"]))
    print("  门: %s" % res.get("gates"))
    print("  结论: %s" % res.get("reason"))
    print("  日志: %s" % LOAD_LOG)
    print("  基线: %s" % BASELINE_PATH)
    return 0 if res["loaded"] else 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
