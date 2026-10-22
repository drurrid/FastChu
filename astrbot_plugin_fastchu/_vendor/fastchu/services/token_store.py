"""落雪授权记录的本地持久化。

文件里含访问令牌与刷新令牌，敏感度等同于登录凭证，因此：

* 默认落在 ``data/auth/oauth_tokens.json``，该目录已在 ``.gitignore`` 里；
* 写入用「同目录临时文件 + ``os.replace``」，断电不会留下半截 JSON；
* POSIX 下尽量把权限设为 600（Windows 上为 best-effort）；
* 日志只记录路径与错误类型，**绝不记录令牌内容**。

存储结构::

    {
      "version": 1,
      "updated_at": 1700000000.0,
      "users": {
        "123456789": {
          "access_token": "...",
          "refresh_token": "...",
          "expires_at": 1700000900.0,
          ...
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

__all__ = ["StoredAuthorization", "TokenStore"]

logger = logging.getLogger(__name__)


@dataclass
class StoredAuthorization:
    """一个用户（QQ）的落雪授权。"""

    actor_id: str
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    scope: str = ""
    #: 访问令牌到期的 Unix 时间戳
    expires_at: float = 0.0
    #: 刷新令牌大致到期时间（落雪文档：30 天；仅用于状态展示）
    refresh_expires_at: float = 0.0
    player_name: str = ""
    friend_code: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def needs_refresh(self, now: float | None = None, *, skew: float = 60.0) -> bool:
        """访问令牌是否即将/已经过期。"""
        return (self.expires_at - skew) <= (time.time() if now is None else now)

    @property
    def access_expired(self) -> bool:
        return self.expires_at <= time.time()

    @property
    def refresh_expired(self) -> bool:
        return bool(self.refresh_expires_at) and self.refresh_expires_at <= time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, actor_id: str, payload: Any) -> "StoredAuthorization | None":
        if not isinstance(payload, dict):
            return None
        access = str(payload.get("access_token") or "")
        refresh = str(payload.get("refresh_token") or "")
        if not access:
            return None
        try:
            expires_at = float(payload.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            expires_at = 0.0
        try:
            refresh_expires_at = float(payload.get("refresh_expires_at") or 0.0)
        except (TypeError, ValueError):
            refresh_expires_at = 0.0
        try:
            created_at = float(payload.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        try:
            updated_at = float(payload.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        friend = payload.get("friend_code")
        return cls(
            actor_id=str(actor_id),
            access_token=access,
            refresh_token=refresh,
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=str(payload.get("scope") or ""),
            expires_at=expires_at,
            refresh_expires_at=refresh_expires_at,
            player_name=str(payload.get("player_name") or ""),
            friend_code=str(friend) if friend not in (None, "") else "",
            created_at=created_at,
            updated_at=updated_at,
        )


class TokenStore:
    """线程安全的 JSON 授权存储。

    实例会在第一次访问时读盘；之后以内存为准。授权写入频率很低，
    这样既避免频繁 IO，也不会出现「读一半文件」的问题。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._users: dict[str, StoredAuthorization] | None = None

    # ------------------------------------------------------------------ 读
    def _ensure_loaded(self) -> dict[str, StoredAuthorization]:
        if self._users is not None:
            return self._users
        users: dict[str, StoredAuthorization] = {}
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("授权文件读取失败，按空记录处理（%s）：%s", self.path, exc)
                payload = {}
            raw_users = payload.get("users") if isinstance(payload, dict) else None
            if isinstance(raw_users, dict):
                for actor_id, item in raw_users.items():
                    try:
                        record = StoredAuthorization.from_dict(str(actor_id), item)
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "授权记录字段损坏，已跳过 %s：%s", actor_id, exc
                        )
                        continue
                    if record is not None:
                        users[str(actor_id)] = record
        self._users = users
        return users

    def get(self, actor_id: str) -> StoredAuthorization | None:
        with self._lock:
            return self._ensure_loaded().get(str(actor_id))

    def all(self) -> Iterator[StoredAuthorization]:
        with self._lock:
            return iter(list(self._ensure_loaded().values()))

    def __len__(self) -> int:
        with self._lock:
            return len(self._ensure_loaded())

    def reload(self) -> None:
        """丢弃内存缓存，下次访问重新读盘（测试或外部修改时用）。"""
        with self._lock:
            self._users = None

    # ------------------------------------------------------------------ 写
    def put(self, record: StoredAuthorization) -> None:
        with self._lock:
            users = self._ensure_loaded()
            record.updated_at = time.time()
            users[str(record.actor_id)] = record
            self._write(users)

    def delete(self, actor_id: str) -> bool:
        with self._lock:
            users = self._ensure_loaded()
            existed = users.pop(str(actor_id), None) is not None
            if existed:
                self._write(users)
            return existed

    def _write(self, users: dict[str, StoredAuthorization]) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "users": {
                actor_id: record.to_dict()
                for actor_id, record in sorted(users.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".oauth_tokens-", suffix=".tmp", dir=self.path.parent
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # pragma: no cover - Windows 权限语义不同
                pass
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
