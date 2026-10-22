"""渲染测试。

除了内容正确，这里还专门锁住**「尽可能轻量级」**这条产品要求：
行数上限、行宽上限、不出现 ``?`` 占位符、不重复输出同一数值。
"""

from __future__ import annotations

import pytest

from fastchu.models import LevelIndex, Song
from fastchu.render import (
    HELP_TEXT,
    render_title_candidates,
    render_title_detail,
    render_title_list,
    render_title_stats,
    render_catalog_summary,
    render_filter_result,
    render_help,
    render_notes,
    render_song_candidates,
    render_song_detail,
    short_version,
    split_message,
)
from fastchu.services.catalog import SongCatalog
from fastchu.services.query import parse_filter
from fastchu.services.search import SongSearch

from .conftest import make_settings

#: 轻量级约束：详情不超过这么多行、单行不超过这么多字符
MAX_DETAIL_LINES = 12  # 分块排版后行数变多，但仍保持"一眼扫完"的量级
MAX_LINE_WIDTH = 90


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def world_vanquisher(catalog: SongCatalog) -> Song:
    song = next((s for s in catalog.songs if s.title == "World Vanquisher"), None)
    if song is None:  # pragma: no cover - 夹具里一定有
        pytest.skip("夹具里没有 World Vanquisher")
    return song


def _song_with_zero_flick() -> tuple[SongCatalog, Song]:
    """构造一首 MAS 谱面 flick 为 0 的曲目。

    真实数据里 MASTER/ULTIMA 基本都有 flick，所以这里用合成数据把
    「详情省零值 / 物量保留零值」这条规则测清楚。
    """
    catalog = SongCatalog.from_dict(
        {
            "songs": [
                {
                    "id": 7,
                    "title": "Zero Flick",
                    "artist": "a",
                    "genre": "原创",
                    "bpm": 150,
                    "version": 20000,
                    "difficulties": [
                        {
                            "difficulty": 3,
                            "level": "14",
                            "level_value": 14.0,
                            "notes": {
                                "total": 1000,
                                "tap": 800,
                                "hold": 100,
                                "slide": 100,
                                "air": 0,
                                "flick": 0,
                            },
                        }
                    ],
                }
            ],
            "genres": [{"id": 1, "genre": "原创"}],
            "versions": [{"id": 1, "title": "CHUNITHM VERSE", "version": 20000}],
        }
    )
    return catalog, catalog.songs[0]


# --------------------------------------------------------------------------- #
# 详情
# --------------------------------------------------------------------------- #


def test_detail_first_line_is_title(world_vanquisher: Song, catalog: SongCatalog, settings) -> None:
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    # 标题行是「#ID - 曲名」
    assert text.splitlines()[0] == f"#{world_vanquisher.id} - {world_vanquisher.title}"


def test_detail_contains_meta_and_id(world_vanquisher: Song, catalog: SongCatalog, settings) -> None:
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    assert world_vanquisher.genre in text
    assert "曲速" in text and str(world_vanquisher.bpm) in text
    assert f"#{world_vanquisher.id}" in text
    assert short_version(catalog, world_vanquisher.version) in text


def test_detail_contains_difficulties(world_vanquisher: Song, catalog: SongCatalog, settings) -> None:
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    master = world_vanquisher.chart(LevelIndex.MASTER)
    assert master is not None
    # 难度直接用定数（不再写 "MAS 15+(15.5)" 那种括号形式）
    assert f"MAS {master.constant_text}" in text
    assert master.constant_text in text


def test_detail_skips_basic_and_advanced(catalog: SongCatalog, settings) -> None:
    """低难度与查歌无关，不该占版面。"""
    song = next(s for s in catalog.songs if s.chart(LevelIndex.BASIC) is not None)
    text = render_song_detail(song, catalog, settings=settings)
    assert "BAS " not in text
    assert "ADV " not in text


def test_detail_is_lightweight(world_vanquisher: Song, catalog: SongCatalog, settings) -> None:
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    lines = text.splitlines()
    assert len(lines) <= MAX_DETAIL_LINES, f"详情 {len(lines)} 行，超出轻量级上限"
    for line in lines:
        assert len(line) <= MAX_LINE_WIDTH, f"行过宽: {line!r}"


def test_detail_has_no_placeholder_marks(
    world_vanquisher: Song, catalog: SongCatalog, settings
) -> None:
    """没有的字段直接不显示，不要用 ``?`` 占位。"""
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    assert "?" not in text
    assert "None" not in text


def test_detail_notes_zero_values_dropped() -> None:
    """详情里的物量行要省掉 0 值（``FLICK0`` 是噪音）。"""
    catalog, song = _song_with_zero_flick()
    text = render_song_detail(song, catalog, notes_mode="full")
    notes_line = next(line for line in text.splitlines() if line.startswith("物量"))
    assert "FLICK0" not in notes_line
    assert "TAP800" in notes_line, "非零分项要保留"


def test_detail_notes_can_be_hidden(
    world_vanquisher: Song, catalog: SongCatalog, settings
) -> None:
    text = render_song_detail(world_vanquisher, catalog, settings=settings, notes_mode="off")
    assert "物量" not in text


def test_detail_hides_aliases(world_vanquisher: Song, catalog: SongCatalog, settings) -> None:
    """别名只用于搜索，不再显示在详情里（按「尽量轻量」的要求去掉）。"""
    text = render_song_detail(world_vanquisher, catalog, settings=settings)
    assert "别名" not in text


def test_detail_worlds_end() -> None:
    catalog = SongCatalog.from_dict(
        {
            "songs": [
                {
                    "id": 1,
                    "title": "WE Song",
                    "genre": "原创",
                    "difficulties": [
                        {"difficulty": 5, "level": "0", "level_value": 0, "star": 4},
                        {"difficulty": 3, "level": "14", "level_value": 14.0},
                    ],
                }
            ],
            "genres": [{"id": 1, "genre": "原创"}],
            "versions": [{"id": 1, "title": "CHUNITHM", "version": 10000}],
        }
    )
    text = render_song_detail(catalog.songs[0], catalog)
    # WE 已不进谱面列表，只剩 EXP/MAS/ULT
    assert "WE ★" not in text, "WORLD’S END 不该出现在详情里"
    assert "MAS 14" in text


# --------------------------------------------------------------------------- #
# 物量
# --------------------------------------------------------------------------- #


def test_notes_lists_exp_and_above(catalog: SongCatalog) -> None:
    song = next(s for s in catalog.songs if s.chart(LevelIndex.MASTER) is not None)
    text = render_notes(song, catalog)
    assert "BAS " not in text
    assert "ADV " not in text
    assert "MAS " in text
    assert text.splitlines()[0].endswith("· 物量")


def test_notes_keeps_zero_values() -> None:
    """物量是数据视图，「没有 flick」本身是信息，所以保留 0。"""
    catalog, song = _song_with_zero_flick()
    text = render_notes(song, catalog)
    assert "FLICK0" in text


def test_notes_indent_detail(catalog: SongCatalog) -> None:
    song = next(s for s in catalog.songs if s.chart(LevelIndex.MASTER) is not None)
    text = render_notes(song, catalog)
    detail_lines = [line for line in text.splitlines() if line.startswith("  ")]
    assert detail_lines, "明细应当缩进一行"


def test_notes_without_note_data_gives_actionable_hint() -> None:
    catalog = SongCatalog.from_dict(
        {
            "songs": [
                {
                    "id": 1,
                    "title": "No Notes",
                    "difficulties": [{"difficulty": 3, "level": "14", "level_value": 14.0}],
                }
            ],
            "genres": [],
            "versions": [],
        }
    )
    text = render_notes(catalog.songs[0], catalog)
    assert "fetch_reference_data" in text


def test_notes_song_without_notes_but_catalog_has_them(catalog: SongCatalog) -> None:
    """曲库有物量但这首没有 → 提示「这首歌没有」，而不是让用户去重抓。"""
    song = Song.model_validate(
        {
            "id": 1,
            "title": "x",
            "difficulties": [{"difficulty": 3, "level": "14", "level_value": 14.0}],
        }
    )
    text = render_notes(song, catalog)
    assert "这首歌没有物量数据" in text


# --------------------------------------------------------------------------- #
# 候选列表
# --------------------------------------------------------------------------- #


def test_candidates_header_and_rows(catalog: SongCatalog, search: SongSearch, settings) -> None:
    matches = search.search("world")
    text = render_song_candidates("world", matches, catalog, settings=settings)
    lines = text.splitlines()
    assert lines[0].startswith("`world` 命中")
    assert any(line[:1].isdigit() for line in lines[1:])


def test_candidates_respects_limit(catalog: SongCatalog, search: SongSearch, tmp_path) -> None:
    settings = make_settings(tmp_path, search_result_limit=2)
    matches = search.search("a")
    text = render_song_candidates("a", matches, catalog, settings=settings)
    numbered = [line for line in text.splitlines() if line[:1].isdigit()]
    assert len(numbered) == 2
    assert "还有" in text


def test_candidate_lines_are_compact(catalog: SongCatalog, search: SongSearch, settings) -> None:
    matches = search.search("world")
    text = render_song_candidates("world", matches, catalog, settings=settings)
    for line in text.splitlines():
        assert len(line) <= MAX_LINE_WIDTH


# --------------------------------------------------------------------------- #
# 筛选结果
# --------------------------------------------------------------------------- #


def test_filter_result_header_and_rows(catalog: SongCatalog, settings) -> None:
    spec = parse_filter("分类=原创", catalog)
    songs = [s for s in catalog.songs if s.genre == "原创"]
    text = render_filter_result(spec, songs, catalog, settings=settings)
    assert text.splitlines()[0].startswith("分类=原创")
    assert str(len(songs)) in text.splitlines()[0]


def test_filter_result_empty(catalog: SongCatalog, settings) -> None:
    spec = parse_filter("分类=原创 bpm=1", catalog)
    text = render_filter_result(spec, [], catalog, settings=settings)
    assert "没有符合条件" in text


def test_filter_result_respects_limit(catalog: SongCatalog, tmp_path) -> None:
    settings = make_settings(tmp_path, filter_result_limit=5)
    spec = parse_filter("分类=原创", catalog)
    songs = [s for s in catalog.songs if s.genre == "原创"]
    text = render_filter_result(spec, songs, catalog, settings=settings)
    numbered = [line for line in text.splitlines() if line[:1].isdigit()]
    assert len(numbered) == 5
    assert "还有" in text


def test_filter_result_lists_hardest_first(catalog: SongCatalog, settings) -> None:
    spec = parse_filter("分类=原创", catalog)
    songs = sorted(
        (s for s in catalog.songs if s.genre == "原创"), key=lambda s: -s.max_constant
    )
    text = render_filter_result(spec, songs, catalog, settings=settings)
    assert songs[0].title[:10] in text


# --------------------------------------------------------------------------- #
# 帮助
# --------------------------------------------------------------------------- #


def test_help_mentions_all_commands() -> None:
    """帮助里必须覆盖机器人实际提供的全部命令。

    用户要的是「直接打 /歌曲名」，所以帮助里必须把这个用法放在最显眼的位置；
    已移除的 ``/筛选``、``/物量`` 不该再出现。
    """
    assert "/歌曲名" in HELP_TEXT
    for command in ("/称号", "/帮助", "/#曲目ID"):
        assert command in HELP_TEXT
    for removed in ("/筛选", "/物量"):
        assert removed not in HELP_TEXT, f"{removed} 已从机器人移除，不该留在帮助里"


def test_help_has_no_catalog_summary(catalog: SongCatalog) -> None:
    """帮助就是命令表——不再附曲库数量/版本这类概况。"""
    text = render_help(catalog)
    assert text == HELP_TEXT
    assert str(len(catalog)) not in text


def test_catalog_summary_warns_without_notes() -> None:
    assert "物量" in render_catalog_summary(SongCatalog([]))


# --------------------------------------------------------------------------- #
# 分页
# --------------------------------------------------------------------------- #


def test_split_message_short_text() -> None:
    assert split_message("hello", limit=100) == ["hello"]


def test_split_message_empty() -> None:
    assert split_message("") == []
    assert split_message("   ") == []


def test_split_message_respects_limit_and_keeps_lines() -> None:
    text = "\n".join(f"{i}. 很长的曲目名称" * 3 for i in range(1, 200))
    chunks = split_message(text, limit=800)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 800
    rebuilt = [line for chunk in chunks for line in chunk.split("\n")]
    assert rebuilt == text.split("\n")


def test_split_message_clamps_tiny_limit() -> None:
    chunks = split_message("x" * 500, limit=1)
    assert all(len(c) <= 200 for c in chunks)


def test_split_message_hard_splits_long_line() -> None:
    chunks = split_message("あ" * 500, limit=200)
    assert "".join(chunks) == "あ" * 500


# --------------------------------------------------------------------------- #
# 称号渲染
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def titles():
    from fastchu.services.titles import TrophyCatalog, load_trophies_from_file

    from .conftest import TROPHY_FIXTURE

    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具")
    return TrophyCatalog(load_trophies_from_file(TROPHY_FIXTURE))


def test_title_detail_shows_color_and_condition(titles) -> None:
    trophy = next(t for t in titles.special if len(t.song_ids) >= 1)
    text = render_title_detail(trophy, titles)
    lines = text.splitlines()
    assert lines[0] == trophy.name
    assert f"[{trophy.color_label}]" in lines[1]
    assert f"#{trophy.id}" in lines[1]
    assert any(line.startswith("条件") for line in lines)


def test_title_detail_is_lightweight(titles) -> None:
    trophy = next(t for t in titles.special if len(t.song_ids) >= 1)
    text = render_title_detail(trophy, titles)
    assert len(text.splitlines()) <= 5
    for line in text.splitlines():
        assert len(line) <= 200


def test_title_detail_lists_songs(titles) -> None:
    trophy = next(t for t in titles.of_category("all_songs"))
    text = render_title_detail(trophy, titles)
    assert "曲目" in text
    assert "共" in text and "首" in text


def test_title_candidates_rows(titles) -> None:
    hits = titles.search("Legend")
    assert hits
    text = render_title_candidates("Legend", hits)
    lines = text.splitlines()
    assert lines[0].startswith("`Legend` 命中")
    assert all("[" in line for line in lines[1:] if line[:1].isdigit())


def test_title_candidates_compact(titles) -> None:
    hits = titles.search("e", limit=30)
    text = render_title_candidates("e", hits)
    for line in text.splitlines():
        assert len(line) <= MAX_LINE_WIDTH


def test_title_stats_shows_partition(titles) -> None:
    text = render_title_stats(titles)
    assert "称号库" in text
    assert "特殊称号" in text
    assert "已排除固定称号" in text
    # 颜色用中文标签，不能把原始枚举值吐给用户
    assert "silver" not in text and "platina" not in text
    assert "铂金" in text or "银" in text


def test_title_stats_lists_categories(titles) -> None:
    text = render_title_stats(titles)
    assert "主要分类" in text
    assert "用法" in text


def test_title_list_renders(titles) -> None:
    picked = titles.listing(special_only=True, color="rainbow", limit=50)
    assert picked
    text = render_title_list(picked, titles, header="虹色特殊称号", limit=5)
    assert text.splitlines()[0] == "虹色特殊称号"
    assert "[" in text


def test_title_list_truncation_note(titles) -> None:
    picked = titles.listing(special_only=True, limit=50)
    text = render_title_list(picked, titles, limit=3)
    assert "还有" in text

