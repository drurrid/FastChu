"""落雪查分器（LXNS）**公共接口**客户端。

只做查歌，所以这里**没有任何鉴权**：

* 曲目列表 ``GET /api/v0/chunithm/song/list``
* 单曲信息 ``GET /api/v0/chunithm/song/{song_id}``
* 曲目别名 ``GET /api/v0/chunithm/alias/list``
* 收藏品列表 ``GET /api/v0/chunithm/{collection_type}/list``
* 游戏资源 ``GET https://assets2.lxns.net/chunithm/jacket/{song_id}.png``

对齐官方文档 <https://maimai.lxns.net/docs/api/chunithm>：接口统一返回
``{"success": bool, "code": int, "message": str|None, "data": ...}`` 信封，
错误码为 400 / 404 / 429 / 500 等。
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import TracebackType
from typing import Any, Mapping, Sequence

import httpx

from ..errors import LxnsError, LxnsNotFoundError, LxnsRateLimitError, LxnsServerError
from ..models import Alias, Song, SongListBundle, Trophy

__all__ = [
    "LxnsClient",
    "DEFAULT_BASE_URL",
    "RETRYABLE_STATUS",
    "decode_json",
    "message_of",
    "code_of",
    "unwrap_envelope",
    "raise_for_status",
]

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://maimai.lxns.net/api/v0"

#: 这些状态码值得重试：限流 + 服务端临时故障
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# --------------------------------------------------------------------------- #
# 信封与错误映射（资源下载、CLI 也会复用，所以放在模块级）
# --------------------------------------------------------------------------- #


def raise_for_status(status: int, message: str | None, code: int | None) -> None:
    """把 HTTP 状态码映射成有语义的异常。"""
    detail = message or httpx.codes.get_reason_phrase(status)
    if status == 404:
        raise LxnsNotFoundError(f"资源不存在: {detail}", status=status, code=code)
    if status == 429:
        raise LxnsRateLimitError(f"触发限流: {detail}", status=status, code=code)
    if status >= 500:
        raise LxnsServerError(f"服务端错误: {detail}", status=status, code=code)
    raise LxnsError(f"请求失败({status}): {detail}", status=status, code=code)


def decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        text = (response.text or "").strip()
        raise LxnsError(
            f"响应不是合法 JSON (status={response.status_code}): {text[:200]}",
            status=response.status_code,
        ) from None


def message_of(payload: Any) -> str | None:
    if isinstance(payload, dict):
        message = payload.get("message")
        return str(message) if message else None
    return None


def code_of(payload: Any) -> int | None:
    if isinstance(payload, dict) and isinstance(payload.get("code"), int):
        return payload["code"]
    return None


def unwrap_envelope(payload: Any, status: int) -> Any:
    """拆 ``{success, code, message, data}`` 信封。

    部分公共接口可能直接返回裸数据，这里两种都兼容。
    """
    if isinstance(payload, dict) and "success" in payload and "data" in payload:
        if not payload.get("success", False):
            raise_for_status(
                int(payload.get("code") or status),
                payload.get("message"),
                payload.get("code"),
            )
        return payload["data"]
    return payload


class LxnsClient:
    """异步公共接口客户端。

    用法::

        async with LxnsClient() as client:
            bundle = await client.get_song_list(notes=True)
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 15.0,
        retries: int = 2,
        backoff: float = 0.5,
        min_interval: float = 0.15,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        self.min_interval = max(0.0, min_interval)

        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    # ------------------------------------------------------------------ 生命周期
    async def __aenter__(self) -> "LxnsClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ 请求
    async def _throttle(self) -> None:
        """保证两次请求之间至少间隔 ``min_interval`` 秒。

        官方明确提示游戏资源访问频率有限制，所以节流是默认行为。
        """
        if self.min_interval <= 0:
            return
        async with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def request(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        method: str = "GET",
        json_body: Any | None = None,
    ) -> Any:
        """发一次请求并返回信封里的 ``data``。

        对 429 / 5xx / 网络错误做指数退避重试；4xx 业务错误立刻抛出。
        """
        url = (
            path
            if path.startswith(("http://", "https://"))
            else f"{self.base_url}/{path.lstrip('/')}"
        )
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._throttle()
            try:
                response = await self._client.request(
                    method, url, params=clean_params or None, json=json_body
                )
            except httpx.TransportError as exc:  # 连接/超时/DNS
                last_exc = LxnsError(f"网络错误: {exc}")
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))
                    continue
                raise last_exc from exc

            status = response.status_code
            payload = decode_json(response)

            if status in RETRYABLE_STATUS and attempt < self.retries:
                delay = self._retry_after(response) or self.backoff * (2**attempt)
                logger.warning(
                    "LXNS %s %s -> %s，%.2fs 后重试(%s/%s)",
                    method,
                    url,
                    status,
                    delay,
                    attempt + 1,
                    self.retries,
                )
                await asyncio.sleep(delay)
                continue

            if status >= 400:
                raise_for_status(status, message_of(payload), code_of(payload))

            return unwrap_envelope(payload, status)

        # 理论不可达：循环内要么 return 要么 raise
        raise last_exc or LxnsError("请求失败且未获得响应")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, min(30.0, float(raw)))
        except ValueError:
            return None

    # ------------------------------------------------------------------ 资源下载
    async def download(
        self, url: str, *, max_bytes: int | None = None, headers: dict[str, str] | None = None
    ) -> bytes:
        """下载二进制资源（曲绘等）。

        与 :meth:`request` 分开，因为响应不是 JSON、也不套信封。
        超过 ``max_bytes`` 时放弃，避免异常大的文件把内存和消息体积撑爆。
        """
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._throttle()
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_exc = LxnsError(f"资源下载网络错误: {exc}")
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))
                    continue
                raise last_exc from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self.retries:
                await asyncio.sleep(self.backoff * (2**attempt))
                continue
            if response.status_code >= 400:
                raise_for_status(response.status_code, None, None)

            data = response.content
            if max_bytes is not None and len(data) > max_bytes:
                raise LxnsError(
                    f"资源超过大小上限: {len(data)} > {max_bytes} ({url})",
                    status=response.status_code,
                )
            return data

        raise last_exc or LxnsError("资源下载失败且未获得响应")

    # ------------------------------------------------------------------ 曲目接口
    async def get_song_list(
        self, *, version: int | None = None, notes: bool = True
    ) -> SongListBundle:
        """曲目列表（含分类与版本表）。

        ``notes=True`` 才返回物量——物量查询需要它，代价是响应更大
        （实测全量约 2.5 MB），属于一次性开销，之后走本地缓存。
        """
        data = await self.request(
            "/chunithm/song/list",
            params={"version": version, "notes": "true" if notes else "false"},
        )
        return SongListBundle.model_validate(data or {})

    async def get_song(self, song_id: int, *, version: int | None = None) -> Song:
        """单曲信息（公共接口）。"""
        data = await self.request(f"/chunithm/song/{int(song_id)}", params={"version": version})
        return Song.model_validate(data or {})

    async def get_alias_list(self) -> list[Alias]:
        """曲目别名列表（公共接口）。"""
        data = await self.request("/chunithm/alias/list")
        raw: Sequence[object]
        if isinstance(data, dict):
            raw = data.get("aliases") or []
        else:
            raw = data or []
        return [Alias.model_validate(item) for item in raw]

    async def get_trophy_list(self, *, version: int | None = None) -> list[Trophy]:
        """称号列表（公共接口）。

        一个响应约 3 MB、5000+ 条，包含每个称号的颜色、``description``
        与**结构化获得条件**（``required``），是称号分类的数据基础。
        """
        raw = await self.get_collection_list("trophy", version=version)
        return [Trophy.model_validate(item) for item in raw]

    async def get_collection_list(
        self, collection_type: str, *, version: int | None = None
    ) -> list[dict]:
        """收藏品列表：``trophy`` / ``character`` / ``plate`` / ``icon``（公共接口）。"""
        if collection_type not in {"trophy", "character", "plate", "icon"}:
            raise ValueError(f"不支持的收藏品类型: {collection_type}")
        data = await self.request(f"/chunithm/{collection_type}/list", params={"version": version})
        if isinstance(data, dict):
            # 响应键名与类型对应（trophies / characters / plates / icons）
            for value in data.values():
                if isinstance(value, list):
                    return list(value)
            return []
        return list(data or [])
