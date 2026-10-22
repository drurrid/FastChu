"""搜索测试。

这是查歌 bot 的**核心能力**，所以覆盖得最细：归一化、别名、以及
「同样命中时谁排前面」——后者最容易做得反直觉。
"""

from __future__ import annotations

import pytest

from fastchu.models import LevelIndex, Song
from fastchu.services.catalog import SongCatalog
from fastchu.services.search import (
    SongSearch,
    fold_diacritics,
    normalize,
    normalize_words,
)

# --------------------------------------------------------------------------- #
# 合成曲库：把排序规则暴露得干干净净
# --------------------------------------------------------------------------- #


def _song(song_id: int, title: str, **kwargs) -> dict:
    payload = {
        "id": song_id,
        "title": title,
        "difficulties": [{"difficulty": 3, "level": "14", "level_value": 14.0}],
    }
    payload.update(kwargs)
    return payload


@pytest.fixture
def tiny_catalog() -> SongCatalog:
    return SongCatalog.from_dict(
        {
            "songs": [
                _song(1, "World Vanquisher", artist="void (Mournfinale)", genre="原创", bpm=170, version=12000),
                _song(2, "Worlds End", genre="东方Project", bpm=200, version=13000),
                _song(3, "Rule the World!!", genre="音击舞萌", bpm=180, version=20000),
                _song(4, "Rebellion", genre="原创", bpm=150, version=20000),
                _song(5, "The Rebel", genre="原创", bpm=160, version=21000),
                _song(6, "脳天直撃", genre="原创", bpm=190, version=21000),
                _song(7, "Café de Étoile", genre="流行 & 动漫", bpm=128, version=22000),
                _song(8, "【東方】Bad Apple!!", genre="东方Project", bpm=138, version=10000),
            ],
            "versions": [
                {"id": 1, "title": "CHUNITHM", "version": 10000},
                {"id": 2, "title": "CHUNITHM STAR", "version": 12000},
                {"id": 3, "title": "CHUNITHM VERSE", "version": 21000},
            ],
            "genres": [
                {"id": 1, "genre": "原创"},
                {"id": 2, "genre": "东方Project"},
                {"id": 3, "genre": "流行 & 动漫"},
            ],
        },
        aliases=[
            {"song_id": 1, "aliases": ["世征", "wv", "噔噔噔"]},
            {"song_id": 6, "aliases": ["脑天", "脑天直击"]},
        ],
    )


@pytest.fixture
def tiny_search(tiny_catalog: SongCatalog) -> SongSearch:
    return SongSearch(tiny_catalog)


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("World", "world"),
        ("ＷＯＲＬＤ", "world"),  # 全角
        ("Bad Apple!!", "badapple"),
        ("B.B.K.K.B.K.K.", "bbkkbkk"),
        ("【東方】", "東方"),
        ("Grievous Lady", "grievouslady"),
        ("  spaced  out  ", "spacedout"),
        ("Ｒｅ：Ｂｏｏｔ", "reboot"),  # 全角 + 标点
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_words_keeps_word_boundaries() -> None:
    assert normalize_words("World Vanquisher") == ["world", "vanquisher"]
    assert normalize_words("Bad Apple!!") == ["bad", "apple"]
    assert normalize_words("【東方】Bad Apple!!") == ["東方", "bad", "apple"]
    assert normalize_words("   ") == []


def test_fold_diacritics() -> None:
    assert fold_diacritics("café") == "cafe"
    assert fold_diacritics("ガ") == "カ"
    assert fold_diacritics("plain") == "plain"


# --------------------------------------------------------------------------- #
# 命中方式
# --------------------------------------------------------------------------- #


def test_exact_title(tiny_search: SongSearch) -> None:
    matches = tiny_search.search("Rebellion")
    assert matches[0].song.id == 4
    assert matches[0].reason == "title_exact"


def test_exact_title_ignores_punctuation_and_case(tiny_search: SongSearch) -> None:
    for keyword in ("rule the world", "Rule The World!!", "ＲＵＬＥ ＴＨＥ ＷＯＲＬＤ"):
        assert tiny_search.search(keyword)[0].song.id == 3


def test_song_id_lookup(tiny_search: SongSearch) -> None:
    matches = tiny_search.search("6")
    assert matches[0].song.id == 6
    assert matches[0].reason == "song_id"


def test_alias_exact_beats_title_prefix(tiny_search: SongSearch) -> None:
    """别名精确命中要给足权重——中二曲子的通用叫法极多。"""
    top = tiny_search.search("wv")[0]
    assert top.song.id == 1
    assert top.reason == "alias_exact"


def test_chinese_alias_finds_japanese_title(tiny_search: SongSearch) -> None:
    top = tiny_search.search("脑天")[0]
    assert top.song.id == 6
    assert top.reason == "alias_exact"


def test_artist_match(tiny_search: SongSearch) -> None:
    matches = tiny_search.search("Mournfinale")
    assert matches[0].song.id == 1
    assert matches[0].reason == "artist_contains"


def test_diacritics_fuzzy_match(tiny_search: SongSearch) -> None:
    """打不出重音符号也应能搜到。"""
    matches = tiny_search.search("cafe de etoile")
    assert matches
    assert matches[0].song.id == 7
    assert matches[0].reason == "loose"


def test_no_match_returns_empty(tiny_search: SongSearch) -> None:
    assert tiny_search.search("完全不存在的曲子名") == []


def test_empty_keyword_returns_empty(tiny_search: SongSearch) -> None:
    assert tiny_search.search("") == []
    assert tiny_search.search("   ") == []
    assert tiny_search.search("!!!") == []  # 归一化后为空


def test_limit(tiny_search: SongSearch) -> None:
    assert len(tiny_search.search("world", limit=1)) == 1
    assert len(tiny_search.search("world")) >= 2


# --------------------------------------------------------------------------- #
# 排序：这是最容易做反的地方
# --------------------------------------------------------------------------- #


def test_first_word_beats_prefix_beats_middle_word(tiny_search: SongSearch) -> None:
    """搜 ``world`` 时的期望顺序：

    1. ``World Vanquisher`` —— 首词就是 world
    2. ``Worlds End``       —— 标题以 world 开头（词的一部分）
    3. ``Rule the World!!`` —— world 是整词，但在末尾
    """
    reasons = [m.reason for m in tiny_search.search("world")]
    titles = [m.song.title for m in tiny_search.search("world")]
    assert titles[0] == "World Vanquisher"
    assert titles.index("Worlds End") < titles.index("Rule the World!!")
    assert reasons[0] == "title_first_word"


def test_prefix_beats_middle_word(tiny_search: SongSearch) -> None:
    """``rebel`` 应当先给 ``Rebellion``（前缀），再给 ``The Rebel``（末尾整词）。"""
    titles = [m.song.title for m in tiny_search.search("rebel")]
    assert titles[0] == "Rebellion"
    assert "The Rebel" in titles
    assert titles.index("Rebellion") < titles.index("The Rebel")


def test_longer_query_picks_correct_song(tiny_search: SongSearch) -> None:
    """加长关键词应当把范围收窄到目标。"""
    assert [m.song.id for m in tiny_search.search("worlds")] == [2]


def test_shorter_title_wins_within_same_tier(tiny_search: SongSearch) -> None:
    """同档内短标题优先（都含 ``world`` 时，短的更具体）。"""
    matches = [m for m in tiny_search.search("world") if m.reason == "title_word"]
    titles = [m.song.title for m in matches]
    assert titles == sorted(titles, key=len) or len(titles) < 2


# --------------------------------------------------------------------------- #
# best()：能否确定是哪一首
# --------------------------------------------------------------------------- #


def test_best_returns_single_match(tiny_search: SongSearch) -> None:
    best = tiny_search.best("Rebellion")
    assert best is not None and best.song.id == 4


def test_best_returns_none_when_ambiguous(tiny_search: SongSearch) -> None:
    """``world`` 命中多首且分数接近时，不能擅自替用户决定。"""
    assert tiny_search.best("world") is None


def test_best_accepts_clear_winner(tiny_search: SongSearch) -> None:
    """榜首明显领先（例如别名精确命中）时可以直出详情。"""
    best = tiny_search.best("wv")
    assert best is not None and best.song.id == 1


def test_best_returns_none_for_no_match(tiny_search: SongSearch) -> None:
    assert tiny_search.best("zzzzz") is None


# --------------------------------------------------------------------------- #
# 真实曲库上的整体表现（离线）
# --------------------------------------------------------------------------- #


def test_real_catalog_exact_title(search: SongSearch, catalog: SongCatalog) -> None:
    song = catalog.songs[0]
    top = search.search(song.title)[0]
    assert top.song.id == song.id


def test_real_catalog_search_is_fast(search: SongSearch) -> None:
    """1458 首的线性扫描必须够快，否则群里并发会拖垮。"""
    import time

    start = time.perf_counter()
    for _ in range(50):
        search.search("world")
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"50 次搜索耗时 {elapsed:.2f}s，太慢"


def test_real_catalog_typical_queries_return_something(search: SongSearch) -> None:
    for keyword in ("Rebellion", "World Vanquisher", "Grievous", "東方"):
        assert search.search(keyword), f"{keyword} 应当能搜到"


def test_index_covers_all_songs(search: SongSearch, catalog: SongCatalog) -> None:
    assert len(search) == len(catalog)


def test_song_title_from_search_is_real(search: SongSearch) -> None:
    song: Song = search.search("Rebellion")[0].song
    assert song.id > 0
    assert song.chart(LevelIndex.MASTER) is not None
