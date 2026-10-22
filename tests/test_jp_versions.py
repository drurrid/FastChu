"""日服版本表测试。

日服数据源（chunirec 镜像）只有发行日期，没有版本字段，
所以「这首歌属于哪个版本」完全靠 :mod:`fastchu.services.jp_versions` 的稼働日表换算。
这张表错了，界面上显示的版本就会全错——值得单独测。
"""

from __future__ import annotations

import datetime as dt

import pytest

from fastchu.services.jp_versions import (
    JP_VERSIONS,
    latest_version,
    version_for,
    version_name,
)


def test_versions_are_sorted_and_unique() -> None:
    days = [version.started for version in JP_VERSIONS]
    assert days == sorted(days), "版本表必须按稼働日升序"
    names = [version.short for version in JP_VERSIONS]
    assert len(names) == len(set(names)), "版本短名不能重复"


def test_latest_is_mate() -> None:
    latest = latest_version()
    assert latest.short == "Mate"
    assert latest.name == "CHUNITHM Mate"
    assert latest.started == dt.date(2026, 7, 2)


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("2026-07-02", "Mate"),  # 稼働当天的曲子
        ("2026-07-03", "Mate"),
        ("2026-10-08", "Mate"),
        ("2026-07-01", "X-VERSE-X"),  # 稼働前一天还是上一版
        ("2025-12-11", "X-VERSE-X"),
        ("2025-12-10", "X-VERSE"),
        ("2025-07-16", "X-VERSE"),
        ("2025-07-15", "VERSE"),
        ("2024-12-12", "VERSE"),
        ("2015-07-16", "CHUNITHM"),
    ],
)
def test_version_for_release_date(release: str, expected: str) -> None:
    version = version_for(release)
    assert version is not None
    assert version.short == expected


@pytest.mark.parametrize("release", [None, "", "2010-01-01", "unknown", "2026-13-99"])
def test_version_for_unknown(release: str | None) -> None:
    assert version_for(release) is None


def test_version_name_helper() -> None:
    assert version_name("2026-10-08") == "Mate"
    assert version_name(None) is None


def test_accepts_datetime_string() -> None:
    """发行日期偶尔可能带时间部分，只取前 10 位。"""
    assert version_name("2026-10-08T00:00:00") == "Mate"
