"""随机称号池、人工颜色覆盖、群聊 @ 限制。"""

from __future__ import annotations

import json
import pathlib

from fastchu.models import Trophy
from fastchu.services.titles import TrophyCatalog, load_jp_only_titles

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _trophy(tid: int, color: str) -> Trophy:
    return Trophy(
        id=tid,
        name=f"t{tid}",
        color=color,
        required=[
            {
                "difficulties": [3],
                "rank": "sss",
                "songs": [{"id": tid, "title": f"t{tid}"}],
            }
        ],
    )


def test_random_pool_excludes_unknown() -> None:
    """随机池只抽有颜色的称号，「未标注」不出现在结果里。"""
    catalog = TrophyCatalog(
        [
            _trophy(1, "silver"),
            _trophy(2, "rainbow"),
            _trophy(3, "unknown"),
            _trophy(4, "unknown"),
        ]
    )
    seen = set()
    for _ in range(60):
        trophy = catalog.random_special()
        assert trophy is not None
        assert trophy.color != "unknown", "未标注的称号不该进随机池"
        seen.add(trophy.id)
    assert seen


def test_random_pool_all_unknown_returns_none() -> None:
    catalog = TrophyCatalog([_trophy(1, "unknown")])
    assert catalog.random_special() is None


def test_jp_only_color_field_is_respected(tmp_path: pathlib.Path) -> None:
    """jp_only.json 里带 color 的条目应当用该颜色，而不是「未标注」。"""
    payload = {
        "meta": {"total": 2},
        "trophies": [
            {"id": 900001, "name": "未来はあなたの手の中に", "color": "rainbow", "condition": "x"},
            {"id": 900002, "name": "普通の日服称号", "color": "", "condition": "y"},
        ],
    }
    path = tmp_path / "jp_only.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    trophies = load_jp_only_titles(path)
    colors = {t.name: t.color for t in trophies}
    assert colors["未来はあなたの手の中に"] == "rainbow"
    assert colors["普通の日服称号"] == "unknown"


def test_real_data_melodiniq_title_is_rainbow() -> None:
    """真实数据回归：Melodiniq（唯一 ULT 16）的称号人工核对为虹色。"""
    path = ROOT / "data" / "titles" / "jp_only.json"
    if not path.is_file():
        import pytest

        pytest.skip("缺少 jp_only.json")

    trophies = load_jp_only_titles(path)
    target = next((t for t in trophies if t.name == "未来はあなたの手の中に"), None)
    assert target is not None, "Melodiniq 的称号应当在日服限定列表里"
    assert target.color == "rainbow"


# ---------------------------------------------------------------- 群聊 @ 限制


def test_group_bare_query_requires_at() -> None:
    """插件源码里，群聊裸查询必须经过 @ 判断（静态检查，插件依赖 astrbot 无法直接 import）。"""
    source = (ROOT / "astrbot_plugin_fastchu" / "main.py").read_text(encoding="utf-8")
    assert "def _is_at_me(" in source
    assert "if not event.is_private_chat():" in source
    # 群聊分支里必须调用 _is_at_me
    group_branch = source.split("if not event.is_private_chat():", 1)[1][:200]
    assert "_is_at_me(event)" in group_branch
