"""称号 → 曲目 的文本反查测试。

落雪对「非单曲条件」的称号（例如 UNLOCK CHALLENGE）不给 ``required.songs``，
所以这类称号在曲目详情里挂不上——例如虹色的《少女が夢見た空の果て》，
它的 ``description`` 里明明写着《Crossmythos Rhapsodia》。

这里覆盖 :func:`fastchu.services.titles.songs_mentioned_in` 的判定规则，
以及 :class:`TrophyCatalog` 用它补索引的行为。
"""

from __future__ import annotations

from fastchu.models import Trophy
from fastchu.services.titles import TrophyCatalog, songs_mentioned_in

TITLES = {
    "Crossmythos Rhapsodia": 2802,
    "Change Our MIRAI！": 901,
    "Change Our MIRAI！ (Our 7 Lights)": 902,
    "STAR": 903,
    "VERSE": 904,
    "CYCLES": 905,
}


def test_links_song_named_in_description() -> None:
    text = "Crossmythos RhapsodiaのUNLOCK CHALLENGEを最も厳しい条件でクリアした証です。"
    assert songs_mentioned_in(text, TITLES) == [2802]


def test_requires_follower_after_title() -> None:
    # 「Crossmythos RhapsodiaX」不是独立提到曲名
    assert songs_mentioned_in("Crossmythos RhapsodiaXの何か", TITLES) == []


def test_ignores_version_context() -> None:
    """『CHUNITHM STAR』说的是版本，不是曲目《STAR》。"""
    assert songs_mentioned_in("『CHUNITHM STAR』のキャッチコピーです", TITLES) == []


def test_ignores_substring_of_hyphenated_word() -> None:
    """X-VERSE 里的 VERSE 不是曲目《VERSE》。"""
    assert songs_mentioned_in("Dubhe - 50days of X-VERSE", TITLES) == []


def test_prefers_longest_title() -> None:
    text = "1クレジット中、全てChange Our MIRAI！ (Our 7 Lights)をプレイ"
    assert songs_mentioned_in(text, TITLES) == [902]


def test_ignores_character_names() -> None:
    """角色名撞曲名时不能挂（キャラクター『…』说的是角色）。"""
    text = "キャラクター『好きな惣菜発表ドラゴン』/RANK SS達成"
    titles = {"好きな惣菜発表ドラゴン": 1}
    assert songs_mentioned_in(text, titles) == []


def test_ignores_short_titles() -> None:
    """短曲名（<4）不参与，避免遍地误伤。"""
    assert songs_mentioned_in("AIRをプレイ", {"AIR": 1}) == []


def test_empty_input() -> None:
    assert songs_mentioned_in("", TITLES) == []


# --------------------------------------------------------------------------- #
# 与 TrophyCatalog 的集成
# --------------------------------------------------------------------------- #


def _trophy(**kwargs) -> Trophy:
    base = {
        "id": 9719,
        "name": "少女が夢見た空の果て",
        "color": "rainbow",
        "description": (
            "Crossmythos RhapsodiaのUNLOCK CHALLENGEを最も厳しい条件でクリアした証です。"
        ),
    }
    base.update(kwargs)
    return Trophy(**base)


def test_catalog_links_by_description() -> None:
    trophy = _trophy()
    assert trophy.song_ids == [], "前提：落雪没给这首歌的曲目关联"

    catalog = TrophyCatalog([trophy], song_titles=TITLES)
    related = catalog.for_song(2802)
    assert [t.name for t in related] == ["少女が夢見た空の果て"]


def test_catalog_without_titles_does_not_link() -> None:
    """不传曲名表时保持旧行为（只按 required.songs）。"""
    catalog = TrophyCatalog([_trophy()])
    assert catalog.for_song(2802) == []


def test_required_songs_take_precedence() -> None:
    """已有结构化曲目关联时，不再走文本反查。"""
    trophy = Trophy(
        id=1,
        name="某称号",
        color="gold",
        description="Crossmythos Rhapsodiaの何か",
        required=[
            {
                "difficulties": [3],
                "full_combo": "alljustice",
                "songs": [{"id": 999, "title": "别的曲子"}],
            }
        ],
    )
    catalog = TrophyCatalog([trophy], song_titles=TITLES)
    assert [t.id for t in catalog.for_song(999)] == [1]
    assert catalog.for_song(2802) == []


# --------------------------------------------------------------------------- #
# 日服独有曲目：按曲名反查（没有国服 ID）
# --------------------------------------------------------------------------- #


def test_for_song_title_finds_trophy() -> None:
    """日服独有曲目没有国服 ID，只能靠曲名反查。"""
    trophy = Trophy(
        id=1,
        name="ゴブリンか？",
        color="normal",
        description="[P&A] 1クレジット中、全てRightfullyをプレイ",
    )
    catalog = TrophyCatalog([trophy], song_titles={"Rightfully": 700123})
    hits = catalog.for_song_title("Rightfully")
    assert [t.name for t in hits] == ["ゴブリンか？"]


def test_for_song_title_normalizes_spaces() -> None:
    """反查时曲名会做全半角/空白归一，所以调用方传哪种写法都能命中。"""
    trophy = Trophy(id=1, name="标签", color="gold", description="やさしい歌をプレイ")
    catalog = TrophyCatalog([trophy], song_titles={"やさしい歌": 700456})
    # 调用方多打了一个全角空格也应当命中
    assert catalog.for_song_title("やさしい　歌"), "全角空格差异不该影响匹配"


def test_for_song_title_unknown_returns_empty() -> None:
    catalog = TrophyCatalog([], song_titles={})
    assert catalog.for_song_title("不存在的歌") == []