# -*- coding: utf-8 -*-
"""敏感字段加密：Webhook secret / API Token 等平台凭证落库前 Fernet 加密。

密钥管理策略：
  - 主密钥落在 backed/data/.fernet_key（权限 600），首运行时自动生成。
  - 环境变量 FERNET_KEY 可覆盖（CI/Docker 注入场景）。
  - 密钥轮换：改密钥后未加密的旧字段在读取时解密失败会返回空串，
    配合业务层的"配置失效 → 提示用户重配"逻辑实现平滑轮换。
"""

import logging
import os
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent


def _key_file() -> Path:
    """与 db.data_root() 同源：桌面版跟随 HUB_DATA_DIR，避免密钥落在安装目录。"""
    from service.publishing.db import data_root
    d = data_root()
    d.mkdir(parents=True, exist_ok=True)
    return d / ".fernet_key"


_KEY_FILE = _key_file()

log = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _restrict_key_file(path: Path) -> None:
    """收紧密钥文件权限：POSIX 用 chmod 600；Windows 用 icacls 只授权当前用户。

    F-09：Windows 的 DACL 默认继承父目录（Users 组可读），chmod 是空操作。
    icacls /inheritance:r 清掉继承 ACE，/grant:r 再只给当前用户。
    收紧失败只告警不抛——密钥已落盘，这里炸掉等于服务起不来，比权限松更糟。
    """
    try:
        path.chmod(0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME", "").strip()
    if not user:
        log.warning("USERNAME 未设置，无法为密钥文件收紧 ACL: %s", path)
        return
    try:
        r = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            log.warning("密钥文件 ACL 收紧失败 (rc=%s): %s", r.returncode,
                        (r.stderr or b"").decode("utf-8", "replace").strip()[-200:])
    except (OSError, subprocess.SubprocessError) as e:
        # SubprocessError 含 TimeoutExpired（icacls 卡死时不是 OSError 子类，
        # 漏掉它会让密钥加载直接炸——收紧失败必须只降级）
        log.warning("icacls 无法完成，密钥文件 ACL 未收紧: %s", e)


def _load_key() -> bytes:
    """读取或生成 Fernet 密钥。优先级：环境变量 > 文件 > 自动生成。"""
    env_key = os.environ.get("FERNET_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")

    if _KEY_FILE.exists():
        key = _KEY_FILE.read_bytes()
        # 存量密钥也顺手收紧：修复前生成的密钥文件在 Windows 上是宽松 ACL
        _restrict_key_file(_KEY_FILE)
        return key

    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    _restrict_key_file(_KEY_FILE)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """加密明文。空串原样返回（避免存一堆空密文干扰判断）。"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """解密密文。失败返回空串（密钥轮换 / 数据损坏时平滑降级）。"""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
