"""日服版本表（版本名 ↔ 稼働日）。

数据取自日文维基百科「チュウニズム」各版本条目里的「稼働開始」日期，
并与 SEGA 官方公告交叉核对（例如 Mate：https://www.sega.co.jp/release/260702_2.html）。

有了这张表，就能把日服曲库里的**发行日期**换算成它属于哪个版本——
日服数据源（chunirec 镜像）本身只有日期、没有版本字段。

新版本上线时在这里追加一行即可（保持按 ``started`` 升序）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "JP_VERSIONS",
    "JpVersion",
    "latest_version",
    "version_for",
    "version_name",
]


@dataclass(frozen=True)
class JpVersion:
    """一个日服版本。"""

    #: 完整名，例如 ``CHUNITHM Mate``
    name: str
    #: 短名，例如 ``Mate``（展示用）
    short: str
    #: 稼働开始日
    started: _dt.date


def _v(name: str, short: str, year: int, month: int, day: int) -> JpVersion:
    return JpVersion(name=f"CHUNITHM {name}" if name else "CHUNITHM", short=short,
                     started=_dt.date(year, month, day))


#: 全部日服版本，按稼働日升序
JP_VERSIONS: tuple[JpVersion, ...] = (
    _v("", "CHUNITHM", 2015, 7, 16),
    _v("PLUS", "PLUS", 2016, 2, 4),
    _v("AIR", "AIR", 2016, 8, 25),
    _v("AIR PLUS", "AIR PLUS", 2017, 2, 9),
    _v("STAR", "STAR", 2017, 8, 24),
    _v("STAR PLUS", "STAR PLUS", 2018, 3, 8),
    _v("AMAZON", "AMAZON", 2018, 10, 25),
    _v("AMAZON PLUS", "AMAZON PLUS", 2019, 4, 11),
    _v("CRYSTAL", "CRYSTAL", 2019, 10, 24),
    _v("CRYSTAL PLUS", "CRYSTAL PLUS", 2020, 7, 16),
    _v("PARADISE", "PARADISE", 2021, 1, 21),
    _v("PARADISE LOST", "PARADISE LOST", 2021, 5, 13),
    _v("NEW", "NEW", 2021, 11, 4),
    _v("NEW PLUS", "NEW PLUS", 2022, 4, 14),
    _v("SUN", "SUN", 2022, 10, 13),
    _v("SUN PLUS", "SUN PLUS", 2023, 5, 11),
    _v("LUMINOUS", "LUMINOUS", 2023, 12, 14),
    _v("LUMINOUS PLUS", "LUMINOUS PLUS", 2024, 6, 20),
    _v("VERSE", "VERSE", 2024, 12, 12),
    _v("X-VERSE", "X-VERSE", 2025, 7, 16),
    _v("X-VERSE-X", "X-VERSE-X", 2025, 12, 11),
    _v("Mate", "Mate", 2026, 7, 2),
)


def latest_version() -> JpVersion:
    """当前最新的日服版本。"""
    return JP_VERSIONS[-1]


def _parse_date(text: str | None) -> _dt.date | None:
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


@lru_cache(maxsize=1024)
def version_for(text: str | None) -> JpVersion | None:
    """把 ``2026-10-08`` 这样的发行日期换算成所属版本。

    早于第一个版本的日期或无法解析时返回 ``None``。
    """
    day = _parse_date(text)
    if day is None:
        return None
    found: JpVersion | None = None
    for version in JP_VERSIONS:
        if version.started <= day:
            found = version
        else:
            break
    return found


def version_name(text: str | None) -> str | None:
    """发行日期对应的版本短名（``Mate`` / ``X-VERSE`` …）。"""
    version = version_for(text)
    return version.short if version is not None else None
