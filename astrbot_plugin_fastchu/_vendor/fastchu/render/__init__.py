"""渲染层。"""

from __future__ import annotations

from .help_card import render_help_card
from .text import (
    HELP_SECTIONS,
    HELP_TEXT,
    HelpItem,
    HelpSection,
    format_difficulty,
    render_candidate_page,
    render_catalog_summary,
    render_filter_result,
    render_help,
    render_notes,
    render_region_candidates,
    render_region_song_detail,
    render_song_candidates,
    render_song_detail,
    render_title_candidates,
    render_title_detail,
    render_title_list,
    render_title_stats,
    short_version,
    split_message,
)

__all__ = [
    "HELP_SECTIONS",
    "HELP_TEXT",
    "HelpItem",
    "HelpSection",
    "format_difficulty",
    "render_candidate_page",
    "render_catalog_summary",
    "render_filter_result",
    "render_help",
    "render_help_card",
    "render_notes",
    "render_region_candidates",
    "render_region_song_detail",
    "render_song_candidates",
    "render_song_detail",
    "render_title_candidates",
    "render_title_detail",
    "render_title_list",
    "render_title_stats",
    "short_version",
    "split_message",
]
