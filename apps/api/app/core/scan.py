"""文件/字节内容安全扫描（M4 W4）：ClamAV 占位接口 + 安全默认启发式。

策略（安全默认拒绝，fail-closed）：
- CLAMAV_ENABLED=False（默认）：走启发式占位实现：
  * 压缩炸弹检测：zip 解压比 > ZIP_BOMB_RATIO（默认 100x）或解压总大小超限 → 拒绝。
  * 加密/高熵 payload 检测：无法识别为已知格式且香农熵 >= ENTROPY_THRESHOLD → 拒绝
    （加密数据通常高熵且无 magic bytes，按安全默认拒绝处理）。
- CLAMAV_ENABLED=True：预留真实 clamd 连接分支（本阶段 TODO + 安全默认拒绝——
  连不上 clamd 即视为不安全，避免绕过）。
- 正常文本/已知格式（pdf/png/jpeg/gzip/zip 合法文档等）均为 safe=True。

本模块仅依赖标准库，无需新增 pip 包。
"""
from __future__ import annotations

import io
import math
import zipfile
from collections import Counter
from dataclasses import dataclass

from app.core.config import get_settings

settings = get_settings()

# ── 启发式阈值 ──
ZIP_BOMB_RATIO = 100  # 解压比超过 100x 视为压缩炸弹
ZIP_BOMB_MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 解压总大小超过 100MB 视为炸弹
ENTROPY_THRESHOLD = 7.5  # 香农熵阈值（0-8），已知格式之外的高熵视为加密/不可信

# 已知文件格式的 magic 前缀：命中则按合法格式放行（zip 仍走压缩炸弹检测）
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"\xd0\xcf\x11\xe0", "ole2"),
]

_CLAMAV_HOST = "127.0.0.1"
_CLAMAV_PORT = 3310


@dataclass
class ScanResult:
    safe: bool
    reason: str


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _is_known_format(data: bytes) -> bool:
    return any(data.startswith(sig) for sig, _ in _MAGIC_SIGNATURES)


def _scan_zip(data: bytes) -> ScanResult | None:
    """若是 zip，则做压缩炸弹检测；返回 None 表示不是 zip。"""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return None
    try:
        total_uncompressed = 0
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                total_uncompressed += info.file_size
        if total_uncompressed > ZIP_BOMB_MAX_UNCOMPRESSED:
            return ScanResult(False, "zip_bomb_size")
        ratio = total_uncompressed / max(1, len(data))
        if ratio > ZIP_BOMB_RATIO:
            return ScanResult(False, "zip_bomb_ratio")
    except (zipfile.BadZipFile, OSError):
        # 损坏/无法读取的压缩包按不安全拒绝
        return ScanResult(False, "bad_archive")
    return ScanResult(True, "zip_ok")


def _scan_with_clamd(data: bytes) -> ScanResult:
    """预留真实 clamd 连接分支（本阶段 TODO）。

    安全默认拒绝：若无法连接/扫描失败，视为不安全，避免绕过扫描。
    """
    # TODO(M4 W4): 接入 clamd。示例：
    #   import clamd
    #   cd = clamd.ClamdNetworkSocket(_CLAMAV_HOST, _CLAMAV_PORT)
    #   res = cd.instream(io.BytesIO(data))
    #   if res.get("stream", ("", ""))[0] != "OK":
    #       return ScanResult(False, "clamav_threat")
    #   return ScanResult(True, "clamav_clean")
    # 当前未装 clamd 且未实现，按安全默认拒绝。
    try:
        import clamd  # noqa: F401 — 显式依赖，未安装即走拒绝
    except Exception:  # noqa: BLE001
        return ScanResult(False, "clamav_unavailable_deny")
    # 若装了但未真正实现，仍安全默认拒绝
    return ScanResult(False, "clamav_not_wired_deny")


def _scan_heuristic(data: bytes) -> ScanResult:
    # 1) 压缩炸弹检测（含合法文档 zip/docx）
    zip_res = _scan_zip(data)
    if zip_res is not None:
        return zip_res

    # 2) 已知格式放行（pdf/png/jpeg/gzip 等）
    if _is_known_format(data):
        return ScanResult(True, "known_format")

    # 3) 高熵且非已知格式 → 疑似加密/不可信 payload，安全默认拒绝
    if _entropy(data) >= ENTROPY_THRESHOLD:
        return ScanResult(False, "high_entropy_encrypted")

    return ScanResult(True, "clean")


async def scan_bytes(data: bytes) -> ScanResult:
    """扫描字节内容，返回是否安全。

    默认启发式（CLAMAV_ENABLED=False）。生产开启 ClamAV 后走 clamd 分支。
    配置项带默认值（getattr），即便 config.py 尚未合并新字段也能安全运行。
    """
    if getattr(settings, "CLAMAV_ENABLED", False):
        return _scan_with_clamd(data)
    return _scan_heuristic(data)
