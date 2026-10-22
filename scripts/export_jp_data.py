"""导出日服全量数据为单个 JSON，供其它项目直接对接。

数据来源（均已在本仓库内缓存/生成）：

* ``data/songs/songs.union.json`` —— 日服曲目、日服定数、版本、发布时间
* ``data/cache/jp_chunirec_all.json`` —— 日服谱面的物量（maxcombo）
* ``data/cache/jp_designers.json`` —— 日服谱师（兜底）
* ``data/cache/jp_titles_wiki.json`` —— 日服 Wiki 称号条件与表格颜色
* ``data/titles/jp_only.json`` —— 国服未收录的日服限定称号 ID
* ``data/aliases/`` —— 社区别名（含按 jp_idx 映射的国服共同曲目别名）

导出范围是**日服存在的全部曲目**（普通曲 + WORLD'S END）、Wiki 收录的全部称号，
以及所有能映射到日服曲目的别名。

用法::

    python scripts/export_jp_data.py
    python scripts/export_jp_data.py --out exports/fastchu-jp-data.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import sys
import unicodedata
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastchu.models import TROPHY_COLOR_LABELS, Trophy
from fastchu.services.titles import classify

UNION_PATH = ROOT / "data" / "songs" / "songs.union.json"
JP_RAW_PATH = ROOT / "data" / "cache" / "jp_chunirec_all.json"
DESIGNERS_PATH = ROOT / "data" / "cache" / "jp_designers.json"
WIKI_TITLES_PATH = ROOT / "data" / "cache" / "jp_titles_wiki.json"
JP_ONLY_PATH = ROOT / "data" / "titles" / "jp_only.json"
CN_TROPHIES_PATH = ROOT / "data" / "cache" / "trophy_list.json"
COMMUNITY_ALIASES_PATH = ROOT / "data" / "aliases" / "community.json"
LOCAL_ALIASES_PATH = ROOT / "data" / "aliases" / "local.json"
DEFAULT_OUT = ROOT / "exports" / "fastchu-jp-data.json"

JP_JACKET_BASE = "https://db.chunirec.net/lmg/music/jkt"

#: 谱面难度展示顺序
DIFFICULTY_ORDER = ["BAS", "ADV", "EXP", "MAS", "ULT", "WE"]

#: 从 Wiki 页面误抓进来的导航链接 / 表头
NAV_NOISE = {
    "基本の遊び方", "雑談掲示板", "ノーマル", "シルバー", "ゴールド",
    "プラチナ", "虹", "レインボー", "称号", "楽曲一覧", "質問掲示板",
}

#: Wiki 条件开头的风格标签，如 ``[VAR] 曲名/难度/条件``
_STYLE_RE = re.compile(r"^\[[^\]]{1,12}\]\s*")

#: 事件日程表被误抓成「称号」的行
_DATE_RE = re.compile(r"^\d{1,4}/\d{1,3}(?:/\d{1,2})?$")

#: 各种「破折号 / 横线」——与 fastchu.services.titles 保持同一套归一规则
_DASH_MAP = {
    ord(char): "-"
    for char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d\u2500\u2501"
}

#: 弯引号 / 修饰符撇号与直引号视为同一字符
_QUOTE_MAP = {
    **{ord(char): "'" for char in "\u2018\u2019\u201b\u02bc\u2032\uff07"},
    **{ord(char): '"' for char in "\u201c\u201d\u201e\u301d\u301e\uff02"},
}


def _name_key(text: str) -> str:
    folded = (
        unicodedata.normalize("NFKC", text or "")
        .translate(_DASH_MAP)
        .translate(_QUOTE_MAP)
    )
    return "".join(folded.split()).casefold()


#: wiki 表格会把脚注编号/「※」一起抓进标题，例如 ``Nice boat.*21``
_FOOTNOTE_SUFFIX_RE = re.compile(r"\s*(?:\*\d+|※)\s*$")


def _clean_wiki_name(name: str) -> str:
    cleaned = (name or "").strip()
    while True:
        stripped = _FOOTNOTE_SUFFIX_RE.sub("", cleaned).strip()
        if not stripped or stripped == cleaned:
            return cleaned
        cleaned = stripped


#: 国服写「2人以上で店内マッチング」，wiki 只写「2人以上」
_MATCHING_PHRASES = ("で店内マッチング", "店内マッチング")


def _condition_key(text: str) -> str:
    key = _name_key(text)
    for phrase in _MATCHING_PHRASES:
        key = key.replace(phrase, "")
    return key


def _closest_title(name: str, candidates: list[dict]) -> dict:
    if len(candidates) == 1:
        return candidates[0]
    key = _name_key(name)
    return max(
        candidates,
        key=lambda item: difflib.SequenceMatcher(
            None, key, _name_key(item.get("name") or "")
        ).ratio(),
    )


def _load(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _is_noise_title(name: str, condition: str) -> bool:
    """Wiki 里的导航链接、表头、事件日期、占位行都不是称号。"""
    if name in NAV_NOISE or not condition:
        return True
    if re.fullmatch(r"[0-9a-fA-F]{8,}", name):
        return True
    if name.startswith("(※称号名不明)") or name[:1] == "(" and "称号名不明" in name:
        return True
    if _DATE_RE.fullmatch(name) or re.fullmatch(r"\d{4,}/\d{1,3}", name):
        return True
    return condition in {"内容", "条件", "入手条件"}


def build_songs(union, raw, designers, jackets) -> list[dict]:
    raw_by_id = {
        str(record.get("meta", {}).get("id")): record
        for record in raw
        if record.get("meta", {}).get("id")
    }
    songs: list[dict] = []
    for song in union.get("songs", []):
        if "jp" not in (song.get("regions") or []):
            continue
        jp_id = song.get("jp_id")
        if not jp_id:
            continue
        raw_charts = raw_by_id.get(str(jp_id), {}).get("data") or {}
        title_designers = designers.get(song.get("title") or "") or {}

        charts: dict[str, dict] = {}
        for difficulty in sorted(
            song.get("charts") or {},
            key=lambda item: DIFFICULTY_ORDER.index(item)
            if item in DIFFICULTY_ORDER
            else len(DIFFICULTY_ORDER),
        ):
            jp_chart = (song["charts"][difficulty] or {}).get("jp") or {}
            raw_chart = raw_charts.get(difficulty) or {}
            designer = jp_chart.get("designer") or title_designers.get(difficulty) or None
            charts[difficulty] = {
                "level": jp_chart.get("level"),
                "const": jp_chart.get("const"),
                "const_unknown": bool(jp_chart.get("const_unknown")),
                "maxcombo": raw_chart.get("maxcombo"),
                "designer": designer,
            }

        jacket = song.get("jp_img") or jackets.get(str(jp_id)) or None
        songs.append(
            {
                "title": song.get("title"),
                "artist": song.get("artist"),
                "bpm": song.get("bpm"),
                "genre": (song.get("genres") or {}).get("jp"),
                "genre_cn": (song.get("genres") or {}).get("cn"),
                "kind": song.get("kind"),
                "regions": song.get("regions"),
                "jp_id": jp_id,
                "jp_idx": song.get("jp_idx"),
                "release": song.get("jp_release"),
                "version": song.get("jp_version"),
                "jacket": jacket,
                "jacket_url": f"{JP_JACKET_BASE}/{jacket}" if jacket else None,
                "cn_id": song.get("cn_id"),
                "cn_version": song.get("cn_version"),
                "charts": charts,
            }
        )
    songs.sort(key=lambda item: ((item.get("title") or "").casefold(), item.get("kind") or ""))
    return songs


def build_titles(wiki, cn_trophies, jp_only_ids) -> list[dict]:
    cn_ids_by_key: dict[str, list[int]] = {}
    cn_by_condition: dict[str, list[dict]] = {}
    for row in cn_trophies:
        name_key = _name_key(row.get("name") or "")
        if not name_key:
            continue
        ids = cn_ids_by_key.setdefault(name_key, [])
        if row.get("id") is not None and row["id"] not in ids:
            ids.append(row["id"])

        trophy = Trophy.model_validate(row)
        if not classify(trophy).is_special or not trophy.description.strip():
            continue
        condition_key = _condition_key(trophy.description)
        if condition_key:
            cn_by_condition.setdefault(condition_key, []).append(row)

    jp_ids_clean = {_clean_wiki_name(name): trophy_id for name, trophy_id in jp_only_ids.items()}
    titles: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    for name, entry in wiki.items():
        condition = (entry.get("condition") or "").strip()
        if _is_noise_title(name, condition):
            continue
        cleaned_name = _clean_wiki_name(name)
        condition_clean = _STYLE_RE.sub("", condition)
        name_key = _name_key(cleaned_name)
        cn_ids = list(cn_ids_by_key.get(name_key) or [])
        if not cn_ids:
            candidates = [
                row
                for row in cn_by_condition.get(_condition_key(condition_clean), [])
                if row.get("id") is not None
            ]
            if candidates:
                cn_ids = [_closest_title(cleaned_name, candidates)["id"]]
        jp_only = not cn_ids
        color = (entry.get("color") or "").strip() or "unknown"
        page = entry.get("page") or ""
        title = {
            "id": (jp_only_ids.get(name) or jp_ids_clean.get(cleaned_name)) if jp_only else None,
            "name": cleaned_name,
            "color": color,
            "color_label": TROPHY_COLOR_LABELS.get(color, color),
            "condition": condition,
            "condition_clean": condition_clean,
            "page": page,
            "section": entry.get("section") or "",
            "url": entry.get("url") or "",
            "jp_only": jp_only,
            "cn_ids": cn_ids,
        }

        # wiki 里同一称号可能被脚注拆成两行（如「ゴブリンか？ ※」），只保留一条
        dedupe_key = (name_key, _condition_key(condition_clean))
        previous_index = seen.get(dedupe_key)
        if previous_index is not None:
            previous = titles[previous_index]
            if previous["jp_only"] and not jp_only:
                titles[previous_index] = title
            continue
        seen[dedupe_key] = len(titles)
        titles.append(title)
    titles.sort(key=lambda item: (item["page"], item["name"]))
    return titles


def build_aliases(union, community, local) -> dict[str, list[str]]:
    by_cn_id = {
        song.get("cn_id"): song
        for song in union.get("songs", [])
        if song.get("cn_id") is not None
    }
    merged: dict[str, list[str]] = {}

    def add(index, aliases) -> None:
        key = str(index)
        bucket = merged.setdefault(key, [])
        for alias in aliases or []:
            alias = str(alias).strip()
            if alias and alias not in bucket:
                bucket.append(alias)

    # 原生日服别名（按 jp_idx）
    for index, aliases in (community.get("jp") or {}).items():
        add(index, aliases)
    for index, aliases in (local.get("jp") or {}).items():
        add(index, aliases)

    # 两服共同曲目的国服别名：同一首歌的社区叫法，按 jp_idx 归并
    for cn_id, aliases in (community.get("cn") or {}).items():
        song = by_cn_id.get(int(cn_id)) if str(cn_id).isdigit() else None
        if song and song.get("jp_idx") is not None:
            add(song["jp_idx"], aliases)

    return {
        key: merged[key]
        for key in sorted(merged, key=lambda item: int(item) if item.isdigit() else 0)
    }


def build_export() -> dict:
    union = _load(UNION_PATH)
    raw = _load(JP_RAW_PATH)
    designers = _load(DESIGNERS_PATH)
    wiki = (_load(WIKI_TITLES_PATH).get("titles") or {})
    jp_only_rows = _load(JP_ONLY_PATH).get("trophies") or []
    cn_trophies = _load(CN_TROPHIES_PATH).get("trophies") or []
    community = _load(COMMUNITY_ALIASES_PATH)
    local = _load(LOCAL_ALIASES_PATH)
    jackets = _load(ROOT / "data" / "cache" / "jp_jackets.json") if (
        ROOT / "data" / "cache" / "jp_jackets.json"
    ).is_file() else {}

    songs = build_songs(union, raw, designers, jackets)
    titles = build_titles(wiki, cn_trophies, {row["name"]: row["id"] for row in jp_only_rows})
    aliases = build_aliases(union, community, local)
    alias_entries = sum(len(values) for values in aliases.values())

    return {
        "meta": {
            "schema": "fastchu-jp-data",
            "version": 1,
            "generated_at": datetime.now(timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %z"),
            "description": "CHUNITHM 日服全量数据包：曲目/定数/物量/谱师/称号/别名",
            "sources": {
                "songs": "chunirec music/showall（经 reiwa.f5.si 镜像），日服",
                "song_crossref": "落雪查分器国服曲库（仅用于 cn_id 交叉引用）",
                "maxcombo": "chunirec",
                "designers": "CHUNITHM 攻略 Wiki 譜面制作者一覧",
                "titles": "CHUNITHM 攻略 Wiki（wikiwiki.jp/chunithmwiki）",
                "aliases": "AmethystTim/ChunithmUtil 社区别名 + 本项目 /添加别名 投稿",
            },
            "counts": {
                "songs": sum(1 for song in songs if song["kind"] == "song"),
                "we": sum(1 for song in songs if song["kind"] == "we"),
                "song_records": len(songs),
                "jp_only_songs": sum(1 for song in songs if song["cn_id"] is None),
                "shared_songs": sum(1 for song in songs if song["cn_id"] is not None),
                "charts": sum(len(song["charts"]) for song in songs),
                "titles": len(titles),
                "jp_only_titles": sum(1 for title in titles if title["jp_only"]),
                "alias_songs": len(aliases),
                "alias_entries": alias_entries,
            },
            "notes": {
                "songs": "只含日服存在的曲目；kind=song 为普通曲，kind=we 为 WORLD'S END",
                "charts": "level 为日服显示等级（可能带小数），const 为日服定数，"
                "const_unknown=true 表示官方未公布/推定，maxcombo 为物量，designer 可能为 null",
                "jp_idx": "日服谱面编号，与国服曲目 id 同一套编号；WORLD'S END 为 null",
                "title_id": "id 是 FastChu 为日服限定称号分配的合成 ID（900000+）；"
                "国服已有对应条目（同名或同条件）时为 null，请用 cn_ids",
                "jacket": "日服曲绘文件名；仅日服独有曲目抓到了文件名，"
                "其它曲目为 null（国服资源库可能有同图）",
                "titles": "condition 为 Wiki 原文，condition_clean 去掉开头的 [VAR]/[P&A] 等标签；"
                "color 来自 Wiki 表格背景色，无法识别时为 unknown；"
                "jp_only=true 表示国服称号数据中没有对应条目（同名或同条件）。"
                "Wiki 只收录活动/不可获得/联机/单曲条件类称号，并非日服全部可获得称号",
                "cn_ids": "与该 Wiki 称号对应的国服称号 ID（同名可能多个，如角色养成同名档位；"
                "条件相同但写法不同的取名字最接近的一条）",
                "aliases": "键为 jp_idx；值为日服原生别名 + 两服共同曲目的国服社区别名，已去重",
            },
        },
        "songs": songs,
        "titles": titles,
        "aliases": aliases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="导出日服全量数据 JSON")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = build_export()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts = payload["meta"]["counts"]
    print(f"已写出：{args.out}")
    print(
        f"  普通曲 {counts['songs']} · WORLD'S END {counts['we']} · "
        f"称号 {counts['titles']}（日服限定 {counts['jp_only_titles']}）· "
        f"别名 {counts['alias_entries']} 条 / {counts['alias_songs']} 首"
    )
    print(f"  文件大小：{args.out.stat().st_size:,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
