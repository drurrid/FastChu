"""抓日服「譜面制作者一覧」→ 每首歌各难度的谱师。

数据源：日服 CHUNITHM wiki 的 ``譜面制作者一覧1`` ~ ``譜面制作者一覧6``。

页面按谱师分节（``<h2>``），每节一张「曲名 / 難 / ノーツデザイナー名」
表格。表格里**没写谱师的行默认属于该节的谱师**，写了谱师的行则是联名 /
特别名义；表格还会用 rowspan / colspan 合并「难」和「谱师」单元格，所以
解析时必须先还原成网格，不能按单元格个数硬取前三列。

用法::

    python scripts/fetch_jp_designers.py            # 用缓存
    python scripts/fetch_jp_designers.py --force    # 重新抓
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "cache" / "jp_designers.json"

BASE = "https://wikiwiki.jp/chunithmwiki/"
USER_AGENT = "Mozilla/5.0 (compatible; FastChu/0.1; +https://maimai.lxns.net)"
DELAY = 1.5

PAGES = [f"譜面制作者一覧{i}" for i in range(1, 7)]

#: 表格里的难度字母 → 我们的难度名
LETTER_TO_LEVEL = {
    "B": "BAS",
    "A": "ADV",
    "E": "EXP",
    "M": "MAS",
    "U": "ULT",
}

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh]([^>]*)>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ROWSPAN_RE = re.compile(r'rowspan="?(\d+)"?', re.I)
_COLSPAN_RE = re.compile(r'colspan="?(\d+)"?', re.I)
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
#: 标题里的脚注 / 编辑图标链（不参与“该节谱师”的名字）
_DECORATION_RE = re.compile(
    r'<a[^>]*class="[^"]*(?:note_super|anchor_super)[^"]*"[^>]*>.*?</a>',
    re.S,
)

#: 表格列数：曲名 / 難 / ノーツデザイナー名（后两列可能被 colspan 合并）
_COLUMNS = 4


def _name_key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).casefold()


def _cell(attrs: str, body: str) -> tuple[str, int, int]:
    text = html_lib.unescape(_TAG_RE.sub("", body)).replace("\u3000", " ").strip()
    row_match = _ROWSPAN_RE.search(attrs or "")
    col_match = _COLSPAN_RE.search(attrs or "")
    rowspan = int(row_match.group(1)) if row_match else 1
    colspan = int(col_match.group(1)) if col_match else 1
    return text, rowspan, colspan


def _heading_designer(raw_heading: str) -> str:
    cleaned = _DECORATION_RE.sub("", raw_heading)
    cleaned = html_lib.unescape(_TAG_RE.sub("", cleaned))
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_tables(section_html: str, fallback_designer: str) -> dict[str, dict[str, str]]:
    """解析一节里的所有谱师表；空白谱师回落到该节的谱师名。"""
    result: dict[str, dict[str, str]] = {}
    for table in re.findall(r"<table[^>]*>.*?</table>", section_html, re.S):
        rows = _ROW_RE.findall(table)
        if not rows:
            continue
        header = [_cell(attrs, body)[0] for attrs, body in _CELL_RE.findall(rows[0])]
        if "曲名" not in header or "難" not in header:
            continue

        #: 被上方 rowspan 占住的格子：(行, 列)
        occupied: set[tuple[int, int]] = set()
        grid: dict[tuple[int, int], str] = {}
        for row_index, raw_row in enumerate(rows[1:], start=1):
            cells = [_cell(attrs, body) for attrs, body in _CELL_RE.findall(raw_row)]
            if not cells:
                continue
            column = 0
            for text, rowspan, colspan in cells:
                while (row_index, column) in occupied:
                    column += 1
                for offset in range(colspan):
                    grid[(row_index, column + offset)] = text
                    for below in range(1, rowspan):
                        occupied.add((row_index + below, column + offset))
                column += colspan

            values = [grid.get((row_index, column), "").strip() for column in range(_COLUMNS)]
            song, letter = values[0], values[1]
            level = LETTER_TO_LEVEL.get(letter.upper())
            if not song or not level:
                continue
            # 难度单元格可能 colspan=2，谱师在最后一列；两列都看一遍
            tail = [
                value
                for value in values[2:]
                if value and value.upper() != letter.upper()
            ]
            designer = tail[0] if tail else fallback_designer
            if not designer:
                continue
            result.setdefault(song, {}).setdefault(level, designer)
    return result


def parse_page(html: str) -> dict[str, dict[str, str]]:
    """解析一页：``{曲名: {难度: 谱师}}``。

    表格必须按所属 ``<h2>`` 分节解析：同一张表里没写谱师的行，谱师就是
    该节的标题。``ROWSPAN`` 负责补回被合并掉的曲名/难度格子。
    """
    result: dict[str, dict[str, str]] = {}
    headings = list(_H2_RE.finditer(html))
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(html)
        designer = _heading_designer(match.group(1))
        for song, levels in _parse_tables(html[start:end], designer).items():
            target = result.setdefault(song, {})
            for level, name in levels.items():
                target.setdefault(level, name)
    return result


def fetch(page: str, *, attempts: int = 4) -> str:
    url = BASE + urllib.parse.quote(page)
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            wait = 30 * (attempt + 1)
            print(f"  [!] {page}: 429，{wait} 秒后重试", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description="抓日服谱面制作者")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新抓取")
    args = parser.parse_args()

    if CACHE_PATH.is_file() and not args.force:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        total = sum(len(v) for v in payload.values())
        print(f"已有缓存：{len(payload)} 首 / {total} 个谱面 -> {CACHE_PATH.relative_to(ROOT)}")
        print("要重新抓加 --force")
        return 0

    merged: dict[str, dict[str, str]] = {}
    for page in PAGES:
        try:
            html = fetch(page)
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] {page}: {exc}", file=sys.stderr)
            continue
        parsed = parse_page(html)
        added = 0
        for song, levels in parsed.items():
            target = merged.setdefault(song, {})
            for level, designer in levels.items():
                if level not in target:
                    target[level] = designer
                    added += 1
        print(f"  {page}: {len(parsed)} 首 / 新增 {added} 个谱面")
        time.sleep(DELAY)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in merged.values())
    print(f"完成：{len(merged)} 首 / {total} 个谱面 -> {CACHE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
