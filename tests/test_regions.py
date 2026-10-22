"""国服 / 日服区域对照测试。

核心行为有两条：

1. **定数相同**时只显示一个值（轻量优先）；
2. **定数不同**时把两服都标出来，且日服推定值带 ``?``。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from fastchu.services.regions import (
    ChartCompare,
    RegionCatalog,
    RegionSong,
    load_region_catalog,
)


# --------------------------------------------------------------------------- #
# ChartCompare
# --------------------------------------------------------------------------- #


def test_const_display_same_value() -> None:
    """两服定数一致时只显示一个值，不啰嗦。"""
    chart = ChartCompare(name="MAS", cn_level="15+", cn_const=15.5, jp_level=15.5, jp_const=15.5)
    assert chart.const_display() == "15.5"
    assert chart.differs is False


def test_const_display_differs() -> None:
    """两服定数不同时必须同时标出，并标明哪边是哪服。"""
    chart = ChartCompare(name="MAS", cn_level="15+", cn_const=15.5, jp_level=15.6, jp_const=15.6)
    assert chart.differs is True
    assert chart.const_display() == "15.5→15.6"


def test_const_display_jp_estimated() -> None:
    """日服定数为推定值时加 ``?``，避免把推定值当成官方值。"""
    chart = ChartCompare(
        name="EXP", cn_level="10+", cn_const=10.8, jp_level=10.5, jp_const=10.5,
        jp_const_unknown=True,
    )
    assert chart.const_display() == "10.8→10.5?"


def test_const_display_integer_has_no_trailing_zero() -> None:
    chart = ChartCompare(name="MAS", cn_level="15", cn_const=15.0, jp_level=15.0, jp_const=15.0)
    assert chart.const_display() == "15"


def test_const_display_cn_only() -> None:
    chart = ChartCompare(name="MAS", cn_level="15", cn_const=15.0)
    assert chart.const_display() == "15"


def test_const_display_jp_only() -> None:
    chart = ChartCompare(name="MAS", jp_level=15.0, jp_const=15.0)
    assert chart.const_display() == "15日"


def test_we_never_compares_constants() -> None:
    """WE 用 ★ 难度、定数恒为 0，不该报出「定数不同」。"""
    chart = ChartCompare(name="WE", cn_const=0.0, jp_const=0.0)
    assert chart.differs is False


# --------------------------------------------------------------------------- #
# RegionSong
# --------------------------------------------------------------------------- #


def _song(**kwargs) -> RegionSong:
    base = {"title": "X", "regions": ["cn", "jp"]}
    base.update(kwargs)
    return RegionSong(**base)


def test_region_tags() -> None:
    assert _song().region_tag() == ""
    assert _song(regions=["jp"]).region_tag() == "仅日服"
    assert _song(regions=["cn"]).region_tag() == "仅国服"


def test_jp_only_and_cn_only() -> None:
    jp = _song(regions=["jp"])
    assert jp.jp_only and not jp.cn_only and jp.in_jp and not jp.in_cn
    cn = _song(regions=["cn"])
    assert cn.cn_only and not cn.jp_only


def test_differing_charts() -> None:
    song = _song(
        charts={
            "EXP": ChartCompare(name="EXP", cn_const=14.3, jp_const=14.2),
            "MAS": ChartCompare(name="MAS", cn_const=15.5, jp_const=15.5),
        }
    )
    assert [c.name for c in song.differing_charts()] == ["EXP"]


def test_ordered_charts_follows_difficulty_order() -> None:
    song = _song(
        charts={
            "ULT": ChartCompare(name="ULT"),
            "EXP": ChartCompare(name="EXP"),
            "BAS": ChartCompare(name="BAS"),
        }
    )
    assert [c.name for c in song.ordered_charts()] == ["BAS", "EXP", "ULT"]


# --------------------------------------------------------------------------- #
# RegionCatalog
# --------------------------------------------------------------------------- #


def _catalog() -> RegionCatalog:
    return RegionCatalog(
        [
            _song(title="Shared Song", cn_id=1, charts={}),
            _song(title="CN Only", cn_id=2, regions=["cn"]),
            _song(title="JP Only", cn_id=None, regions=["jp"]),
            _song(title="Shared Song WE", cn_id=3, regions=["cn", "jp"], kind="we"),
        ]
    )


def test_catalog_lookup_by_cn_id() -> None:
    catalog = _catalog()
    assert catalog.for_cn_id(1) is not None
    assert catalog.for_cn_id(2).cn_only is True
    assert catalog.for_cn_id(999) is None


def test_catalog_find_jp_only() -> None:
    catalog = _catalog()
    hits = catalog.find("JP Only", jp_only=True)
    assert hits and hits[0].jp_only
    assert catalog.find("Shared", jp_only=True) == []


def test_catalog_find_ranking() -> None:
    """完全匹配 > 前缀 > 包含。"""
    catalog = _catalog()
    hits = catalog.find("Shared Song")
    assert hits[0].title == "Shared Song"


def test_catalog_stats() -> None:
    stats = _catalog().stats()
    assert stats["total"] == 4
    assert stats["cn_only"] == 1
    assert stats["jp_only"] == 1


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #


def test_load_missing_file_returns_none(tmp_path: pathlib.Path) -> None:
    """数据不存在时优雅降级，不抛异常。"""
    assert load_region_catalog(tmp_path / "nope.json") is None


def test_load_broken_file_returns_none(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_region_catalog(path) is None


def test_load_real_payload(tmp_path: pathlib.Path) -> None:
    payload = {
        "meta": {"total": 2},
        "songs": [
            {
                "title": "A",
                "artist": "a",
                "bpm": 170,
                "kind": "song",
                "regions": ["cn", "jp"],
                "genres": {"cn": "原创", "jp": "ORIGINAL"},
                "cn_id": 464,
                "jp_id": "hash",
                "charts": {
                    "MAS": {
                        "cn": {"level": "15+", "const": 15.5},
                        "jp": {"level": 15.6, "const": 15.6, "const_unknown": False},
                        "const_differs": True,
                    }
                },
            },
            {
                "title": "B",
                "kind": "song",
                "regions": ["jp"],
                "cn_id": None,
                "jp_id": "hash2",
                "charts": {"EXP": {"cn": None, "jp": {"level": 14, "const": 14.0}}},
            },
        ],
    }
    path = tmp_path / "union.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    catalog = load_region_catalog(path)
    assert catalog is not None
    assert len(catalog) == 2

    song = catalog.for_cn_id(464)
    assert song is not None
    assert song.charts["MAS"].differs is True
    assert song.charts["MAS"].const_display() == "15.5→15.6"

    jp_only = catalog.find("B", jp_only=True)
    assert jp_only and jp_only[0].charts["EXP"].const_display() == "14日"


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def test_render_detail_shows_both_constants(catalog, tmp_path) -> None:
    """详情里定数不同时要同时标出两服。"""
    from fastchu.render import render_song_detail

    from .conftest import make_settings

    song = next(s for s in catalog.songs if s.difficulties)
    region = RegionSong(
        title=song.title,
        regions=["cn", "jp"],
        charts={
            "MAS": ChartCompare(name="MAS", cn_level="15+", cn_const=15.5, jp_const=15.6),
        },
    )
    text = render_song_detail(song, catalog, settings=make_settings(tmp_path), region=region)
    if song.chart(__import__("fastchu.models", fromlist=["LevelIndex"]).LevelIndex.MASTER):
        assert "15.5→15.6" in text


def test_render_detail_without_region_is_plain(catalog, tmp_path) -> None:
    from fastchu.render import render_song_detail

    from .conftest import make_settings

    song = next(s for s in catalog.songs if s.chart(__import__("fastchu.models", fromlist=["LevelIndex"]).LevelIndex.MASTER))
    text = render_song_detail(song, catalog, settings=make_settings(tmp_path))
    assert "国/" not in text
    assert "日" not in text.replace("日服", "") or "→" in text
    assert "日" not in text.replace(song.title, "")


# --------------------------------------------------------------------------- #
# 命令层：日服独有曲回退
# --------------------------------------------------------------------------- #


def _union_payload() -> dict:
    return {
        "meta": {"total": 1},
        "songs": [
            {
                "title": "JP Only Song",
                "artist": "jp artist",
                "bpm": 190,
                "kind": "song",
                "regions": ["jp"],
                "genres": {"cn": "原创", "jp": "ORIGINAL"},
                "cn_id": None,
                "jp_id": "hash",
                "jp_release": "2026-03-15",
                "jp_idx": 3046,
                "jp_img": "396226dcdcbdf45d",
                "charts": {
                    "EXP": {"cn": None, "jp": {"level": 10.1, "const": 10.1, "const_unknown": True}},
                    "MAS": {"cn": None, "jp": {"level": 13.6, "const": 13.6, "const_unknown": False}},
                },
            }
        ],
    }


async def test_jp_extra_difficulty_is_shown(tmp_path: pathlib.Path) -> None:
    """日服追加上、国服没有的难度（如 JP 先行 ULTIMA）要显示出来。"""
    from fastchu.models import LevelIndex, Song
    from fastchu.render import render_song_detail
    from fastchu.services.catalog import SongCatalog
    from fastchu.services.regions import _parse_song

    catalog = SongCatalog.from_dict(
        {
            "songs": [
                {
                    "id": 145,
                    "title": "Test Song",
                    "artist": "a",
                    "genre": "彩绿",
                    "bpm": 200,
                    "version": 20000,
                    "difficulties": [
                        {"difficulty": 2, "level": "8+", "level_value": 8.5},
                        {"difficulty": 3, "level": "12", "level_value": 12.0},
                    ],
                }
            ],
            "genres": [{"id": 1, "genre": "彩绿"}],
            "versions": [{"id": 1, "title": "CHUNITHM VERSE", "version": 20000}],
        }
    )
    song = catalog.get(145)
    assert song is not None
    region = _parse_song(
        {
            "title": "Test Song",
            "regions": ["cn", "jp"],
            "cn_id": 145,
            "jp_id": "hash",
            "charts": {
                "EXP": {"cn": {"level": 8.5, "const": 8.5}, "jp": {"level": 8.5, "const": 8.5}},
                "MAS": {"cn": {"level": 12, "const": 12.3}, "jp": {"level": 12, "const": 12.3}},
                "ULT": {"cn": None, "jp": {"level": 14, "const": 14.5}},
            },
        }
    )
    text = render_song_detail(song, catalog, region=region)
    assert "ULT 14.5（日服）" in text
    assert "EXP" in text and "MAS" in text


async def test_jp_only_jacket_downloaded_with_referer(tmp_path: pathlib.Path) -> None:
    """日服曲绘必须由机器人带 Referer 下载后内联。

    unidb 对没有 Referer 的请求统一返回占位图（粉紫「呪文」），
    所以直接把 URL 交给 QQ 是不行的。
    """
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    union = tmp_path / "union.json"
    union.write_text(json.dumps(_union_payload()), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union, jacket_mode="url")
    service = SongService(settings)
    calls: list[dict] = []

    async def fake_download(url, *, max_bytes=None, headers=None):
        calls.append({"url": url, "headers": headers or {}})
        return b"\x89PNG\r\n\x1a\nfake"

    from fastchu.services import commands as cmds

    cmds._JP_JACKET_CACHE.clear()
    service.client.download = fake_download  # type: ignore[method-assign]
    try:
        reply = await run_lookup(service, settings, "JP Only Song")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert calls, "应当由机器人自己下载曲绘"
    assert calls[0]["headers"].get("Referer") == "https://db.chunirec.net/"
    assert reply.jacket is not None
    assert reply.jacket.has_data, "必须内联数据，不能只给 URL"
    assert reply.jacket.url.endswith("396226dcdcbdf45d.jpg")


async def test_jp_jacket_failure_sends_no_image(tmp_path: pathlib.Path) -> None:
    """下载失败就不配图——发一张占位图比没有图更糟。"""
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    union = tmp_path / "union.json"
    union.write_text(json.dumps(_union_payload()), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union, jacket_mode="url")
    service = SongService(settings)

    async def boom(*args, **kwargs):
        raise RuntimeError("网络挂了")

    from fastchu.services import commands as cmds

    cmds._JP_JACKET_CACHE.clear()
    service.client.download = boom  # type: ignore[method-assign]
    try:
        reply = await run_lookup(service, settings, "JP Only Song")
    finally:
        await service.aclose()

    assert reply.jacket is None


async def test_jp_jacket_disabled_when_off(tmp_path: pathlib.Path) -> None:
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    union = tmp_path / "union.json"
    union.write_text(json.dumps(_union_payload()), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union, jacket_mode="off")
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "JP Only Song")
    finally:
        await service.aclose()

    assert reply.jacket is None


async def test_lookup_jp_only_by_id(tmp_path: pathlib.Path) -> None:
    """日服独有曲目也能用 #ID 查。"""
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    union = tmp_path / "union.json"
    union.write_text(json.dumps(_union_payload()), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "#3046")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert "JP Only Song" in reply.text


async def test_region_detail_skips_basic_advanced(tmp_path: pathlib.Path) -> None:
    """日服详情与国服同口径：不列 BAS/ADV。"""
    from fastchu.services.commands import run_lookup
    from fastchu.services.regions import _parse_song
    from fastchu.render import render_region_song_detail

    payload = _union_payload()
    payload["songs"][0]["charts"]["BAS"] = {
        "cn": None,
        "jp": {"level": 4, "const": 4.0},
    }
    payload["songs"][0]["charts"]["ADV"] = {
        "cn": None,
        "jp": {"level": 8, "const": 8.0},
    }
    song = _parse_song(payload["songs"][0])
    text = render_region_song_detail(song)
    assert "BAS" not in text
    assert "ADV" not in text
    assert "EXP" in text


async def test_lookup_falls_back_to_jp_only(tmp_path: pathlib.Path) -> None:
    """国服搜不到、日服有时，应当给出「仅日服」的详情，而不是「没找到」。"""
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    union = tmp_path / "union.json"
    union.write_text(json.dumps(_union_payload()), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "JP Only Song")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert "仅日服" in reply.text
    assert "原创" in reply.text          # 分类已映射成国服写法
    assert "EXP 10.1?" in reply.text     # 日服推定定数带 ?
    assert "MAS 13.6" in reply.text
    # 日服自己有谱面 ID（与国服同一套编号）
    assert "#3046" in reply.text


async def test_lookup_jp_fallback_candidates(tmp_path: pathlib.Path) -> None:
    """日服命中多首时给候选，而不是硬猜一首。"""
    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    payload = _union_payload()
    payload["songs"].append(
        {
            "title": "JP Only Song 2",
            "artist": "another",
            "kind": "song",
            "regions": ["jp"],
            "cn_id": None,
            "charts": {"MAS": {"cn": None, "jp": {"level": 13, "const": 13.0}}},
        }
    )
    union = tmp_path / "union.json"
    union.write_text(json.dumps(payload), encoding="utf-8")
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        # 用前缀查询：两首都命中、且都不是完全同名 → 应当给候选而不是硬猜
        reply = await run_lookup(service, settings, "JP Only")
    finally:
        await service.aclose()

    assert reply.kind == "region_candidates"
    assert "仅日服" in reply.text


async def test_lookup_without_region_data_still_reports_miss(tmp_path: pathlib.Path) -> None:
    """没有并集数据时，日服回退整体跳过，行为与以前一致。"""
    from fastchu.services.commands import MISS_HINT, run_lookup
    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    settings = make_settings(tmp_path)  # region_cache_path 指向不存在的文件
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "JP Only Song")
    finally:
        await service.aclose()

    assert reply.kind == "hint"
    assert reply.texts == [MISS_HINT]


def test_we_chart_never_shows_a_constant(catalog, tmp_path) -> None:
    """回归测试：WE 用 ★ 难度、没有数值定数，区域对照不能让 ``(0)`` 冒出来。

    早期版本在 ``_difficulty_line`` 里无条件用区域数据覆盖定数，
    结果 WE 谱面渲染成 ``WE ★5(0)``。
    """
    from fastchu.models import LevelIndex
    from fastchu.render import render_song_detail

    from .conftest import make_settings

    we_song = next(s for s in catalog.songs if s.chart(LevelIndex.WORLDS_END) is not None)
    region = RegionSong(
        title=we_song.title,
        regions=["cn", "jp"],
        kind="we",
        charts={"WE": ChartCompare(name="WE", cn_const=0.0, jp_const=0.0)},
    )
    text = render_song_detail(we_song, catalog, settings=make_settings(tmp_path), region=region)
    assert "(0)" not in text
    # WE 已不进谱面列表：既不能冒出 (0)，也不该出现 WE ★
    assert "WE" not in text
