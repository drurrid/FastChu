"""曲库加载器：磁盘缓存 + 过期回退。

曲库（``/chunithm/song/list`` 与 ``/chunithm/alias/list``）是**公共接口，不需要任何密钥**，
一次抓取可以服务很久，所以整体策略是「以本地缓存为主、联网为辅」：

* 缓存文件新鲜（TTL 内）→ 直接读盘，**查询零网络开销**；
* 过期或缺文件 → 在线拉取并写回；
* 在线拉取失败但本地有文件、且 ``allow_stale_cache`` → 用过期缓存继续；
* 缓存里没有物量、但配置要求 ``fetch_notes`` → 视为过期并重新抓取
  （否则物量查询会静默地一无所获）。
"""

from __future__ import annotations

import json
import logging
import pathlib
import time

from ..api.client import LxnsClient
from ..config import BotSettings
from ..errors import ChunithmBotError
from ..models import Alias, Trophy
from .alias_overlay import load_alias_overlay, merge_aliases
from .catalog import SongCatalog, load_catalog_from_files
from .titles import load_trophies_from_file

__all__ = ["CatalogLoader"]

logger = logging.getLogger(__name__)


class CatalogLoader:
    """带磁盘缓存的曲库提供者。

    曲库与称号各自独立缓存：称号拉取失败不该导致查歌不可用，
    反之亦然，所以两者的失败是分开兜底的。
    """

    def __init__(self, settings: BotSettings, client: LxnsClient) -> None:
        self.settings = settings
        self.client = client
        self._cached: SongCatalog | None = None
        self._trophies: list[Trophy] | None = None

    async def get(self, *, force_refresh: bool = False) -> SongCatalog:
        if self._cached is not None and not force_refresh:
            return self._cached

        path = self.settings.song_cache_path
        alias_path = self.settings.alias_cache_path

        if not force_refresh and self._is_fresh(path):
            try:
                catalog = self._load_from_disk(path, alias_path)
            except (ChunithmBotError, ValueError) as exc:
                logger.warning("曲库缓存损坏，改为在线拉取: %s", exc)
            else:
                if not self._needs_notes(catalog):
                    logger.debug("曲库命中缓存: %s", catalog.summary())
                    self._cached = catalog
                    return catalog
                logger.info("曲库缓存缺少物量数据，重新抓取")

        try:
            self._cached = await self._fetch()
        except ChunithmBotError:
            if self.settings.allow_stale_cache and path.is_file():
                logger.warning("曲库拉取失败，回退到本地缓存: %s", path)
                self._cached = self._load_from_disk(path, alias_path)
                return self._cached
            raise
        return self._cached

    def _load_from_disk(self, path: pathlib.Path, alias_path: pathlib.Path) -> SongCatalog:
        return load_catalog_from_files(
            path, alias_path, self.settings.alias_overlay_paths
        )

    # ------------------------------------------------------------------ 缓存判定
    def _is_fresh(self, path: pathlib.Path) -> bool:
        ttl = self.settings.song_cache_ttl
        if ttl <= 0 or not path.is_file():
            return False
        return (time.time() - path.stat().st_mtime) < ttl

    def _needs_notes(self, catalog: SongCatalog) -> bool:
        """缓存缺物量但配置要求物量 → 需要重新抓取。"""
        return self.settings.fetch_notes and not catalog.has_note_data

    # ------------------------------------------------------------------ 拉取
    async def _fetch(self) -> SongCatalog:
        bundle = await self.client.get_song_list(notes=self.settings.fetch_notes)
        aliases: list[Alias] = []
        try:
            aliases = await self.client.get_alias_list()
        except ChunithmBotError as exc:  # 别名不是必需品
            logger.warning("别名列表拉取失败，继续: %s", exc)

        self._write(self.settings.song_cache_path, bundle.model_dump(mode="json", by_alias=True))
        if aliases:
            self._write(
                self.settings.alias_cache_path,
                {"aliases": [a.model_dump(mode="json") for a in aliases]},
            )
        # 落雪缓存只存官方别名；overlay 每次加载时现合并，刷新不会丢
        aliases = merge_aliases(aliases, load_alias_overlay(self.settings.alias_overlay_paths))
        catalog = SongCatalog.from_bundle(bundle, aliases)
        logger.info("曲库已更新: %s", catalog.summary())
        return catalog

    @staticmethod
    def _write(path: pathlib.Path, payload: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:  # 缓存写不进去不该影响主流程
            logger.warning("写入曲库缓存失败 %s: %s", path, exc)

    # ------------------------------------------------------------------ 称号
    async def get_trophies(self, *, force_refresh: bool = False) -> list[Trophy]:
        """称号列表，带磁盘缓存。

        与曲库独立缓存、独立兜底：称号拉取失败不该让查歌不可用。
        """
        if self._trophies is not None and not force_refresh:
            return self._trophies

        path = self.settings.trophy_cache_path

        if not force_refresh and self._is_fresh(path):
            try:
                items = load_trophies_from_file(path)
            except (ChunithmBotError, ValueError, OSError) as exc:
                logger.warning("称号缓存损坏，改为在线拉取: %s", exc)
            else:
                if items:
                    logger.debug("称号命中缓存: %d 个", len(items))
                    self._trophies = items
                    return items

        try:
            self._trophies = await self._fetch_trophies()
        except ChunithmBotError:
            if self.settings.allow_stale_cache and path.is_file():
                logger.warning("称号拉取失败，回退到本地缓存: %s", path)
                self._trophies = load_trophies_from_file(path)
                return self._trophies
            raise
        return self._trophies

    async def _fetch_trophies(self) -> list[Trophy]:
        trophies = await self.client.get_trophy_list()
        self._write(
            self.settings.trophy_cache_path,
            {"trophies": [t.model_dump(mode="json") for t in trophies]},
        )
        logger.info("称号列表已更新: %d 个", len(trophies))
        return trophies
