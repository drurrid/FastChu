"""条件筛选：按分类 / 版本 / BPM 列曲。

写法是 ``键=值``，多个条件用空白或逗号分隔::

    分类=原创
    版本=VERSE bpm=150-200
    分类=东方Project,原创

**解析期就把值解析成曲库里的真实对象**（而不是等筛选时才发现写错），
这样能直接告诉用户「分类 `xyz` 不存在，可用分类是……」——比返回空列表有用得多。

注意：按需求「只保留直接用歌曲查询」，机器人的 ``/筛选`` **命令已移除**，
本模块目前只被命令行与测试使用；条件解析逻辑保留，随时可以把命令挂回去。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import QuerySyntaxError
from ..models import Song
from .catalog import SongCatalog
from .search import normalize

__all__ = ["SongFilter", "parse_filter", "filter_songs", "FILTER_KEYS_HELP"]

#: 支持的键及其别名（ASCII 键一律小写，查表时统一 casefold）
_KEY_ALIASES: dict[str, str] = {
    "分类": "genre",
    "类型": "genre",
    "genre": "genre",
    "genres": "genre",
    "版本": "version",
    "version": "version",
    "ver": "version",
    "bpm": "bpm",
    "速度": "bpm",
}

FILTER_KEYS_HELP = "分类=<分类名> · 版本=<版本名或版本号> · bpm=<150 | 150-200 | >=150 | <=200>"

#: ``bpm`` 的几种写法。允许 1~3 位数字（中二实测约 40~300，但没必要把范围写死）
_BPM_RANGE = re.compile(r"^(\d{1,3})\s*[-~～]\s*(\d{1,3})$")
_BPM_LOWER = re.compile(r"^>=?\s*(\d{1,3})$")
_BPM_UPPER = re.compile(r"^<=?\s*(\d{1,3})$")
_BPM_SINGLE = re.compile(r"^(\d{1,3})$")

#: 条件之间的分隔符（空白 / 逗号 / 顿号）
_SPLIT = re.compile(r"[\s,，、]+")
#: 同一个键里多个取值之间的分隔符
_VALUE_SPLIT = re.compile(r"[,，、|/]")

#: ``键<比较运算符>值``，例如 ``bpm>=150``
_CONDITION_WITH_OP = re.compile(r"^([^=:：<>]+?)\s*(>=|<=|>|<)\s*(.+)$")
#: ``键=值`` / ``键:值``
_CONDITION_WITH_SEP = re.compile(r"^([^=:：]+?)\s*[=:：]\s*(.+)$")


def _split_condition(token: str) -> tuple[str, str] | None:
    """把 ``键=值`` / ``键:值`` / ``键>=值`` 拆成 ``(键, 值)``。

    拆不出来返回 ``None``（由调用方决定是报错还是当作上一个键的追加取值）。
    """
    if match := _CONDITION_WITH_OP.match(token):
        return match.group(1).strip(), f"{match.group(2)}{match.group(3).strip()}"
    if match := _CONDITION_WITH_SEP.match(token):
        return match.group(1).strip(), match.group(2).strip()
    return None


@dataclass(frozen=True)
class SongFilter:
    """已解析、已校验的筛选条件。"""

    #: 解析后的精确分类名（多选之间是「或」）
    genre_names: frozenset[str] = frozenset()
    #: 用户原始输入，用于展示
    genre_tokens: tuple[str, ...] = ()
    #: 解析后的版本号（多选之间是「或」）
    version_ids: frozenset[int] = frozenset()
    version_tokens: tuple[str, ...] = ()
    bpm_min: int | None = None
    bpm_max: int | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.genre_names or self.version_ids or self.bpm_min or self.bpm_max)

    def describe(self, catalog: SongCatalog) -> str:
        parts: list[str] = []
        if self.genre_tokens:
            parts.append(f"分类={'/'.join(self.genre_tokens)}")
        if self.version_tokens:
            parts.append(f"版本={'/'.join(self.version_tokens)}")
        if self.bpm_min is not None and self.bpm_max is not None:
            parts.append(
                f"BPM={self.bpm_min}" if self.bpm_min == self.bpm_max else f"BPM={self.bpm_min}-{self.bpm_max}"
            )
        elif self.bpm_min is not None:
            parts.append(f"BPM>={self.bpm_min}")
        elif self.bpm_max is not None:
            parts.append(f"BPM<={self.bpm_max}")
        return " · ".join(parts)

    def matches(self, song: Song) -> bool:
        if self.genre_names and (song.genre or "") not in self.genre_names:
            return False
        if self.version_ids and (song.version or 0) not in self.version_ids:
            return False
        if self.bpm_min is not None or self.bpm_max is not None:
            if song.bpm is None:
                return False
            if self.bpm_min is not None and song.bpm < self.bpm_min:
                return False
            if self.bpm_max is not None and song.bpm > self.bpm_max:
                return False
        return True


def _parse_bpm(value: str) -> tuple[int | None, int | None]:
    raw = value.strip()
    if match := _BPM_RANGE.match(raw):
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        return low, high
    if match := _BPM_LOWER.match(raw):
        return int(match.group(1)), None
    if match := _BPM_UPPER.match(raw):
        return None, int(match.group(1))
    if match := _BPM_SINGLE.match(raw):
        exact = int(match.group(1))
        return exact, exact
    raise QuerySyntaxError(
        f"看不懂 BPM 条件 `{value}`。可以写 150、150-200、>=150 或 <=200。",
        user_message=f"看不懂 BPM 条件 `{value}`。可以写 150、150-200、>=150 或 <=200。",
    )


def _split_conditions(text: str) -> list[str]:
    return [token for token in _SPLIT.split(text.strip()) if token]


def parse_filter(text: str, catalog: SongCatalog) -> SongFilter:
    """解析筛选条件并在曲库中校验。

    条件之间用空白或逗号分隔。为了让 ``分类=原创,东方`` 这种写法也能用，
    **没有分隔符的片段会被当作上一个键的追加取值**。

    :raises QuerySyntaxError: 键不支持、值缺失，或分类/版本在曲库里不存在
    """
    tokens = _split_conditions(text)
    if not tokens:
        raise QuerySyntaxError(
            "筛选条件为空",
            user_message=f"请至少给一个条件。支持：{FILTER_KEYS_HELP}",
        )

    # --- 第一步：拆成 (键, 值) 列表
    conditions: list[tuple[str, str]] = []
    for token in tokens:
        split = _split_condition(token)
        if split is None:
            if not conditions:
                raise QuerySyntaxError(
                    f"条件 `{token}` 写法不对",
                    user_message=(
                        f"条件 `{token}` 写法不对，要写成「键=值」。支持：{FILTER_KEYS_HELP}"
                    ),
                )
            # 承接上一个键的取值，支持「分类=原创,东方」这种简写
            key, value = conditions[-1]
            conditions[-1] = (key, f"{value},{token}")
            continue

        raw_key, value = split
        key = _KEY_ALIASES.get(raw_key.casefold())
        if key is None:
            raise QuerySyntaxError(
                f"不支持的筛选键 `{raw_key}`",
                user_message=f"不支持的筛选键 `{raw_key}`。支持：{FILTER_KEYS_HELP}",
            )
        conditions.append((key, value))

    # --- 第二步：逐条解析并校验
    genre_names: set[str] = set()
    genre_tokens: list[str] = []
    version_ids: set[int] = set()
    version_tokens: list[str] = []
    bpm_bounds: list[tuple[int | None, int | None]] = []

    for key, value in conditions:
        if key == "bpm":
            bpm_bounds.append(_parse_bpm(value))
            continue

        values = [v.strip() for v in _VALUE_SPLIT.split(value) if v.strip()]
        if not values:
            raise QuerySyntaxError(
                f"条件值为空: {key}={value}",
                user_message=f"条件 `{key}=` 没给值。支持：{FILTER_KEYS_HELP}",
            )

        if key == "genre":
            for item in values:
                matched = catalog.match_genres(item)
                if not matched:
                    available = " / ".join(catalog.genre_names()) or "（曲库无分类数据）"
                    raise QuerySyntaxError(
                        f"分类 `{item}` 不存在",
                        user_message=f"分类 `{item}` 不存在。可用分类：{available}",
                    )
                genre_names.update(g.genre for g in matched)
                genre_tokens.append(item)
        else:  # version
            for item in values:
                matched = catalog.match_versions(item)
                if not matched:
                    newest = sorted(catalog.versions, key=lambda v: v.version, reverse=True)[:6]
                    available = (
                        " / ".join(f"{v.title}({v.version})" for v in newest)
                        or "（曲库无版本数据）"
                    )
                    raise QuerySyntaxError(
                        f"版本 `{item}` 不存在",
                        user_message=f"版本 `{item}` 不存在。最近几个版本：{available}",
                    )
                version_ids.update(v.version for v in matched)
                version_tokens.append(item)

    # 多个 bpm 条件取交集：下限取最大、上限取最小
    bpm_min: int | None = None
    bpm_max: int | None = None
    for low, high in bpm_bounds:
        if low is not None:
            bpm_min = low if bpm_min is None else max(bpm_min, low)
        if high is not None:
            bpm_max = high if bpm_max is None else min(bpm_max, high)

    if bpm_min is not None and bpm_max is not None and bpm_min > bpm_max:
        raise QuerySyntaxError(
            f"BPM 区间为空: {bpm_min} > {bpm_max}",
            user_message=f"BPM 区间为空：{bpm_min} > {bpm_max}，换个范围试试。",
        )

    return SongFilter(
        genre_names=frozenset(genre_names),
        genre_tokens=tuple(genre_tokens),
        version_ids=frozenset(version_ids),
        version_tokens=tuple(version_tokens),
        bpm_min=bpm_min,
        bpm_max=bpm_max,
    )


def filter_songs(catalog: SongCatalog, spec: SongFilter) -> list[Song]:
    """按条件筛曲，按**最高定数降序**排列（同定数按曲名）。

    这样列表开头就是这批里最值得注意的曲子，而不是毫无信息量的曲库顺序。
    """
    hits = [song for song in catalog.songs if spec.matches(song)]
    hits.sort(key=lambda s: (-s.max_constant, s.title))
    return hits
