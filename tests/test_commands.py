"""命令层测试。

:mod:`fastchu.services.commands` 是**插件与本地测试页共用**的逻辑，
所以它的行为必须在两侧都成立：QQ 群里发什么、测试页就显示什么。
"""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import httpx
import pytest
import respx

from fastchu.errors import ChunithmBotError, LxnsError
from fastchu.render import HELP_TEXT
from fastchu.services.commands import (
    EMPTY_KEYWORD_HINT,
    GENERIC_ERROR,
    MISS_HINT,
    MISS_TITLE_HINT,
    Reply,
    Segment,
    run_help,
    run_lookup,
    run_title,
)
from fastchu.services.song_service import SongService

from .conftest import TROPHY_FIXTURE, make_settings


@asynccontextmanager
async def make_service(tmp_path: pathlib.Path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    service = SongService(settings)
    try:
        yield service
    finally:
        await service.aclose()


async def _boom(*args, **kwargs):
    raise LxnsError("no network")


# --------------------------------------------------------------------------- #
# /查歌
# --------------------------------------------------------------------------- #


async def test_lookup_empty_keyword(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "   ")
    assert reply.kind == "hint"
    assert reply.texts == [EMPTY_KEYWORD_HINT]
    assert reply.song is None


async def test_lookup_unique_gives_detail(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "World Vanquisher")

    assert reply.kind == "detail"
    assert reply.song is not None and reply.song.title == "World Vanquisher"
    assert len(reply.texts) == 1
    assert reply.is_single_message
    assert reply.text.startswith("#464 - World Vanquisher")
    assert reply.char_count > 50


async def test_lookup_detail_has_required_fields(tmp_path: pathlib.Path) -> None:
    """详情必须包含：曲师 / BPM / 分类 / 版本。"""
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "#464")

    song = reply.song
    assert song is not None
    text = reply.text
    assert song.artist and song.artist in text
    assert song.genre and song.genre in text
    assert "曲速" in text and str(song.bpm) in text
    assert "STAR" in text  # 版本（去掉 CHUNITHM 前缀）


async def test_lookup_detail_has_related_titles(tmp_path: pathlib.Path) -> None:
    """曲目详情要带相关特殊称号，并且**标出颜色与所需条件**。"""
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")

    async with make_service(tmp_path) as service:
        title_catalog = await service.titles()
        catalog = await service.catalog()
        # 挑一首「夹具里确实有专属特殊称号」且曲库存在的曲子
        song_id = next(
            (
                song_id
                for trophy in title_catalog.special
                for song_id in trophy.song_ids
                if catalog.get(song_id) is not None and title_catalog.for_song(song_id)
            ),
            None,
        )
        assert song_id is not None, "夹具里应当有带专属称号的曲子"
        reply = await run_lookup(service, service.settings, f"#{song_id}")
        expected = title_catalog.for_song(song_id)

    assert reply.extra.get("related_titles", 0) == len(expected)
    assert "特殊称号" in reply.text

    # 每个称号都要出现，且带颜色标记与条件。
    # 注意：只有一个称号时渲染成内联形式 ``特殊称号 [金] xxx · 条件``，
    # 多个称号时才是「标题行 + 每称号一行」，所以不能断言行首一定是 ``[``。
    for trophy in expected:
        assert trophy.name in reply.text, trophy.name
        assert f"[{trophy.color_label}]" in reply.text, trophy.color_label

    condition_lines = [line for line in reply.text.splitlines() if "·" in line and "[" in line]
    assert condition_lines, "应当有带条件的称号行"
    for line in condition_lines:
        assert line.split("·", 1)[1].strip(), f"条件为空: {line!r}"


async def test_lookup_detail_excludes_all_songs_titles(tmp_path: pathlib.Path) -> None:
    """版本全完成称号（Legend of X 等）不该出现在单曲详情里。"""
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")

    async with make_service(tmp_path) as service:
        title_catalog = await service.titles()
        catalog = await service.catalog()
        # 找一首「只命中版本全完成称号」的曲子（#464 就是这种）
        song_id = next(
            (
                song_id
                for trophy in title_catalog.trophies
                for song_id in trophy.song_ids
                if catalog.get(song_id) is not None
                and title_catalog.for_song(song_id) == []
                and title_catalog.for_song(song_id, include_all_songs=True)
            ),
            None,
        )
        assert song_id is not None, "夹具里应当有只命中版本全完成称号的曲子"
        reply = await run_lookup(service, service.settings, f"#{song_id}")

    assert reply.extra.get("related_titles", 0) == 0
    assert "特殊称号" not in reply.text
    assert "全曲称号" not in reply.text


async def test_lookup_detail_survives_missing_trophy_cache(tmp_path: pathlib.Path) -> None:
    """称号库不可用不该让查歌挂掉。"""
    async with make_service(tmp_path, trophy_cache_path=tmp_path / "nope.json") as service:
        service.loader.get_trophies = _boom  # type: ignore[method-assign]
        reply = await run_lookup(service, service.settings, "#464")

    assert reply.kind == "detail"
    assert "World Vanquisher" in reply.text
    assert "全曲称号" not in reply.text


async def test_lookup_not_found_gives_hint(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "绝对不存在的曲子zzz")
    assert reply.kind == "hint"
    assert reply.texts == [MISS_HINT]


async def test_lookup_ambiguous_gives_candidates(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "world")

    assert reply.kind == "candidates"
    assert reply.song is None
    assert reply.matched_count > 1
    assert reply.text.startswith("`world` 命中")


async def test_lookup_by_song_id(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "#464")
    assert reply.kind == "detail"
    assert reply.song is not None and reply.song.id == 464


async def test_lookup_alias(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_lookup(service, service.settings, "wv")
    assert reply.kind == "detail"
    assert reply.song is not None and reply.song.title == "World Vanquisher"


async def test_lookup_notes_mode_has_no_jacket(tmp_path: pathlib.Path) -> None:
    """物量是纯数据视图，不该配图。"""
    async with make_service(tmp_path, jacket_mode="url") as service:
        reply = await run_lookup(service, service.settings, "#464", want_notes=True)

    assert reply.kind == "notes"
    assert reply.jacket is None
    assert "物量" in reply.text


# --------------------------------------------------------------------------- #
# 曲绘
# --------------------------------------------------------------------------- #


@respx.mock
async def test_lookup_jacket_off(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path, jacket_mode="off") as service:
        reply = await run_lookup(service, service.settings, "#464")
    assert reply.jacket is None


@respx.mock
async def test_lookup_jacket_url_mode_makes_no_request(tmp_path: pathlib.Path) -> None:
    """默认的 url 模式是「轻量级」的关键：只带地址，不下载。"""
    route = respx.route(host="assets2.lxns.net")
    async with make_service(tmp_path, jacket_mode="url") as service:
        reply = await run_lookup(service, service.settings, "#464")

    assert reply.jacket is not None
    assert reply.jacket.url.endswith("/jacket/464.png")
    assert not reply.jacket.has_data
    assert not route.called


@respx.mock
async def test_lookup_jacket_base64_mode_downloads(tmp_path: pathlib.Path) -> None:
    respx.get("https://assets2.lxns.net/chunithm/jacket/464.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG-data")
    )
    async with make_service(tmp_path, jacket_mode="base64") as service:
        reply = await run_lookup(service, service.settings, "#464")

    assert reply.jacket is not None and reply.jacket.has_data


# --------------------------------------------------------------------------- #
# /称号
# --------------------------------------------------------------------------- #


async def test_title_without_keyword_gives_hint(tmp_path: pathlib.Path) -> None:
    """``/称号`` 不带参数 → 用法提示（分类菜单已删除）。"""
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")
    async with make_service(tmp_path) as service:
        reply = await run_title(service, service.settings, "")

    assert reply.kind == "hint"
    assert "称号条件" in reply.text
    assert not reply.candidates


async def test_title_exact_gives_detail(tmp_path: pathlib.Path) -> None:
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")
    async with make_service(tmp_path) as service:
        title_catalog = await service.titles()
        target = next(t for t in title_catalog.special if len(t.name) > 8)
        reply = await run_title(service, service.settings, target.name)

    assert reply.kind == "title"
    assert reply.trophy is not None and reply.trophy.id == target.id
    assert f"[{target.color_label}]" in reply.text  # 必须标出颜色
    assert "条件" in reply.text


async def test_title_ambiguous_gives_candidates(tmp_path: pathlib.Path) -> None:
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")
    async with make_service(tmp_path) as service:
        reply = await run_title(service, service.settings, "Legend")

    assert reply.kind in ("title_candidates", "title")
    if reply.kind == "title_candidates":
        assert reply.matched_count > 1
        assert "[" in reply.text


async def test_title_not_found(tmp_path: pathlib.Path) -> None:
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")
    async with make_service(tmp_path) as service:
        reply = await run_title(service, service.settings, "绝对不存在的称号zzz")
    assert reply.kind == "hint"
    assert reply.texts == [MISS_TITLE_HINT]


# --------------------------------------------------------------------------- #
# /查歌帮助
# --------------------------------------------------------------------------- #


async def test_help(tmp_path: pathlib.Path) -> None:
    async with make_service(tmp_path) as service:
        reply = await run_help(service, service.settings)

    assert reply.kind == "help"
    assert reply.text == HELP_TEXT
    assert "/称号" in reply.text

    messages = reply.messages()
    if reply.card_png is None:
        assert messages == [[Segment(type="text", text=HELP_TEXT)]]
    else:
        assert reply.card_png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(messages) == 1
        assert len(messages[0]) == 1
        segment = messages[0][0]
        assert segment.type == "image"
        assert segment.image_data_uri.startswith("data:image/png;base64,")


# --------------------------------------------------------------------------- #
# 错误路径
# --------------------------------------------------------------------------- #


class _BoomService:
    """catalog() 直接抛指定的异常。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def catalog(self, **_kwargs):
        raise self._exc


async def test_expected_error_uses_user_message(tmp_path: pathlib.Path) -> None:
    from fastchu.errors import LxnsRateLimitError

    service = _BoomService(LxnsRateLimitError("429", status=429))
    reply = await run_lookup(service, make_settings(tmp_path), "anything")  # type: ignore[arg-type]

    assert reply.kind == "error"
    assert reply.texts == [LxnsRateLimitError("x").user_message]


async def test_unexpected_error_is_generic(tmp_path: pathlib.Path) -> None:
    service = _BoomService(RuntimeError("boom"))
    reply = await run_lookup(service, make_settings(tmp_path), "anything")  # type: ignore[arg-type]

    assert reply.kind == "error"
    assert reply.texts == [GENERIC_ERROR]


async def test_title_unexpected_error_is_generic(tmp_path: pathlib.Path) -> None:
    service = _BoomService(RuntimeError("boom"))
    reply = await run_title(service, make_settings(tmp_path), "x")  # type: ignore[arg-type]
    assert reply.texts == [GENERIC_ERROR]


# --------------------------------------------------------------------------- #
# Reply 本身
# --------------------------------------------------------------------------- #


def test_reply_properties() -> None:
    reply = Reply(kind="detail", texts=["abc", "def"])
    assert reply.text == "abc\n\ndef"
    assert reply.char_count == 6  # 3 + 3，不含拼接用的换行
    assert not reply.is_single_message
    assert not reply.is_error


def test_reply_single_and_error() -> None:
    reply = Reply(kind="error", texts=["x"])
    assert reply.is_single_message
    assert reply.is_error


def test_reply_empty_texts() -> None:
    reply = Reply(kind="hint")
    assert reply.text == ""
    assert reply.char_count == 0
    assert reply.is_single_message
    assert reply.messages() == []


# --------------------------------------------------------------------------- #
# messages()：段结构的唯一事实来源
# --------------------------------------------------------------------------- #


def test_messages_single_text_only() -> None:
    reply = Reply(kind="detail", texts=["正文"])
    messages = reply.messages()
    assert len(messages) == 1
    assert [(s.type, s.text) for s in messages[0]] == [("text", "正文")]


def test_messages_single_text_with_url_jacket() -> None:
    """单条正文时曲绘拼进同一条消息（轻量：只发一次）。"""
    from fastchu.services.jacket import JacketImage

    reply = Reply(
        kind="detail",
        texts=["正文"],
        jacket=JacketImage(song_id=464, url="https://x/464.png"),
    )
    messages = reply.messages()
    assert len(messages) == 1
    assert [s.type for s in messages[0]] == ["text", "image"]
    assert messages[0][1].image_payload == "https://x/464.png"


def test_messages_prefers_inline_data_uri() -> None:
    from fastchu.services.jacket import JacketImage

    jacket = JacketImage(song_id=1, url="https://x/1.png", data=b"png")
    reply = Reply(kind="detail", texts=["正文"], jacket=jacket)
    segment = reply.messages()[0][1]
    assert segment.image_payload == jacket.data_uri
    assert segment.image_data_uri is not None


def test_messages_paginated_drops_jacket() -> None:
    """分页时配图会错位，所以每条只发文字。"""
    from fastchu.services.jacket import JacketImage

    reply = Reply(
        kind="filter",
        texts=["第一段", "第二段"],
        jacket=JacketImage(song_id=1, url="https://x/1.png"),
    )
    messages = reply.messages()
    assert len(messages) == 2
    assert all([s.type for s in m] == ["text"] for m in messages)
    assert [m[0].text for m in messages] == ["第一段", "第二段"]


def test_segment_image_payload_fallback() -> None:
    assert Segment(type="image", image_url="http://x").image_payload == "http://x"
    assert Segment(type="image").image_payload == ""


def test_error_hierarchy_is_importable() -> None:
    assert issubclass(ChunithmBotError, Exception)
    assert issubclass(LxnsError, ChunithmBotError)
