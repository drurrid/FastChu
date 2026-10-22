"""把帮助命令表渲染成一张 PNG 卡片。

Pillow 只在运行时需要：缺包或找不到中文字体时返回 ``None``，
调用方自动回退到 :data:`fastchu.render.text.HELP_TEXT`。
"""

from __future__ import annotations

import functools
import io
import logging
from pathlib import Path

from .text import HELP_SECTIONS, HelpItem, HelpSection

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 运行环境没装 Pillow
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = ["render_help_card"]

WIDTH = 1080
HEIGHT = 1350
MARGIN = 72

_BG = (14, 17, 22)
_TEXT = (235, 239, 244)
_MUTED = (148, 158, 170)
_DIM = (100, 110, 122)
_LINE = (33, 38, 46)
_ACCENT = (76, 194, 255)

_FONT_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)
_FONT_REGULAR_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

_SECTION_HEADER = 50
_ROW_HEIGHT = 80
_SECTION_GAP = 24
_CONTENT_TOP = 286
_FOOTER_LINE = 1250


@functools.lru_cache(maxsize=2)
def _font_path(bold: bool) -> Path | None:
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_REGULAR_CANDIDATES
    for name in candidates:
        path = Path(name)
        if path.is_file():
            return path
    return None


@functools.lru_cache(maxsize=16)
def _font(size: int, bold: bool = False):
    path = _font_path(bold) or _font_path(False)
    if path is None or ImageFont is None:
        return None
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError:
        return None


def _draw_header(draw: "ImageDraw.ImageDraw") -> None:
    title_font = _font(64, True)
    subtitle_font = _font(26)
    draw.text((MARGIN, 88), "FastChu", font=title_font, fill=_TEXT)
    draw.text(
        (MARGIN + 2, 168),
        "CHUNITHM 查歌助手",
        font=subtitle_font,
        fill=_MUTED,
    )
    draw.line((MARGIN, 238, WIDTH - MARGIN, 238), fill=_LINE, width=1)


def _draw_section_header(
    draw: "ImageDraw.ImageDraw", y: int, section: HelpSection
) -> None:
    draw.text((MARGIN, y), section.title, font=_font(28, True), fill=_ACCENT)


def _ellipsize(
    draw: "ImageDraw.ImageDraw", text: str, font, max_width: float
) -> str:
    if max_width <= 0:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    shortened = text
    while shortened and draw.textlength(shortened + "…", font=font) > max_width:
        shortened = shortened[:-1]
    return shortened + "…" if shortened else ""


def _draw_row(
    draw: "ImageDraw.ImageDraw", y: int, item: HelpItem, last: bool
) -> None:
    left, right = MARGIN, WIDTH - MARGIN
    command_font = _font(30, True)
    description_font = _font(24)
    command_width = draw.textlength(item.command, font=command_font)
    description_left = left + command_width + 28
    available = right - description_left
    center_y = y + _ROW_HEIGHT // 2

    if available >= 120:
        draw.text(
            (left, center_y), item.command, font=command_font, fill=_TEXT, anchor="lm"
        )
        description = _ellipsize(
            draw, item.description, description_font, available
        )
        draw.text(
            (right, center_y),
            description,
            font=description_font,
            fill=_MUTED,
            anchor="rm",
        )
    else:
        command = _ellipsize(draw, item.command, command_font, right - left)
        description = _ellipsize(
            draw, item.description, description_font, right - left
        )
        draw.text((left, y + 17), command, font=command_font, fill=_TEXT)
        draw.text(
            (left, y + 52), description, font=description_font, fill=_MUTED
        )

    if not last:
        line_y = y + _ROW_HEIGHT - 1
        draw.line((left, line_y, right, line_y), fill=_LINE, width=1)


def _draw_footer(draw: "ImageDraw.ImageDraw") -> None:
    draw.line((MARGIN, _FOOTER_LINE, WIDTH - MARGIN, _FOOTER_LINE), fill=_LINE, width=1)
    font = _font(20)
    draw.text(
        (MARGIN, _FOOTER_LINE + 26),
        "示例  /Rebellion · /世征 · /#464",
        font=font,
        fill=_DIM,
    )
    draw.text(
        (WIDTH - MARGIN, _FOOTER_LINE + 26),
        "数据：落雪查分器 · 日服 Wiki",
        font=font,
        fill=_DIM,
        anchor="ra",
    )


def _draw_card() -> "Image.Image":
    image = Image.new("RGB", (WIDTH, HEIGHT), _BG)
    draw = ImageDraw.Draw(image)
    _draw_header(draw)

    y = _CONTENT_TOP
    for section in HELP_SECTIONS:
        _draw_section_header(draw, y, section)
        row_y = y + _SECTION_HEADER
        last_index = len(section.items) - 1
        for index, item in enumerate(section.items):
            _draw_row(draw, row_y, item, index == last_index)
            row_y += _ROW_HEIGHT
        y = row_y + _SECTION_GAP

    _draw_footer(draw)
    return image


@functools.lru_cache(maxsize=1)
def render_help_card() -> bytes | None:
    """渲染并缓存帮助卡；环境不支持时返回 ``None``。"""
    if Image is None or _font(24) is None:
        logger.warning("Pillow 或中文字体不可用，/帮助 将回退为文字")
        return None
    try:
        image = _draw_card()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - 出图失败不能影响帮助命令
        logger.exception("渲染 /帮助 卡片失败，回退为文字")
        return None
