"""抓取日服 CHUNITHM Wiki 的称号条件与颜色（称号考据）。

落雪（国服）数据里，很多称号的 ``description`` 只是「CHUNITHMから引き継ぎ」
或者干脆是 ``-``——**没有真正的获得条件**。日服 Wiki 记录了这些称号的原始条件，
包括不少现在已无法获得的活动/赛事称号，以及国服特有的赛事称号（WEC 等）。

Wiki 的称号表格用**单元格背景色**区分稀有度：灰=普通、浅蓝=银、金=金、
青绿=铂金、粉=虹；抓取时一并保存，供日服限定称号使用。

抓取结果写成 ``data/cache/jp_titles_wiki.json``，由
``scripts/build_title_report.py`` 用来给称号补条件和颜色。

数据源：https://wikiwiki.jp/chunithmwiki/

用法::

    python scripts/fetch_jp_titles.py
    python scripts/fetch_jp_titles.py --force
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "jp_titles_wiki.json"

BASE = "https://wikiwiki.jp/chunithmwiki/"
USER_AGENT = "Mozilla/5.0 (compatible; FastChu/0.1; +https://maimai.lxns.net)"

#: 已知的称号页面。抓取时会再从页面导航里发现新页面。
SEED_PAGES = [
    "新規取得不可称号一覧(その他)",
    "新規取得不可称号一覧(楽曲プレイ)",
    "マッチング称号一覧",
    "楽曲プレイ称号一覧",
    "進行度・他ゲーム連動称号一覧",
]

#: 页面里出现的导航链接，若名字像称号页就一并抓
_LIKE_TITLE_PAGE = re.compile(r"称号")
#: 「レアリティ変更一覧」是变更记录：单元格背景是**变更前**的旧颜色，
#: 会把旧稀有度回填进缓存，必须排除。
_SKIP_PAGE = re.compile(
    r"^(編集|コメント|掲示板|雑談|質問|情報提供|難易度議論|最新のコメント|.*レアリティ変更)"
)

#: Wiki 用称号单元格的背景色区分稀有度。已和落雪数据交叉验证：
#: 灰=普通、浅蓝=银、金=金、青绿=铂金、粉=虹。
#: 白色 / 未标注的多是「スペシャル」或说明文字行，颜色不固定，留空。
COLOR_BY_BACKGROUND: dict[str, str] = {
    "#d3d3d3": "normal",
    "#f0f8ff": "silver",
    "#ffd700": "gold",
    "#7fffd4": "platina",
    "#ff9bb7": "rainbow",
}


def _cell_color(cell) -> str:
    """读取单元格背景色并映射为称号颜色；识别不了的返回空串。"""
    style = cell.get("style") or ""
    match = re.search(r"background-color\s*:\s*([^;]+)", style, flags=re.IGNORECASE)
    background = (match.group(1) if match else cell.get("bgcolor") or "").strip().lower()
    return COLOR_BY_BACKGROUND.get(background, "")


def fetch(page: str, timeout: float = 90.0) -> str | None:
    url = BASE + urllib.parse.quote(page)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"  [skip] {page}: {type(exc).__name__} {exc}", file=sys.stderr)
        return None


def _expand_table(table) -> list[list[tuple[str, str]]]:
    """把表格展开成规整的二维数组，**处理 rowspan**，并保留单元格颜色。

    Wiki 里一个条件常常用 ``rowspan="11"`` 覆盖连续 11 个称号，
    后面的行只有 1 个单元格。如果只认 ``>=2`` 列的行，这些称号就全丢了
    （初代引继的那批称号正是这种结构）。

    返回的每个单元格是 ``(文本, 颜色)``；颜色来自背景色映射，
    识别不了时为空串。
    """
    rows: list[list[tuple[str, str]]] = []
    #: 列号 → (剩余行数, 文本, 颜色)
    spans: dict[int, tuple[int, str, str]] = {}

    for tr in table.iter("tr"):
        cells = [cell for cell in tr if cell.tag in ("td", "th")]
        if not cells:
            continue

        row: list[tuple[str, str]] = []
        column = 0
        remaining = iter(cells)

        while True:
            if spans.get(column, (0, "", ""))[0] > 0:
                left, value, color = spans[column]
                row.append((value, color))
                spans[column] = (left - 1, value, color)
                column += 1
                continue

            cell = next(remaining, None)
            if cell is None:
                break

            text = " ".join(cell.text_content().split())
            color = _cell_color(cell)
            try:
                span = int(cell.get("rowspan") or 1)
            except (TypeError, ValueError):
                span = 1

            row.append((text, color))
            if span > 1:
                spans[column] = (span - 1, text, color)
            column += 1

        if row:
            rows.append(row)

    return rows


def parse_rows(html: str) -> list[tuple[str, str, str, str]]:
    """从页面里提取 ``(section, 称号名, 条件, 颜色)``。

    页面结构是「h2/h3/h4 分节 + 若干表格」，表格通常是
    ``| 称号名 | 入手条件 |``（条件常用 rowspan 覆盖多行）。
    称号所在的第一个单元格用背景色区分稀有度。
    """
    import lxml.html

    doc = lxml.html.fromstring(html)
    rows: list[tuple[str, str, str, str]] = []
    section = ""

    for element in doc.iter():
        if element.tag in ("h2", "h3", "h4", "h5"):
            section = " ".join(element.text_content().split())
        elif element.tag == "table":
            for cells in _expand_table(element):
                if len(cells) < 2:
                    continue
                (name, color), (condition, _) = cells[0], cells[1]
                if not name or not condition:
                    continue
                # 跳过表头行
                if name in ("称号名", "称号", "名前") or condition in ("入手条件", "条件", "獲得条件"):
                    continue
                rows.append((section, name, condition, color))
    return rows


def discover_pages(html: str, known: set[str]) -> set[str]:
    """从页面导航里发现其它称号页面。

    只接受「纯页面名」形式的链接——Wiki 的编辑/历史链接形如
    ``::cmd/edit?page=...``，早期版本会把它们也当成页面抓走。
    """
    import lxml.html

    doc = lxml.html.fromstring(html)
    found: set[str] = set()
    for anchor in doc.iter("a"):
        href = anchor.get("href") or ""
        if "/chunithmwiki/" not in href or "::cmd/" in href:
            continue
        tail = href.split("/chunithmwiki/", 1)[1]
        if "?" in tail or "&" in tail or "#" in tail or "::" in tail:
            continue
        page = urllib.parse.unquote(tail).strip()
        if not page or page in known:
            continue
        if _LIKE_TITLE_PAGE.search(page) and not _SKIP_PAGE.search(page):
            found.add(page)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取日服 Wiki 称号条件")
    parser.add_argument("--out", type=pathlib.Path, default=CACHE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0, help="页面间隔（秒），别给 wiki 压力")
    args = parser.parse_args()

    if args.out.is_file() and not args.force:
        data = json.loads(args.out.read_text(encoding="utf-8"))
        print(
            f"已有缓存 {args.out.relative_to(ROOT)}"
            f"（{len(data.get('titles', {}))} 个称号 / {len(data.get('pages', []))} 个页面），"
            "跳过。加 --force 重新抓取。"
        )
        return 0

    known: set[str] = set(SEED_PAGES)
    queue = list(SEED_PAGES)
    pages_done: list[dict] = []
    titles: dict[str, dict] = {}

    while queue:
        page = queue.pop(0)
        print(f"抓取 {page} ...")
        html = fetch(page)
        time.sleep(args.delay)
        if html is None:
            continue

        rows = parse_rows(html)
        pages_done.append({"page": page, "rows": len(rows), "url": BASE + urllib.parse.quote(page)})
        print(f"  {len(rows)} 行")

        for section, name, condition, color in rows:
            entry = titles.get(name)
            # 同一称号在多个页面出现时，保留第一条（先抓的页面更专门）；
            # 但颜色若第一条没识别出来，用后面页面的补上
            if entry is None:
                titles[name] = {
                    "condition": condition,
                    "section": section,
                    "page": page,
                    "url": BASE + urllib.parse.quote(page),
                    "color": color,
                }
            elif color and not entry.get("color"):
                entry["color"] = color

        for page_name in discover_pages(html, known):
            known.add(page_name)
            queue.append(page_name)

    payload = {
        "source": "https://wikiwiki.jp/chunithmwiki/",
        "note": "日服 Wiki 记录的全称号与获得条件；用于给国服数据补条件",
        "pages": pages_done,
        "titles": titles,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"页面 {len(pages_done)} 个 · 称号 {len(titles)} 个 → {args.out.relative_to(ROOT)}"
          f"（{args.out.stat().st_size:,} B）")

    # ---- 覆盖率：对照国服数据看能补上多少
    cn_path = ROOT / "data" / "cache" / "trophy_list.json"
    if cn_path.is_file():
        payload_cn = json.loads(cn_path.read_text(encoding="utf-8"))
        cn_titles = payload_cn["trophies"] if isinstance(payload_cn, dict) else payload_cn
        vague = [
            t for t in cn_titles
            if (t.get("description") or "").strip() in ("", "-") or "引き継ぎ" in (t.get("description") or "")
        ]
        hit = [t for t in vague if t["name"] in titles]
        print(f"国服称号 {len(cn_titles)} 个，其中无有效说明 {len(vague)} 个")
        print(f"  其中能在 Wiki 找到条件：{len(hit)}（{len(hit) / max(len(vague), 1):.1%}）")
        missing = [t["name"] for t in vague if t["name"] not in titles]
        print(f"  仍缺条件：{len(missing)}")
        for name in missing[:10]:
            print(f"    - {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
