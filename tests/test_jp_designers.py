"""wiki「譜面制作者一覧」解析。"""

from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch_jp_designers.py"
_spec = importlib.util.spec_from_file_location("fetch_jp_designers", SCRIPT)
assert _spec is not None and _spec.loader is not None
fetch_jp_designers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_jp_designers)

parse_page = fetch_jp_designers.parse_page


def _page(heading: str, rows: str) -> str:
    return f"""
    <h2>{heading} <a class="anchor_super" name="x"></a></h2>
    <table><thead><tr>
      <th>曲名</th><th>難</th><th colspan="2">ノーツデザイナー名</th>
    </tr></thead><tbody>{rows}</tbody></table>
    """


def test_section_designer_fills_blank_rows() -> None:
    """行内没写谱师时，归属该节标题的谱师。"""
    html = _page(
        "ロシェ＠ペンギン",
        "<tr><td>C</td><td colspan=\"2\">M</td></tr>",
    )
    assert parse_page(html)["C"]["MAS"] == "ロシェ＠ペンギン"


def test_rowspan_and_colspan_are_restored() -> None:
    """谱师单元格 rowspan=2 时，下一行也要拿到同一谱师。"""
    html = _page(
        "じゃこレモン",
        "<tr><td>A</td><td>E</td>"
        '<td rowspan="2" colspan="2">じゃこレモン</td></tr>'
        "<tr><td>B</td><td>E</td></tr>",
    )
    parsed = parse_page(html)
    assert parsed["A"]["EXP"] == "じゃこレモン"
    assert parsed["B"]["EXP"] == "じゃこレモン"


def test_explicit_designer_wins_over_section() -> None:
    html = _page(
        "ロシェ＠ペンギン",
        '<tr><td>D</td><td colspan="2">M</td><td>むにむにペンギン</td></tr>',
    )
    assert parse_page(html)["D"]["MAS"] == "むにむにペンギン"


def test_multiple_sections_keep_their_own_designer() -> None:
    html = _page(
        "ロシェ＠ペンギン",
        '<tr><td>E</td><td colspan="2">M</td></tr>',
    ) + _page(
        "Jack",
        '<tr><td>F</td><td colspan="2">M</td></tr>',
    )
    parsed = parse_page(html)
    assert parsed["E"]["MAS"] == "ロシェ＠ペンギン"
    assert parsed["F"]["MAS"] == "Jack"


def test_heading_decorations_are_ignored() -> None:
    html = _page(
        'ロシェ＠ペンギン <a class="note_super">*28</a>',
        '<tr><td>G</td><td colspan="2">M</td></tr>',
    )
    assert parse_page(html)["G"]["MAS"] == "ロシェ＠ペンギン"
