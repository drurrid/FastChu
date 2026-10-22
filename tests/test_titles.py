"""称号分类与检索测试。

这一层是整个「称号盘点」的数据基础，所以要锁住两件事：

1. **分类规则**：常规 AJ 与角色养成必须被排除，且规则可解释；
2. **颜文字识别**：不能靠符号密度这种统计量，否则 ``D.E.A.D.L.Y.``
   这类标题会被大面积误判。
"""

from __future__ import annotations

import pytest

from fastchu.models import Trophy
from fastchu.services.titles import (
    EXCLUDED_CATEGORIES,
    TrophyCatalog,
    TrophyCategory,
    classify,
    is_kaomoji,
    load_trophies_from_file,
    tier,
    tier_label,
)

from .conftest import TROPHY_FIXTURE


@pytest.fixture(scope="module")
def catalog() -> TrophyCatalog:
    if not TROPHY_FIXTURE.is_file():  # pragma: no cover
        pytest.skip("缺少称号夹具，请运行 scripts/build_title_fixture.py")
    return TrophyCatalog(load_trophies_from_file(TROPHY_FIXTURE))


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #


def _trophy(**kwargs) -> Trophy:
    base = {"id": 1, "name": "x", "color": "gold", "description": ""}
    base.update(kwargs)
    return Trophy.model_validate(base)


def test_per_song_aj_is_excluded_only_when_named_after_song() -> None:
    """单曲 AJ：**称号名就是曲名**时才当作固定的常规 AJ 称号排除。"""
    trophy = _trophy(
        name="Blue Noise",
        description="Blue Noise/EXPERT/ALL JUSTICE達成",
        required=[{"difficulties": [2], "full_combo": "alljustice", "songs": [{"id": 33, "title": "Blue Noise"}]}],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.PER_SONG_AJ
    assert info.category in EXCLUDED_CATEGORIES
    assert not info.is_special
    assert info.excluded == "常规 AJ 称号"


def test_named_aj_title_is_special() -> None:
    """回归测试：**名字不是曲名**的 AJ 称号是专属成就称号，必须保留。

    典型例子：``End Time`` 的 ``賢者タイム``（全难度 AJ）。
    早期实现把所有「单曲 + AJ」一律排除，把这些称号一起洗掉了。
    """
    trophy = _trophy(
        name="賢者タイム",
        color="platina",
        description="End Time/MASTER以下全て/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [0, 1, 2, 3],
                "full_combo": "alljustice",
                "songs": [{"id": 619, "title": "End Time"}],
            }
        ],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.PER_SONG_AJ_SPECIAL
    assert info.is_special


def test_all_difficulty_aj_with_other_name_is_special() -> None:
    """``MASTER以下全て``（全难度 AJ）且名字不是曲名 → 特殊称号。"""
    trophy = _trophy(
        name="賢者タイム",
        description="End Time/MASTER以下全て/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [0, 1, 2, 3],
                "full_combo": "alljustice",
                "songs": [{"id": 619, "title": "End Time"}],
            }
        ],
    )
    assert classify(trophy).category == TrophyCategory.PER_SONG_AJ_SPECIAL


def test_full_combo_is_not_aj() -> None:
    """FULL COMBO ≠ ALL JUSTICE：FC 称号单独归类，不能算「单曲 AJ 特殊称号」。"""
    trophy = _trophy(
        name="各駅停車で旅をして",
        description="ラグトレイン/MASTER/SPEED 1/FULL COMBO達成",
        required=[
            {
                "difficulties": [3],
                "full_combo": "fullcombo",
                "songs": [{"id": 500, "title": "ラグトレイン"}],
            }
        ],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.PER_SONG_FC
    assert info.is_special


def test_all_justice_still_aj() -> None:
    """对照：alljustice 仍然是 AJ 分支。"""
    trophy = _trophy(
        name="賢者タイム",
        description="End Time/MASTER/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [3],
                "full_combo": "alljustice",
                "songs": [{"id": 619, "title": "End Time"}],
            }
        ],
    )
    assert classify(trophy).category == TrophyCategory.PER_SONG_AJ_SPECIAL


def test_rule_is_name_only_not_difficulty_count() -> None:
    """判据只看名字：名字==曲名就是固定称号，不看覆盖了几个难度。"""
    trophy = _trophy(
        name="Some Song",
        description="Some Song/MASTER以下全て/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [0, 1, 2, 3],
                "full_combo": "alljustice",
                "songs": [{"id": 1, "title": "Some Song"}],
            }
        ],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.PER_SONG_AJ
    assert not info.is_special


def test_single_difficulty_named_aj_still_special() -> None:
    """单难度但名字另有其名，同样是特殊称号。"""
    trophy = _trophy(
        name="パカラー",
        description="Blue Noise/JET/EXPERT/ALL JUSTICE達成",
        required=[
            {
                "difficulties": [2],
                "full_combo": "alljustice",
                "songs": [{"id": 33, "title": "Blue Noise"}, {"id": 89, "title": "JET"}],
            }
        ],
    )
    assert classify(trophy).category == TrophyCategory.PER_SONG_AJ_SPECIAL


def test_character_rank_is_excluded() -> None:
    """无 required + 提到角色等级 → 角色养成称号，必须排除。"""
    trophy = _trophy(description="キャラクターランク 100/プレイ", color="platina")
    info = classify(trophy)
    assert info.category == TrophyCategory.CHARACTER_RANK
    assert not info.is_special


def test_ultima_aj_is_excluded() -> None:
    """究极（ULTIMA）称号的名字就是曲名，属于固定称号——全部排除。"""
    trophy = _trophy(
        color="ultima",
        description="晴る/ULTIMA/ALL JUSTICE達成",
        required=[{"difficulties": [4], "full_combo": "alljustice", "songs": [{"id": 1001, "title": "晴る"}]}],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.ULTIMA_AJ
    assert not info.is_special


def test_ultima_rank_still_rank_by_condition() -> None:
    """非究极色的单曲评级仍然归为评级称号。

    （究极色一律按固定称号排除，不再看条件；这里用普通颜色验证评级分支。）
    """
    trophy = _trophy(
        color="gold",
        description="晴る/ULTIMA/RANK SSS達成",
        required=[{"difficulties": [4], "rank": "sss", "songs": [{"id": 1001, "title": "晴る"}]}],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.PER_SONG_RANK


def test_all_songs_by_song_count() -> None:
    songs = [{"id": i, "title": f"s{i}"} for i in range(60)]
    trophy = _trophy(
        color="image",
        description="CHUNITHMの全ての譜面でALL JUSTICEを達成した証です。",
        required=[{"difficulties": [0, 1, 2, 3], "full_combo": "alljustice", "songs": songs}],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.ALL_SONGS
    assert info.is_special


def test_multiplayer_by_description() -> None:
    trophy = _trophy(
        description="M.S.S.Planet/2人で店内マッチング/FULL CHAIN達成",
        required=[{"full_chain": "fullchain", "songs": [{"id": 117, "title": "M.S.S.Planet"}]}],
    )
    info = classify(trophy)
    assert info.category == TrophyCategory.MULTIPLAYER
    assert info.is_special


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("CHUNITHMから引き継ぎ", TrophyCategory.INHERITED),
        ("『CHUNITHM NEW』でログイン300日達成", TrophyCategory.LOGIN_DAYS),
        ("プレイヤーレベルが10回限界突破", TrophyCategory.PLAYER_LEVEL),
        ("マップ制覇！", TrophyCategory.MAP_CLEAR),
        ("RATING 14.00達成", TrophyCategory.RATING),
        ("デンジャースキル発動", TrophyCategory.DANGER_SKILL),
        ("1クレジット中、全ておねがいダーリンをプレイ", TrophyCategory.ONE_CREDIT),
        ("-", TrophyCategory.EVENT),
    ],
)
def test_no_required_categories_by_description(description: str, expected: str) -> None:
    info = classify(_trophy(description=description))
    assert info.category == expected
    assert info.is_special


# --------------------------------------------------------------------------- #
# 颜文字
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["(^ω^)", "(´･ω･｀)", "(*｀・▽・´*)", "щ(ﾟДﾟщ)", "＼(^o^)／", ":-)", "(((； ﾟДﾟ)))", "(/>_<)"],
)
def test_kaomoji_positive(name: str) -> None:
    assert is_kaomoji(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "D.E.A.D.L.Y.",
        "合格！！",
        "B.B.K.K.B.K.K.",
        "Imperishable Night 2006 (2016 Refine)",
        "Like the Wind [Reborn]",
        "Bad Apple!! feat.nomico (Nhato Remix)",
        "CRYSTAL_ACCESS",
        "神威 (NAOKI × ZPP MIX)",
        "NEW COMER",
        # 以下三个是真实的误判样本：角色名里的 '・'/／' 只是日文标点，
        # 早期版本把它们当成了「强特征字符 + 肢体符号」，一次误判 74 个角色养成称号
        "星咲 あかり／マーメイド・ランデブー",
        "MIR-202【アルテミス・レナ】",
        "BOW AND ARROW （アニメ・オープニングver.）",
    ],
)
def test_kaomoji_negative(name: str) -> None:
    """这些标题符号密度也很高，但都不是颜文字。"""
    assert is_kaomoji(name) is False


def test_kaomoji_found_in_fixture(catalog: TrophyCatalog) -> None:
    kaomoji = catalog.kaomoji()
    assert len(kaomoji) >= 25
    assert any("ω" in t.name for t in kaomoji)


# --------------------------------------------------------------------------- #
# 目录
# --------------------------------------------------------------------------- #


def test_special_and_excluded_partition(catalog: TrophyCatalog) -> None:
    """两个集合必须正好划分全部称号，不重不漏。"""
    special_ids = {t.id for t in catalog.special}
    excluded_ids = {t.id for t in catalog.excluded}
    assert special_ids.isdisjoint(excluded_ids)
    assert len(special_ids) + len(excluded_ids) == len(catalog)


def test_categories_cover_everything(catalog: TrophyCatalog) -> None:
    counts = catalog.category_counts()
    assert sum(counts.values()) == len(catalog)
    for required in (TrophyCategory.ALL_SONGS, TrophyCategory.PER_SONG_AJ, TrophyCategory.CHARACTER_RANK):
        assert counts.get(required, 0) > 0


def test_for_song_returns_only_special(catalog: TrophyCatalog) -> None:
    """曲目反查结果里不能混进常规 AJ / 角色养成称号。"""
    from fastchu.services.titles import load_trophies_from_file as _load

    trophies = _load(TROPHY_FIXTURE)
    song_ids = {sid for t in trophies for sid in t.song_ids}
    assert song_ids, "夹具里应当有关联曲目的称号"

    for song_id in list(song_ids)[:20]:
        for trophy in catalog.for_song(song_id):
            assert catalog.category_of(trophy).is_special


def test_for_song_excludes_all_songs_by_default(catalog: TrophyCatalog) -> None:
    """版本全完成称号（Spirit/Tribute/Legend of X）默认不随单曲展示。

    同一个版本里每首歌都命中同一组，挂在单曲详情上纯属噪音。
    """
    from fastchu.services.titles import load_trophies_from_file as _load

    trophies = _load(TROPHY_FIXTURE)
    # 找一首确实命中版本全完成称号的曲子
    song_id = next(
        sid
        for t in catalog.of_category(TrophyCategory.ALL_SONGS)
        for sid in t.song_ids
    )

    default_view = catalog.for_song(song_id)
    full_view = catalog.for_song(song_id, include_all_songs=True)

    assert len(full_view) > len(default_view)
    assert any(
        catalog.category_of(t).category == TrophyCategory.ALL_SONGS for t in full_view
    )
    assert not any(
        catalog.category_of(t).category == TrophyCategory.ALL_SONGS for t in default_view
    )
    assert len(trophies) > 0


def test_for_song_sorted_by_color_then_id(catalog: TrophyCatalog) -> None:
    """颜色更稀有的称号排前面，方便一眼看到最特殊的。"""
    from fastchu.services.titles import COLOR_ORDER

    song_id = next(
        sid for t in catalog.special for sid in t.song_ids if len(catalog.for_song(sid)) > 1
    )
    related = catalog.for_song(song_id)
    keys = [(-COLOR_ORDER.get(t.color, 0), t.id) for t in related]
    assert keys == sorted(keys)


def test_search_finds_known_title(catalog: TrophyCatalog) -> None:
    hits = catalog.search("Legend")
    assert hits
    assert any("Legend" in hit.trophy.name for hit in hits)


def test_search_exact_beats_prefix(catalog: TrophyCatalog) -> None:
    name = catalog.special[0].name
    hits = catalog.search(name)
    assert hits and hits[0].trophy.name == name


def test_search_respects_color_filter(catalog: TrophyCatalog) -> None:
    hits = catalog.search("", color="rainbow")
    assert hits == [], "空关键词不该返回结果"
    hits = catalog.search("L", color="rainbow", limit=50)
    assert all(hit.trophy.color == "rainbow" for hit in hits)


def test_listing_by_color(catalog: TrophyCatalog) -> None:
    rainbows = catalog.listing(special_only=True, color="rainbow", limit=100)
    assert rainbows
    assert all(t.color == "rainbow" for t in rainbows)


def test_listing_sorted_by_tier(catalog: TrophyCatalog) -> None:
    titles = catalog.listing(special_only=True, limit=200)
    tiers = [tier(t, catalog.category_of(t)) for t in titles]
    assert tiers == sorted(tiers)


def test_find_returns_none_when_ambiguous(catalog: TrophyCatalog) -> None:
    assert catalog.find("e") is None  # 太多命中


def test_find_returns_best_when_unique(catalog: TrophyCatalog) -> None:
    trophy = next(t for t in catalog.special if len(t.name) > 8)
    best = catalog.find(trophy.name)
    assert best is not None and best.trophy.id == trophy.id


# --------------------------------------------------------------------------- #
# 分层
# --------------------------------------------------------------------------- #


def test_tier_all_songs_is_top(catalog: TrophyCatalog) -> None:
    for trophy in catalog.of_category(TrophyCategory.ALL_SONGS)[:5]:
        assert tier(trophy, catalog.category_of(trophy)) == 1


def test_tier_labels_cover_all() -> None:
    for value in (1, 2, 3, 4):
        assert tier_label(value)
    assert tier_label(99) == tier_label(4)


def test_load_tolerates_envelope_shapes(tmp_path) -> None:
    import json

    payload = {"trophies": [{"id": 1, "name": "a", "color": "normal"}]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert len(load_trophies_from_file(path)) == 1

    path.write_text(json.dumps({"data": payload}), encoding="utf-8")
    assert len(load_trophies_from_file(path)) == 1

    path.write_text(json.dumps(payload["trophies"]), encoding="utf-8")
    assert len(load_trophies_from_file(path)) == 1


def test_colors_use_actual_api_values(catalog: TrophyCatalog) -> None:
    """实际接口用 ``platina`` / ``ultima``，文档写的 ``platinum`` / ``copper`` 并不出现。

    颜色存成字符串就是为了将来出现新颜色时不会解析失败。
    """
    colors = {t.color for t in catalog.trophies}
    assert "platina" in colors
    assert "ultima" in colors
    for trophy in catalog.trophies:
        assert trophy.color_label  # 未收录的颜色也必须有标签兜底
