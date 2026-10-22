"""曲绘。

官方资源路径是 ``{assets_base_url}/jacket/{song_id}.png``，
并明确提示「游戏资源的访问频率有限制，请勿频繁请求」。因此这里提供三种模式：

* ``url``（**默认，最轻量**）——机器人只把地址交给 OneBot 实现，自己不下载、不缓存。
  适合「输出要尽可能轻量」的取向：机器人侧零带宽、零磁盘、零延迟。
* ``base64``——下载后缓存到本地，再内联成 data URI 发出去。
  适合 OneBot 实现无法访问外网的场景；代价是消息体积膨胀约 1/3。
* ``off``——只发文字。

本地缓存用「临时文件 + 原子替换」，并对下载失败做**负缓存标记**，
避免每次查询都对同一张不存在的图重复发请求。
"""

from __future__ import annotations

import base64
import logging
import pathlib
from dataclasses import dataclass

from ..api.client import LxnsClient
from ..config import BotSettings
from ..errors import AssetUnavailableError

__all__ = ["JacketImage", "JacketProvider"]

logger = logging.getLogger(__name__)

#: 负缓存文件名后缀：标记「这个 song_id 的曲绘确实取不到」
_MISSING_SUFFIX = ".missing"


@dataclass(frozen=True)
class JacketImage:
    """一张曲绘。

    ``data`` 为 ``None`` 时表示**只拿地址**（``url`` 模式）；
    有 ``data`` 时 :attr:`data_uri` 可直接交给 OneBot 的图片消息段。
    """

    song_id: int
    url: str
    data: bytes | None = None

    @property
    def has_data(self) -> bool:
        return self.data is not None

    @property
    def size_bytes(self) -> int:
        return len(self.data) if self.data else 0

    @property
    def data_uri(self) -> str:
        """``data:image/png;base64,...``；没有数据时抛错（调用方应先判断）。"""
        if self.data is None:
            raise AssetUnavailableError("这张曲绘只有地址，没有下载数据")
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @property
    def filename(self) -> str:
        return f"jacket_{self.song_id}.png"


class JacketProvider:
    """按配置取曲绘。"""

    def __init__(self, settings: BotSettings, client: LxnsClient) -> None:
        self.settings = settings
        self.client = client
        self.cache_dir = pathlib.Path(settings.jacket_cache_dir)

    # ------------------------------------------------------------------ 对外
    async def get(self, song_id: int) -> JacketImage | None:
        """取曲绘。

        :returns: ``None`` 表示不发送图片（``off`` 模式）。取不到时返回带地址的
            :class:`JacketImage`（``url`` 模式）或 ``None``（``base64`` 模式下载失败）。
        """
        mode = self.settings.jacket_mode
        if mode == "off":
            return None

        url = self.settings.jacket_url(song_id)
        if mode == "url":
            # 最轻量：不下载、不落盘
            return JacketImage(song_id=song_id, url=url)

        data = await self._load_or_download(song_id, url)
        if data is None:
            return None
        return JacketImage(song_id=song_id, url=url, data=data)

    # ------------------------------------------------------------------ 缓存
    def cache_path(self, song_id: int) -> pathlib.Path:
        return self.cache_dir / f"{int(song_id)}.png"

    def _missing_marker(self, song_id: int) -> pathlib.Path:
        return self.cache_dir / f"{int(song_id)}{_MISSING_SUFFIX}"

    async def _load_or_download(self, song_id: int, url: str) -> bytes | None:
        path = self.cache_path(song_id)
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError as exc:  # 缓存坏了就重新下
                logger.warning("读取曲绘缓存失败 %s: %s", path, exc)

        if self._missing_marker(song_id).is_file():
            # 之前已经确认取不到，不再重试（保护官方资源接口）
            logger.debug("曲绘 %s 已标记为缺失，跳过下载", song_id)
            return None

        try:
            data = await self.client.download(url, max_bytes=self.settings.jacket_max_bytes)
        except Exception as exc:  # noqa: BLE001 - 曲绘失败不该影响文字回复
            logger.warning("下载曲绘失败 song_id=%s: %s", song_id, exc)
            self._write_missing_marker(song_id)
            return None

        self._write_cache(path, data)
        return data

    def _write_cache(self, path: pathlib.Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            tmp.replace(path)  # 原子替换
        except OSError as exc:
            logger.warning("写入曲绘缓存失败 %s: %s", path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass

    def _write_missing_marker(self, song_id: int) -> None:
        marker = self._missing_marker(song_id)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    def clear_missing_marker(self, song_id: int) -> None:
        """清掉负缓存（例如曲绘后来补档了）。"""
        try:
            self._missing_marker(song_id).unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
