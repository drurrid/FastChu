"""物量显示三档 + 详情排版测试。

``notes_mode`` 三档：

* ``off``     —— 不显示物量
* ``compact`` —— 只给总物量（默认）
* ``full``    —— 总物量 + TAP/HOLD/SLIDE/AIR/FLICK

排版方面还锁住两件事：标签按**显示宽度**对齐（``曲师`` 与 ``BPM`` 同列），
以及块之间留空行。
"""

from __future__ import annotations

import pytest

from fastchu.config import BotSettings, get_settings
from fastchu.models import LevelIndex, Song
from fastchu.render import render_song_detail
from fastchu.services.catalog import SongCatalog

from .conftest import make_settings
from .test_render import _song_with_zero_flick  # noqa: F401  —— 复用构造器


@pytest.fixture
def catalog_and_song():
    catalog, song = _song_with_zero_flick()
    return catalog, song


def _notes_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("物量"))


def test_default_mode_is_compact() -> None:
    assert BotSettings.model_fields["notes_mode"].default == "compact"


@pytest.mark.parametrize("mode", ["off", "compact", "full"])
def test_modes_are_accepted(mode: str) -> None:
    settings = BotSettings(notes_mode=mode)
    assert settings.notes_mode == mode


def test_invalid_mode_rejected() -> None:
    with pytest.raises(Exception):
        BotSettings(notes_mode="verbose")


def test_mode_off_hides_notes(catalog_and_song) -> None:
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog, notes_mode="off")
    assert "物量" not in text


def test_mode_compact_only_total(catalog_and_song) -> None:
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog, notes_mode="compact")
    line = _notes_line(text)
    assert "物量" in line
    assert "TAP" not in line, "简洁档不该出现分项"
    assert "FLICK" not in line


def test_mode_full_has_breakdown(catalog_and_song) -> None:
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog, notes_mode="full")
    line = _notes_line(text)
    assert "TAP" in line and "HOLD" in line
    # 分项之间用 / 分隔，比纯空格好读
    assert " / " in line


def test_mode_from_settings(tmp_path, catalog_and_song) -> None:
    catalog, song = catalog_and_song
    settings = make_settings(tmp_path, notes_mode="off")
    assert "物量" not in render_song_detail(song, catalog, settings=settings)


def test_explicit_mode_beats_settings(tmp_path, catalog_and_song) -> None:
    catalog, song = catalog_and_song
    settings = make_settings(tmp_path, notes_mode="off")
    text = render_song_detail(song, catalog, settings=settings, notes_mode="compact")
    assert "物量" in text


# --------------------------------------------------------------------------- #
# 排版
# --------------------------------------------------------------------------- #


def test_info_labels_are_two_cjk_chars() -> None:
    """标签必须都是两个汉字——这是「任意字体下都能对齐」的前提。"""
    for label in ("曲师", "曲速", "难度", "物量"):
        assert len(label) == 2
        assert all(ord(ch) > 0x2E80 for ch in label)


def test_info_line_uses_full_width_gap(catalog_and_song) -> None:
    """标签与值之间用全角空格；比例字体下才能对齐（ASCII 空格对不齐）。"""
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog)
    assert "\u3000" in text
    assert "曲速\u3000" in text or "曲师\u3000" in text


def test_detail_blocks_separated_by_blank_line(catalog_and_song) -> None:
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog)
    assert "\n\n" in text, "标题/信息/称号之间应有空行"


def test_detail_first_line_is_title(catalog_and_song) -> None:
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog)
    assert text.splitlines()[0] == f"#{song.id} - {song.title}"


def test_jacket_can_be_separate(tmp_path) -> None:
    """``jacket_separate`` 打开后，曲绘单独成一条消息。"""
    import asyncio

    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    settings = make_settings(tmp_path, jacket_mode="url", jacket_separate=True)

    async def _run():
        service = SongService(settings)
        try:
            return await run_lookup(service, service.settings, "#464")
        finally:
            await service.aclose()

    reply = asyncio.run(_run())
    if reply.jacket is None:
        pytest.skip("该固件没取到曲绘")
    messages = reply.messages()
    assert len(messages) == 2, "正文与曲绘应分开"
    assert [seg.type for seg in messages[0]] == ["text"]
    assert [seg.type for seg in messages[1]] == ["image"]


def test_jacket_inline_by_default(tmp_path) -> None:
    import asyncio

    from fastchu.services.commands import run_lookup
    from fastchu.services.song_service import SongService

    settings = make_settings(tmp_path, jacket_mode="url")

    async def _run():
        service = SongService(settings)
        try:
            return await run_lookup(service, service.settings, "#464")
        finally:
            await service.aclose()

    reply = asyncio.run(_run())
    if reply.jacket is None:
        pytest.skip("该固件没取到曲绘")
    messages = reply.messages()
    assert len(messages) == 1
    assert [seg.type for seg in messages[0]] == ["text", "image"]


def test_id_is_in_header_not_footer(catalog_and_song) -> None:
    """``#ID`` 跟着分类/版本走，不再单独占最后一行。"""
    catalog, song = catalog_and_song
    text = render_song_detail(song, catalog)
    # #ID 在标题行（第 1 行），元信息里不再重复
    assert f"#{song.id}" in text.splitlines()[0]
    assert f"#{song.id}" not in text.splitlines()[2]
    assert not text.rstrip().endswith(f"#{song.id}")
