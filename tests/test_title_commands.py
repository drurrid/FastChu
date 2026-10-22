"""``/称号条件`` 与 ``/随机称号``。"""

from __future__ import annotations

import json
import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.services.commands import (
    run_rainbow_titles,
    run_random_title,
    run_title,
    run_title_condition,
)
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


async def test_condition_search_without_keyword_gives_hint(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_title_condition(service, service.settings, "")
    assert reply.kind == "hint"
    assert "条件" in reply.text


async def test_condition_search_finds_by_condition(tmp_path: pathlib.Path) -> None:
    """按条件检索：命中项的条件里必须真的有这个词。"""
    async with make_service(tmp_path) as service:
        catalog = await service.titles()
        sample = next(
            (t for t in catalog.special if "ALL JUSTICE" in (t.description or "").upper()),
            None,
        )
        if sample is None:
            pytest.skip("夹具里没有含 ALL JUSTICE 的称号")

        reply = await run_title_condition(service, service.settings, "ALL JUSTICE")

    assert reply.kind == "title_candidates"
    assert reply.candidates
    assert all(item.trophy is not None for item in reply.candidates)


async def test_condition_search_miss(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_title_condition(
            service, service.settings, "绝对不可能出现的条件词zzz"
        )
    assert reply.kind == "hint"


async def test_condition_search_then_detail(tmp_path: pathlib.Path) -> None:
    """条件检索 → 选序号 → 详情。"""
    async with make_service(tmp_path) as service:
        catalog = await service.titles()
        sample = next(
            (t for t in catalog.special if "ALL JUSTICE" in (t.description or "").upper()),
            None,
        )
        if sample is None:
            pytest.skip("夹具里没有含 ALL JUSTICE 的称号")

        store = SelectionStore()
        key = "u1"
        listed = await run_title_condition(
            service, service.settings, "ALL JUSTICE", selections=store, key=key
        )
        assert listed.kind == "title_candidates"

        detail = await run_title_condition(
            service, service.settings, "1", selections=store, key=key
        )
        assert detail.kind == "title"
        assert detail.trophy is not None


async def test_numeric_song_alias_does_not_hijack_title_index(
    tmp_path: pathlib.Path,
) -> None:
    """称号候选没有别名，曲目的数字别名不能抢走「回 1」的序号。"""
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"cn": {"3": ["1"]}}), encoding="utf-8")
    settings = make_settings(tmp_path, community_alias_path=overlay)
    service = SongService(settings)
    try:
        catalog = await service.titles()
        sample = next(
            (t for t in catalog.special if "ALL JUSTICE" in (t.description or "").upper()),
            None,
        )
        if sample is None:
            pytest.skip("夹具里没有含 ALL JUSTICE 的称号")

        store = SelectionStore()
        listed = await run_title_condition(
            service, settings, "ALL JUSTICE", selections=store, key="u1"
        )
        assert listed.kind == "title_candidates"

        detail = await run_title_condition(
            service, settings, "1", selections=store, key="u1"
        )
        assert detail.kind == "title"
        assert detail.trophy is not None
    finally:
        await service.aclose()


async def test_random_title_returns_one(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_random_title(service, service.settings)
        catalog = await service.titles()

    assert reply.kind == "title"
    assert reply.trophy is not None
    assert reply.trophy.id in {t.id for t in catalog.special}


async def test_random_title_varies(tmp_path: pathlib.Path) -> None:
    """随机抽取应当有变化（抽 20 次至少两个不同结果）。"""
    async with make_service(tmp_path) as service:
        names = set()
        for _ in range(20):
            reply = await run_random_title(service, service.settings)
            if reply.trophy is not None:
                names.add(reply.trophy.id)
    assert len(names) >= 2


async def test_help_mentions_new_commands(tmp_path: pathlib.Path) -> None:
    from fastchu.services.commands import run_help

    async with make_service(tmp_path) as service:
        text = (await run_help(service, service.settings)).text

    assert "/称号条件" in text
    assert "/随机称号" in text


async def test_rainbow_titles_lists_only_special_rainbows(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        catalog = await service.titles()
        reply = await run_rainbow_titles(service, service.settings)

    assert reply.kind == "title_candidates"
    assert reply.candidates
    assert {item.trophy.id for item in reply.candidates} == {
        trophy.id for trophy in catalog.special if trophy.color == "rainbow"
    }
    assert all(item.trophy.color == "rainbow" for item in reply.candidates)
    # 夹具里的虹色角色称号（真・神推し）不能出现
    assert 6246 not in {item.trophy.id for item in reply.candidates}


async def test_rainbow_titles_include_jp_only_but_skip_character_template(
    tmp_path: pathlib.Path,
) -> None:
    jp_path = tmp_path / "jp_only.json"
    jp_path.write_text(
        json.dumps(
            {
                "trophies": [
                    {
                        "id": 900001,
                        "name": "日服虹称号",
                        "color": "rainbow",
                        "condition": "イベント1位",
                    },
                    {
                        "id": 900002,
                        "name": "(キャラクター名)",
                        "color": "rainbow",
                        "condition": (
                            "ゲキチュウマイオリジナルキャラクター(一部除く)が"
                            "RANK200に到達"
                        ),
                    },
                    {
                        "id": 900003,
                        "name": "日服金称号",
                        "color": "gold",
                        "condition": "イベント参加",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = make_settings(tmp_path, jp_only_titles_path=jp_path)
    service = SongService(settings)
    try:
        reply = await run_rainbow_titles(service, settings)
    finally:
        await service.aclose()

    ids = {item.trophy.id for item in reply.candidates}
    assert 900001 in ids
    assert 900002 not in ids
    assert 900003 not in ids


async def test_rainbow_titles_then_detail(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        store = SelectionStore()
        listed = await run_rainbow_titles(
            service, service.settings, selections=store, key="u1"
        )
        assert listed.kind == "title_candidates"

        detail = await run_rainbow_titles(
            service, service.settings, "1", selections=store, key="u1"
        )

    assert detail.kind == "title"
    assert detail.trophy is not None


async def test_rainbow_titles_hidden_from_help(tmp_path: pathlib.Path) -> None:
    from fastchu.services.commands import RESERVED_COMMAND_WORDS, run_help

    async with make_service(tmp_path) as service:
        text = (await run_help(service, service.settings)).text

    assert "/虹称号" not in text
    assert "虹称号" in RESERVED_COMMAND_WORDS


async def test_condition_search_skips_image_and_song_named(tmp_path: pathlib.Path) -> None:
    """「图片」称号、与曲目同名的称号都不出现在条件检索结果里。"""
    from fastchu.models import Trophy

    async with make_service(tmp_path) as service:
        catalog = await service.titles()

    image_titles = [t for t in catalog.special if t.color == "image"]
    if image_titles:
        for trophy in image_titles[:3]:
            hits = catalog.search_condition(trophy.description or "", limit=200)
            assert all(h.color != "image" for h in hits), "图片称号不该出现在条件检索里"

    # 构造一个「名字 == 曲名」的称号，确认被过滤
    from fastchu.services.titles import TrophyCatalog

    same = Trophy(
        id=1,
        name="Some Song",
        color="normal",
        description="Some Song/MASTER/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [3],
                "full_combo": "alljustice",
                "songs": [{"id": 1, "title": "Some Song"}],
            }
        ],
    )
    cat = TrophyCatalog([same])
    assert cat.search_condition("ALL JUSTICE") == []