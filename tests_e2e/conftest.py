"""端到端（在真实 NoneBot 运行时里）测试的配置。

这里验证的是**纯函数测试覆盖不到**的部分：消息文本能不能命中命令规则、
handler 有没有被正确调用、最终发出去的内容对不对、错误路径会不会把提示发出来。

为什么单独放一个目录：nonebug 的初始化 fixture 是 **session 级 autouse**——
它会 ``nonebot.init()``、把 matcher provider 换成 ``NoneBugProvider``、
并按 ASGI lifespan 启停 driver。这些副作用对主测试套件（纯逻辑测试）是纯负担。

主套件 ``pytest`` 会带上 ``tests_e2e``（见 ``pyproject.toml`` 的 ``testpaths``），
两者可以一起跑。
"""

from __future__ import annotations

import os
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "data"
SONG_FIXTURE = FIXTURE_DIR / "song_list.json"
ALIAS_FIXTURE = FIXTURE_DIR / "alias_list.json"
TROPHY_FIXTURE = FIXTURE_DIR / "trophy_list.json"

# 只有 DRIVER 必须在导入期设定：nonebug 的 session 级 autouse fixture 会
# ``nonebot.init()``，而它早于任何 function 级 fixture。
os.environ.setdefault("DRIVER", "~fastapi")

#: 其余配置在每个用例的 fixture 里通过 monkeypatch 设置，退出时自动还原，
#: 避免污染主测试套件（两个套件可能在同一会话里一起跑）。
E2E_ENV: dict[str, str] = {
    # 曲库与称号直接用随包夹具，TTL 设得足够长 → 全程读盘，零网络
    "FASTCHU_SONG_CACHE_PATH": str(SONG_FIXTURE),
    "FASTCHU_ALIAS_CACHE_PATH": str(ALIAS_FIXTURE),
    "FASTCHU_TROPHY_CACHE_PATH": str(TROPHY_FIXTURE),
    # 区域对照不参与端到端测试：否则详情会掺进「国/日」定数标注
    "FASTCHU_REGION_CACHE_PATH": str(FIXTURE_DIR / "_no_regions.json"),
    "FASTCHU_SONG_CACHE_TTL": "315360000",
    # 曲绘关掉：命令测试只关心文本，也避免任何外网请求
    "FASTCHU_JACKET_MODE": "off",
    # 关掉按用户冷却，否则同一个测试里发第二次消息会被拦
    "FASTCHU_QUERY_COOLDOWN": "0",
    # 端到端测试不碰落雪 OAuth / 个人数据：不读真实绑定，也不发网络请求
    "FASTCHU_OAUTH_ENABLED": "false",
    # 给足空间，一次回复一条消息（分页另有专门用例）
    "FASTCHU_MAX_MESSAGE_LENGTH": "6000",
    "FASTCHU_SEARCH_RESULT_LIMIT": "5",
}
