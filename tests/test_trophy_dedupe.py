"""称号查重回归：破折号变体不能被当成两个称号。"""

from __future__ import annotations

from collections import defaultdict

import pytest

from fastchu.services.titles import _name_key


def test_dash_variants_normalize_to_same_key() -> None:
    """U+2015（――）与 U+2014（——）、U+2500（──）都算同一个字符。"""
    a = _name_key("――――　　UNKNOWN　　――――")  # U+2015 + 全角空格
    b = _name_key("———— UNKNOWN ————")  # U+2014 + 半角空格
    assert a == b == "----unknown----"

    assert _name_key("『美しく儚く世界は廻る──』") == _name_key("『美しく儚く世界は廻る――』")


def test_dash_normalization_keeps_real_names_distinct() -> None:
    """归一化不能把本来不同的名字合并。"""
    assert _name_key("A-1") != _name_key("A1")
    assert _name_key("ラミアー") != _name_key("ラミア")


def test_quote_variants_normalize_to_same_key() -> None:
    """弯引号与直引号是同一个名字（wiki 与落雪写法不同）。"""
    assert _name_key("LET'S PLAY！WHITE WINTER DAYS") == _name_key(
        "LET’S PLAY！WHITE WINTER DAYS"
    )
    assert _name_key('The "ELITE" of SUN') == _name_key("The “ELITE” of SUN")


def test_jp_only_shadow_of_cn_title_is_dropped() -> None:
    """日服限定条目若与国服称号重名，保留国服条目。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    cn = Trophy(
        id=6263,
        name="LET'S PLAY！WHITE WINTER DAYS",
        color="platina",
        description="Snow Colored Score/ULTIMA/RANK SSS達成",
    )
    jp_only = Trophy(
        id=JP_ONLY_ID_BASE + 570,
        name="LET’S PLAY！WHITE WINTER DAYS",
        color="platina",
        description="Snow Colored Score/ULTIMA/RANK SSS達成",
    )
    catalog = TrophyCatalog([cn, jp_only])

    assert [trophy.id for trophy in catalog.trophies] == [6263]


def test_same_name_cn_titles_are_not_merged() -> None:
    """角色养成同名称号（150/200 档）属于两条，不能被误删。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import TrophyCatalog

    platinum = Trophy(id=1, name="ショウニペンギン", color="platina")
    rainbow = Trophy(id=2, name="ショウニペンギン", color="rainbow")
    catalog = TrophyCatalog([platinum, rainbow])

    assert [trophy.id for trophy in catalog.trophies] == [1, 2]


def test_search_accepts_quote_variants() -> None:
    """用户从 wiki 复制的弯引号也要能搜到国服条目。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import TrophyCatalog

    catalog = TrophyCatalog(
        [Trophy(id=1, name="LET'S PLAY！WHITE WINTER DAYS", color="platina")]
    )

    assert catalog.search("LET’S PLAY！WHITE WINTER DAYS")
    assert catalog.search_exact("LET’S PLAY！WHITE WINTER DAYS")


def test_wiki_footnote_markers_are_stripped() -> None:
    """wiki 抓下来的标题会带 `*12` / `※` 脚注，不能当成名字的一部分。"""
    from fastchu.services.titles import _clean_wiki_name

    assert _clean_wiki_name("Nice boat.*21") == "Nice boat."
    assert _clean_wiki_name("太正櫻に浪漫の嵐*10 ※") == "太正櫻に浪漫の嵐"
    assert _clean_wiki_name("(※称号名不明)*12") == "(※称号名不明)"


def test_semantic_title_variant_shadow_is_dropped_and_searchable() -> None:
    """「ゲキ」与「激」这种写法差异，按条件判定为同一个称号。"""
    from fastchu.models import Trophy, TrophyRequired, TrophyRequiredSong
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    cn = Trophy(
        id=5764,
        name="文武両道人望ゲキアツプリンセス",
        color="silver",
        description="ハッピーエンドをはじめから/EXPERT/FULL COMBO達成",
        required=[
            TrophyRequired(
                difficulties=[2],
                full_combo="fullcombo",
                songs=[TrophyRequiredSong(id=2224, title="ハッピーエンドをはじめから")],
            )
        ],
    )
    jp_only = Trophy(
        id=JP_ONLY_ID_BASE + 675,
        name="文武両道人望激アツプリンセス",
        color="silver",
        description="ハッピーエンドをはじめから/EXPERT/FULL COMBO達成",
    )
    catalog = TrophyCatalog(
        [cn, jp_only],
        song_titles={"ハッピーエンドをはじめから": 2224},
    )

    assert [trophy.id for trophy in catalog.trophies] == [5764]
    assert [trophy.id for trophy in catalog.for_song(2224)] == [5764]
    assert catalog.get(jp_only.id) is None
    assert catalog.search("激アツ")[0].trophy.id == 5764
    assert catalog.search_exact("文武両道人望激アツプリンセス")[0].id == 5764


def test_multiplayer_condition_phrase_is_normalized() -> None:
    """国服写「2人以上で店内マッチング」，wiki 只写「2人以上」，条件仍相同。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    cn = Trophy(
        id=951,
        name="バッカお前･･････俺がついてるだろ！",
        color="silver",
        description="FLOWER/2人以上で店内マッチング/プレイ中に1回MISS",
    )
    jp_only = Trophy(
        id=JP_ONLY_ID_BASE + 35,
        name="バッカお前……俺がついてるだろ！",
        color="silver",
        description="FLOWER/2人以上/プレイ中に1回MISS",
    )
    catalog = TrophyCatalog([cn, jp_only])

    assert [trophy.id for trophy in catalog.trophies] == [951]


def test_jp_only_same_name_different_condition_is_kept() -> None:
    """同名但条件不同的日服称号（不同获取方式）不能合并。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    first = Trophy(
        id=JP_ONLY_ID_BASE + 41,
        name="ヤバイわよ！",
        color="normal",
        description="※現在新規入手不可",
    )
    second = Trophy(
        id=JP_ONLY_ID_BASE + 429,
        name="ヤバイわよ！*28",
        color="normal",
        description="Lost Princess/3人で店内マッチング/デンジャースキル発動",
    )
    catalog = TrophyCatalog([first, second])

    assert [trophy.id for trophy in catalog.trophies] == [first.id, second.id]
    assert [trophy.name for trophy in catalog.trophies] == ["ヤバイわよ！", "ヤバイわよ！"]


def test_jp_only_footnote_duplicate_is_dropped() -> None:
    """同一份 wiki 里被脚注拆成两条的称号只保留一条。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    first = Trophy(
        id=JP_ONLY_ID_BASE + 30,
        name="ゴブリンか？",
        color="silver",
        description="1クレジット中、全てRightfullyをプレイ",
    )
    second = Trophy(
        id=JP_ONLY_ID_BASE + 630,
        name="ゴブリンか？ ※",
        color="silver",
        description="1クレジット中、全てrightfullyをプレイ",
    )
    catalog = TrophyCatalog([first, second])

    assert [trophy.id for trophy in catalog.trophies] == [first.id]


def test_condition_dedupe_keeps_distinct_cn_titles() -> None:
    """同条件的多条国服称号（不同乐器）都要保留，别名指向最接近的一条。"""
    from fastchu.models import Trophy
    from fastchu.services.titles import JP_ONLY_ID_BASE, TrophyCatalog

    condition = "Music Evolution/MASTER以下全て/プレイ"
    cn = [
        Trophy(id=5789, name="騒霊ヴァイオリニスト", color="silver", description=condition),
        Trophy(id=5790, name="騒霊トランペッター", color="silver", description=condition),
        Trophy(id=5791, name="騒霊キーボーディスト", color="silver", description=condition),
    ]
    jp_only = [
        Trophy(
            id=JP_ONLY_ID_BASE + 700,
            name="騒霊のキーボーディスト",
            color="silver",
            description=condition,
        ),
        Trophy(
            id=JP_ONLY_ID_BASE + 701,
            name="騒霊のトランペッター",
            color="silver",
            description=condition,
        ),
        Trophy(
            id=JP_ONLY_ID_BASE + 702,
            name="騒霊のヴァイオリニスト",
            color="silver",
            description=condition,
        ),
    ]
    catalog = TrophyCatalog(cn + jp_only)

    assert {trophy.id for trophy in catalog.trophies} == {5789, 5790, 5791}
    assert catalog.search("騒霊のトランペッター")[0].trophy.id == 5790
    assert catalog.search("騒霊のキーボーディスト")[0].trophy.id == 5791
    assert catalog.search("騒霊のヴァイオリニスト")[0].trophy.id == 5789



def test_no_duplicate_titles_in_catalog(tmp_path) -> None:
    """真实数据回归：特殊称号里不该有「同名同条件」的两条。"""
    import asyncio
    import pathlib

    from fastchu.services.song_service import SongService
    from fastchu.services.titles import _condition_key

    from .conftest import make_settings

    root = pathlib.Path(__file__).resolve().parents[1]
    if not (root / "data" / "titles" / "jp_only.json").is_file():
        pytest.skip("缺少称号数据")

    async def run():
        settings = make_settings(tmp_path, jp_only_titles_path=root / "data" / "titles" / "jp_only.json")
        service = SongService(settings)
        try:
            return await service.titles()
        finally:
            await service.aclose()

    catalog = asyncio.run(run())
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for trophy in catalog.special:
        groups[(_name_key(trophy.name), _condition_key(trophy.description))].append(trophy)

    dups = {key: items for key, items in groups.items() if len(items) > 1}
    assert not dups, {key: [t.name for t in items] for key, items in dups.items()}


def test_real_data_song_titles_are_deduped(tmp_path) -> None:
    """真实数据回归：#2224 / #2416 的国服与日服限定重复只能出现一条。"""
    import asyncio
    import pathlib

    from fastchu.services.song_service import SongService

    from .conftest import make_settings

    root = pathlib.Path(__file__).resolve().parents[1]
    trophy_cache = root / "data" / "cache" / "trophy_list.json"
    jp_only_path = root / "data" / "titles" / "jp_only.json"
    if not trophy_cache.is_file() or not jp_only_path.is_file():
        pytest.skip("缺少真实称号数据")

    async def run():
        settings = make_settings(
            tmp_path,
            trophy_cache_path=trophy_cache,
            jp_only_titles_path=jp_only_path,
        )
        service = SongService(settings)
        try:
            return {
                song_id: await service.related_titles(song_id)
                for song_id in (2224, 2416)
            }
        finally:
            await service.aclose()

    related = asyncio.run(run())
    assert [trophy.id for trophy in related[2224]] == [5764]
    assert [trophy.id for trophy in related[2416]] == [6263]

