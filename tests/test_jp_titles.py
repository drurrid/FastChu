"""wiki 称号表格的颜色与 rowspan 解析。"""

from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch_jp_titles.py"
_spec = importlib.util.spec_from_file_location("fetch_jp_titles", SCRIPT)
assert _spec is not None and _spec.loader is not None
fetch_jp_titles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_jp_titles)

parse_rows = fetch_jp_titles.parse_rows
discover_pages = fetch_jp_titles.discover_pages


def _table(rows: str) -> str:
    return (
        "<table><tbody>"
        "<tr><th>称号名</th><th>入手条件</th></tr>"
        f"{rows}"
        "</tbody></table>"
    )


def test_cell_color_reads_style_and_bgcolor() -> None:
    import lxml.html

    def color(markup: str) -> str:
        return fetch_jp_titles._cell_color(lxml.html.fromstring(markup))

    assert color('<td style="background-color:#d3d3d3">x</td>') == "normal"
    assert color('<td style="background-color: #F0F8FF;">x</td>') == "silver"
    assert color('<td style="color:#000; background-color:#ffd700">x</td>') == "gold"
    assert color('<td bgcolor="#7FFFD4">x</td>') == "platina"
    assert color('<td style="background-color:#ff9bb7">x</td>') == "rainbow"
    # 白色 / 未标注是「スペシャル」或说明行，不猜颜色
    assert color('<td style="background-color:#ffffff">x</td>') == ""
    assert color("<td>x</td>") == ""


def test_parse_rows_keeps_cell_color() -> None:
    html = _table(
        '<tr><td style="background-color:#ffd700">金称号</td><td>条件A</td></tr>'
        '<tr><td style="background-color:#f0f8ff">银称号</td><td>条件B</td></tr>'
    )
    assert parse_rows(html) == [
        ("", "金称号", "条件A", "gold"),
        ("", "银称号", "条件B", "silver"),
    ]


def test_rowspan_inherits_text_and_color() -> None:
    html = _table(
        '<tr><td style="background-color:#7fffd4" rowspan="2">铂金称号</td>'
        "<td>共通条件</td></tr>"
        "<tr><td>另一条</td></tr>"
    )
    rows = parse_rows(html)
    # 第二行只剩一个单元格，rowspan 补回称号名和颜色
    assert rows == [
        ("", "铂金称号", "共通条件", "platina"),
        ("", "铂金称号", "另一条", "platina"),
    ]


def test_rarity_change_page_is_not_discovered() -> None:
    """稀有度变更页记录的是变更前颜色，不能当抓取源。"""
    html = (
        '<a href="/chunithmwiki/楽曲プレイ称号一覧">x</a>'
        '<a href="/chunithmwiki/CHUNITHM称号レアリティ変更一覧">x</a>'
    )
    assert discover_pages(html, set()) == {"楽曲プレイ称号一覧"}
