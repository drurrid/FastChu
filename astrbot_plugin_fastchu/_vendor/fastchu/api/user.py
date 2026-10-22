"""落雪**个人**接口客户端（需要 OAuth Bearer 令牌）。

与公共接口的 :class:`fastchu.api.client.LxnsClient` 分开，原因：

* 需要把用户令牌放在 ``Authorization`` 头里；
* 401/403 要区分成「重新授权」和「权限不足」，不能混成普通 4xx；
* 个人数据只是增强信息，调用方需要有超时与降级空间。

``/user/chunithm/player/trophies`` 返回玩家**已获得**的收藏品列表
（该接口未写进官方文档，但落雪前端与官方 SDK 都这样调用）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

import httpx
from pydantic import BaseModel, ConfigDict

from ..errors import (
    LxnsError,
    LxnsNotFoundError,
    OAuthReauthRequired,
    OAuthScopeMissing,
)
from .client import (
    RETRYABLE_STATUS,
    decode_json,
    raise_for_status,
    unwrap_envelope,
)

__all__ = [
    "PersonalPlayer",
    "PersonalScore",
    "OwnedTrophy",
    "LxnsUserClient",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class OwnedTrophy(_Model):
    """已获得的称号/收藏品。"""

    id: int
    name: str = ""
    color: str = "normal"
    level: int | None = None
    completed: bool | None = None

    @property
    def owned(self) -> bool:
        """``completed`` 未提供时，出现在「已获得列表」里即视为已获得。"""
        return self.completed is not False


class PersonalPlayer(_Model):
    """``GET /user/chunithm/player``。"""

    name: str = ""
    level: int | None = None
    rating: float | None = None
    friend_code: int | str | None = None
    trophy: OwnedTrophy | None = None
    upload_time: str | None = None


class PersonalScore(_Model):
    """``GET /user/chunithm/player/scores`` 里的一条成绩。"""

    id: int
    song_name: str = ""
    level: str = ""
    #: 0=BASIC … 4=ULTIMA，5=WORLD'S END
    level_index: int | None = None
    score: int | None = None
    rating: float | None = None
    over_power: float | None = None
    clear: str = ""
    full_combo: str = ""
    full_chain: str = ""
    rank: str = ""


def _list_of(payload: Any, key: str) -> Sequence[Any]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        for item in payload.values():
            if isinstance(item, list):
                return item
        return []
    if isinstance(payload, list):
        return payload
    return []


class LxnsUserClient:
    """带用户令牌的落雪接口客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 8.0,
        retries: int = 1,
        backoff: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "LxnsUserClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ 请求
    async def _get(self, path: str, token: str) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_exc = LxnsError(f"个人数据网络错误: {exc}")
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))
                    continue
                raise last_exc from exc

            status = response.status_code
            payload = decode_json(response)

            if status in RETRYABLE_STATUS and attempt < self.retries:
                await asyncio.sleep(self.backoff * (2**attempt))
                continue
            if status == 401:
                raise OAuthReauthRequired("落雪个人接口拒绝了访问令牌", status=status)
            if status == 403:
                raise OAuthScopeMissing("落雪个人接口权限不足", status=status)
            if status == 404:
                raise LxnsNotFoundError("个人数据不存在", status=status)
            if status >= 400:
                raise_for_status(status, None, None)

            return unwrap_envelope(payload, status)

        raise last_exc or LxnsError("个人数据请求失败")

    # ------------------------------------------------------------------ 接口
    async def get_player(self, token: str) -> PersonalPlayer:
        data = await self._get("/user/chunithm/player", token)
        return PersonalPlayer.model_validate(data or {})

    async def get_scores(self, token: str) -> list[PersonalScore]:
        data = await self._get("/user/chunithm/player/scores", token)
        out: list[PersonalScore] = []
        for item in _list_of(data, "scores"):
            try:
                out.append(PersonalScore.model_validate(item))
            except Exception:  # noqa: BLE001 - 单条坏数据不该让整批失败
                continue
        return out

    async def get_owned_trophies(self, token: str) -> list[OwnedTrophy]:
        """玩家已获得的称号（一次请求拿全量，避免逐个查）。"""
        data = await self._get("/user/chunithm/player/trophies", token)
        out: list[OwnedTrophy] = []
        for item in _list_of(data, "trophies"):
            try:
                trophy = OwnedTrophy.model_validate(item)
            except Exception:  # noqa: BLE001
                continue
            if trophy.owned:
                out.append(trophy)
        return out
