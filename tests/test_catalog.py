"""曲库测试：加载、查询、版本/分类匹配。"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
import respx

from fastchu.errors import ConfigurationError
from fastchu.models import LevelIndex
from fastchu.services.catalog import SongCatalog, load_catalog_from_files

from .conftest import ALIAS_FIXTURE, SONG_FIXTURE, make_settings


def test_loads_fixture(catalog: SongCatalog) -> None:
    assert len(catalog) > 1000
    assert catalog.versions and catalog.genres
    assert catalog.aliases, "测试夹具应当带别名"


def test_fixture_has_note_data(catalog: SongCatalog) -> None:
    """夹具必须带物量，否则物量查询功能无法离线测试。"""
    assert catalog.has_note_data


def test_require_raises_for_missing(catalog: SongCatalog) -> None:
    with pytest.raises(KeyError):
        catalog.require(999_999_999)
    assert catalog.get(999_999_999) is None


def test_aliases_of(catalog: SongCatalog) -> None:
    with_alias = next((a for a in catalog.aliases if a.aliases), None)
    assert with_alias is not None
    assert catalog.aliases_of(with_alias.song_id) == with_alias.aliases
    assert catalog.aliases_of(999_999_999) == []


def test_chart_and_constant(catalog: SongCatalog) -> None:
    song = catalog.songs[0]
    chart = next(d for d in song.difficulties if d.has_constant)
    assert catalog.chart(song.id, chart.difficulty) is not None
    assert catalog.constant(song.id, chart.difficulty) == pytest.approx(chart.level_value)


def test_constant_is_none_for_worlds_end(catalog: SongCatalog) -> None:
    """WORLD'S END 定数为 0，应当视为「没有定数」。"""
    found = False
    for song in catalog.songs:
        if song.chart(LevelIndex.WORLDS_END) is not None:
            assert catalog.constant(song.id, LevelIndex.WORLDS_END) is None
            found = True
            break
    assert found, "夹具里应当有 WORLD'S END 谱面"


def test_charts_with_constant_defaults_to_master_ultima(catalog: SongCatalog) -> None:
    for song in catalog.songs[:50]:
        for chart in catalog.charts_with_constant(song):
            assert chart.difficulty in {LevelIndex.MASTER, LevelIndex.ULTIMA}
            assert chart.has_constant


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #


def test_current_version_is_highest_version_with_songs(catalog: SongCatalog) -> None:
    """当前版本 = **实际有曲目的**最高版本，不是版本列表的最大值。

    真实数据里版本列表已包含 X-VERSE-X(24500)，但曲目最新只到 X-VERSE(24000)；
    若取 24500，所有曲目都会被判为旧版本。
    """
    versions_with_songs = {s.version for s in catalog.songs if s.version}
    assert catalog.current_version() == max(versions_with_songs)
    assert catalog.declared_version() == max(v.version for v in catalog.versions)
    assert catalog.current_version() <= catalog.declared_version()


def test_current_version_ignores_announced_version_without_songs() -> None:
    """回归测试：版本列表里有更高版本、但没有任何曲目属于它时，不能当当前版本。"""
    catalog = SongCatalog.from_dict(
        {
            "songs": [
                {"id": 1, "title": "old", "version": 23000, "difficulties": []},
                {"id": 2, "title": "new", "version": 24000, "difficulties": []},
            ],
            "versions": [
                {"id": 1, "title": "CHUNITHM VERSE", "version": 23000},
                {"id": 2, "title": "CHUNITHM X-VERSE", "version": 24000},
                {"id": 3, "title": "CHUNITHM X-VERSE-X", "version": 24500},
            ],
            "genres": [],
        }
    )
    assert catalog.declared_version() == 24500
    assert catalog.current_version() == 24000
    assert catalog.is_current_version_song(2) is True
    assert catalog.is_current_version_song(1) is False


def test_current_version_falls_back_to_version_list_when_no_songs() -> None:
    catalog = SongCatalog.from_dict(
        {"songs": [], "versions": [{"id": 1, "title": "X", "version": 24000}], "genres": []}
    )
    assert catalog.current_version() == 24000


def test_version_title(catalog: SongCatalog) -> None:
    assert catalog.version_title(catalog.current_version())
    assert catalog.version_title(None) == "未知版本"
    assert "99999" in catalog.version_title(99999)


def test_match_versions_by_number(catalog: SongCatalog) -> None:
    hits = catalog.match_versions("24000")
    assert [v.version for v in hits] == [24000]


def test_match_versions_exact_beats_substring(catalog: SongCatalog) -> None:
    """``VERSE`` 不该把 ``X-VERSE`` 一起带出来。"""
    titles = {v.title for v in catalog.match_versions("VERSE")}
    assert titles == {"CHUNITHM VERSE"}


def test_match_versions_x_verse(catalog: SongCatalog) -> None:
    assert {v.title for v in catalog.match_versions("X-VERSE")} == {"CHUNITHM X-VERSE"}
    assert {v.title for v in catalog.match_versions("x-verse")} == {"CHUNITHM X-VERSE"}


def test_match_versions_prefix(catalog: SongCatalog) -> None:
    hits = catalog.match_versions("lum")
    assert hits
    assert all("LUMINOUS" in v.title for v in hits)


def test_match_versions_unknown(catalog: SongCatalog) -> None:
    assert catalog.match_versions("不存在的版本") == []
    assert catalog.match_versions("") == []


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #


def test_match_genres(catalog: SongCatalog) -> None:
    assert {g.genre for g in catalog.match_genres("原创")} == {"原创"}
    assert {g.genre for g in catalog.match_genres("东方")} == {"东方Project"}
    assert catalog.match_genres("不存在") == []
    assert catalog.match_genres("  ") == []


def test_genre_names(catalog: SongCatalog) -> None:
    assert catalog.genre_names() == [g.genre for g in catalog.genres]


# --------------------------------------------------------------------------- #
# 构造与容错
# --------------------------------------------------------------------------- #


def test_aliases_accept_dicts_and_models() -> None:
    """别名既接受字典也接受模型，与项目其它地方的 pydantic 用法一致。"""
    from fastchu.models import Alias

    payload = {"songs": [{"id": 1, "title": "x", "difficulties": []}], "genres": [], "versions": []}
    from_dicts = SongCatalog.from_dict(payload, [{"song_id": 1, "aliases": ["a"]}])
    from_models = SongCatalog.from_dict(payload, [Alias(song_id=1, aliases=["a"])])
    assert from_dicts.aliases_of(1) == from_models.aliases_of(1) == ["a"]


def test_summary(catalog: SongCatalog) -> None:
    text = catalog.summary()
    assert "曲库" in text and "别名" in text


def test_missing_file_raises_configuration_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ConfigurationError, match="曲库文件不存在"):
        load_catalog_from_files(tmp_path / "nope.json")


def test_empty_catalog() -> None:
    catalog = SongCatalog([])
    assert catalog.is_empty()
    assert catalog.current_version() == 0
    assert not catalog.has_note_data


def test_load_without_alias_file() -> None:
    catalog = load_catalog_from_files(SONG_FIXTURE, None)
    assert len(catalog) > 1000
    assert catalog.aliases == []


def test_fixture_files_exist() -> None:
    assert SONG_FIXTURE.is_file() and ALIAS_FIXTURE.is_file()


# --------------------------------------------------------------------------- #
# 曲库加载器：缓存命中时必须零网络
# --------------------------------------------------------------------------- #


@respx.mock
async def test_loader_hits_cache_without_network(tmp_path: pathlib.Path) -> None:
    """缓存新鲜时**绝不发起请求**——这是「首次抓取后零网络开销」的核心保证。"""
    from fastchu.api.client import LxnsClient
    from fastchu.services.catalog_loader import CatalogLoader

    route = respx.route(host="maimai.lxns.net")
    client = LxnsClient(min_interval=0.0, retries=0)
    loader = CatalogLoader(make_settings(tmp_path), client)

    catalog = await loader.get()

    assert len(catalog) > 1000
    assert not route.called, "缓存命中时不该发请求"
    await client.aclose()


@respx.mock
async def test_loader_fetches_when_cache_missing(tmp_path: pathlib.Path) -> None:
    from fastchu.api.client import LxnsClient
    from fastchu.services.catalog_loader import CatalogLoader

    payload = {"success": True, "code": 200, "data": {"songs": [], "genres": [], "versions": []}}
    songs_route = respx.get("https://maimai.lxns.net/api/v0/chunithm/song/list").mock(
        return_value=httpx.Response(200, json=payload)
    )
    respx.get("https://maimai.lxns.net/api/v0/chunithm/alias/list").mock(
        return_value=httpx.Response(200, json=payload)
    )

    settings = make_settings(tmp_path, song_cache_path=tmp_path / "missing.json")
    client = LxnsClient(min_interval=0.0, retries=0)
    await CatalogLoader(settings, client).get()

    assert songs_route.called
    await client.aclose()


@respx.mock
async def test_loader_refetches_when_notes_missing(tmp_path: pathlib.Path) -> None:
    """缓存里没有物量、但配置要求物量 → 必须重新抓，而不是让 /物量 静默返回空。"""
    from fastchu.api.client import LxnsClient
    from fastchu.services.catalog_loader import CatalogLoader

    # 造一个「有曲目但没有物量」的缓存
    no_notes = tmp_path / "song_list.json"
    no_notes.write_text(
        json.dumps(
            {
                "songs": [
                    {
                        "id": 1,
                        "title": "x",
                        "difficulties": [{"difficulty": 3, "level": "14", "level_value": 14.0}],
                    }
                ],
                "genres": [],
                "versions": [],
            }
        ),
        encoding="utf-8",
    )

    payload = {"success": True, "code": 200, "data": {"songs": [], "genres": [], "versions": []}}
    songs_route = respx.get("https://maimai.lxns.net/api/v0/chunithm/song/list").mock(
        return_value=httpx.Response(200, json=payload)
    )
    respx.get("https://maimai.lxns.net/api/v0/chunithm/alias/list").mock(
        return_value=httpx.Response(200, json=payload)
    )

    settings = make_settings(tmp_path, song_cache_path=no_notes, fetch_notes=True)
    client = LxnsClient(min_interval=0.0, retries=0)
    await CatalogLoader(settings, client).get()

    assert songs_route.called, "缺物量时应当重新抓取"
    await client.aclose()


@respx.mock
async def test_loader_keeps_cache_without_notes_when_disabled(tmp_path: pathlib.Path) -> None:
    """关掉 fetch_notes 时，没有物量的缓存就是可用的，不必重抓。"""
    from fastchu.api.client import LxnsClient
    from fastchu.services.catalog_loader import CatalogLoader

    no_notes = tmp_path / "song_list.json"
    no_notes.write_text(
        json.dumps(
            {
                "songs": [{"id": 1, "title": "x", "difficulties": []}],
                "genres": [],
                "versions": [],
            }
        ),
        encoding="utf-8",
    )
    route = respx.route(host="maimai.lxns.net")

    settings = make_settings(tmp_path, song_cache_path=no_notes, fetch_notes=False)
    client = LxnsClient(min_interval=0.0, retries=0)
    catalog = await CatalogLoader(settings, client).get()

    assert not route.called
    assert len(catalog) == 1
    await client.aclose()


@respx.mock
async def test_loader_falls_back_to_stale_cache_on_failure(tmp_path: pathlib.Path) -> None:
    """在线拉取失败时用本地缓存兜底，查歌不该被曲库接口拖垮。"""
    from fastchu.api.client import LxnsClient
    from fastchu.errors import LxnsServerError
    from fastchu.services.catalog_loader import CatalogLoader

    # 把夹具复制成"过期"缓存（ttl=0 → 视为过期）
    stale = tmp_path / "song_list.json"
    stale.write_text(SONG_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    respx.route(host="maimai.lxns.net").mock(
        return_value=httpx.Response(503, json={"success": False, "code": 503})
    )

    settings = make_settings(
        tmp_path, song_cache_path=stale, song_cache_ttl=0, allow_stale_cache=True
    )
    client = LxnsClient(min_interval=0.0, retries=0)
    catalog = await CatalogLoader(settings, client).get()

    assert len(catalog) > 1000, "应当回退到本地缓存"
    await client.aclose()
