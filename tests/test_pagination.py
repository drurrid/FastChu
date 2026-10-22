"""候选分页测试。

移动端一行放不下太多信息，所以：

* 候选**每页 8 条**，标题显示 ``（1/4 页）``；
* 每条候选占两行：序号 + 名字，第二行缩进给难度/分类（避免长行折行）；
* 回「下一页」/「上一页」翻页；序号只在**当前页**内有效。
"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.services.commands import run_query, run_title_query
from fastchu.services.selection import SelectionStore
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


def _page_no(text: str) -> str:
    """从标题行里取出 ``1/4`` 这样的页码。"""
    head = text.splitlines()[0]
    if "（" in head and "页）" in head:
        return head.split("（", 1)[1].split(" 页）", 1)[0]
    return "1/1"


async def test_first_page_header_and_size(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        reply = await run_query(
            service, service.settings, "a", selections=store, key="u1"
        )
        if reply.kind != "candidates":
            pytest.skip("该关键词没有命中多首")

        page_size = service.settings.search_result_limit
        numbered = [line for line in reply.text.splitlines() if line[:1].isdigit()]
        assert len(numbered) <= page_size
        assert "页）" in reply.text, "多页时标题要带页码"
        assert _page_no(reply.text).startswith("1/")

        # 两行格式：数字行后面跟一行缩进
        lines = reply.text.splitlines()
        first_index = next(i for i, line in enumerate(lines) if line.startswith("1. "))
        assert lines[first_index + 1].startswith("   ")


async def test_next_and_prev_page(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        first = await run_query(
            service, service.settings, "a", selections=store, key="u1"
        )
        if first.kind != "candidates":
            pytest.skip("该关键词没有命中多首")
        if "（1/1 页）" in first.text:
            pytest.skip("该关键词只有一页")

        second = await run_query(
            service, service.settings, "下一页", selections=store, key="u1"
        )
        assert second.kind == "candidates"
        assert _page_no(second.text).startswith("2/")
        # 第二页的序号仍然从 1 开始
        assert "1. " in second.text

        back = await run_query(
            service, service.settings, "上一页", selections=store, key="u1"
        )
        assert _page_no(back.text).startswith("1/")


async def test_next_on_last_page_gives_hint(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        reply = await run_query(
            service, service.settings, "a", selections=store, key="u1"
        )
        if reply.kind != "candidates":
            pytest.skip("该关键词没有命中多首")

        # 一直翻到底
        for _ in range(30):
            moved = await run_query(
                service, service.settings, "下一页", selections=store, key="u1"
            )
            if moved.kind == "hint":
                assert "最后一页" in moved.text
                return
        pytest.fail("翻页没有终止")


async def test_selection_is_page_relative(tmp_path: pathlib.Path) -> None:
    """第 2 页的「1」指的是第 2 页第一条，不是全局第一条。"""
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        first = await run_query(
            service, service.settings, "a", selections=store, key="u1"
        )
        if first.kind != "candidates":
            pytest.skip("该关键词没有命中多首")
        if "（1/1 页）" in first.text:
            pytest.skip("该关键词只有一页")

        page_two = await run_query(
            service, service.settings, "下一页", selections=store, key="u1"
        )
        expected_title = page_two.candidates[
            service.settings.search_result_limit
        ].song.title

        picked = await run_query(
            service, service.settings, "1", selections=store, key="u1"
        )
        assert picked.kind == "detail"
        assert picked.song is not None
        assert picked.song.title == expected_title


async def test_title_candidates_paginate(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        reply = await run_title_query(
            service, service.settings, "RATING", selections=store, key="u1"
        )
        if reply.kind != "title_candidates":
            pytest.skip("该关键词没有命中多个称号")
        assert "个称号" in reply.text.splitlines()[0]


async def test_no_page_number_when_single_page(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        reply = await run_query(
            service, service.settings, "world", selections=store, key="u1"
        )
        if reply.kind != "candidates":
            pytest.skip("该关键词没有命中多首")
        if reply.matched_count > service.settings.search_result_limit:
            pytest.skip("该关键词跨了多页")
        assert "页）" not in reply.text, "只有一页时不显示页码"


async def test_new_query_resets_page(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        first = await run_query(
            service, service.settings, "a", selections=store, key="u1"
        )
        if first.kind != "candidates":
            pytest.skip("该关键词没有命中多首")
        if "（1/1 页）" in first.text:
            pytest.skip("该关键词只有一页")

        await run_query(service, service.settings, "下一页", selections=store, key="u1")
        fresh = await run_query(
            service, service.settings, "world", selections=store, key="u1"
        )
        assert _page_no(fresh.text).startswith("1/")
