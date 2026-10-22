"""查歌统一入口。

把「曲库缓存 + 搜索索引 + 曲绘」包成一个对象，插件层只做
「解析输入 → 调服务 → 发消息」。

两个刻意的设计：

* **索引与曲库同生命周期**——曲库刷新时索引一起重建，不会出现
  「曲库是新的、索引是旧的」这种难查的错位。
* **一律懒初始化**——导入插件时不读配置、不碰网络，机器人启动顺序上更安全。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Sequence

from ..api.client import LxnsClient
from ..config import BotSettings, get_settings
from ..errors import ChunithmBotError, SongNotFoundError
from ..models import Song, Trophy
from .alias_overlay import AliasSection, append_local_alias
from .catalog import SongCatalog
from .catalog_loader import CatalogLoader
from .jacket import JacketImage, JacketProvider
from .personal import PersonalService
from .search import SongMatch, SongSearch, normalize
from .regions import RegionCatalog, RegionSong, load_region_catalog
from .titles import (
    TrophyCatalog,
    apply_condition_overrides,
    load_condition_overrides,
    JP_SONG_ID_BASE,
    load_jp_only_titles,
)

__all__ = ["FindResult", "SongService", "get_service", "reset_service", "aclose_service"]

logger = logging.getLogger(__name__)


@dataclass
class FindResult:
    """一次「找歌」的结果。"""

    keyword: str
    matches: list[SongMatch] = field(default_factory=list)
    #: 唯一命中（能确定是哪首）时才有值
    song: Song | None = None
    #: 命中来源：``id`` / ``alias`` / ``exact`` / ``search``
    source: str = "search"

    @property
    def is_empty(self) -> bool:
        return self.song is None and not self.matches

    @property
    def is_unique(self) -> bool:
        return self.song is not None

    def require_song(self) -> Song:
        """取唯一命中的曲目；没有就抛 :class:`SongNotFoundError`。"""
        if self.song is None:
            raise SongNotFoundError(f"未唯一命中: {self.keyword}")
        return self.song


def _link_jp_only_songs(
    trophies: list[Trophy],
    song_titles: dict[str, int] | None,
) -> None:
    """给日服限定称号补「关联曲目」。

    这些称号来自 wiki，没有落雪的结构化 ``required``；但条件文本长这样::

        YOUNITHM/ULTIMA以下全て/ALL JUSTICE達成

    所以用「曲名 + 后面跟 /」的方式反查。

    **必须复用 ``song_titles`` 这张表**（RrophyCatalog 的曲名索引就是它）——
    两处如果各造一套 ID，称号挂到了曲目上，``for_song_title`` 却查不到，
    表现就是「查歌时看不到对应的特殊称号」。
    """
    from ..models import TrophyRequired, TrophyRequiredSong
    from .titles import songs_mentioned_in

    if not trophies or not song_titles:
        return

    for trophy in trophies:
        if trophy.required:
            continue
        ids = songs_mentioned_in(trophy.description or "", song_titles)
        if not ids:
            continue
        # id -> 曲名的反查（同一个 id 可能对应多个写法，取第一个）
        id_to_title: dict[int, str] = {}
        for title, song_id in song_titles.items():
            id_to_title.setdefault(song_id, title)
        songs = [
            TrophyRequiredSong(id=sid, title=id_to_title.get(sid, ""))
            for sid in ids
            if sid in id_to_title
        ]
        if songs:
            trophy.required = [TrophyRequired(songs=songs)]


class SongService:
    """曲库 + 搜索 + 曲绘。"""

    def __init__(
        self,
        settings: BotSettings,
        *,
        client: LxnsClient | None = None,
        loader: CatalogLoader | None = None,
        jackets: JacketProvider | None = None,
        personal: PersonalService | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or LxnsClient(
            base_url=settings.lxns_base_url,
            timeout=settings.http_timeout,
            retries=settings.http_retries,
            backoff=settings.http_backoff,
            min_interval=settings.min_request_interval,
        )
        self.loader = loader or CatalogLoader(settings, self.client)
        self.jackets = jackets or JacketProvider(settings, self.client)
        self._owns_personal = personal is None
        self.personal = personal or PersonalService(settings)
        self._catalog: SongCatalog | None = None
        self._search: SongSearch | None = None
        self._titles: TrophyCatalog | None = None
        self._titles_source: list[Trophy] | None = None
        self._regions: RegionCatalog | None = None
        self._regions_loaded = False
        #: 别名投稿是「读文件 → 写文件 → 改内存」，串行化避免并发写坏文件
        self._alias_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 曲库
    async def catalog(self, *, force_refresh: bool = False) -> SongCatalog:
        catalog = await self.loader.get(force_refresh=force_refresh)
        if self._catalog is not catalog or force_refresh:
            # 曲库换了 → 索引必须跟着换
            self._catalog = catalog
            self._search = SongSearch(catalog)
            logger.debug("搜索索引已重建：%d 首", len(self._search))
        return catalog

    async def search_index(self, *, force_refresh: bool = False) -> SongSearch:
        await self.catalog(force_refresh=force_refresh)
        assert self._search is not None  # catalog() 保证已构建
        return self._search

    # ------------------------------------------------------------------ 称号
    async def titles(self, *, force_refresh: bool = False) -> TrophyCatalog:
        """称号目录（含分类与「曲目 → 称号」反查索引）。

        与曲库同款策略：缓存为主，且索引跟着数据走。

        构建索引前会做两件事：

        * **叠日服 Wiki 的条件补充**：落雪对一部分称号只给「CHUNITHMから引き継ぎ」
          这种没信息量的说明，真正的达成条件在日服 Wiki 里；
        * **带上曲名表**：落雪对「非单曲条件」的称号（例如 UNLOCK CHALLENGE）
          不给 ``required.songs``，只能从 ``description`` 里反查曲名，
          否则这类称号（如虹色《少女が夢見た空の果て》）在曲目详情里挂不上。
        """
        trophies = await self.loader.get_trophies(force_refresh=force_refresh)
        if self._titles is None or self._titles_source is not trophies:
            catalog = None
            region_catalog = None
            song_titles: dict[str, int] | None = None
            try:
                catalog = await self.catalog()
                song_titles = {song.title: song.id for song in catalog.songs}
                # 日服独有曲目用合成 ID 也进索引，这样「曲名 + 条件」的文本反查
                # 才能把称号挂到它们身上（否则日服曲目详情里永远没有称号）
                region_catalog = await self.regions()
                if region_catalog is not None:
                    for offset, region_song in enumerate(region_catalog.songs, start=1):
                        if region_song.in_cn or not region_song.title:
                            continue
                        song_titles.setdefault(
                            region_song.title, JP_SONG_ID_BASE + offset
                        )
            except Exception as exc:  # noqa: BLE001 - 曲库拿不到时退化为「只按 required.songs」
                logger.warning("读取曲库失败，称号索引不含文本反查: %s", exc)
                song_titles = None

            # 日服限定称号（wiki 有、落雪没有）并进来；
            # 它们没有结构化 required，用条件文本反查补上关联曲目
            jp_only = load_jp_only_titles(
                getattr(self.settings, "jp_only_titles_path", "")
            )
            if jp_only:
                _link_jp_only_songs(jp_only, song_titles)
                trophies = list(trophies) + jp_only
                logger.debug("日服限定称号已并入：%d 个", len(jp_only))

            overrides = load_condition_overrides(self.settings.condition_overrides_path)
            if overrides:
                trophies = apply_condition_overrides(trophies, overrides)
                logger.debug("条件补充已叠加：%d 个称号", len(overrides))

            self._titles = TrophyCatalog(trophies, song_titles=song_titles)
            self._titles_source = trophies
            logger.debug("称号索引已重建：%d 个", len(self._titles))
        return self._titles

    async def regions(self) -> RegionCatalog | None:
        """国服 / 日服并集对照（含定数差异）。

        数据文件不存在时返回 ``None``——查歌不依赖它，只是少了「日服定数」这一层信息。
        """
        if self._regions_loaded:
            return self._regions
        self._regions_loaded = True
        self._regions = load_region_catalog(
            self.settings.region_cache_path, self.settings.alias_overlay_paths
        )
        return self._regions

    # ------------------------------------------------------------------ 别名投稿
    async def add_aliases(self, song: Song, aliases: Sequence[str]) -> list[str]:
        """给国服曲目追加别名并写入本地投稿文件。"""
        async with self._alias_lock:
            catalog = await self.catalog()
            existing = {normalize(alias) for alias in catalog.aliases_of(song.id)}
            new = [
                alias
                for alias in aliases
                if normalize(alias) and normalize(alias) not in existing
            ]
            if not new:
                return []
            self._write_local_alias("cn", song.id, new)
            added = catalog.add_aliases(song.id, new)
            if added:
                self._search = SongSearch(catalog)
            return added

    async def add_region_aliases(
        self, song: RegionSong, aliases: Sequence[str]
    ) -> list[str]:
        """给日服独有曲目追加别名并写入本地投稿文件。"""
        async with self._alias_lock:
            catalog = await self.regions()
            if catalog is None or song.jp_idx is None:
                return []
            existing = {normalize(alias) for alias in song.aliases}
            new = [
                alias
                for alias in aliases
                if normalize(alias) and normalize(alias) not in existing
            ]
            if not new:
                return []
            self._write_local_alias("jp", song.jp_idx, new)
            return catalog.add_aliases(song.jp_idx, new)

    def _write_local_alias(
        self, section: AliasSection, song_id: int, aliases: Sequence[str]
    ) -> None:
        try:
            append_local_alias(self.settings.local_alias_path, section, song_id, aliases)
        except (OSError, ValueError) as exc:
            logger.exception("写入本地别名失败 song_id=%s", song_id)
            raise ChunithmBotError(
                f"写入本地别名失败: {exc}", user_message="别名保存失败，请稍后再试。"
            ) from exc

    async def related_titles(self, song_id: int, *, limit: int = 8) -> list[Trophy]:
        """某首歌涉及的特殊称号（已排除常规 AJ / 角色养成）。"""
        catalog = await self.titles()
        return catalog.for_song(song_id)[:limit]

    # ------------------------------------------------------------------ 找歌
    async def find(
        self, keyword: str, *, limit: int | None = None, exact: bool = False
    ) -> FindResult:
        """按 ``#ID`` / 纯数字 / 关键词找歌。

        * 纯数字且**精确命中别名** → 按别名结果返回（别名优先于 ID）；
        * ``#123`` 或纯数字且命中曲目 ID → 唯一命中；
        * 否则走搜索：只有一首、或榜首明显领先时算唯一命中。
        """
        raw = (keyword or "").strip()
        if not raw:
            return FindResult(keyword=raw)

        catalog = await self.catalog()
        index = await self.search_index()

        # --- 纯数字别名优先：ID 还能用 #123 明确指定，别名没有别的写法
        if raw.isdecimal():
            alias_matches = index.search_alias_exact(raw)
            if alias_matches:
                return FindResult(
                    keyword=raw,
                    matches=alias_matches,
                    song=alias_matches[0].song if len(alias_matches) == 1 else None,
                    source="alias",
                )

        # --- 显式 ID（#123 或纯数字且曲库确实有这个 ID）
        explicit_id = None
        if raw.startswith("#") and raw[1:].strip().isdigit():
            explicit_id = int(raw[1:].strip())
        elif raw.isdigit():
            # 纯数字可能是曲名的一部分，所以下面还要兜底搜索
            candidate = int(raw)
            if catalog.get(candidate) is not None:
                explicit_id = candidate

        if explicit_id is not None:
            song = catalog.get(explicit_id)
            if song is None:
                raise SongNotFoundError(f"曲库中没有曲目 ID {explicit_id}")
            return FindResult(keyword=raw, song=song, source="id")

        # --- 精确匹配（``/歌名 c``）
        if exact:
            exact_matches = index.search_exact(raw)
            if not exact_matches:
                return FindResult(keyword=raw)
            return FindResult(
                keyword=raw,
                matches=exact_matches,
                song=exact_matches[0].song if len(exact_matches) == 1 else None,
                source="exact",
            )

        # --- 关键词
        effective_limit = limit if limit is not None else max(self.settings.search_result_limit, 2)
        matches = index.search(raw, limit=effective_limit)
        if not matches:
            return FindResult(keyword=raw)

        best = index.best(raw)
        if best is not None:
            return FindResult(keyword=raw, matches=matches, song=best.song, source="search")
        return FindResult(keyword=raw, matches=matches, source="search")

    # ------------------------------------------------------------------ 曲绘
    async def jacket(self, song_id: int) -> JacketImage | None:
        return await self.jackets.get(song_id)

    # ------------------------------------------------------------------ 其它
    async def aclose(self) -> None:
        if self._owns_personal:
            await self.personal.aclose()
        if self._owns_client:
            await self.client.aclose()


# --------------------------------------------------------------------------- #
# 进程级单例
# ---------------------------------------------------------------------------

_service: SongService | None = None


def get_service() -> SongService:
    """取进程级服务实例（NoneBot 插件用）。"""
    global _service
    if _service is None:
        _service = SongService(get_settings())
    return _service


def reset_service() -> None:
    """丢弃单例，供测试与热重载使用。"""
    global _service
    _service = None


async def aclose_service() -> None:
    """关闭并丢弃单例（进程退出时调用）。"""
    global _service
    if _service is None:
        return
    service, _service = _service, None
    await service.aclose()
