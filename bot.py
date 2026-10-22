"""FastChu 启动入口。

本地开发::

    pip install -e ".[dev]"
    cp .env.example .env
    python bot.py

与 NapCat / LLOneBot 对接（反向 WebSocket，最省事）:

1. 启动本机器人：``python bot.py``（默认监听 ``http://127.0.0.1:8080``）
2. 在 NapCat 的网络配置里新增 **WebSocket 客户端**（反向 WS）：
   地址填 ``ws://127.0.0.1:8080/onebot/v11/ws``
3. 在 QQ 群里发 ``/帮助`` 或 ``/Rebellion`` 验证连通性

第一次查询会自动抓取曲库（公共接口，约 2.5 MB，带物量）并落盘缓存，
之后查询**完全走本地缓存，零网络开销**。

也可以用正向连接（本机器人主动连 NapCat）：把 ``DRIVER`` 保持 ``~fastapi``，
在 ``.env`` 里给 ``ONEBOT_WS_URLS`` 加一条 ``["ws://127.0.0.1:3001"]``。
"""

from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 先 init，才能 import 任何需要 NoneBot 上下文的模块
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 查歌插件；核心逻辑在 fastchu.services / fastchu.render 里，与框架无关
nonebot.load_plugin("fastchu.plugins.song")

if __name__ == "__main__":
    nonebot.run()
