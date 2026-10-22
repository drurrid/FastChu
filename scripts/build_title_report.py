"""生成中二节奏称号数据集。

产出（默认写到 ``data/titles/``）：

============================  ==================================================
``trophies.all.json``         全部称号（含分类、颜色、条件、关联曲目）
``trophies.special.json``     过滤后的「特殊获得条件」称号
``trophies.special.csv``      同上，Excel 友好（UTF-8 BOM）
``trophies.special.md``       同上，可读报告（按稀有度分层 + 分类）
``trophies.kaomoji.json``     全部颜文字称号
``trophies.kaomoji.md``       颜文字称号报告
``README.md``                 字段说明、统计、复现命令
============================  ==================================================

用法::

    python scripts/fetch_reference_data.py      # 先抓曲库与称号
    python scripts/build_title_report.py
    python scripts/build_title_report.py --include-character-rainbow
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
import unicodedata
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastchu.models import DIFFICULTY_LABELS, FULL_CHAIN_LABELS, FULL_COMBO_LABELS, RANK_LABELS, Trophy
from fastchu.services.titles import (
    CATEGORY_LABELS,
    COLOR_ORDER,
    TIER_LABELS,
    TrophyCatalog,
    TrophyCategory,
    classify,
    is_kaomoji,
    load_trophies_from_file,
    tier,
    tier_label,
)

DEFAULT_CACHE = ROOT / "data" / "cache" / "trophy_list.json"
DEFAULT_OUT = ROOT / "data" / "titles"

#: 日服 Wiki 抓下来的称号条件（由 scripts/fetch_jp_titles.py 生成）
WIKI_CACHE = ROOT / "data" / "cache" / "jp_titles_wiki.json"


#: 各种「破折号 / 横线」字符——wiki 与落雪写法不同，查重/反查时必须归一
_DASH_MAP = {
    ord(char): "-"
    for char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d\u2500\u2501"
}

#: 弯引号 / 修饰符撇号与直引号视为同一字符（wiki 与国服数据常有差异）
_QUOTE_MAP = {
    **{ord(char): "'" for char in "\u2018\u2019\u201b\u02bc\u2032\uff07"},
    **{ord(char): '"' for char in "\u201c\u201d\u201e\u301d\u301e\uff02"},
}

def _name_key(name: str) -> str:
    """称号名匹配键：全半角统一 + 破折号/引号归一 + 去掉所有空白。

    Wiki 里很多称号用全角空格排版（``や　ら　な　い　か``），
    和国服数据的写法差一个空格就会匹配不上。
    """
    folded = (
        unicodedata.normalize("NFKC", name or "")
        .translate(_DASH_MAP)
        .translate(_QUOTE_MAP)
    )
    return re.sub(r"\s+", "", folded).casefold()


#: 日服限定称号的合成 ID 起点（与落雪的 0~5620 不冲突）
JP_ONLY_ID_BASE = 900_000

#: 人工核对过的日服称号颜色（Wiki 表格颜色缺失或不准时的兜底）
#: 目前只有 Melodiniq（唯一 ULT 16）的那首专属称号。
KNOWN_TITLE_COLORS: dict[str, str] = {
    "未来はあなたの手の中に": "rainbow",
}


def _load_wiki_raw() -> dict[str, dict]:
    """读取 Wiki 原始条目：``{称号原名: {condition, page, section}}``。"""
    if not WIKI_CACHE.is_file():
        return {}
    try:
        payload = json.loads(WIKI_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    titles = payload.get("titles") or {}
    return {name: entry for name, entry in titles.items() if isinstance(entry, dict)}


def load_wiki_conditions(path: pathlib.Path) -> dict[str, dict]:
    """读取 Wiki 称号条件，返回 ``{匹配键: {condition, page, section}}``。"""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for name, entry in (payload.get("titles") or {}).items():
        key = _name_key(name)
        if key and key not in out:
            out[key] = entry
    return out

#: 报告里各分类的展示顺序（越靠前越「难/特殊」）
REPORT_ORDER: list[str] = [
    TrophyCategory.ALL_SONGS,
    TrophyCategory.PER_SONG_AJ_ULTIMA,
    TrophyCategory.MULTIPLAYER,
    TrophyCategory.MATCHING_PLAYER,
    TrophyCategory.PLAYER_LEVEL,
    TrophyCategory.PER_SONG_AJ_SPECIAL,
    TrophyCategory.PER_SONG_RANK,
    TrophyCategory.PER_SONG_CHAIN,
    TrophyCategory.RATING,
    TrophyCategory.LOGIN_DAYS,
    TrophyCategory.MAP_CLEAR,
    TrophyCategory.ONE_CREDIT,
    TrophyCategory.DANGER_SKILL,
    TrophyCategory.EVENT,
    TrophyCategory.OTHER,
    TrophyCategory.INHERITED,
    TrophyCategory.PER_SONG_OTHER,
]


# --------------------------------------------------------------------------- #
# 行数据
# --------------------------------------------------------------------------- #


#: 这些 Wiki 页面上的条目是「某首歌的专属称号」，与同名的引继/活动称号
#: **无法区分**（两边的 lxns 数据都没有 required.songs）。
#: 曾经因此把 #39「邪眼」标成了某首配信停止曲的
#: ``BASIC/SPEED SONIC/ALL JUSTICE達成``——所以整页排除。
_CONFLICT_PAGES = frozenset({"新規取得不可称号一覧(楽曲プレイ)"})


def _is_vague(description: str) -> bool:
    """国服数据里这条说明是否「没有真正的条件」。"""
    text = (description or "").strip()
    return text in ("", "-") or "引き継ぎ" in text


def trophy_row(
    trophy: Trophy, catalog: TrophyCatalog, wiki: dict[str, dict] | None = None
) -> dict:
    info = catalog.category_of(trophy)
    wiki = wiki or {}
    wiki_entry = wiki.get(_name_key(trophy.name))

    lxns_condition = trophy.condition_text()
    jp_condition = (wiki_entry or {}).get("condition") or ""
    # 条件取「国服有说明就用国服，否则用日服 Wiki」
    # 同名撞车页面的条件不可信，宁可保留落雪的原文
    if wiki_entry and (wiki_entry.get("page") or "") in _CONFLICT_PAGES:
        jp_condition = ""

    if _is_vague(trophy.description) and jp_condition:
        condition_final = jp_condition
        condition_source = "wiki"
    elif not _is_vague(trophy.description):
        condition_final = lxns_condition
        condition_source = "lxns"
    elif jp_condition:
        condition_final = jp_condition
        condition_source = "wiki"
    else:
        condition_final = lxns_condition
        condition_source = "none"
    song_titles = []
    seen = set()
    for req in trophy.required:
        for song in req.songs:
            if song.id not in seen:
                seen.add(song.id)
                song_titles.append(song.title or str(song.id))

    return {
        "id": trophy.id,
        "name": trophy.name,
        "color": trophy.color,
        "color_label": trophy.color_label,
        "category": info.category,
        "category_label": CATEGORY_LABELS.get(info.category, info.category),
        "tier": tier(trophy, info),
        "tier_label": tier_label(tier(trophy, info)),
        "is_special": info.is_special,
        "excluded_reason": info.excluded,
        "description": trophy.description,
        "condition_text": trophy.condition_text(),
        "condition_final": condition_final,
        "condition_source": condition_source,
        "jp_condition": jp_condition,
        "jp_condition_page": (wiki_entry or {}).get("page"),
        "jp_condition_section": (wiki_entry or {}).get("section"),
        "song_count": trophy.song_count,
        "song_ids": trophy.song_ids,
        "song_titles": song_titles,
        "difficulties": [DIFFICULTY_LABELS.get(d, str(d)) for d in trophy.difficulties],
        "rank": RANK_LABELS.get(trophy.rank, trophy.rank) if trophy.rank else None,
        "full_combo": FULL_COMBO_LABELS.get(trophy.full_combo, trophy.full_combo)
        if trophy.full_combo
        else None,
        "full_chain": FULL_CHAIN_LABELS.get(trophy.full_chain, trophy.full_chain)
        if trophy.full_chain
        else None,
        "is_kaomoji": is_kaomoji(trophy.name),
    }


def sort_key(row: dict) -> tuple:
    return (row["tier"], -COLOR_ORDER.get(row["color"], 0), row["category"], row["id"])


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def write_json(path: pathlib.Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    fields = [
        "id", "name", "color", "color_label", "category", "category_label",
        "tier", "tier_label", "song_count", "song_titles", "difficulties",
        "rank", "full_combo", "full_chain", "condition_final", "condition_source",
        "description", "jp_condition", "is_kaomoji",
    ]
    # utf-8-sig：Excel 直接双击打开也不会把日文/中文显示成乱码
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["song_titles"] = " / ".join(flat["song_titles"][:6])
            if len(flat["song_titles"]) and row["song_count"] > 6:
                flat["song_titles"] += f" …共{row['song_count']}首"
            flat["difficulties"] = "/".join(flat["difficulties"])
            flat["is_kaomoji"] = "是" if flat["is_kaomoji"] else ""
            writer.writerow(flat)


def md_cell(text: str, limit: int = 120) -> str:
    """Markdown 表格单元格转义。"""
    clean = (text or "").replace("|", "\\|").replace("\n", " ").strip()
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return clean or "—"


def condition_cell(row: dict) -> str:
    """条件单元格：日服 Wiki 补全的加 ※ 标记，来源可追溯。"""
    text = row.get("condition_final") or row.get("condition_text") or ""
    if row.get("condition_source") == "wiki":
        text = f"{text} ※日服Wiki"
    elif row.get("condition_source") == "none":
        text = "（无公开说明）"
    return md_cell(text, 150)


def render_table(rows: list[dict]) -> str:
    lines = [
        "| 颜色 | 称号 | 条件 | 关联曲目 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        songs = "、".join(row["song_titles"][:3])
        if row["song_count"] > 3:
            songs += f" …共{row['song_count']}首"
        lines.append(
            f"| {row['color_label']} | {md_cell(row['name'], 60)} | "
            f"{condition_cell(row)} | {md_cell(songs, 80)} |"
        )
    return "\n".join(lines)


def render_special_report(rows: list[dict], meta: dict) -> str:
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)

    tier_counts: dict[int, int] = {}
    for row in rows:
        tier_counts[row["tier"]] = tier_counts.get(row["tier"], 0) + 1

    out: list[str] = [
        "# 中二节奏 · 特殊获得条件称号总表",
        "",
        f"- 数据源：落雪查分器公共接口 `GET /api/v0/chunithm/trophy/list`",
        f"- 生成时间：{meta['generated_at']}",
        f"- 称号总数：**{meta['total']}**",
        f"- 排除固定称号：**{meta['excluded']}**"
        f"（常规 AJ {meta['excluded_aj']} / 究极固定 {meta['excluded_ultima']}"
        f" / 角色养成 {meta['excluded_character']}）",
        f"- 本表收录：**{len(rows)}** 个特殊称号",
        "",
        "> 判定依据是接口返回的结构化条件（`required`）加上官方 `description`，"
        "不是主观判断。分类规则见 `data/titles/README.md`。",
        "",
        "## 稀有度分层（自定义排序用，非官方）",
        "",
        "| 层级 | 数量 | 含义 |",
        "| --- | --- | --- |",
    ]
    for value in (1, 2, 3, 4):
        out.append(f"| {TIER_LABELS[value]} | {tier_counts.get(value, 0)} | 见 README |")
    out.append("")

    for category in REPORT_ORDER:
        group = by_category.get(category)
        if not group:
            continue
        group.sort(key=lambda r: (-COLOR_ORDER.get(r["color"], 0), r["id"]))
        out.append(f"## {CATEGORY_LABELS.get(category, category)}（{len(group)}）")
        out.append("")
        out.append(render_table(group))
        out.append("")

    return "\n".join(out)


def render_kaomoji_report(rows: list[dict], meta: dict) -> str:
    out: list[str] = [
        "# 中二节奏 · 颜文字称号全收集",
        "",
        f"- 生成时间：{meta['generated_at']}",
        f"- 命中数量：**{len(rows)}**（其中特殊称号 "
        f"{sum(1 for r in rows if r['is_special'])} 个）",
        "",
        "> 判定规则：括号内含颜文字特征字符 / ASCII 表情（如 `(^o^)`）/ "
        "强特征字符 + 肢体符号 / `:-)` `orz`。规则见 `fastchu/services/titles.py`。",
        "",
        "| 颜色 | 称号 | 分类 | 条件 |",
        "| --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda r: (-COLOR_ORDER.get(r["color"], 0), r["id"])):
        out.append(
            f"| {row['color_label']} | {md_cell(row['name'], 80)} | "
            f"{md_cell(row['category_label'], 24)} | {condition_cell(row)} |"
        )
    out.append("")
    return "\n".join(out)


def render_readme(meta: dict, category_counts: dict[str, int], color_counts: dict[str, int]) -> str:
    jp_color = meta.get("jp_only_color", {})
    rows = ["| 分类 | 数量 | 是否收录 | 判定规则 |", "| --- | --- | --- | --- |"]
    rules = {
        TrophyCategory.ULTIMA_AJ: "`color == ultima` → 称号名就是曲名，属于每个谱面的固定称号",
        TrophyCategory.PER_SONG_AJ: "`required` 只有 `full_combo`、只涉及 1 首歌，且**称号名就是曲名** → 每个谱面的固定 AJ 称号",
        TrophyCategory.PER_SONG_AJ_SPECIAL: "同样是单曲 AJ/FC，但**称号名不是曲名**（或覆盖全难度）→ 专属成就称号，保留",
        TrophyCategory.CHARACTER_RANK: "无 `required`，`description` 含 `キャラクターランク` → 角色养成",
        TrophyCategory.PER_SONG_AJ_ULTIMA: "同上但涉及 ULTIMA 谱面 → 稀有，保留",
        TrophyCategory.PER_SONG_RANK: "`required` 含 `rank`，只涉及 1~4 首歌",
        TrophyCategory.PER_SONG_CHAIN: "`required` 含 `full_chain`（数据里都同时是联机条件）",
        TrophyCategory.PER_SONG_FC: "`required` 只有 `full_combo=fullcombo` → 单曲 FULL COMBO（不是 AJ）",
        TrophyCategory.PER_SONG_OTHER: "单曲但只有「打一遍」之类条件",
        TrophyCategory.MULTIPLAYER: "`description` 含 `マッチング` → 需要店内/好友联机",
        TrophyCategory.ALL_SONGS: "涉及曲目数 ≥ 40 → 某版本全部谱面的 ACHIEVE",
        TrophyCategory.RATING: "`description` 含 `RATING x.xx`",
        TrophyCategory.MAP_CLEAR: "`description` 含 `マップ制覇`",
        TrophyCategory.PLAYER_LEVEL: "`description` 含 `プレイヤーレベル`",
        TrophyCategory.LOGIN_DAYS: "`description` 含 `ログイン`",
        TrophyCategory.DANGER_SKILL: "`description` 含 `デンジャースキル`",
        TrophyCategory.ONE_CREDIT: "`description` 含 `クレジット中`",
        TrophyCategory.MATCHING_PLAYER: "需要和佩戴特定称号的玩家联机",
        TrophyCategory.INHERITED: "`description` 含 `引き継ぎ` → 旧作继承",
        TrophyCategory.EVENT: "无 `required` 且 `description` 为 `-` → 活动/联名限定",
        TrophyCategory.OTHER: "其余无法自动归类",
    }
    for category, count in category_counts.items():
        excluded = category in (
            TrophyCategory.ULTIMA_AJ,
            TrophyCategory.PER_SONG_AJ,
            TrophyCategory.CHARACTER_RANK,
        )
        rows.append(
            f"| `{category}` | {count} | {'❌ 排除' if excluded else '✅ 收录'} | "
            f"{rules.get(category, '—')} |"
        )

    lxns_count = meta["special"] - meta["wiki_special_enriched"] - meta["condition_missing"]
    wiki_special_enriched = meta["wiki_special_enriched"]
    condition_missing = meta["condition_missing"]
    color_rows = ["| 颜色 | 全部 | 特殊称号中 |", "| --- | --- | --- |"]
    for color in sorted(color_counts, key=lambda c: -color_counts[c]):
        color_rows.append(
            f"| `{color}` | {color_counts[color]} | {meta['special_color'].get(color, 0)} |"
        )

    return f"""# 中二节奏称号数据集

由 `scripts/build_title_report.py` 从落雪查分器公共接口生成，**不需要任何密钥**。

## 文件

| 文件 | 内容 |
| --- | --- |
| `trophies.all.json` | 全部 {meta['total']} 个称号，含分类与条件 |
| `trophies.special.json` | 过滤后的 {meta['special']} 个「特殊获得条件」称号 |
| `trophies.special.csv` | 同上，Excel 可直接打开（UTF-8 BOM） |
| `trophies.special.md` | 同上，按稀有度分层 + 分类的可读报告 |
| `trophies.kaomoji.json` | {meta['kaomoji']} 个颜文字称号 |
| `trophies.kaomoji.md` | 颜文字称号报告 |

生成时间：{meta['generated_at']}

## 过滤逻辑

用户要的是「去掉固定称号（常规 AJ / 究极）和角色养成称号」，只留特殊获得条件。实测分布：

{chr(10).join(rows)}

**排除 {meta['excluded']} 个（常规 AJ {meta['excluded_aj']} / 究极固定 {meta['excluded_ultima']} / 角色养成 {meta['excluded_character']}），保留 {meta['special']} 个。**

## 颜色分布

{chr(10).join(color_rows)}

> 注意：虹色（`rainbow`）称号共 {color_counts.get('rainbow', 0)} 个，
> 其中 {meta['rainbow_character']} 个是角色养成（把某个角色练到指定等级）。
> 按需求已排除；如果盘点视频需要把它们算进来，重新生成时加
> `--include-character-rainbow`。

## 字段说明（JSON）

| 字段 | 说明 |
| --- | --- |
| `id` | 称号 ID |
| `name` | 称号文本 |
| `color` / `color_label` | 颜色枚举 / 中文标签 |
| `category` / `category_label` | 分类枚举 / 中文标签 |
| `tier` / `tier_label` | 自定义稀有度分层（1 最难 ~ 4 最普通） |
| `is_special` | 是否为特殊称号 |
| `excluded_reason` | 被排除的原因（空 = 收录） |
| `description` | 官方条件文本（原样保留） |
| `condition_text` | 可读条件（`description` 为空时用结构化条件拼） |
| `song_count` / `song_ids` / `song_titles` | 关联曲目 |
| `difficulties` / `rank` / `full_combo` / `full_chain` | 结构化条件 |
| `is_kaomoji` | 是否颜文字称号 |

## 日服 Wiki 考据补全

国服数据里有一批称号**没有真正的获得条件**：208 个的 `description` 是
`CHUNITHMから引き継ぎ` 或 `-`。本数据集从日服
[CHUNITHM 攻略 Wiki](https://wikiwiki.jp/chunithmwiki/) 抓取了 {meta.get('wiki_titles', 0)} 条称号记录，
按标准化称号名匹配，补全了 **{wiki_special_enriched}** 个特殊称号的条件。

| `condition_source` | 数量 | 含义 |
| --- | ---: | --- |
| `lxns` | {lxns_count} | 国服数据自带说明 |
| `wiki` | {wiki_special_enriched} | 日服 Wiki 补全，报告里带 `※日服Wiki` 标记 |
| `none` | {condition_missing} | 两处都查不到（多为最新活动称号） |

仍缺条件的多是 **WEC2026 / 全勤王 / 四倍率** 等最新活动称号——Wiki 也还没收录。

另外收录 **{meta.get('jp_only', 0)}** 个「日服有、国服没有」的称号（`jp_only.json`）。
它们的条件与颜色来自同一批 Wiki 表格：颜色按单元格背景色解析
（虹 {jp_color.get('rainbow', 0)} / 铂金 {jp_color.get('platina', 0)} / 金 {jp_color.get('gold', 0)} /
银 {jp_color.get('silver', 0)} / 普通 {jp_color.get('normal', 0)} / 未标注 {jp_color.get('unknown', 0)}）。

### 「CHUNITHMから引き継ぎ」是什么意思

Wiki 页面写得很清楚：2021/11/4 起，初代 CHUNITHM 的这批称号在 NEW 之后的机体上
**新规取得不可**，官方把入手条件的描述统一改成了「CHUNITHMからの引き継ぎ」。
所以它不是条件，而是「已经无法获得」的标记。

### 已知局限：同名冲突

极少数称号在**旧作与现行版本里同名但条件不同**（例如 `皇帝`）。
Wiki 侧按第一次出现的条目匹配，因此这类称号给出的可能是**现行版本**的条件。

## 复现

```bash
python scripts/fetch_reference_data.py            # 抓曲库 + 称号
python scripts/build_title_report.py              # 重新生成本目录
python scripts/build_title_report.py --include-character-rainbow
```

## 已知数据问题

* 官方文档写的颜色枚举是 `normal/copper/silver/gold/platinum/rainbow/image`，
  但**实际接口**返回 `platina`（不是 `platinum`）、额外有 `ultima`、没有 `copper`。
  本项目以实际数据为准，颜色用字符串保存并做标签映射，未知颜色不会解析失败。
* 全曲系称号（`Spirit/Tribute/Legend of <版本>`）只覆盖到 `VERSE`，
  最新版本（X-VERSE 等）尚未出现在接口数据中。
"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="生成中二节奏称号数据集")
    parser.add_argument("--cache", type=pathlib.Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--include-character-rainbow",
        action="store_true",
        help="把虹色的角色养成称号也当作特殊称号收录（盘点视频可能想要）",
    )
    args = parser.parse_args()

    if not args.cache.is_file():
        print(
            f"找不到称号缓存 {args.cache}。\n先运行：python scripts/fetch_reference_data.py",
            file=sys.stderr,
        )
        return 1

    trophies = load_trophies_from_file(args.cache)
    catalog = TrophyCatalog(trophies)

    wiki = load_wiki_conditions(WIKI_CACHE)
    rows = [trophy_row(t, catalog, wiki) for t in trophies]
    rows.sort(key=sort_key)

    # 默认按「排除固定称号」过滤；可选地把虹色角色称号放回来
    def keep(row: dict) -> bool:
        if row["is_special"]:
            return True
        if args.include_character_rainbow:
            return row["category"] == TrophyCategory.CHARACTER_RANK and row["color"] == "rainbow"
        return False

    special_rows = [row for row in rows if keep(row)]

    # ---- 日服限定：wiki 有、落雪没有的称号（国服未收录）----
    lxns_keys = {_name_key(row["name"]) for row in rows}
    #: 从 wiki 页面误抓进来的导航链接 / 颜色名，不算称号
    _NAV_NOISE = {
        "基本の遊び方", "雑談掲示板", "ノーマル", "シルバー", "ゴールド",
        "プラチナ", "虹", "レインボー", "称号", "楽曲一覧", "質問掲示板",
    }
    # wiki 用「 ※」标记「与国服版本不同的另一条」，去掉标记后往往与落雪同名，
    # 这类是**同一称号**，不应该再列一遍（否则详情里同时出现两条几乎一样的）
    _MARK_RE = re.compile(r"\s*[※*]+\s*$")

    jp_only_rows: list[dict] = []
    for name, entry in _load_wiki_raw().items():
        if name in _NAV_NOISE or not entry.get("condition"):
            continue
        # 纯十六进制串是 wiki 表格里的编码噪音，不是称号名
        if re.fullmatch(r"[0-9a-fA-F]{8,}", name):
            continue
        if _name_key(name) in lxns_keys:
            continue
        if _name_key(_MARK_RE.sub("", name)) in lxns_keys:
            continue
        condition = (entry.get("condition") or "").strip()
        # wiki 写成 ``[VAR] 曲名/难度/条件``，国服是 ``曲名/难度/条件``——
        # 去掉开头的风格标签，两边格式才对得上
        condition = re.sub(r"^\[[^\]]{1,12}\]\s*", "", condition)

        # Wiki 表格背景色是主要来源；人工核对表与 ULTIMA 特例只在它缺失时兜底
        color = (entry.get("color") or "").strip()
        if not color:
            color = next(
                (
                    known_color
                    for known_name, known_color in KNOWN_TITLE_COLORS.items()
                    if _name_key(known_name) == _name_key(name)
                ),
                "",
            )
        # 「ULTIMA以下全て ALL JUSTICE」（含 ULTIMA 的全难度 AJ）实测是虹色：
        # 已核对 Melodiniq 与 YOUNITHM 两首，其余同类也按虹处理。
        if not color and "ULTIMA以下全て" in condition and "ALL JUSTICE" in condition:
            color = "rainbow"

        jp_only_rows.append(
            {
                "name": name,
                "color": color,
                "condition": condition,
                "page": entry.get("page") or "",
                "section": entry.get("section") or "",
            }
        )
    jp_only_rows.sort(key=lambda r: (r["page"], r["name"]))
    for index, row in enumerate(jp_only_rows, start=1):
        # 合成 ID：与落雪的 0~5620 不冲突
        row["id"] = JP_ONLY_ID_BASE + index

    # 颜文字按**称号名**去重：同一首歌的几个难度会给出同名称号（固定 AJ），
    # 全收集列表里重复出现没有意义。rows 已按稀有度/颜色排序，保留第一条即可。
    kaomoji_rows: list[dict] = []
    seen_names: set[str] = set()
    for row in rows:
        if not row["is_kaomoji"]:
            continue
        if row["name"] in seen_names:
            continue
        seen_names.add(row["name"])
        kaomoji_rows.append(row)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    jp_only_color: dict[str, int] = {}
    for row in jp_only_rows:
        key = row["color"] or "unknown"
        jp_only_color[key] = jp_only_color.get(key, 0) + 1

    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "total": len(rows),
        "special": len(special_rows),
        "excluded": len(rows) - len(special_rows),
        "excluded_aj": sum(1 for r in rows if r["category"] == TrophyCategory.PER_SONG_AJ),
        "excluded_character": sum(
            1 for r in rows if r["category"] == TrophyCategory.CHARACTER_RANK
        ),
        "excluded_ultima": sum(
            1 for r in rows if r["category"] == TrophyCategory.ULTIMA_AJ
        ),
        "rainbow_character": sum(
            1 for r in rows if r["category"] == TrophyCategory.CHARACTER_RANK and r["color"] == "rainbow"
        ),
        "kaomoji": len(kaomoji_rows),
        "wiki_enriched": sum(1 for r in rows if r["condition_source"] == "wiki"),
        "wiki_special_enriched": sum(
            1 for r in special_rows if r["condition_source"] == "wiki"
        ),
        "condition_missing": sum(1 for r in special_rows if r["condition_source"] == "none"),
        "wiki_titles": len(wiki),
        "jp_only": len(jp_only_rows),
        "jp_only_color": jp_only_color,
        "special_color": catalog.color_counts(special_only=True),
    }

    write_json(out / "trophies.all.json", {"meta": {"total": len(rows)}, "trophies": rows})
    write_json(
        out / "trophies.special.json",
        {"meta": meta, "trophies": special_rows},
    )
    # 运行时用的「条件补充」：只有 wiki 覆盖的条目才需要，
    # 插件启动时把它叠到称号的 description 上（否则「皇帝」只会显示 CHUNITHMから引き継ぎ）
    write_json(
        out / "conditions.json",
        {
            "meta": {
                "total": sum(1 for row in rows if row["condition_source"] == "wiki"),
            },
            "conditions": {
                str(row["id"]): {
                    "condition": row["condition_final"],
                    "source": row["condition_source"],
                }
                for row in rows
                if row["condition_source"] == "wiki" and row.get("condition_final")
            },
        },
    )
    write_json(
        out / "trophies.kaomoji.json",
        {"meta": {"total": len(kaomoji_rows)}, "trophies": kaomoji_rows},
    )
    # 日服限定：wiki 有、落雪没有的称号（国服未收录）
    write_json(
        out / "jp_only.json",
        {
            "meta": {"total": len(jp_only_rows)},
            "trophies": jp_only_rows,
        },
    )
    write_csv(out / "trophies.special.csv", special_rows)
    (out / "trophies.special.md").write_text(
        render_special_report(special_rows, meta), encoding="utf-8"
    )
    (out / "trophies.kaomoji.md").write_text(
        render_kaomoji_report(kaomoji_rows, meta), encoding="utf-8"
    )
    (out / "README.md").write_text(
        render_readme(meta, catalog.category_counts(), catalog.color_counts()), encoding="utf-8"
    )

    print(f"称号总数        : {meta['total']}")
    print(f"排除（固定）    : {meta['excluded']}")
    print(f"  ├ 常规 AJ     : {meta['excluded_aj']}")
    print(f"  ├ 究极（固定）: {meta['excluded_ultima']}")
    print(f"  └ 角色养成    : {meta['excluded_character']}"
          f"（其中虹色 {meta['rainbow_character']}）")
    print(f"特殊称号        : {meta['special']}")
    print(f"颜文字称号      : {meta['kaomoji']}")
    print()
    print("=== 特殊称号分类 ===")
    counts: dict[str, int] = {}
    for row in special_rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {category:<20} {count:>5}  {CATEGORY_LABELS.get(category, '')}")
    print()
    print("=== 稀有度分层 ===")
    tiers: dict[int, int] = {}
    for row in special_rows:
        tiers[row["tier"]] = tiers.get(row["tier"], 0) + 1
    for value in sorted(tiers):
        print(f"  {TIER_LABELS[value]:<16} {tiers[value]:>5}")
    print()
    print(f"输出目录：{out}")
    for path in sorted(out.iterdir()):
        print(f"  {path.name:<26} {path.stat().st_size:>9,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
