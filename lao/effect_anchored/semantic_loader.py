#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义库版本化加载器 · semantic_loader
217号施工令 · D2-1 · 2026-09-07

职责：
  1. 读取语义库 YAML 文件（177_*.yaml）
  2. 解析 meta.version / meta.status
  3. 校验版本已签章（拒绝草稿态 / 未登记版本）→ 拒绝未签章版本入库
  4. 写加载日志（semantic_load_log.jsonl，每次加载留痕）

铁律（承 177 号语义库签章纪律）：
  只有创始人签章的版本才允许被 LAO 事实库注入使用；
  DRAFT_UNSIGNED / PENDING_RULING 等草稿态一律拒绝并留痕升级。
"""
import os
import sys
import glob
import json
import hashlib
from datetime import datetime, timezone

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# ---- 已签章版本登记表（唯一权威口径，随签章件更新）---------------------
# 依据: 177_语义库_v1.0_签章版_20260907.md（创始人 2026-09-07 签章，从 v0.3 草稿升根）
SIGNED_VERSIONS = {
    "1.0": {
        "signed_by": "创始人",
        "signed_at": "2026-09-07",
        "signature_ref": "177_语义库_v1.0_签章版_20260907.md",
    },
}

# 草稿/未签章态：命中即拒绝入库
UNSIGNED_STATUS = {"DRAFT_UNSIGNED", "DRAFT", "PENDING_RULING", "UNSIGNED", ""}
# 已签章态标识
SIGNED_STATUS = {"SIGNED", "RATIFIED"}

# 默认语义库目录与日志落点（均可用环境变量覆盖）
DEFAULT_LIB_DIR = os.environ.get(
    "SEMANTIC_LIB_DIR", os.path.expanduser("~/share/ZWISERFIT_OS")
)
DEFAULT_GLOB = os.environ.get("SEMANTIC_LIB_GLOB", "177_*.yaml")
LOAD_LOG = os.environ.get(
    "SEMANTIC_LOAD_LOG",
    os.path.expanduser("~/.lao/semantic/semantic_load_log.jsonl"),
)


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_log(record):
    """追加一条加载事件到 JSONL 日志（fail-open，不因日志失败中断加载）。"""
    try:
        os.makedirs(os.path.dirname(LOAD_LOG), exist_ok=True)
        with open(LOAD_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[semantic_loader] 日志写入失败: %s\n" % e)


def validate_version(version, status):
    """校验版本是否已签章。返回 (ok: bool, reason: str)。"""
    v = (version or "").strip()
    st = (status or "").strip().upper()
    if not v:
        return False, "缺少 version 字段"
    if st in UNSIGNED_STATUS:
        return False, "草稿态未签章 (status=%s)" % (status or "<空>")
    if v not in SIGNED_VERSIONS:
        return False, "版本 %s 未在已签章登记表中" % v
    if st not in SIGNED_STATUS:
        return False, "status=%s 非已签章态 (期望 SIGNED/RATIFIED)" % status
    return True, "已签章版本 %s (%s)" % (v, SIGNED_VERSIONS[v]["signed_at"])


def load_semantic_file(path):
    """加载单个语义库文件并校验版本。返回结果 dict（并写日志）。"""
    fname = os.path.basename(path)
    result = {
        "ts": _now_iso(),
        "file": fname,
        "path": path,
        "version": None,
        "status": None,
        "loaded": False,
        "reason": None,
        "sha256": None,
    }
    if yaml is None:
        result["reason"] = "PyYAML 未安装，无法解析"
        _write_log(result)
        return result
    try:
        result["sha256"] = _sha256_file(path)
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        meta = (doc.get("meta") or {}) if isinstance(doc, dict) else {}
        version = meta.get("version")
        status = meta.get("status")
        result["version"] = version
        result["status"] = status
        ok, reason = validate_version(version, status)
        result["loaded"] = ok
        result["reason"] = reason
        if ok:
            result["signature"] = SIGNED_VERSIONS.get(str(version).strip())
    except Exception as e:  # noqa: BLE001
        result["reason"] = "解析失败: %s" % e
    _write_log(result)
    return result


def load_all(lib_dir=None, pattern=None):
    """扫描目录内所有语义库文件，逐个加载校验。返回结果列表。"""
    lib_dir = lib_dir or DEFAULT_LIB_DIR
    pattern = pattern or DEFAULT_GLOB
    files = sorted(glob.glob(os.path.join(lib_dir, pattern)))
    return [load_semantic_file(p) for p in files]


def _main(argv):
    lib_dir = argv[1] if len(argv) > 1 else DEFAULT_LIB_DIR
    results = load_all(lib_dir)
    ok_n = sum(1 for r in results if r["loaded"])
    rej_n = len(results) - ok_n
    print("语义库加载: 目录=%s 文件=%d 通过=%d 拒绝=%d 日志=%s"
          % (lib_dir, len(results), ok_n, rej_n, LOAD_LOG))
    for r in results:
        flag = "OK " if r["loaded"] else "REJ"
        print("  [%s] %s  version=%s status=%s  -> %s"
              % (flag, r["file"], r["version"], r["status"], r["reason"]))
    # 若存在被拒绝的已发现文件，返回非零，供上游探针感知
    return 0 if rej_n == 0 else 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
