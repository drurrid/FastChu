"""称号详情 → 回数字查对应曲目。"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.services.commands import run_title, run_title_query
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


async def _title_with_songs(service: SongService):
    catalog = await service.titles()
    for trophy in catalog.special:
        if trophy.song_ids:
            return trophy
    return None


async def test_title_detail_hints_song_reply(tmp_path: pathlib.Path) -> None:
    """称号详情末尾要有「回 N 查看曲目」的提示。"""
    async with make_service(tmp_path) as service:
        trophy = await _title_with_songs(service)
        if trophy is None:
            pytest.skip("夹具里没有带曲目的称号")

        store = SelectionStore()
        reply = await run_title_query(
            service, service.settings, trophy.name, selections=store, key="u1"
        )

    assert reply.kind == "title"
    assert "查看曲目" in reply.text


async def test_reply_number_opens_song(tmp_path: pathlib.Path) -> None:
    """回 1 → 出对应曲目详情。"""
    async with make_service(tmp_path) as service:
        trophy = await _title_with_songs(service)
        if trophy is None:
            pytest.skip("夹具里没有带曲目的称号")

        store = SelectionStore()
        key = "u1"
        await run_title_query(service, service.settings, trophy.name, selections=store, key=key)
        pending = store.peek(key)
        assert pending is not None, "称号详情后应当留下曲目候选"

        song_reply = await run_title_query(service, service.settings, "1", selections=store, key=key)

    assert song_reply.kind == "detail"
    assert song_reply.song is not None
    assert song_reply.song.id in trophy.song_ids


async def test_title_without_songs_no_selection(tmp_path: pathlib.Path) -> None:
    """没有关联曲目的称号不该留下待选。"""
    async with make_service(tmp_path) as service:
        catalog = await service.titles()
        trophy = next((t for t in catalog.special if not t.song_ids), None)
        if trophy is None:
            pytest.skip("夹具里没有不带曲目的称号")

        store = SelectionStore()
        reply = await run_title_query(
            service, service.settings, trophy.name, selections=store, key="u1"
        )
        if reply.kind != "title":
            pytest.skip("这个称号不是唯一命中")

        assert store.peek("u1") is None


async def test_plain_run_title_has_no_hint(tmp_path: pathlib.Path) -> None:
    """不带选择槽位的调用（CLI/测试页）不该出现「回 N」提示。"""
    async with make_service(tmp_path) as service:
        trophy = await _title_with_songs(service)
        if trophy is None:
            pytest.skip("夹具里没有带曲目的称号")

        reply = await run_title(service, service.settings, trophy.name)

    assert reply.kind == "title"
    assert "查看曲目" not in reply.text
