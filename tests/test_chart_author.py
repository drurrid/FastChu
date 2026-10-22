"""/谁写的：查某个难度的谱师。"""

from __future__ import annotations

import json
import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.models import LevelIndex
from fastchu.services.commands import (
    run_chart_author,
    run_query,
    split_chart_difficulty,
)
from fastchu.services.selection import SelectionStore
from fastchu.services.song_service import SongService

from .conftest import make_settings

UNION_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "songs" / "songs.union.json"


@asynccontextmanager
async def make_service(tmp_path: pathlib.Path, *, real_region: bool = False):
    kwargs = {"region_cache_path": UNION_PATH} if real_region else {}
    settings = make_settings(tmp_path, **kwargs)
    service = SongService(settings)
    try:
        yield service
    finally:
        await service.aclose()


def test_split_difficulty_suffixes() -> None:
    assert split_chart_difficulty("四次元紫") == ("四次元", LevelIndex.MASTER)
    assert split_chart_difficulty("四次元 MAS") == ("四次元", LevelIndex.MASTER)
    assert split_chart_difficulty("四次元mas") == ("四次元", LevelIndex.MASTER)
    assert split_chart_difficulty("四次元红") == ("四次元", LevelIndex.EXPERT)
    assert split_chart_difficulty("四次元EXP") == ("四次元", LevelIndex.EXPERT)
    assert split_chart_difficulty("四次元黑") == ("四次元", LevelIndex.ULTIMA)
    assert split_chart_difficulty("四次元") == ("四次元", None)
    # 只有难度、没有歌名时不当难度处理
    assert split_chart_difficulty("紫") == ("紫", None)


async def test_author_with_difficulty(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        catalog = await service.catalog()
        song = None
        for candidate in catalog.songs:
            chart = candidate.chart(LevelIndex.MASTER)
            if chart is None or not chart.note_designer:
                continue
            # 曲名要能唯一定位，否则会走「多目标 → 列候选」那条路
            found = await service.find(candidate.title, limit=service.settings.search_result_limit)
            if found.song is not None:
                song = candidate
                break
        if song is None:
            pytest.skip("曲库里没有能唯一定位、带 MASTER 谱师的曲目")

        reply = await run_chart_author(service, service.settings, f"{song.title} MAS")

    assert reply.kind == "author"
    expected = song.chart(LevelIndex.MASTER).note_designer
    assert reply.text == f"{expected}写的"


async def test_author_defaults_to_highest_difficulty(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        catalog = await service.catalog()
        song = None
        for candidate in catalog.songs:
            chart = candidate.chart(LevelIndex.MASTER)
            if (
                chart is None
                or not chart.note_designer
                or candidate.chart(LevelIndex.ULTIMA) is not None
            ):
                continue
            found = await service.find(
                candidate.title, limit=service.settings.search_result_limit
            )
            if found.song is not None:
                song = candidate
                break
        if song is None:
            pytest.skip("找不到能唯一定位、只有 MASTER 的曲目")

        reply = await run_chart_author(service, service.settings, song.title)

    assert reply.kind == "author"
    assert reply.text.endswith("写的")


async def test_author_unknown_song(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_chart_author(service, service.settings, "绝对不存在的歌zzz")

    assert reply.kind == "hint"


async def test_author_empty_gives_hint(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_chart_author(service, service.settings, "")

    assert reply.kind == "hint"
    assert "谁写的" in reply.text


def test_help_lists_author_command() -> None:
    from fastchu.render import HELP_TEXT

    assert "/谁写的" in HELP_TEXT


async def test_author_jp_only_song(tmp_path: pathlib.Path) -> None:
    """日服独有曲目也要能查谱师（数据来自 wiki「譜面制作者一覧」）。"""
    if not UNION_PATH.is_file():
        pytest.skip("缺少并集数据")
    async with make_service(tmp_path, real_region=True) as service:
        region = await service.regions()
        if region is None:
            pytest.skip("没有并集数据")
        target = next(
            (
                song
                for song in region.songs
                if song.jp_only
                and any(chart.jp_designer for chart in song.charts.values())
            ),
            None,
        )
        if target is None:
            pytest.skip("并集里没有带日服谱师的曲目")

        reply = await run_chart_author(service, service.settings, target.title)

    assert reply.kind == "author"
    assert reply.text.endswith("写的")


async def test_author_jp_difficulty_suffix(tmp_path: pathlib.Path) -> None:
    """日服曲目 + 难度后缀（如「紫」）也能查。"""
    if not UNION_PATH.is_file():
        pytest.skip("缺少并集数据")
    async with make_service(tmp_path, real_region=True) as service:
        region = await service.regions()
        if region is None:
            pytest.skip("没有并集数据")
        target = next(
            (
                song
                for song in region.songs
                if song.jp_only
                and (chart := song.charts.get("MAS"))
                and chart.jp_designer
            ),
            None,
        )
        if target is None:
            pytest.skip("没有带 MAS 谱师的日服曲目")

        reply = await run_chart_author(service, service.settings, f"{target.title}紫")

    assert reply.kind == "author"
    assert reply.text == f"{target.charts['MAS'].jp_designer}写的"


async def test_author_jp_only_song_by_alias(tmp_path: pathlib.Path) -> None:
    """日服独有曲目只记得别名时，/谁写的 也要能找到（如「左下爱」）。"""
    union = tmp_path / "union.json"
    union.write_text(
        json.dumps(
            {
                "songs": [
                    {
                        "title": "JP Only Song",
                        "artist": "jp artist",
                        "kind": "song",
                        "regions": ["jp"],
                        "cn_id": None,
                        "jp_id": "hash",
                        "jp_idx": 3046,
                        "charts": {
                            "MAS": {
                                "cn": None,
                                "jp": {
                                    "level": 15,
                                    "const": 15.1,
                                    "designer": "ロシェ＠ペンギン",
                                },
                            }
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    local = tmp_path / "local.json"
    local.write_text(
        json.dumps({"jp": {"3046": ["左下爱"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    settings = make_settings(
        tmp_path, region_cache_path=union, local_alias_path=local
    )
    service = SongService(settings)
    try:
        reply = await run_chart_author(service, settings, "左下爱")
    finally:
        await service.aclose()

    assert reply.kind == "author"
    assert reply.text == "ロシェ＠ペンギン写的"
    assert reply.extra["difficulty"] == "MAS"


async def test_author_jp_only_difficulty_on_both_region_song(tmp_path: pathlib.Path) -> None:
    """国服只到 MASTER、ULTIMA 只在日服的曲目，``<歌名>黑`` 要落到日服谱师。"""
    if not UNION_PATH.is_file():
        pytest.skip("缺少并集数据")
    async with make_service(tmp_path, real_region=True) as service:
        region = await service.regions()
        if region is None:
            pytest.skip("没有并集数据")
        target = next(
            (
                song
                for song in region.songs
                if song.in_cn
                and (ult := song.charts.get("ULT"))
                and ult.jp_designer
                and not ult.cn_designer
            ),
            None,
        )
        if target is None:
            pytest.skip("并集里没有「国服缺 ULTIMA」的曲目")

        reply = await run_chart_author(service, service.settings, f"{target.title}黑")

    assert reply.kind == "author"
    assert reply.text == f"{target.charts['ULT'].jp_designer}写的"


async def test_author_default_takes_highest_across_regions(tmp_path: pathlib.Path) -> None:
    """不给难度时，日服 ULTIMA 比国服 MASTER 高，默认应取 ULTIMA 的谱师。"""
    if not UNION_PATH.is_file():
        pytest.skip("缺少并集数据")
    async with make_service(tmp_path, real_region=True) as service:
        region = await service.regions()
        if region is None:
            pytest.skip("没有并集数据")
        target = next(
            (
                song
                for song in region.songs
                if song.in_cn
                and (ult := song.charts.get("ULT"))
                and ult.jp_designer
                and ult.jp_const is not None
                and (mas := song.charts.get("MAS"))
                and mas.jp_const is not None
                and ult.jp_const > mas.jp_const
            ),
            None,
        )
        if target is None:
            pytest.skip("并集里没有合适的曲目")

        reply = await run_chart_author(service, service.settings, target.title)

    assert reply.kind == "author"
    expected = (target.charts["ULT"].cn_designer or target.charts["ULT"].jp_designer) or ""
    assert reply.text == f"{expected.strip()}写的"


#: ``a`` 在随包夹具里命中多首（8 首），用来测「多目标 → 选序号」
MULTI_KEYWORD = "a"
SELECTION_KEY = "test:user"


async def test_author_multiple_matches_lists_candidates(tmp_path: pathlib.Path) -> None:
    """``/谁写的`` 命中多首时，和查歌一样先列候选。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        reply = await run_chart_author(
            service,
            service.settings,
            MULTI_KEYWORD,
            selections=store,
            key=SELECTION_KEY,
        )

        assert reply.kind == "candidates"
        assert len(reply.candidates) > 1
        assert store.peek(SELECTION_KEY) is not None
        assert reply.text.rstrip().endswith("回序号看谱师")


async def test_author_selection_resolves_to_author(tmp_path: pathlib.Path) -> None:
    """列完候选后回 ``1``，直接出那首的谱师（而不是曲目详情）。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        listed = await run_chart_author(
            service,
            service.settings,
            MULTI_KEYWORD,
            selections=store,
            key=SELECTION_KEY,
        )
        assert listed.kind == "candidates"
        first = listed.candidates[0]
        chosen = first.song

        reply = await run_query(
            service, service.settings, "1", selections=store, key=SELECTION_KEY
        )

        assert reply.kind == "author"
        assert reply.song is not None and chosen is not None
        assert reply.song.id == chosen.id


async def test_author_selection_keeps_requested_difficulty(tmp_path: pathlib.Path) -> None:
    """带难度列候选，选中后仍然查那个难度的谱师。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        listed = await run_chart_author(
            service,
            service.settings,
            "a紫",
            selections=store,
            key=SELECTION_KEY,
        )
        assert listed.kind == "candidates"

        target = None
        index = 0
        for position, item in enumerate(listed.candidates, start=1):
            song = item.song
            if song is None:
                continue
            chart = song.chart(LevelIndex.MASTER)
            if chart is not None and chart.note_designer:
                target = (song, chart.note_designer)
                index = position
                break
        if target is None:
            pytest.skip("候选里没有带 MASTER 谱师的曲目")

        reply = await run_query(
            service,
            service.settings,
            str(index),
            selections=store,
            key=SELECTION_KEY,
        )

    assert reply.kind == "author"
    assert reply.text == f"{target[1]}写的"


async def test_author_single_hit_clears_stale_candidates(tmp_path: pathlib.Path) -> None:
    """再发一次查不到的命令时，旧候选要作废，避免回错序号。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        await run_chart_author(
            service,
            service.settings,
            MULTI_KEYWORD,
            selections=store,
            key=SELECTION_KEY,
        )
        assert store.peek(SELECTION_KEY) is not None

        await run_chart_author(
            service,
            service.settings,
            "绝对不可能存在的歌zzz",
            selections=store,
            key=SELECTION_KEY,
        )

    assert store.peek(SELECTION_KEY) is None


