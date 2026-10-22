"""筛选条件测试：解析、校验、匹配、排序。

重点在**报错质量**——条件写错时必须告诉用户「错在哪、能用什么」，
而不是返回一个空列表让人一脸懵。
"""

from __future__ import annotations

import pytest

from fastchu.errors import QuerySyntaxError
from fastchu.models import LevelIndex, Song
from fastchu.services.catalog import SongCatalog
from fastchu.services.query import SongFilter, filter_songs, parse_filter


@pytest.fixture
def catalog() -> SongCatalog:
    return SongCatalog.from_dict(
        {
            "songs": [
                {
                    "id": 1,
                    "title": "Original Hard",
                    "genre": "原创",
                    "bpm": 180,
                    "version": 23000,
                    "difficulties": [{"difficulty": 3, "level": "15", "level_value": 15.0}],
                },
                {
                    "id": 2,
                    "title": "Original Soft",
                    "genre": "原创",
                    "bpm": 120,
                    "version": 23000,
                    "difficulties": [{"difficulty": 3, "level": "12", "level_value": 12.0}],
                },
                {
                    "id": 3,
                    "title": "Touhou Tune",
                    "genre": "东方Project",
                    "bpm": 155,
                    "version": 24000,
                    "difficulties": [
                        {"difficulty": 3, "level": "14", "level_value": 14.0},
                        {"difficulty": 4, "level": "14+", "level_value": 14.5},
                    ],
                },
                {
                    "id": 4,
                    "title": "No BPM",
                    "genre": "其他游戏",
                    "version": 24000,
                    "difficulties": [{"difficulty": 3, "level": "13", "level_value": 13.0}],
                },
            ],
            "versions": [
                {"id": 1, "title": "CHUNITHM VERSE", "version": 23000},
                {"id": 2, "title": "CHUNITHM X-VERSE", "version": 24000},
            ],
            "genres": [
                {"id": 1, "genre": "原创"},
                {"id": 2, "genre": "东方Project"},
                {"id": 3, "genre": "其他游戏"},
            ],
        }
    )


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def test_parse_genre_by_name(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=原创", catalog)
    assert spec.genre_names == {"原创"}


def test_parse_genre_by_substring(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=东方", catalog)
    assert spec.genre_names == {"东方Project"}


def test_parse_genre_key_aliases(catalog: SongCatalog) -> None:
    for key in ("分类", "类型", "genre", "GENRE", "genres"):
        assert parse_filter(f"{key}=原创", catalog).genre_names == {"原创"}


def test_parse_multiple_genres_as_or(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=原创,东方Project", catalog)
    assert spec.genre_names == {"原创", "东方Project"}
    spec = parse_filter("分类=原创|东方Project", catalog)
    assert spec.genre_names == {"原创", "东方Project"}


def test_parse_version_by_number(catalog: SongCatalog) -> None:
    assert parse_filter("版本=23000", catalog).version_ids == {23000}


def test_parse_version_by_title(catalog: SongCatalog) -> None:
    assert parse_filter("版本=VERSE", catalog).version_ids == {23000}
    assert parse_filter("版本=x-verse", catalog).version_ids == {24000}


def test_parse_bpm_forms(catalog: SongCatalog) -> None:
    assert (parse_filter("bpm=150", catalog).bpm_min, parse_filter("bpm=150", catalog).bpm_max) == (150, 150)
    spec = parse_filter("bpm=150-200", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (150, 200)
    spec = parse_filter("bpm=200-150", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (150, 200), "写反了也要能用"
    spec = parse_filter("bpm>=150", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (150, None)
    spec = parse_filter("bpm<=200", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (None, 200)
    spec = parse_filter("bpm=150~200", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (150, 200)


def test_parse_multiple_bpm_conditions_intersect(catalog: SongCatalog) -> None:
    spec = parse_filter("bpm>=150 bpm<=180", catalog)
    assert (spec.bpm_min, spec.bpm_max) == (150, 180)


def test_parse_combined_conditions(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=原创 版本=VERSE bpm=150-200", catalog)
    assert spec.genre_names == {"原创"}
    assert spec.version_ids == {23000}
    assert (spec.bpm_min, spec.bpm_max) == (150, 200)


def test_parse_accepts_colon_separator(catalog: SongCatalog) -> None:
    assert parse_filter("分类:原创", catalog).genre_names == {"原创"}
    assert parse_filter("分类：原创", catalog).genre_names == {"原创"}  # 全角冒号


def test_parse_accepts_space_separated_tokens(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=原创，bpm=180", catalog)
    assert spec.genre_names == {"原创"}
    assert spec.bpm_min == 180


# --------------------------------------------------------------------------- #
# 解析报错：必须可操作
# --------------------------------------------------------------------------- #


def test_empty_condition(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("   ", catalog)
    assert "分类" in info.value.user_message


def test_missing_value(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("分类=", catalog)
    assert "键=值" in info.value.user_message


def test_missing_equals(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError, match="写法不对"):
        parse_filter("原创", catalog)


def test_unknown_key_lists_supported(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("难度=14", catalog)
    assert "不支持的筛选键" in info.value.user_message
    assert "分类" in info.value.user_message and "bpm" in info.value.user_message


def test_unknown_genre_lists_available(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("分类=不存在的分类", catalog)
    assert "可用分类" in info.value.user_message
    assert "原创" in info.value.user_message


def test_unknown_version_lists_recent(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("版本=99999", catalog)
    assert "最近几个版本" in info.value.user_message
    assert "X-VERSE" in info.value.user_message


def test_invalid_bpm(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("bpm=很快", catalog)
    assert "150-200" in info.value.user_message


def test_empty_bpm_range(catalog: SongCatalog) -> None:
    with pytest.raises(QuerySyntaxError) as info:
        parse_filter("bpm>=200 bpm<=100", catalog)
    assert "区间为空" in info.value.user_message


# --------------------------------------------------------------------------- #
# 匹配与排序
# --------------------------------------------------------------------------- #


def test_filter_by_genre(catalog: SongCatalog) -> None:
    songs = filter_songs(catalog, parse_filter("分类=原创", catalog))
    assert {s.id for s in songs} == {1, 2}


def test_filter_by_version(catalog: SongCatalog) -> None:
    songs = filter_songs(catalog, parse_filter("版本=24000", catalog))
    assert {s.id for s in songs} == {3, 4}


def test_filter_by_bpm_range(catalog: SongCatalog) -> None:
    songs = filter_songs(catalog, parse_filter("bpm=150-190", catalog))
    assert {s.id for s in songs} == {1, 3}


def test_filter_excludes_songs_without_bpm(catalog: SongCatalog) -> None:
    """设了 BPM 条件时，没有 BPM 的曲目不该被当成命中。"""
    songs = filter_songs(catalog, parse_filter("bpm>=1", catalog))
    assert 4 not in {s.id for s in songs}


def test_filter_combined(catalog: SongCatalog) -> None:
    songs = filter_songs(catalog, parse_filter("版本=23000 bpm>=150", catalog))
    assert {s.id for s in songs} == {1}


def test_filter_result_sorted_by_constant_desc(catalog: SongCatalog) -> None:
    songs = filter_songs(catalog, parse_filter("分类=原创", catalog))
    assert [s.id for s in songs] == [1, 2], "15.0 应当在 12.0 前面"


def test_filter_no_match_returns_empty(catalog: SongCatalog) -> None:
    assert filter_songs(catalog, parse_filter("版本=23000 bpm>=199", catalog)) == []


def test_filter_empty_spec_matches_everything(catalog: SongCatalog) -> None:
    spec = SongFilter()
    assert spec.is_empty
    assert len(filter_songs(catalog, spec)) == len(catalog)


def test_describe(catalog: SongCatalog) -> None:
    assert parse_filter("分类=原创", catalog).describe(catalog) == "分类=原创"
    assert parse_filter("bpm=150-200", catalog).describe(catalog) == "BPM=150-200"
    assert parse_filter("bpm>=150", catalog).describe(catalog) == "BPM>=150"
    text = parse_filter("分类=原创 版本=VERSE", catalog).describe(catalog)
    assert "分类=原创" in text and "版本=VERSE" in text


def test_filter_matches_uses_song_fields(catalog: SongCatalog) -> None:
    spec = parse_filter("分类=原创", catalog)
    song: Song = catalog.get(1)
    assert spec.matches(song)
    assert not spec.matches(catalog.get(3))


def test_max_constant_used_for_sorting(catalog: SongCatalog) -> None:
    """曲目的排序依据是**最高定数**（ULT 14.5 高于 MAS 14.0）。"""
    song = catalog.get(3)
    assert song.max_constant == pytest.approx(14.5)
    assert song.chart(LevelIndex.ULTIMA) is not None
