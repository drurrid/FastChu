"""FastChu —— 中二节奏（CHUNITHM）查歌 QQ 机器人。

只做**查歌**：曲名/别名搜索、曲目详情、物量、按分类/版本/BPM 筛选。
全部数据来自落雪查分器的**公共接口**，因此不需要任何密钥，也不获取玩家数据。

核心层（``models`` / ``api`` / ``services`` / ``render``）**不依赖 NoneBot**，
可独立测试与复用；框架胶水全部放在 ``fastchu.plugins`` 下。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
