"""两步式查询测试。

用户流程：搜到多首 → 回一个序号 → 直接看那一首的详情。
这里覆盖三块：

* 序号解析（半角 / 全角 / ``#`` 前缀 / 非法输入）；
* :class:`SelectionStore` 的存取、过期、取消；
* :func:`run_query` / :func:`run_title_query` 的端到端行为（用测试固件曲库）。
"""

from __future__ import annotations

import pathlib
import time
from contextlib import asynccontextmanager

import pytest

from fastchu.services.commands import (
    CANCEL_HINT,
    Reply,
    run_query,
    run_title_query,
)
from fastchu.services.selection import (
    SelectionItem,
    SelectionStore,
    is_cancel,
    parse_selection_index,
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


# --------------------------------------------------------------------------- #
# 序号解析
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", 1),
        ("2", 2),
        ("#3", 3),
        ("＃4", 4),  # 全角井号
        ("５", 5),  # 全角数字
        (" 6 ", 6),
    ],
)
def test_parse_selection_index_accepts(text: str, expected: int) -> None:
    assert parse_selection_index(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "abc", "2a", "12.5", "-1", "取消", "一"])
def test_parse_selection_index_rejects(text: str) -> None:
    assert parse_selection_index(text) is None


def test_is_cancel() -> None:
    assert is_cancel("取消")
    assert is_cancel("  算了 ")
    assert is_cancel("CANCEL")
    assert not is_cancel("2")


# --------------------------------------------------------------------------- #
# SelectionStore
# --------------------------------------------------------------------------- #


def test_store_remember_and_peek_without_clearing() -> None:
    store = SelectionStore()
    items = [SelectionItem(kind="song", label="A"), SelectionItem(kind="song", label="B")]
    store.remember("u1", "kw", items)

    assert "u1" in store
    assert store.peek("u1") is not None
    # peek 不清除，选错了还能再选
    assert store.peek("u1") is not None
    assert len(store) == 1


def test_store_take_clears() -> None:
    store = SelectionStore()
    store.remember("u1", "kw", [SelectionItem(kind="song", label="A")])

    pending = store.take("u1")
    assert pending is not None and pending.keyword == "kw"
    assert store.peek("u1") is None


def test_store_expires_after_ttl() -> None:
    store = SelectionStore(ttl=0.01)
    store.remember("u1", "kw", [SelectionItem(kind="song", label="A")])
    time.sleep(0.05)
    assert store.peek("u1") is None


def test_store_remember_empty_clears() -> None:
    store = SelectionStore()
    store.remember("u1", "kw", [SelectionItem(kind="song", label="A")])
    store.remember("u1", "kw", [])
    assert store.peek("u1") is None


def test_store_users_are_independent() -> None:
    store = SelectionStore()
    store.remember("u1", "kw", [SelectionItem(kind="song", label="A")])
    assert store.peek("u2") is None


def test_pending_pick_out_of_range() -> None:
    store = SelectionStore()
    store.remember("u1", "kw", [SelectionItem(kind="song", label="A")])
    pending = store.peek("u1")
    assert pending is not None
    assert pending.pick(1) is not None
    assert pending.pick(0) is None
    assert pending.pick(2) is None


# --------------------------------------------------------------------------- #
# run_query 端到端
# --------------------------------------------------------------------------- #


async def test_multi_hit_then_index_gives_detail(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()

        first = await run_query(
            service, service.settings, "world", selections=store, key="u1"
        )
        assert first.kind == "candidates"
        assert first.matched_count > 1
        assert len(first.candidates) > 1, "候选必须带序号信息，否则第二步无从选起"

        target = first.candidates[1]  # 列表里的第 2 项
        assert target.song is not None

        second = await run_query(
            service, service.settings, "2", selections=store, key="u1"
        )
        assert second.kind == "detail"
        assert second.song is not None
        assert second.song.id == target.song.id

        # 选完就清空，避免下次误触
        assert store.peek("u1") is None


async def test_index_out_of_range_keeps_pending(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        first = await run_query(
            service, service.settings, "world", selections=store, key="u1"
        )
        assert first.kind == "candidates"

        second = await run_query(
            service, service.settings, "99", selections=store, key="u1"
        )
        assert second.kind == "hint"
        assert "序号超出范围" in second.text
        # 没选中，候选保留
        assert store.peek("u1") is not None


async def test_cancel_clears_pending(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        await run_query(service, service.settings, "world", selections=store, key="u1")

        reply = await run_query(
            service, service.settings, "取消", selections=store, key="u1"
        )
        assert reply.kind == "hint"
        assert reply.texts == [CANCEL_HINT]
        assert store.peek("u1") is None


async def test_unique_hit_clears_pending(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        await run_query(service, service.settings, "world", selections=store, key="u1")
        assert store.peek("u1") is not None

        reply = await run_query(
            service, service.settings, "World Vanquisher", selections=store, key="u1"
        )
        assert reply.kind == "detail"
        # 新查询不是多选 → 旧候选作废
        assert store.peek("u1") is None


async def test_pending_only_affects_same_user(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        await run_query(service, service.settings, "world", selections=store, key="u1")

        # 另一个人发「2」不该被当成选择
        reply = await run_query(
            service, service.settings, "2", selections=store, key="u2"
        )
        assert reply.kind != "detail"
        assert store.peek("u1") is not None


async def test_without_store_behaves_like_plain_lookup(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_query(service, service.settings, "world")
        assert isinstance(reply, Reply)
        assert reply.kind == "candidates"


async def test_title_two_step(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()

        first = await run_title_query(
            service, service.settings, "RATING", selections=store, key="u1"
        )
        assert first.kind == "title_candidates"
        assert len(first.candidates) > 1

        second = await run_title_query(
            service, service.settings, "1", selections=store, key="u1"
        )
        assert second.kind == "title"
        assert second.trophy is not None
        assert store.peek("u1") is None


async def test_candidates_hold_full_pool(tmp_path: pathlib.Path) -> None:
    """``candidates`` 存的是**完整候选池**，正文只渲染第一页。"""
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
        assert len(reply.candidates) >= len(numbered)
