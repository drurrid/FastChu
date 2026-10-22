"""别名 overlay 与 /添加别名 测试。"""

from __future__ import annotations

import json
import pathlib

from fastchu.services.alias_overlay import (
    append_local_alias,
    load_alias_overlay,
    merge_aliases,
)
from fastchu.services.catalog import load_catalog_from_files
from fastchu.services.commands import (
    ADD_ALIAS_HINT,
    run_add_alias,
    run_lookup,
    run_query,
)
from fastchu.services.regions import RegionCatalog, RegionSong, load_region_catalog
from fastchu.services.search import SongSearch
from fastchu.services.selection import SelectionStore
from fastchu.services.song_service import SongService

from .conftest import ALIAS_FIXTURE, SONG_FIXTURE, make_settings


def _write(path: pathlib.Path, payload: dict) -> pathlib.Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _union_payload() -> dict:
    return {
        "meta": {"total": 2},
        "songs": [
            {
                "title": "JP Only Song",
                "artist": "jp artist",
                "kind": "song",
                "regions": ["jp"],
                "cn_id": None,
                "jp_id": "hash",
                "jp_idx": 3046,
                "charts": {"MAS": {"cn": None, "jp": {"level": 13.6, "const": 13.6}}},
            },
            {
                "title": "JP Second",
                "artist": "another",
                "kind": "song",
                "regions": ["jp"],
                "cn_id": None,
                "jp_id": "hash2",
                "jp_idx": 3047,
                "charts": {"MAS": {"cn": None, "jp": {"level": 12.0, "const": 12.0}}},
            },
        ],
    }


# --------------------------------------------------------------------------- #
# overlay 读取
# --------------------------------------------------------------------------- #


def test_load_overlay_merges_files_and_dedupes(tmp_path: pathlib.Path) -> None:
    community = _write(
        tmp_path / "community.json",
        {"cn": {"3": ["哔哔开", "bk"]}, "jp": {"3046": ["JP别名"]}},
    )
    local = _write(
        tmp_path / "local.json",
        {"cn": {"3": ["哔哔开", "新别名"]}, "jp": {"3046": ["第二个"]}},
    )

    overlay = load_alias_overlay([community, local])
    assert overlay.cn[3] == ["哔哔开", "bk", "新别名"]
    assert overlay.jp[3046] == ["JP别名", "第二个"]


def test_load_overlay_skips_broken_file(tmp_path: pathlib.Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    good = _write(tmp_path / "good.json", {"cn": {"3": ["ok"]}})

    overlay = load_alias_overlay([broken, good])
    assert overlay.cn == {3: ["ok"]}


def test_merge_aliases_skips_official_duplicates() -> None:
    catalog = load_catalog_from_files(SONG_FIXTURE, ALIAS_FIXTURE)
    official = [
        item for item in catalog.aliases if item.song_id == 3
    ]
    assert official

    from fastchu.services.alias_overlay import AliasOverlay

    merged = merge_aliases(official, AliasOverlay(cn={3: ["bk", "哔哔开"]}))
    assert merged[0].aliases.count("bk") == 1
    assert "哔哔开" in merged[0].aliases


def test_catalog_loads_overlay_and_search_finds_alias(tmp_path: pathlib.Path) -> None:
    overlay = _write(tmp_path / "overlay.json", {"cn": {"3": ["哔哔开"]}})
    catalog = load_catalog_from_files(SONG_FIXTURE, ALIAS_FIXTURE, [overlay])
    search = SongSearch(catalog)

    matches = search.search("哔哔开")
    assert matches and matches[0].song.id == 3
    assert "哔哔开" in catalog.aliases_of(3)


# --------------------------------------------------------------------------- #
# 日服别名检索
# --------------------------------------------------------------------------- #


def test_region_catalog_finds_alias() -> None:
    catalog = RegionCatalog(
        [
            RegionSong(title="Alpha", regions=["jp"], jp_idx=1, aliases=["阿尔法"]),
            RegionSong(title="Alpha Two", regions=["jp"], jp_idx=2, aliases=["阿尔法二号"]),
        ]
    )

    hits = catalog.find("阿尔法", jp_only=True)
    assert [song.jp_idx for song in hits] == [1, 2]
    assert [song.jp_idx for song in catalog.find_exact("阿尔法", jp_only=True)] == [1]


def test_region_title_still_outranks_alias() -> None:
    catalog = RegionCatalog(
        [
            RegionSong(title="Beta", regions=["jp"], jp_idx=1, aliases=["α"]),
            RegionSong(title="α", regions=["jp"], jp_idx=2),
        ]
    )
    hits = catalog.find("α", jp_only=True)
    assert hits[0].jp_idx == 2  # 曲名完全匹配优先于别名


def test_load_region_catalog_applies_alias_overlay(tmp_path: pathlib.Path) -> None:
    union = _write(tmp_path / "union.json", _union_payload())
    overlay = _write(tmp_path / "overlay.json", {"jp": {"3046": ["日服别名"]}})

    catalog = load_region_catalog(union, [overlay])
    assert catalog is not None
    hits = catalog.find("日服别名", jp_only=True)
    assert hits and hits[0].jp_idx == 3046


async def test_lookup_finds_jp_only_by_alias(tmp_path: pathlib.Path) -> None:
    union = _write(tmp_path / "union.json", _union_payload())
    overlay = _write(tmp_path / "overlay.json", {"jp": {"3046": ["日服别名"]}})
    settings = make_settings(
        tmp_path,
        region_cache_path=union,
        community_alias_path=overlay,
    )
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "日服别名")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert "JP Only Song" in reply.text
    assert "日服别名" not in reply.text  # 别名只参与检索，不进详情


async def test_numeric_jp_alias_beats_jp_id(tmp_path: pathlib.Path) -> None:
    """日服独有曲目的数字别名优先于同号谱面 ID。"""
    payload = _union_payload()
    payload["songs"][0]["aliases"] = ["3047"]  # 3047 是另一首日服曲目的 ID
    union = _write(tmp_path / "union.json", payload)
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "3047")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert "#3046 - JP Only Song" in reply.text


async def test_numeric_jp_alias_beats_cn_id(tmp_path: pathlib.Path) -> None:
    """国服恰好有同号曲目时，日服独有曲目的别名仍然优先。"""
    payload = _union_payload()
    payload["songs"][0]["aliases"] = ["19"]  # 国服 19 是 DRAGONLADY
    union = _write(tmp_path / "union.json", payload)
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "19")
    finally:
        await service.aclose()

    assert reply.kind == "region_detail"
    assert "#3046 - JP Only Song" in reply.text


# --------------------------------------------------------------------------- #
# 纯数字别名与 ID / 序号
# --------------------------------------------------------------------------- #


async def test_numeric_alias_beats_song_id(tmp_path: pathlib.Path) -> None:
    """纯数字别名优先于同号曲目 ID；显式 #ID 不受影响。"""
    overlay = _write(tmp_path / "overlay.json", {"cn": {"3": ["19"]}})
    settings = make_settings(tmp_path, community_alias_path=overlay)
    service = SongService(settings)
    try:
        reply = await run_lookup(service, settings, "19")
        assert reply.kind == "detail"
        assert reply.song is not None and reply.song.id == 3

        explicit = await run_lookup(service, settings, "#19")
        assert explicit.kind == "detail"
        assert explicit.song is not None and explicit.song.id == 19
    finally:
        await service.aclose()


async def test_numeric_alias_beats_pending_index(tmp_path: pathlib.Path) -> None:
    """有待选候选时，纯数字别名优先于序号；#N 仍可明确选序号。"""
    overlay = _write(tmp_path / "overlay.json", {"cn": {"3": ["2"]}})
    settings = make_settings(tmp_path, community_alias_path=overlay)
    service = SongService(settings)
    store = SelectionStore()
    try:
        first = await run_query(
            service, settings, "world", selections=store, key="u1"
        )
        assert first.kind == "candidates"
        second_item = first.candidates[1]
        assert second_item.song is not None and second_item.song.id != 3

        picked = await run_query(service, settings, "2", selections=store, key="u1")
        assert picked.kind == "detail"
        assert picked.song is not None and picked.song.id == 3
        assert store.peek("u1") is None

        # 再来一次，用 #2 明确按序号选择
        first = await run_query(
            service, settings, "world", selections=store, key="u1"
        )
        assert first.kind == "candidates"
        second_item = first.candidates[1]
        picked = await run_query(service, settings, "#2", selections=store, key="u1")
        assert picked.kind == "detail"
        assert picked.song is not None and picked.song.id == second_item.song.id
    finally:
        await service.aclose()


# --------------------------------------------------------------------------- #
# /添加别名
# --------------------------------------------------------------------------- #


async def test_add_alias_to_cn_song(tmp_path: pathlib.Path) -> None:
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        reply = await run_add_alias(service, "#3 哔哔开")
        assert reply.kind == "alias"
        assert "B.B.K.K.B.K.K." in reply.text

        search = await service.search_index()
        matches = search.search("哔哔开")
        assert matches and matches[0].song.id == 3
    finally:
        await service.aclose()

    saved = json.loads(settings.local_alias_path.read_text(encoding="utf-8"))
    assert "哔哔开" in saved["cn"]["3"]


async def test_added_alias_persists_for_new_service(tmp_path: pathlib.Path) -> None:
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        await run_add_alias(service, "#3 持久别名")
    finally:
        await service.aclose()

    reloaded = SongService(settings)
    try:
        search = await reloaded.search_index()
        matches = search.search("持久别名")
        assert matches and matches[0].song.id == 3
    finally:
        await reloaded.aclose()


async def test_add_alias_to_jp_only_song(tmp_path: pathlib.Path) -> None:
    union = _write(tmp_path / "union.json", _union_payload())
    settings = make_settings(tmp_path, region_cache_path=union)
    service = SongService(settings)
    try:
        reply = await run_add_alias(service, "#3046 日服新别名")
        assert reply.kind == "alias"
        assert "仅日服" in reply.text

        regions = await service.regions()
        assert regions is not None
        hits = regions.find_exact("日服新别名", jp_only=True)
        assert hits and hits[0].jp_idx == 3046
    finally:
        await service.aclose()

    saved = json.loads(settings.local_alias_path.read_text(encoding="utf-8"))
    assert "日服新别名" in saved["jp"]["3046"]


async def test_add_alias_rejects_other_songs_alias(tmp_path: pathlib.Path) -> None:
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        reply = await run_add_alias(service, "#6 bk")
    finally:
        await service.aclose()

    assert reply.kind == "hint"
    assert "已被" in reply.text


async def test_add_alias_usage_and_validation(tmp_path: pathlib.Path) -> None:
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        empty = await run_add_alias(service, "")
        assert empty.kind == "hint"
        assert empty.texts == [ADD_ALIAS_HINT]

        single = await run_add_alias(service, "#3 长")
        assert single.kind == "hint"
        assert "单字" in single.text
    finally:
        await service.aclose()


async def test_add_numeric_alias_is_searchable(tmp_path: pathlib.Path) -> None:
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        reply = await run_add_alias(service, "#3 9999")
        assert reply.kind == "alias"
        assert "纯数字别名会优先" in reply.text

        found = await run_lookup(service, settings, "9999")
        assert found.kind == "detail"
        assert found.song is not None and found.song.id == 3
    finally:
        await service.aclose()


async def test_append_local_alias_raises_on_broken_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "local.json"
    path.write_text("{broken", encoding="utf-8")
    try:
        append_local_alias(path, "cn", 3, ["新别名"])
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("损坏的本地文件不应被静默覆盖")
