"""共用的插件装载助手。

主测试套件（``tests/test_plugin.py``）与端到端套件（``tests_e2e``）都可能加载同一批插件。
NoneBot 的 ``load_plugin`` 对同一个插件第二次调用会抛
``RuntimeError: Plugin already exists``，所以这里按 ``get_loaded_plugins()`` 做幂等，
使得「先跑哪个套件」都不影响结果。
"""

from __future__ import annotations

import nonebot
from nonebot.plugin import Plugin

__all__ = ["ensure_plugin_loaded"]


def ensure_plugin_loaded(module_path: str) -> Plugin:
    """加载插件；已经加载过就返回已有的 ``Plugin`` 对象。"""
    for plugin in nonebot.get_loaded_plugins():
        if plugin.module_name == module_path:
            return plugin
    return nonebot.load_plugin(module_path)
