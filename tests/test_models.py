"""数据模型测试：字段对齐官方文档、容错、以及对渲染有用的派生属性。"""

from __future__ import annotations

import pytest

from fastchu.models import LevelIndex, Notes, Song, SongDifficulty, SongListBundle


# --------------------------------------------------------------------------- #
# 难度枚举
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, LevelIndex.BASIC),
        (5, LevelIndex.WORLDS_END),
        ("3", LevelIndex.MASTER),
        ("mas", LevelIndex.MASTER),
        ("MASTER", LevelIndex.MASTER),
        ("Ult", LevelIndex.ULTIMA),
        ("紫", LevelIndex.MASTER),
        ("we", LevelIndex.WORLDS_END),
        ("world's end", LevelIndex.WORLDS_END),
    ],
)
def test_level_index_parse(raw, expected: LevelIndex) -> None:
    assert LevelIndex.parse(raw) is expected


@pytest.mark.parametrize("raw", ["nope", "99", "", None, -1])
def test_level_index_parse_invalid(raw) -> None:
    assert LevelIndex.parse(raw) is None


def test_level_index_labels() -> None:
    assert LevelIndex.MASTER.label == "MASTER"
    assert LevelIndex.MASTER.short == "MAS"
    assert LevelIndex.WORLDS_END.short == "WE"


# --------------------------------------------------------------------------- #
# 物量
# --------------------------------------------------------------------------- #


def test_notes_as_pairs_skips_missing() -> None:
    notes = Notes(total=100, tap=80, flick=0)
    pairs = dict(notes.as_pairs())
    assert pairs == {"总物量": 100, "TAP": 80, "FLICK": 0}
    assert "HOLD" not in pairs, "缺失字段不该出现"


def test_notes_all_none_is_empty() -> None:
    assert Notes().as_pairs() == []


# --------------------------------------------------------------------------- #
# 谱面
# --------------------------------------------------------------------------- #


def test_difficulty_display_level() -> None:
    chart = SongDifficulty.model_validate({"difficulty": 3, "level": "14+", "level_value": 14.8})
    assert chart.display_level == "14+"
    assert chart.constant_text == "14.8"
    assert chart.has_constant


def test_difficulty_display_level_falls_back_to_constant() -> None:
    """标级缺失时用定数兜底，而不是显示问号。"""
    chart = SongDifficulty.model_validate({"difficulty": 3, "level_value": 14.8})
    assert chart.display_level == "14.8"


def test_worlds_end_has_no_constant() -> None:
    chart = SongDifficulty.model_validate(
        {"difficulty": 5, "level": "0", "level_value": 0, "origin_id": 163, "kanji": "止", "star": 3}
    )
    assert chart.is_worlds_end
    assert not chart.has_constant
    assert chart.constant_text == "-"
    assert chart.display_level == "★3"


def test_worlds_end_missing_star() -> None:
    chart = SongDifficulty.model_validate({"difficulty": 5, "level": "0", "level_value": 0})
    assert chart.display_level == "★?"


def test_difficulty_tolerates_unknown_fields() -> None:
    chart = SongDifficulty.model_validate(
        {"difficulty": 3, "level": "14", "level_value": 14.0, "brand_new": 1}
    )
    assert chart.level_value == pytest.approx(14.0)


# --------------------------------------------------------------------------- #
# 曲目
# --------------------------------------------------------------------------- #


@pytest.fixture
def song() -> Song:
    return Song.model_validate(
        {
            "id": 464,
            "title": "World Vanquisher",
            "artist": "void (Mournfinale)",
            "genre": "原创",
            "bpm": 170,
            "version": 12000,
            "difficulties": [
                {"difficulty": 2, "level": "13+", "level_value": 13.8, "notes": {"total": 1200}},
                {"difficulty": 3, "level": "15+", "level_value": 15.5, "notes": {"total": 3000}},
            ],
        }
    )


def test_song_chart_lookup(song: Song) -> None:
    assert song.chart(LevelIndex.MASTER) is not None
    assert song.chart(LevelIndex.MASTER).level_value == pytest.approx(15.5)  # type: ignore[union-attr]
    assert song.chart(LevelIndex.ULTIMA) is None


def test_song_max_constant(song: Song) -> None:
    assert song.max_constant == pytest.approx(15.5)


def test_song_max_constant_ignores_worlds_end() -> None:
    we_only = Song.model_validate(
        {
            "id": 1,
            "title": "x",
            "difficulties": [{"difficulty": 5, "level": "0", "level_value": 0, "star": 2}],
        }
    )
    assert we_only.max_constant == 0.0


def test_song_playable_difficulties_sorted(song: Song) -> None:
    levels = [d.difficulty for d in song.playable_difficulties]
    assert levels == [LevelIndex.EXPERT, LevelIndex.MASTER]


def test_song_difficulty_summary(song: Song) -> None:
    assert song.difficulty_summary() == "EXP 13+ / MAS 15+"


def test_song_charts_by_descending_constant(song: Song) -> None:
    charts = song.charts_by_descending_constant()
    assert charts[0].level_value >= charts[-1].level_value


def test_song_has_notes(song: Song) -> None:
    assert song.has_notes
    assert not Song.model_validate({"id": 1, "title": "x"}).has_notes


def test_song_defaults() -> None:
    song = Song.model_validate({"id": 1, "title": "x"})
    assert song.difficulties == []
    assert song.disabled is False
    assert song.locked is False
    assert song.bpm is None


def test_song_list_bundle_defaults() -> None:
    bundle = SongListBundle.model_validate({})
    assert (bundle.songs, bundle.genres, bundle.versions) == ([], [], [])


def test_song_list_bundle_parses_real_shape(raw_bundle: dict) -> None:
    bundle = SongListBundle.model_validate(raw_bundle)
    assert len(bundle.songs) > 1000
    assert bundle.genres and bundle.versions
    first = bundle.songs[0]
    assert first.id > 0 and first.title
