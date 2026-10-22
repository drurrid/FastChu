"""日服数据包导出：合并、过滤与别名映射。"""

from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "export_jp_data.py"
_spec = importlib.util.spec_from_file_location("export_jp_data", SCRIPT)
assert _spec is not None and _spec.loader is not None
export_jp_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_jp_data)


def test_noise_titles_are_filtered() -> None:
    assert export_jp_data._is_noise_title("ノーマル", "シルバー")
    assert export_jp_data._is_noise_title("10/22", "楽曲追加")
    assert export_jp_data._is_noise_title("20267/2", "CHUNITHMMate")
    assert export_jp_data._is_noise_title("(※称号名不明)", "課題曲マップ")
    assert export_jp_data._is_noise_title("deadbeef", "x")
    assert not export_jp_data._is_noise_title("皇帝", "RATING 16.00達成")
    assert not export_jp_data._is_noise_title("わかる", "称号「せやな」をセットしたプレイヤーとマッチング")


def test_songs_merge_maxcombo_jacket_and_designer() -> None:
    union = {
        "songs": [
            {
                "title": "A",
                "artist": "x",
                "bpm": 100,
                "kind": "song",
                "regions": ["cn", "jp"],
                "genres": {"cn": "其他", "jp": "VARIETY"},
                "cn_id": 1,
                "jp_id": "j1",
                "jp_idx": 1,
                "jp_release": "2020-01-01",
                "jp_version": "V1",
                "jp_img": None,
                "cn_version": 10000,
                "charts": {
                    "MAS": {
                        "jp": {
                            "level": 12,
                            "const": 12.5,
                            "const_unknown": True,
                            "designer": None,
                        }
                    },
                    "BAS": {
                        "jp": {
                            "level": 2,
                            "const": 2,
                            "const_unknown": False,
                            "designer": "谱师B",
                        }
                    },
                },
            },
            {
                "title": "CN only",
                "kind": "song",
                "regions": ["cn"],
                "cn_id": 2,
                "jp_id": None,
                "charts": {},
            },
        ]
    }
    raw = [{"meta": {"id": "j1"}, "data": {"BAS": {"maxcombo": 100}, "MAS": {"maxcombo": 900}}}]
    songs = export_jp_data.build_songs(
        union, raw, {"A": {"MAS": "谱师A"}}, {"j1": "j.jpg"}
    )
    assert len(songs) == 1
    song = songs[0]
    assert list(song["charts"]) == ["BAS", "MAS"]
    assert song["charts"]["BAS"]["maxcombo"] == 100
    assert song["charts"]["BAS"]["designer"] == "谱师B"
    assert song["charts"]["MAS"]["maxcombo"] == 900
    assert song["charts"]["MAS"]["designer"] == "谱师A"
    assert song["charts"]["MAS"]["const_unknown"] is True
    assert song["jacket"] == "j.jpg"
    assert song["jacket_url"].endswith("/lmg/music/jkt/j.jpg")
    assert song["regions"] == ["cn", "jp"]


def test_aliases_merge_native_and_shared_cn() -> None:
    union = {
        "songs": [
            {"cn_id": 10, "jp_idx": 100},
            {"cn_id": 11, "jp_idx": None},
        ]
    }
    community = {
        "jp": {"200": ["原生日服"]},
        "cn": {"10": ["国服别名", "国服别名"], "11": ["CN 独有"]},
    }
    local = {"jp": {"100": ["投稿别名"]}}
    aliases = export_jp_data.build_aliases(union, community, local)
    assert aliases == {
        "100": ["投稿别名", "国服别名"],
        "200": ["原生日服"],
    }


def test_titles_use_cn_ids_and_clean_condition() -> None:
    wiki = {
        "ノーマル": {"condition": "シルバー", "color": "normal"},
        "10/22": {"condition": "楽曲追加"},
        "Real": {
            "condition": "[VAR] X/MASTER/プレイ",
            "color": "gold",
            "page": "p",
            "section": "s",
            "url": "u",
        },
        "Only": {
            "condition": "[ORI] Y/ULTIMA/ALL JUSTICE",
            "color": "rainbow",
            "page": "p",
            "section": "s",
            "url": "u",
        },
    }
    cn_trophies = [{"id": 2, "name": "Real"}]
    titles = export_jp_data.build_titles(wiki, cn_trophies, {"Only": 900001})
    by_name = {title["name"]: title for title in titles}
    assert set(by_name) == {"Real", "Only"}

    assert by_name["Real"]["jp_only"] is False
    assert by_name["Real"]["cn_ids"] == [2]
    assert by_name["Real"]["condition_clean"] == "X/MASTER/プレイ"
    assert by_name["Real"]["color_label"] == "金"

    assert by_name["Only"]["jp_only"] is True
    assert by_name["Only"]["id"] == 900001
    assert by_name["Only"]["cn_ids"] == []
    assert by_name["Only"]["color_label"] == "虹"


def test_titles_match_semantic_variants_by_condition() -> None:
    """「ゲキ」与「激」写法不同但条件相同，导出时应映射到国服 ID。"""
    wiki = {
        "Nice boat.*21": {
            "condition": "[東方] ケロ⑨destiny/2人以上/プレイ中に1回MISS",
            "color": "silver",
        },
        "文武両道人望激アツプリンセス": {
            "condition": "[P&A] ハッピーエンドをはじめから/EXPERT/FULL COMBO達成",
            "color": "silver",
        },
    }
    cn_trophies = [
        {
            "id": 1845,
            "name": "Nice boat.",
            "color": "silver",
            "description": "ケロ⑨destiny/2人以上で店内マッチング/プレイ中に1回MISS",
        },
        {
            "id": 5764,
            "name": "文武両道人望ゲキアツプリンセス",
            "color": "silver",
            "description": "ハッピーエンドをはじめから/EXPERT/FULL COMBO達成",
        },
    ]
    titles = export_jp_data.build_titles(
        wiki, cn_trophies, {"文武両道人望激アツプリンセス": 900675}
    )
    by_name = {title["name"]: title for title in titles}

    assert set(by_name) == {"Nice boat.", "文武両道人望激アツプリンセス"}
    assert by_name["Nice boat."]["jp_only"] is False
    assert by_name["Nice boat."]["cn_ids"] == [1845]
    assert by_name["文武両道人望激アツプリンセス"]["jp_only"] is False
    assert by_name["文武両道人望激アツプリンセス"]["cn_ids"] == [5764]
    assert by_name["文武両道人望激アツプリンセス"]["id"] is None


def test_duplicate_wiki_rows_are_merged() -> None:
    """同一 wiki 称号被脚注拆成两行时，导出只保留一条。"""
    wiki = {
        "ゴブリンか？": {
            "condition": "[東方] 1クレジット中、全てRightfullyをプレイ",
            "color": "silver",
            "page": "p",
        },
        "ゴブリンか？ ※": {
            "condition": "[東方] 1クレジット中、全てRightfullyをプレイ",
            "color": "silver",
            "page": "p",
        },
    }
    titles = export_jp_data.build_titles(
        wiki,
        [],
        {"ゴブリンか？": 900030, "ゴブリンか？ ※": 900630},
    )

    assert len(titles) == 1
    assert titles[0]["name"] == "ゴブリンか？"
    assert titles[0]["id"] == 900030

