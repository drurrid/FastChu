"""曲库：数据容器与索引。

把两个**公共接口**的结果组织成便于查询的结构：

* ``GET /chunithm/song/list`` —— 曲目、分类表、版本表、谱面定数、物量
* ``GET /chunithm/alias/list`` —— 曲目别名

关键词搜索在 :mod:`fastchu.services.search`，条件筛选在
:mod:`fastchu.services.query`；这里只负责「装数据 + 按 ID/版本/分类取数」。
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Iterable, Sequence

from ..errors import ConfigurationError
from ..models import Alias, Genre, LevelIndex, RATING_ELIGIBLE_DIFFICULTIES, Song, SongDifficulty, SongListBundle, Version
from .alias_overlay import load_alias_overlay, merge_aliases

__all__ = ["SongCatalog", "load_catalog_from_files"]

logger = logging.getLogger(__name__)


def _coerce_aliases(aliases: Sequence[Alias | dict]) -> list[Alias]:
    """别名既接受 :class:`Alias` 也接受原始字典。

    项目里其它地方（``SongListBundle.model_validate`` 等）都能直接吃字典，
    这里保持一致，避免调用方为了构造曲库还得自己先转模型。
    """
    return [item if isinstance(item, Alias) else Alias.model_validate(item) for item in aliases]


class SongCatalog:
    """曲库。除 :meth:`add_aliases` 外视为只读。"""

    def __init__(
        self,
        songs: Sequence[Song],
        *,
        versions: Sequence[Version] = (),
        genres: Sequence[Genre] = (),
        aliases: Sequence[Alias] = (),
    ) -> None:
        self.songs: list[Song] = list(songs)
        self.versions: list[Version] = list(versions)
        self.genres: list[Genre] = list(genres)
        self.aliases: list[Alias] = list(aliases)

        self._by_id: dict[int, Song] = {song.id: song for song in self.songs}
        self._version_title: dict[int, str] = {v.version: v.title for v in self.versions}
        self._alias_by_song: dict[int, list[str]] = {
            a.song_id: list(a.aliases) for a in self.aliases
        }

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_bundle(cls, bundle: SongListBundle, aliases: Sequence[Alias | dict] = ()) -> "SongCatalog":
        return cls(
            bundle.songs,
            versions=bundle.versions,
            genres=bundle.genres,
            aliases=_coerce_aliases(aliases),
        )

    @classmethod
    def from_dict(cls, payload: dict, aliases: Sequence[Alias | dict] = ()) -> "SongCatalog":
        return cls.from_bundle(SongListBundle.model_validate(payload), aliases)

    # ------------------------------------------------------------------ 基础查询
    def __len__(self) -> int:
        return len(self.songs)

    def get(self, song_id: int) -> Song | None:
        return self._by_id.get(song_id)

    def require(self, song_id: int) -> Song:
        song = self._by_id.get(song_id)
        if song is None:
            raise KeyError(f"曲库中没有曲目 ID {song_id}")
        return song

    def aliases_of(self, song_id: int) -> list[str]:
        return self._alias_by_song.get(song_id, [])

    def add_aliases(self, song_id: int, aliases: Sequence[str]) -> list[str]:
        """给曲目追加别名，返回真正新增的部分（已存在/空值会跳过）。"""
        if song_id not in self._by_id:
            return []
        from .search import normalize

        current = self._alias_by_song.setdefault(song_id, [])
        keys = {normalize(alias) for alias in current}
        added: list[str] = []
        for alias in aliases:
            key = normalize(alias)
            if not key or key in keys:
                continue
            keys.add(key)
            current.append(alias)
            added.append(alias)

        if added:
            for item in self.aliases:
                if item.song_id == song_id:
                    item.aliases = list(current)
                    break
            else:
                self.aliases.append(Alias(song_id=song_id, aliases=list(current)))
        return added

    def genre_names(self) -> list[str]:
        return [g.genre for g in self.genres]

    # ------------------------------------------------------------------ 谱面
    def chart(self, song_id: int, level_index: LevelIndex) -> SongDifficulty | None:
        song = self._by_id.get(song_id)
        return song.chart(level_index) if song else None

    def constant(self, song_id: int, level_index: LevelIndex) -> float | None:
        chart = self.chart(song_id, level_index)
        return chart.level_value if chart and chart.has_constant else None

    def charts_with_constant(
        self, song: Song, *, difficulties: Iterable[LevelIndex] = RATING_ELIGIBLE_DIFFICULTIES
    ) -> list[SongDifficulty]:
        """该曲在指定难度上有定数的谱面（默认 MASTER / ULTIMA）。"""
        wanted = set(difficulties)
        return [d for d in song.difficulties if d.has_constant and d.difficulty in wanted]

    # ------------------------------------------------------------------ 版本
    def current_version(self) -> int:
        """当前稼动版本。

        **取「实际存在曲目的最高版本」，而不是版本列表的最大值。**

        版本列表可能包含刚公布、曲库还没同步的版本（实测 X-VERSE-X / 24500
        已在版本列表里，但曲目数据最新只到 X-VERSE / 24000）。若直接用最大值，
        所有曲目都会被判为「旧版本」——筛选与「当前版本」相关展示都会出错。
        """
        present = [song.version for song in self.songs if song.version]
        if present:
            return max(present)
        if self.versions:
            return max(v.version for v in self.versions)
        return 0

    def declared_version(self) -> int:
        """版本列表声明的最高版本（可能还没有曲目）。"""
        if self.versions:
            return max(v.version for v in self.versions)
        return self.current_version()

    def version_title(self, version: int | None) -> str:
        if version is None:
            return "未知版本"
        return self._version_title.get(version, f"版本 {version}")

    def version_of(self, song_id: int) -> int | None:
        song = self._by_id.get(song_id)
        return song.version if song else None

    def is_current_version_song(self, song_id: int) -> bool:
        """是否属于当前版本的新曲。"""
        version = self.version_of(song_id)
        return version is not None and version >= self.current_version()

    def match_versions(self, token: str) -> list[Version]:
        """按版本号或版本名匹配版本。

        分档匹配，**精确优先**，避免 ``VERSE`` 同时命中 ``VERSE`` 与 ``X-VERSE``：

        1. 纯数字 → 精确版本号；
        2. 与「去掉 CHUNITHM 前缀的版本名」或完整版本名**完全相等**；
        3. 版本名的**前缀**（如 ``lum`` → ``LUMINOUS``）；
        4. 兜底子串匹配。

        支持 ``23000``、``verse``、``X-VERSE`` 这类写法（大小写与标点不敏感）。
        """
        from .search import normalize

        raw = token.strip()
        if not raw:
            return []

        if raw.isdigit():
            wanted = int(raw)
            return [v for v in self.versions if v.version == wanted]

        key = normalize(raw)
        if not key:
            return []

        shorts = {v.version: normalize(self.version_title(v.version).removeprefix("CHUNITHM ")) for v in self.versions}
        fulls = {v.version: normalize(self.version_title(v.version)) for v in self.versions}

        # 2. 完全相等
        exact = [v for v in self.versions if key in (shorts[v.version], fulls[v.version])]
        if exact:
            return exact

        # 3. 前缀（只对简名，避免 "chunithm" 前缀把全部版本拉进来）
        prefixed = [v for v in self.versions if shorts[v.version].startswith(key)]
        if prefixed:
            return prefixed

        # 4. 兜底子串
        return [v for v in self.versions if key in fulls[v.version]]

    # ------------------------------------------------------------------ 分类
    def match_genres(self, token: str) -> list[Genre]:
        """按分类名匹配分类（子串、大小写与标点不敏感）。"""
        from .search import normalize

        key = normalize(token)
        if not key:
            return []
        return [g for g in self.genres if key in normalize(g.genre)]

    # ------------------------------------------------------------------ 统计
    def is_empty(self) -> bool:
        return not self.songs

    @property
    def has_note_data(self) -> bool:
        """曲库里是否存在物量数据。

        缓存可能是用 ``notes=false`` 抓的，那时物量查询会一无所获——
        调用方据此给出「重新抓取」的明确提示，而不是假装「这首歌没有物量」。
        """
        return any(song.has_notes for song in self.songs)

    def summary(self) -> str:
        return (
            f"曲库 {len(self.songs)} 首 · 版本 {len(self.versions)} 个 · "
            f"分类 {len(self.genres)} 个 · 别名 {len(self.aliases)} 条"
        )


def load_catalog_from_files(
    song_path: pathlib.Path,
    alias_path: pathlib.Path | None = None,
    overlay_paths: Sequence[pathlib.Path] = (),
) -> SongCatalog:
    """从本地 JSON 文件加载曲库。

    文件格式与接口响应一致（``{"songs": [...], "genres": [...], "versions": [...]}``），
    由 ``scripts/fetch_reference_data.py`` 生成。``overlay_paths`` 里的补充别名
    会在落雪别名之后合并。
    """
    if not song_path.is_file():
        raise ConfigurationError(
            f"曲库文件不存在: {song_path}\n"
            "请先运行 `python scripts/fetch_reference_data.py` 抓取公共曲库数据"
            "（公共接口无需任何密钥）。"
        )
    payload = json.loads(song_path.read_text(encoding="utf-8"))

    aliases: list[Alias] = []
    if alias_path is not None and alias_path.is_file():
        raw = json.loads(alias_path.read_text(encoding="utf-8"))
        items = raw.get("aliases") if isinstance(raw, dict) else raw
        aliases = [Alias.model_validate(item) for item in (items or [])]
    else:
        logger.debug("别名文件不存在，跳过: %s", alias_path)

    if overlay_paths:
        aliases = merge_aliases(aliases, load_alias_overlay(overlay_paths))

    return SongCatalog.from_dict(payload, aliases)
