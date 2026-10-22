"""``/随机称号`` 之后回 1 查曲目。"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.services.commands import run_random_title, run_title_query
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


async def test_random_title_single_song_hint(tmp_path: pathlib.Path) -> None:
    """随机称号若恰好只有一首关联曲目，也要提示「回 1 查看曲目」。"""
    async with make_service(tmp_path) as service:
        catalog = await service.catalog()
        for _ in range(120):
            store = SelectionStore()
            reply = await run_random_title(service, service.settings, selections=store, key="u1")
            if reply.trophy is None:
                continue
            ids = reply.trophy.song_ids
            # 曲目必须在国服曲库里，否则本来就挂不上候选（不是缺陷）
            if len(ids) == 1 and catalog.get(ids[0]) is not None:
                break
        else:
            pytest.skip("没抽到「关联曲目在曲库里」的单曲称号")

        assert "回 1 查看曲目" in reply.text
        assert store.peek("u1") is not None

        song_reply = await run_title_query(
            service, service.settings, "1", selections=store, key="u1"
        )

    assert song_reply.kind == "detail"
    assert song_reply.song is not None
    assert song_reply.song.id == reply.trophy.song_ids[0]


async def test_random_title_without_store_has_no_hint(tmp_path: pathlib.Path) -> None:
    """不传待选槽位（CLI/测试页）时不该出现提示。"""
    async with make_service(tmp_path) as service:
        reply = await run_random_title(service, service.settings)

    assert reply.kind == "title"
    assert "查看曲目" not in reply.text
