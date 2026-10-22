"""插件装配检查：handler 里用到的核心函数必须真的被导入。

AstrBot 插件是**手写导入**的（不能从 fastchu 里 ``import *``），
漏一行就只会在用户点命令时抛 ``NameError``——这类问题在单元测试里
看不出来，所以要静态扫一遍。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "astrbot_plugin_fastchu" / "main.py"


def _imported_names(source: str) -> set[str]:
    block = re.search(
        r"from fastchu\.services\.commands import[^(]*\((.*?)\)", source, re.S
    )
    if block is None:
        return set()
    names: set[str] = set()
    for line in block.group(1).splitlines():
        name = line.split("#", 1)[0].strip().rstrip(",")
        if name:
            names.add(name)
    return names


def test_all_core_calls_are_imported() -> None:
    source = PLUGIN.read_text(encoding="utf-8")
    imported = _imported_names(source)
    assert imported, "没找到 commands 的导入块"

    called = set(re.findall(r"\b(run_[a-z_]+)\(", source))
    missing = called - imported
    assert not missing, f"handler 里调用了未导入的函数: {sorted(missing)}"


def test_condition_and_random_are_wired() -> None:
    source = PLUGIN.read_text(encoding="utf-8")
    imported = _imported_names(source)
    assert "run_title_condition" in imported
    assert "run_random_title" in imported
    assert '@filter.command("称号条件"' in source
    assert '@filter.command("随机称号"' in source


def test_hidden_rainbow_titles_are_wired() -> None:
    source = PLUGIN.read_text(encoding="utf-8")
    imported = _imported_names(source)
    assert "run_rainbow_titles" in imported
    assert '@filter.command("虹称号")' in source


def test_oauth_commands_are_wired() -> None:
    source = PLUGIN.read_text(encoding="utf-8")
    imported = _imported_names(source)
    for name in (
        "run_auth_start",
        "run_auth_code",
        "run_auth_status",
        "run_auth_revoke",
    ):
        assert name in imported
    for command in ("授权", "授权状态", "取消授权"):
        assert f'@filter.command("{command}"' in source
