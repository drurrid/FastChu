"""称号条件补充（日服 Wiki 考据）测试。

落雪对一部分称号只给「CHUNITHMから引き継ぎ」这种没信息量的说明，
真正的达成条件在日服 Wiki 里。``build_title_report.py`` 会生成
``data/titles/conditions.json``，运行时装称号时把它叠上去。
"""

from __future__ import annotations

import json
import pathlib
from contextlib import asynccontextmanager

import pytest

from fastchu.models import Trophy
from fastchu.services.commands import run_title
from fastchu.services.song_service import SongService
from fastchu.services.titles import apply_condition_overrides, load_condition_overrides

from .conftest import make_settings

FIXTURE_TROPHY_ID = 1  # 夹具里的「はじめまして」，description 是 CHUNITHMから引き継ぎ


def _write_overrides(path: pathlib.Path, mapping: dict[int, str]) -> pathlib.Path:
    path.write_text(
        json.dumps(
            {
                "meta": {"total": len(mapping)},
                "conditions": {
                    str(trophy_id): {"condition": text, "source": "wiki"}
                    for trophy_id, text in mapping.items()
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@asynccontextmanager
async def make_service(tmp_path: pathlib.Path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    service = SongService(settings)
    try:
        yield service
    finally:
        await service.aclose()


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def test_load_overrides(tmp_path: pathlib.Path) -> None:
    path = _write_overrides(tmp_path / "conditions.json", {3352: "『CHUNITHM PARADISE LOST』/RATING 16.00達成"})
    overrides = load_condition_overrides(path)
    assert overrides == {3352: "『CHUNITHM PARADISE LOST』/RATING 16.00達成"}


def test_load_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    assert load_condition_overrides(tmp_path / "nope.json") == {}


def test_load_corrupt_file_returns_empty(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_condition_overrides(path) == {}


def test_apply_overrides_replaces_description() -> None:
    trophy = Trophy(id=1, name="皇帝", description="CHUNITHMから引き継ぎ")
    enriched = apply_condition_overrides([trophy], {1: "wiki 条件"})
    assert enriched[0].description == "wiki 条件"
    # 原对象不被修改
    assert trophy.description == "CHUNITHMから引き継ぎ"


def test_apply_without_overrides_is_noop() -> None:
    trophies = [Trophy(id=1, name="x", description="d")]
    assert apply_condition_overrides(trophies, {}) is trophies


# --------------------------------------------------------------------------- #
# 端到端：查称号时显示的是补充后的条件
# --------------------------------------------------------------------------- #


async def test_title_query_uses_override(tmp_path: pathlib.Path) -> None:
    overrides_file = _write_overrides(
        tmp_path / "conditions.json",
        {FIXTURE_TROPHY_ID: "『测试』补充出来的条件"},
    )
    async with make_service(tmp_path, condition_overrides_path=overrides_file) as service:
        reply = await run_title(service, service.settings, "はじめまして")

    assert reply.kind == "title"
    assert "『测试』补充出来的条件" in reply.text
    assert "CHUNITHMから引き継ぎ" not in reply.text


async def test_title_query_without_override_keeps_lxns(
    tmp_path: pathlib.Path,
) -> None:
    async with make_service(tmp_path) as service:  # 默认路径不存在 → 不叠加
        reply = await run_title(service, service.settings, "はじめまして")

    assert reply.kind == "title"
    assert "CHUNITHMから引き継ぎ" in reply.text


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_condition_ignored(bad: str | None) -> None:
    trophy = Trophy(id=1, name="x", description="原说明")
    enriched = apply_condition_overrides([trophy], {1: bad or ""})
    assert enriched[0].description in ("原说明", bad)


# --------------------------------------------------------------------------- #
# 真实数据回归：同名撞车
# --------------------------------------------------------------------------- #


def test_inherited_title_not_polluted_by_same_name_wiki_entry() -> None:
    """#39「邪眼」是引继称号，曾被 wiki「配信停止楽曲」页的同名条目覆盖成
    ``BASIC/SPEED SONIC/ALL JUSTICE達成``——两边的落雪数据都没有 required.songs，
    无法区分，所以这类页面整页排除。
    """
    import json

    target = pathlib.Path(__file__).resolve().parents[1] / "data" / "titles" / "conditions.json"
    if not target.is_file():
        pytest.skip("缺少 conditions.json")

    payload = json.loads(target.read_text(encoding="utf-8"))
    conditions = payload.get("conditions", payload)
    assert "39" not in conditions, "引继称号不该被同名 wiki 条目覆盖"
    # 对照：皇帝这种真·评级称号仍然保留 wiki 条件
    assert "3352" in conditions
