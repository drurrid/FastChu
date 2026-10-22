"""构建国服 / 日服曲库并集，并标注定数差异。

数据来源：

* **国服**：落雪查分器 ``GET /api/v0/chunithm/song/list``（``data/cache/song_list.json``）
* **日服**：``chunirec`` 的 ``music/showall`` 镜像（reiwa.f5.si 的 ``chunirec_all.json``），
  每个难度都带谱面定数 ``const`` 与「定数未知/推定」标记 ``is_const_unknown``

两服差异是真实存在的：日服曲目更多（新版本 + 大量限定曲），同一首歌的定数也可能不同。

## 配对的两个坑

1. **WORLD'S END 必须单独配对**。两服都把 WE 当独立曲目（国服是 8xxx 号段），
   但 WE 的标题规则不同：日服带修改符后缀（``幾四音-Ixion-【止】``），国服不带
   （``幾四音-Ixion-``）。如果和普通曲混在一个标题空间里配对，WE 会错配到同名的普通曲。
2. **WE 的定数恒为 0**（WE 用 ★ 难度，没有数值定数），所以不参与定数比较。

输出::

    data/songs/songs.union.json   并集数据（含两服定数对照）
    data/songs/README.md          统计与字段说明

用法::

    python scripts/fetch_reference_data.py        # 国服曲库
    python scripts/fetch_jp_reference.py          # 日服曲库
    python scripts/build_song_regions.py
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import unicodedata
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 日服谱面制作者缓存（fetch_jp_designers.py 生成）：``{曲名: {难度: 谱师}}``
JP_DESIGNERS_PATH = ROOT / "data" / "cache" / "jp_designers.json"

#: 日服曲绘文件名缓存（fetch_jp_jackets.py 生成）
JP_JACKETS_PATH = ROOT / "data" / "cache" / "jp_jackets.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastchu.services.jp_versions import version_name  # noqa: E402

CN_CACHE = ROOT / "data" / "cache" / "song_list.json"
JP_CACHE = ROOT / "data" / "cache" / "jp_chunirec_all.json"
OUT_DIR = ROOT / "data" / "songs"

#: chunirec 的难度键 → 项目内的 LevelIndex
JP_DIFFICULTY_KEYS: dict[str, int] = {"BAS": 0, "ADV": 1, "EXP": 2, "MAS": 3, "ULT": 4}
DIFFICULTY_NAMES: dict[int, str] = {0: "BAS", 1: "ADV", 2: "EXP", 3: "MAS", 4: "ULT", 5: "WE"}

#: 日服 WE 标题末尾的修改符后缀，例如 ``【止】【戻】``
WE_SUFFIX_RE = re.compile(r"[【\[][^】\]]*[】\]]\s*$")

#: 两服分类名对照（日服 chunirec 用英文/侧写，国服用中文）
GENRE_MAP_JP_TO_CN: dict[str, str] = {
    "ORIGINAL": "原创",
    "niconico": "niconico",
    "VARIETY": "其他游戏",
    "ゲキマイ": "音击舞萌",
    "POPS&ANIME": "流行 & 动漫",
    "東方Project": "东方Project",
    "イロドリミドリ": "彩绿",
    "WORLD'S END": "WORLD'S END",
}


def normalize(text: str) -> str:
    """曲名标准化：全半角统一 + 大小写归一 + 去空白。"""
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


def is_we_cn(song: dict) -> bool:
    indices = [d.get("difficulty") for d in song.get("difficulties") or []]
    return bool(indices) and all(i == 5 for i in indices)


def is_we_jp(item: dict) -> bool:
    return (item.get("meta") or {}).get("genre") == "WORLD'S END"


def load_cn(path: pathlib.Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("songs") or payload.get("data", {}).get("songs") or []
    return list(payload)


def load_jp(path: pathlib.Path) -> list[dict]:
    # 该镜像带 UTF-8 BOM
    return json.loads(path.read_text(encoding="utf-8-sig"))


def cn_charts(song: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for diff in song.get("difficulties") or []:
        index = diff.get("difficulty")
        if index is None:
            continue
        out[int(index)] = {
            "level": diff.get("level"),
            "const": diff.get("level_value"),
            "notes": diff.get("notes"),
            "designer": (diff.get("note_designer") or "").strip() or None,
        }
    return out


def jp_charts(item: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for key, index in JP_DIFFICULTY_KEYS.items():
        chart = (item.get("data") or {}).get(key)
        if not chart:
            continue
        out[index] = {
            "level": chart.get("level"),
            "const": chart.get("const"),
            "const_unknown": bool(chart.get("is_const_unknown")),
            "maxcombo": chart.get("maxcombo"),
        }
    if (chart := (item.get("data") or {}).get("WE")):
        out[5] = {
            "level": chart.get("level"),
            "const": chart.get("const"),
            "const_unknown": bool(chart.get("is_const_unknown")),
            "maxcombo": chart.get("maxcombo"),
        }
    return out


def _title_key(title: str, *, we: bool) -> str:
    """配对用的标题键。WE 需要先剥掉日服的修改符后缀。"""
    if we:
        title = WE_SUFFIX_RE.sub("", title or "")
    return normalize(title)


def _match(
    cn_group: list[dict], jp_group: list[dict], *, we: bool
) -> tuple[list[tuple[dict, dict]], set[int], set[int], list[str]]:
    """在同一类（普通 / WE）内部按标题配对，同名多首时用艺术家消歧。"""
    cn_by: dict[str, list[dict]] = collections.defaultdict(list)
    for song in cn_group:
        cn_by[_title_key(song.get("title", ""), we=we)].append(song)
    jp_by: dict[str, list[dict]] = collections.defaultdict(list)
    for item in jp_group:
        jp_by[_title_key(item["meta"]["title"], we=we)].append(item)

    pairs: list[tuple[dict, dict]] = []
    used_cn: set[int] = set()
    used_jp: set[int] = set()
    ambiguous: list[str] = []

    for key in sorted(set(cn_by) & set(jp_by)):
        cn_candidates = cn_by[key]
        jp_candidates = jp_by[key]

        if len(cn_candidates) == 1 and len(jp_candidates) == 1:
            pairs.append((cn_candidates[0], jp_candidates[0]))
            used_cn.add(id(cn_candidates[0]))
            used_jp.add(id(jp_candidates[0]))
            continue

        # 多首同名（同一首曲的多个 WE 变体、或不同曲的同名）：用艺术家消歧
        for cn_song in cn_candidates:
            if id(cn_song) in used_cn:
                continue
            cn_artist = normalize(cn_song.get("artist", ""))
            for jp_item in jp_candidates:
                if id(jp_item) in used_jp:
                    continue
                if normalize(jp_item["meta"].get("artist", "")) == cn_artist:
                    pairs.append((cn_song, jp_item))
                    used_cn.add(id(cn_song))
                    used_jp.add(id(jp_item))
                    break

        if any(id(s) not in used_cn for s in cn_candidates) or any(
            id(i) not in used_jp for i in jp_candidates
        ):
            ambiguous.append(cn_candidates[0].get("title") or key)

    return pairs, used_cn, used_jp, ambiguous


def load_jp_designers() -> dict[str, dict[str, str]]:
    """读取日服谱面制作者（wiki「譜面制作者一覧」抓来的）。"""
    if not JP_DESIGNERS_PATH.is_file():
        return {}
    try:
        return json.loads(JP_DESIGNERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_jp_jackets() -> dict[str, str]:
    """读取日服曲绘文件名缓存。"""
    if not JP_JACKETS_PATH.is_file():
        return {}
    try:
        return json.loads(JP_JACKETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build(cn_songs: list[dict], jp_items: list[dict]) -> tuple[list[dict], dict]:
    jp_jackets = load_jp_jackets()
    jp_designers = load_jp_designers()
    cn_normal = [s for s in cn_songs if not is_we_cn(s)]
    cn_we = [s for s in cn_songs if is_we_cn(s)]
    jp_normal = [i for i in jp_items if not is_we_jp(i)]
    jp_we = [i for i in jp_items if is_we_jp(i)]

    pairs_normal, used_cn_n, used_jp_n, amb_n = _match(cn_normal, jp_normal, we=False)
    pairs_we, used_cn_w, used_jp_w, amb_w = _match(cn_we, jp_we, we=True)

    used_cn = used_cn_n | used_cn_w
    used_jp = used_jp_n | used_jp_w

    songs: list[dict] = []

    def charts_block(cn: dict[int, dict], jp: dict[int, dict], *, we: bool) -> dict[str, dict]:
        block: dict[str, dict] = {}
        for index in sorted(set(cn) | set(jp)):
            name = DIFFICULTY_NAMES.get(index, str(index))
            cn_chart = cn.get(index) or {}
            jp_chart = jp.get(index) or {}
            cn_const = cn_chart.get("const")
            jp_const = jp_chart.get("const")
            # WE 用 ★ 难度，两边的 const 都是 0，比较没有意义
            comparable = not we and cn_const and jp_const
            differs = bool(comparable) and abs(float(cn_const) - float(jp_const)) > 1e-9
            block[name] = {
                "cn": (
                    {
                        "level": cn_chart.get("level"),
                        "const": cn_const,
                        "designer": cn_chart.get("designer"),
                    }
                    if cn_chart
                    else None
                ),
                "jp": (
                    {
                        "level": jp_chart.get("level"),
                        "const": jp_const,
                        "const_unknown": jp_chart.get("const_unknown", False),
                        "designer": jp_chart.get("designer"),
                    }
                    if jp_chart
                    else None
                ),
                "const_differs": differs,
            }
        return block

    def make(
        cn_song: dict | None,
        jp_item: dict | None,
        *,
        we: bool,
    ) -> dict:
        meta = (jp_item or {}).get("meta") or {}
        if cn_song is not None:
            title = cn_song.get("title") or meta.get("title")
            artist = cn_song.get("artist") or meta.get("artist")
            bpm = cn_song.get("bpm") or meta.get("bpm")
        else:
            title = meta.get("title")
            artist = meta.get("artist")
            bpm = meta.get("bpm")

        cn_block = cn_charts(cn_song) if cn_song else {}
        jp_block = jp_charts(jp_item) if jp_item else {}
        # wiki 抓来的日服谱师：按曲名匹配（两服同名）
        if title_designers := jp_designers.get(title or ""):
            for index, chart in jp_block.items():
                name = DIFFICULTY_NAMES.get(index)
                designer = title_designers.get(name or "")
                if designer:
                    chart["designer"] = designer
        if we and jp_block and not cn_block:
            pass  # 纯日服 WE，保持原样

        regions = []
        if cn_song is not None:
            regions.append("cn")
        if jp_item is not None:
            regions.append("jp")

        return {
            "title": title,
            "artist": artist,
            "bpm": bpm,
            "kind": "we" if we else "song",
            "regions": regions,
            "genres": {
                # 仅日服的曲目也要给出国服写法的分类名，
                # 否则界面上会冒出 ORIGINAL / VARIETY 这种英文枚举
                "cn": (cn_song or {}).get("genre")
                or GENRE_MAP_JP_TO_CN.get(meta.get("genre")),
                "jp": meta.get("genre"),
            },
            "cn_id": (cn_song or {}).get("id"),
            "jp_id": meta.get("id"),
            "cn_version": (cn_song or {}).get("version"),
            "jp_release": meta.get("release"),
            # 日服谱面 ID：与国服 id 是同一套编号（两服都有的曲子实测完全相等）
            "jp_idx": int(meta["idx"]) if str(meta.get("idx", "")).isdigit() else None,
            # 日服曲绘的真实文件名（由 fetch_jp_jackets.py 从 unidb 曲目页抓来）
            "jp_img": jp_jackets.get(meta.get("id") or ""),
            # 日服数据源只有发行日期，版本靠 jp_versions 的稼働日表换算
            "jp_version": version_name(meta.get("release")),
            "charts": charts_block(cn_block, jp_block, we=we),
        }

    for cn_song, jp_item in pairs_normal:
        songs.append(make(cn_song, jp_item, we=False))
    for cn_song, jp_item in pairs_we:
        songs.append(make(cn_song, jp_item, we=True))
    for song in cn_songs:
        if id(song) not in used_cn:
            songs.append(make(song, None, we=is_we_cn(song)))
    for item in jp_items:
        if id(item) not in used_jp:
            songs.append(make(None, item, we=is_we_jp(item)))

    songs.sort(key=lambda s: ((s["title"] or "").casefold(), s["kind"], s["cn_id"] or 0))

    diff_pairs = [
        (
            song["title"],
            name,
            chart["cn"]["const"],
            chart["jp"]["const"],
            chart["jp"].get("const_unknown", False),
        )
        for song in songs
        for name, chart in song["charts"].items()
        if chart.get("const_differs")
    ]

    def count(**kwargs: object) -> int:
        return sum(
            1
            for s in songs
            if all(s.get(k) == v for k, v in kwargs.items())
        )

    stats = {
        "cn_total": len(cn_songs),
        "jp_total": len(jp_items),
        "cn_songs": len(cn_normal),
        "cn_we": len(cn_we),
        "jp_songs": len(jp_normal),
        "jp_we": len(jp_we),
        "both": sum(1 for s in songs if len(s["regions"]) == 2),
        "both_songs": count(regions=["cn", "jp"], kind="song"),
        "both_we": count(regions=["cn", "jp"], kind="we"),
        "cn_only": sum(1 for s in songs if s["regions"] == ["cn"]),
        "jp_only": sum(1 for s in songs if s["regions"] == ["jp"]),
        "jp_only_songs": count(regions=["jp"], kind="song"),
        "jp_only_we": count(regions=["jp"], kind="we"),
        "const_diff_pairs": len(diff_pairs),
        "const_diff_songs": len({p[0] for p in diff_pairs}),
        "ambiguous_titles": amb_n + amb_w,
        "const_unknown_jp": sum(
            1
            for s in songs
            for c in s["charts"].values()
            if (c.get("jp") or {}).get("const_unknown")
        ),
        "samples": diff_pairs[:20],
    }
    return songs, stats


def render_readme(meta: dict, stats: dict) -> str:
    lines = [
        "# 国服 / 日服曲库并集",
        "",
        f"- 生成时间：{meta['generated_at']}",
        f"- 国服来源：落雪查分器 `GET /api/v0/chunithm/song/list`"
        f"（{stats['cn_songs']} 首普通曲 + {stats['cn_we']} 个 WORLD'S END）",
        f"- 日服来源：chunirec `music/showall` 镜像（reiwa.f5.si）"
        f"（{stats['jp_songs']} 首普通曲 + {stats['jp_we']} 个 WORLD'S END）",
        "",
        "## 统计",
        "",
        "| 项目 | 数量 |",
        "| --- | --- |",
        f"| 两服都有（普通曲） | **{stats['both_songs']}** |",
        f"| 两服都有（WORLD'S END） | {stats['both_we']} |",
        f"| 仅国服 | {stats['cn_only']} |",
        f"| 仅日服（普通曲） | **{stats['jp_only_songs']}** |",
        f"| 仅日服（WORLD'S END） | {stats['jp_only_we']} |",
        f"| 定数不同的（曲目, 难度）组合 | **{stats['const_diff_pairs']}** |",
        f"| 涉及曲目数 | {stats['const_diff_songs']} |",
        f"| 日服标记为「定数推定」的谱面 | {stats['const_unknown_jp']} |",
        "",
        "## 为什么要单独处理 WORLD'S END",
        "",
        "两服都把 WE 当独立曲目（国服是 `8xxx` 号段），但标题规则不同：",
        "日服带修改符后缀（`幾四音-Ixion-【止】`），国服不带（`幾四音-Ixion-`）。",
        "如果和普通曲混在同一个标题空间里配对，WE 会错配到同名的普通曲上。",
        "另外 WE 用 ★ 难度、定数恒为 0，**不参与定数比较**。",
        "",
        "## 字段",
        "",
        "| 字段 | 说明 |",
        "| --- | --- |",
        "| `kind` | `song`（普通曲）/ `we`（WORLD'S END） |",
        "| `regions` | `[\"cn\",\"jp\"]` / `[\"cn\"]` / `[\"jp\"]` |",
        "| `cn_id` / `jp_id` | 两服各自的曲目 ID（单服独有时另一侧为 `null`） |",
        "| `jp_release` / `jp_version` | 日服发行日期 / 所属版本（Mate、X-VERSE 等，由稼働日表换算） |",
        "| `jp_idx` | 日服谱面 ID（与国服 `id` 同一套编号；WE 无此字段） |",
        "| `jp_img` | 日服曲绘文件名哈希（`db.chunirec.net/lmg/music/jkt/{jp_img}.jpg`；WE 无此字段） |",
        "| `genres.cn` / `genres.jp` | 两服分类名（日服分类已映射出国服写法） |",
        "| `charts.<难度>` | `cn` / `jp` 两侧的标级与定数，`const_differs` 标出差异 |",
        "| `charts.<难度>.jp.const_unknown` | 日服该定数是否为推定值（官方未公布） |",
        "",
        "## 定数差异样例",
        "",
        "| 曲目 | 难度 | 国服 | 日服 | 日服定数推定 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for title, name, cn_const, jp_const, unknown in stats["samples"]:
        lines.append(
            f"| {title} | {name} | {cn_const} | {jp_const} | {'是' if unknown else '否'} |"
        )
    if stats["ambiguous_titles"]:
        lines.extend(["", "## 同名未完全消歧（已尽量按艺术家配对）", ""])
        for title in stats["ambiguous_titles"][:40]:
            lines.append(f"- {title}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建国服/日服曲库并集")
    parser.add_argument("--cn", type=pathlib.Path, default=CN_CACHE)
    parser.add_argument("--jp", type=pathlib.Path, default=JP_CACHE)
    parser.add_argument("--out", type=pathlib.Path, default=OUT_DIR)
    args = parser.parse_args()

    for path in (args.cn, args.jp):
        if not path.is_file():
            print(
                f"缺少数据 {path}\n先运行：python scripts/fetch_reference_data.py"
                " 与 python scripts/fetch_jp_reference.py",
                file=sys.stderr,
            )
            return 1

    cn_songs = load_cn(args.cn)
    jp_items = load_jp(args.jp)
    songs, stats = build(cn_songs, jp_items)

    args.out.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "sources": {
            "cn": "https://maimai.lxns.net/api/v0/chunithm/song/list",
            "jp": "https://reiwa.f5.si/chunirec_all.json",
        },
        **stats,
    }
    (args.out / "songs.union.json").write_text(
        json.dumps({"meta": meta, "songs": songs}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "README.md").write_text(render_readme(meta, stats), encoding="utf-8")

    print(f"国服 {stats['cn_total']} 条（普通 {stats['cn_songs']} + WE {stats['cn_we']}）")
    print(f"日服 {stats['jp_total']} 条（普通 {stats['jp_songs']} + WE {stats['jp_we']}）")
    print(f"  两服都有 : {stats['both']}（普通 {stats['both_songs']} / WE {stats['both_we']}）")
    print(f"  仅国服   : {stats['cn_only']}")
    print(f"  仅日服   : {stats['jp_only']}（普通 {stats['jp_only_songs']} / WE {stats['jp_only_we']}）")
    print(f"  定数不同 : {stats['const_diff_pairs']} 个（涉及 {stats['const_diff_songs']} 首）")
    print(f"  日服推定定数: {stats['const_unknown_jp']}")
    print()
    print("=== 定数差异样例 ===")
    for title, name, cn_const, jp_const, unknown in stats["samples"][:12]:
        print(
            f"  {title:<32} {name}  国服 {cn_const} / 日服 {jp_const}"
            f"{'  (日服为推定值)' if unknown else ''}"
        )
    print()
    print(f"输出：{args.out}")
    for path in sorted(args.out.iterdir()):
        print(f"  {path.name:<22} {path.stat().st_size:>9,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
