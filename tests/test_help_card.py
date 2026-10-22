"""帮助卡片：命令表数据、PNG 渲染与图片回复。"""

from __future__ import annotations

import io

import pytest

from fastchu.render import HELP_SECTIONS, HELP_TEXT, render_help_card
from fastchu.render.help_card import HEIGHT, WIDTH
from fastchu.services.commands import Reply, Segment


def test_help_sections_are_the_single_source() -> None:
    commands = [item.command for section in HELP_SECTIONS for item in section.items]
    assert commands[0] == "/歌曲名或别名"
    for command in (
        "/#曲目ID",
        "/称号 <关键词>",
        "/称号条件 <条件>",
        "/随机称号",
        "/谁写的 <歌名> [难度]",
        "/添加别名 <曲目> <别名>",
        "/授权",
        "/帮助",
    ):
        assert command in commands
        assert command in HELP_TEXT
    assert "/虹称号" not in HELP_TEXT


def test_help_card_is_portrait_png() -> None:
    data = render_help_card()
    if data is None:
        pytest.skip("当前环境没有 Pillow 或中文字体")

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) < 400_000

    from PIL import Image

    image = Image.open(io.BytesIO(data))
    assert image.size == (1080, 1350)
    assert image.mode == "RGB"


def test_help_card_is_cached() -> None:
    first = render_help_card()
    second = render_help_card()
    assert first is second


def test_help_card_has_flat_dark_background() -> None:
    data = render_help_card()
    if data is None:
        pytest.skip("当前环境没有 Pillow 或中文字体")

    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    corners = {
        image.getpixel(point)
        for point in (
            (2, 2),
            (WIDTH - 3, 2),
            (2, HEIGHT - 3),
            (WIDTH - 3, HEIGHT - 3),
        )
    }
    assert len(corners) == 1
    assert all(channel < 30 for channel in corners.pop())


def test_reply_card_becomes_a_single_image() -> None:
    reply = Reply(kind="help", texts=[HELP_TEXT], card_png=b"\x89PNG\r\n\x1a\n")
    messages = reply.messages()

    assert len(messages) == 1
    assert len(messages[0]) == 1
    segment = messages[0][0]
    assert segment.type == "image"
    assert segment.image_data_uri is not None
    assert segment.image_data_uri.startswith("data:image/png;base64,")


def test_reply_without_card_keeps_text_fallback() -> None:
    reply = Reply(kind="help", texts=[HELP_TEXT])

    assert reply.messages() == [[Segment(type="text", text=HELP_TEXT)]]
