"""分页 ``pN`` 与精准后缀 ``c``。"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.render import render_candidate_page
from fastchu.services.commands import run_lookup, run_query, split_exact
from fastchu.services.selection import (
    SelectionItem,
    SelectionStore,
    parse_page_target,
)
from fastchu.services.song_service import SongService

from .conftest import make_settings


@asynccontextmanager
async def make_service(tmp_path: pathlib.Path):
    settings = make_settings(tmp_path)
    service = SongService(settings)
    try:
        yield service
    finally:
        await service.aclose()


# ---------------------------------------------------------------- pN 分页
def test_parse_page_target() -> None:
    assert parse_page_target("p1") == 0
    assert parse_page_target("P2") == 1
    assert parse_page_target("ｐ３") == 2
    assert parse_page_target("p10") == 9
    assert parse_page_target("3") is None
    assert parse_page_target("下一页") is None
    assert parse_page_target("") is None


def test_footer_shows_p_pages() -> None:
    items = [SelectionItem(kind="song", label=f"曲{i}") for i in range(20)]
    text = render_candidate_page("kw", items, page=0, per_page=8)
    assert "p2" in text.splitlines()[-1]
    # 页数少时把页码全列出来
    small = render_candidate_page("kw", items[:10], page=0, per_page=8)
    assert "p1" in small.splitlines()[-1] and "p2" in small.splitlines()[-1]


async def test_pn_jumps_to_page(tmp_path: pathlib.Path) -> None:
    """``p2`` 直接跳到第 2 页。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        key = "u1"
        first = await run_query(service, service.settings, "a", selections=store, key=key)
        if first.kind != "candidates" or "1/" not in first.text:
            pytest.skip("这条查询没有多页结果")

        second = await run_query(service, service.settings, "p2", selections=store, key=key)

    assert second.kind == "candidates"
    assert "（2/" in second.text


async def test_pn_out_of_range(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        key = "u1"
        first = await run_query(service, service.settings, "a", selections=store, key=key)
        if first.kind != "candidates":
            pytest.skip("这条查询没有多页结果")

        reply = await run_query(service, service.settings, "p99", selections=store, key=key)

    assert reply.kind == "hint"
    assert "页" in reply.text


# ---------------------------------------------------------------- 精准后缀 c
def test_split_exact() -> None:
    assert split_exact("cr c") == ("cr", True)
    assert split_exact("cr C") == ("cr", True)
    assert split_exact("cr") == ("cr", False)
    assert split_exact("cr c c") == ("cr c", True)
    assert split_exact("") == ("", False)


async def test_exact_alias_hits_single_song(tmp_path: pathlib.Path) -> None:
    """``/bk c`` 只认别名为 bk 的那首，不做模糊扩展。"""
    async with make_service(tmp_path) as service:
        catalog = await service.catalog()
        # bk 是 B.B.K.K.B.K.K. 的别名（真实数据）
        target = next(
            (s for s in catalog.songs if any(a.lower() == "bk" for a in catalog.aliases_of(s.id))),
            None,
        )
        if target is None:
            pytest.skip("数据里没有别名为 bk 的曲目")

        fuzzy = await run_lookup(service, service.settings, "bk")
        exact = await run_lookup(service, service.settings, "bk c")

    assert exact.kind == "detail"
    assert exact.song is not None and exact.song.id == target.id
    # 模糊查询通常会命中多首（或至少不是强制唯一）
    assert fuzzy.kind in ("candidates", "detail")


async def test_exact_miss_does_not_fall_back_to_fuzzy(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "cr c")

    assert reply.kind == "hint"
    assert "没找到" in reply.text


async def test_song_candidates_sorted_by_difficulty(tmp_path: pathlib.Path) -> None:
    """别名命中多首时按难度降序，最难的排第一。"""
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "a")

    if reply.kind != "candidates":
        pytest.skip("这条查询没有候选列表")

    constants = [
        item.song.max_constant
        for item in reply.candidates
        if item.song is not None and item.song.max_constant
    ]
    assert constants == sorted(constants, reverse=True)
