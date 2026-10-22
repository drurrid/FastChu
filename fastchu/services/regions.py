"""国服 / 日服数据对照。

落雪（国服）与 chunirec（日服）的曲库并不一致：

* **日服曲目更多**（新版本 + 大量限定曲，实测比国服多 277 首普通曲）；
* **同一首歌的定数可能不同**（实测 33 个「曲目 × 难度」组合有差异）。

这个模块读 :mod:`scripts.build_song_regions` 生成的 ``data/songs/songs.union.json``，
提供两个能力：

* :meth:`RegionCatalog.for_cn_id` —— 给国服曲目挂上「日服定数是多少」；
* :meth:`RegionCatalog.find`  —— 检索**日服独有**的曲目（国服没有的歌）。

数据缺失时整个模块优雅降级（返回空目录），不影响查歌。
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .alias_overlay import load_alias_overlay

__all__ = ["ChartCompare", "RegionSong", "RegionCatalog", "load_region_catalog", "format_const"]

logger = logging.getLogger(__name__)

#: 难度展示顺序
DIFFICULTY_ORDER = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")


def _f(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


@dataclass(frozen=True)
class ChartCompare:
    """一个难度在两服的标级与定数对照。"""

    name: str
    cn_level: str | None = None
    cn_const: float | None = None
    jp_level: float | None = None
    jp_const: float | None = None
    jp_const_unknown: bool = False
    #: 日服谱面制作者（来自 wiki「譜面制作者一覧」）
    cn_designer: str | None = None
    jp_designer: str | None = None

    @property
    def differs(self) -> bool:
        """两服定数是否不同（WE 用 ★ 难度，不参与比较）。"""
        if self.name == "WE":
            return False
        if self.cn_const is None or self.jp_const is None:
            return False
        return abs(self.cn_const - self.jp_const) > 1e-9

    def const_display(self) -> str:
        """只返回**定数部分**，供难度行内嵌（``13.8→14.0``）。

        * 两服相同 → 只显示一个值，保持简洁；
        * 两服不同 → ``国服值→日服值``；
        * 日服定数为推定值 → 尾部加 ``?``；
        * 只有一侧有 → 标出是哪一服。
        """
        cn, jp = self.cn_const, self.jp_const
        mark = "?" if self.jp_const_unknown else ""
        if cn is None and jp is None:
            return "—"
        if cn is None:
            return f"{format_const(jp)}{mark}日"
        if jp is None:
            return format_const(cn)
        if self.name == "WE" or abs(cn - jp) <= 1e-9:
            return format_const(cn)
        return f"{format_const(cn)}→{format_const(jp)}{mark}"

    def render(self, *, prefer_jp: bool = False) -> str:
        """渲染成 ``15.5→15.6`` 这种形式。

        只有定数不同时才同时标注两服；相同时保持简洁，只显示一个值。
        """
        cn = self.cn_const
        jp = self.jp_const
        mark = "?" if self.jp_const_unknown else ""

        if prefer_jp or cn is None:
            if jp is None:
                return "—"
            return f"{format_const(jp)}{mark}日"
        if jp is None:
            return f"{format_const(cn)}"
        if self.name == "WE" or abs(cn - jp) <= 1e-9:
            return f"{format_const(cn)}"
        return f"{format_const(cn)}→{format_const(jp)}{mark}"


def format_const(value: float | None) -> str:
    """定数显示：整数不带小数点尾零，其余保留一位。"""
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


@dataclass
class RegionSong:
    """并集里的一首歌。"""

    title: str
    artist: str = ""
    #: 中文别名（社区 overlay / 群友投稿）
    aliases: list[str] = field(default_factory=list)
    bpm: int | None = None
    kind: str = "song"
    regions: list[str] = field(default_factory=list)
    genres: dict[str, str | None] = field(default_factory=dict)
    cn_id: int | None = None
    jp_id: str | None = None
    cn_version: int | None = None
    jp_release: str | None = None
    #: 日服谱面 ID（与国服 id 同一套编号）
    jp_idx: int | None = None
    #: 日服曲绘的文件名哈希
    jp_img: str | None = None
    #: 日服版本短名（Mate / X-VERSE …）：由发行日期按稼働日表换算
    jp_version: str | None = None
    charts: dict[str, ChartCompare] = field(default_factory=dict)

    @property
    def in_cn(self) -> bool:
        return "cn" in self.regions

    @property
    def in_jp(self) -> bool:
        return "jp" in self.regions

    @property
    def cn_only(self) -> bool:
        return self.in_cn and not self.in_jp

    @property
    def jp_only(self) -> bool:
        return self.in_jp and not self.in_cn

    @property
    def is_we(self) -> bool:
        return self.kind == "we"

    def ordered_charts(self) -> list[ChartCompare]:
        return [self.charts[name] for name in DIFFICULTY_ORDER if name in self.charts]

    def region_tag(self) -> str:
        """区域标记，只在「不是两服都有」时才有意义。"""
        if self.jp_only:
            return "仅日服"
        if self.cn_only:
            return "仅国服"
        return ""

    def add_aliases(self, aliases: list[str]) -> list[str]:
        """追加别名，返回真正新增的部分（去重按归一化文本）。"""
        keys = {_normalize(alias) for alias in self.aliases}
        added: list[str] = []
        for alias in aliases:
            alias = alias.strip()
            key = _normalize(alias)
            if not alias or not key or key in keys:
                continue
            keys.add(key)
            self.aliases.append(alias)
            added.append(alias)
        return added

    def differing_charts(self) -> list[ChartCompare]:
        return [chart for chart in self.ordered_charts() if chart.differs]


class RegionCatalog:
    """并集索引。"""

    def __init__(self, songs: list[RegionSong]) -> None:
        self.songs = songs
        self._by_cn_id: dict[int, RegionSong] = {}
        self._by_jp_idx: dict[int, RegionSong] = {}
        self._by_title: dict[tuple[str, str], RegionSong] = {}
        for song in songs:
            if song.cn_id is not None:
                self._by_cn_id[song.cn_id] = song
            if song.jp_idx is not None:
                self._by_jp_idx.setdefault(song.jp_idx, song)
            self._by_title.setdefault((_normalize(song.title), song.kind), song)

    def __len__(self) -> int:
        return len(self.songs)

    def for_cn_id(self, cn_id: int) -> RegionSong | None:
        return self._by_cn_id.get(cn_id)

    def find(self, keyword: str, *, jp_only: bool = False, limit: int = 10) -> list[RegionSong]:
        """按曲名或别名检索。

        排序：曲名完全匹配 > 别名完全匹配 > 曲名前缀 > 别名前缀 >
        曲名包含 > 别名包含。曲名始终优先于别名。

        :param jp_only: 只看国服没有的日服曲目
        """
        key = _normalize(keyword)
        if not key:
            return []

        buckets: dict[str, list[RegionSong]] = {
            name: [] for name in (
                "title_exact", "alias_exact", "title_prefix",
                "alias_prefix", "title_contains", "alias_contains",
            )
        }
        for song in self.songs:
            if jp_only and not song.jp_only:
                continue
            title = _normalize(song.title)
            aliases = [_normalize(alias) for alias in song.aliases]
            aliases = [alias for alias in aliases if alias]
            if title == key:
                buckets["title_exact"].append(song)
            elif key in aliases:
                buckets["alias_exact"].append(song)
            elif title.startswith(key):
                buckets["title_prefix"].append(song)
            elif any(alias.startswith(key) for alias in aliases):
                buckets["alias_prefix"].append(song)
            elif key in title:
                buckets["title_contains"].append(song)
            elif any(key in alias for alias in aliases):
                buckets["alias_contains"].append(song)

        ranked: list[RegionSong] = []
        for name in buckets:
            ranked.extend(buckets[name])
        return ranked[:limit]

    def find_exact(self, keyword: str, *, jp_only: bool = False) -> list[RegionSong]:
        """曲名或别名归一化后完全相等（曲名在前）。"""
        key = _normalize(keyword)
        if not key:
            return []
        title_hits: list[RegionSong] = []
        alias_hits: list[RegionSong] = []
        for song in self.songs:
            if jp_only and not song.jp_only:
                continue
            if _normalize(song.title) == key:
                title_hits.append(song)
            elif any(_normalize(alias) == key for alias in song.aliases):
                alias_hits.append(song)
        return title_hits + alias_hits

    def find_alias_exact(
        self, keyword: str, *, jp_only: bool = False
    ) -> list[RegionSong]:
        """只按别名做精确匹配（不含曲名），用于数字别名优先于谱面 ID。"""
        key = _normalize(keyword)
        if not key:
            return []
        return [
            song
            for song in self.songs
            if (not jp_only or song.jp_only)
            and any(_normalize(alias) == key for alias in song.aliases)
        ]

    def add_aliases(self, jp_idx: int, aliases: list[str]) -> list[str]:
        song = self._by_jp_idx.get(jp_idx)
        return song.add_aliases(aliases) if song is not None else []

    def find_by_id(self, song_id: int) -> "RegionSong | None":
        """按谱面 ID 找并集里的曲子（国服/日服通用编号）。"""
        return self._by_jp_idx.get(song_id)
    def latest_jp_version(self) -> tuple[str | None, str | None]:
        """日服最新版本短名与最新发行日期。

        版本由发行日期按 ``jp_versions`` 的稼働日表换算——日服数据源本身只有日期。
        """
        from .jp_versions import version_for

        latest: str | None = None
        for song in self.songs:
            release = song.jp_release
            if release and (latest is None or release > latest):
                latest = release
        version = version_for(latest)
        return (version.short if version is not None else None, latest)

    def stats(self) -> dict[str, int]:
        both = sum(1 for s in self.songs if s.in_cn and s.in_jp)
        return {
            "total": len(self.songs),
            "both": both,
            "cn_only": sum(1 for s in self.songs if s.cn_only),
            "jp_only": sum(1 for s in self.songs if s.jp_only),
            "const_diff": sum(1 for s in self.songs if s.differing_charts()),
        }


def _parse_song(raw: dict) -> RegionSong:
    charts: dict[str, ChartCompare] = {}
    for name, block in (raw.get("charts") or {}).items():
        cn = block.get("cn") or {}
        jp = block.get("jp") or {}
        charts[name] = ChartCompare(
            name=name,
            cn_level=cn.get("level"),
            cn_const=_f(cn.get("const")),
            jp_level=_f(jp.get("level")),
            jp_const=_f(jp.get("const")),
            jp_const_unknown=bool(jp.get("const_unknown")),
            cn_designer=(cn.get("designer") or None),
            jp_designer=(jp.get("designer") or None),
        )

    return RegionSong(
        title=raw.get("title") or "",
        artist=raw.get("artist") or "",
        aliases=list(raw.get("aliases") or []),
        bpm=raw.get("bpm"),
        kind=raw.get("kind") or "song",
        regions=list(raw.get("regions") or []),
        genres=dict(raw.get("genres") or {}),
        cn_id=raw.get("cn_id"),
        jp_id=raw.get("jp_id"),
        cn_version=raw.get("cn_version"),
        jp_release=raw.get("jp_release"),
        jp_idx=raw.get("jp_idx"),
        jp_img=raw.get("jp_img"),
        jp_version=raw.get("jp_version"),
        charts=charts,
    )


def load_region_catalog(
    path: str | Path, overlay_paths: tuple[Path, ...] | list[Path] = ()
) -> RegionCatalog | None:
    """读取并集文件；不存在或损坏时返回 ``None``（调用方据此降级）。

    ``overlay_paths`` 里的日服别名会按 ``jp_idx`` 挂到曲目上。
    """
    path = Path(path)
    if not path.is_file():
        logger.debug("并集数据不存在，跳过区域对照: %s", path)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_songs = payload.get("songs") if isinstance(payload, dict) else payload
        songs = [_parse_song(item) for item in (raw_songs or [])]
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("并集数据读取失败，跳过区域对照: %s", exc)
        return None
    if not songs:
        return None

    if overlay_paths:
        overlay = load_alias_overlay(overlay_paths)
        by_idx = {song.jp_idx: song for song in songs if song.jp_idx is not None}
        for jp_idx, aliases in overlay.jp.items():
            song = by_idx.get(jp_idx)
            if song is not None:
                song.add_aliases(aliases)

    logger.debug("区域对照已加载: %d 首", len(songs))
    return RegionCatalog(songs)
