"""NoneBot 插件装载与消息组装测试。

装载测试抓的是「导入期就报错」这类问题——NoneBot 插件在导入时注册命令，
写错的话要等机器人启动才炸，很难在开发中发现。

消息组装测试锁住**轻量级**的落地方式：正文与曲绘必须合并成**一条**消息。
"""

from __future__ import annotations

import sys

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from fastchu.services.commands import Segment
from fastchu.services.jacket import JacketImage

from .plugin_bootstrap import ensure_plugin_loaded

_PLUGIN_NAME = "fastchu.plugins.song"
_initialized = False


@pytest.fixture(scope="module")
def plugin():
    global _initialized
    if not _initialized:
        nonebot.init(driver="~fastapi", log_level="ERROR", superusers={"10001"})
        _initialized = True
    loaded = ensure_plugin_loaded(_PLUGIN_NAME)
    assert loaded is not None, "插件装载失败"
    return loaded


def test_plugin_metadata(plugin) -> None:
    assert plugin.metadata is not None
    assert plugin.metadata.name == "FastChu 查歌"
    assert plugin.metadata.supported_adapters == {"~onebot.v11"}
    assert "/称号" in plugin.metadata.usage
    assert "/Rebellion" in plugin.metadata.usage or "/<歌曲名" in plugin.metadata.usage
    assert plugin.metadata.homepage


def test_commands_are_registered(plugin) -> None:
    """查歌、查称号、帮助与落雪账号绑定。"""
    module = sys.modules[_PLUGIN_NAME]
    for name in (
        "song_cmd",
        "title_cmd",
        "help_cmd",
        "auth_cmd",
        "auth_status_cmd",
        "auth_revoke_cmd",
    ):
        assert hasattr(module, name), f"缺少命令 {name}"
        assert module.__dict__[name] is not None
    for removed in ("notes_cmd", "filter_cmd"):
        assert not hasattr(module, removed), f"{removed} 应当已移除"


def test_plugin_only_imports_nonebot_in_plugins_package() -> None:
    """核心层不许依赖 NoneBot——否则测试页就没法与插件共用逻辑。"""
    import pathlib

    import fastchu

    package_root = pathlib.Path(fastchu.__file__).resolve().parent
    offenders = []
    for path in package_root.rglob("*.py"):
        if "plugins" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "import nonebot" in text or "from nonebot" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert not offenders, f"核心层不该 import NoneBot: {offenders}"


def test_help_text_mentions_all_commands(plugin) -> None:
    module = sys.modules[_PLUGIN_NAME]
    text = module.HELP_TEXT
    assert "/歌曲名" in text
    for command in ("/称号", "/帮助"):
        assert command in text
    for removed in ("/筛选", "/物量"):
        assert removed not in text, f"{removed} 已从机器人移除"


# --------------------------------------------------------------------------- #
# 消息组装：轻量级的关键
#
# 段结构由 commands.Reply.messages() 决定（见 test_commands.py），
# 这里只验证「段 → Message」的转换没走样。
# --------------------------------------------------------------------------- #


def test_to_message_text_only(plugin) -> None:
    module = sys.modules[_PLUGIN_NAME]
    message = module.to_message([Segment(type="text", text="正文")])
    assert isinstance(message, Message)
    assert len(message) == 1
    assert message[0].type == "text"
    assert message[0].data["text"] == "正文"


def test_to_message_with_url_image(plugin) -> None:
    """url 模式：正文 + 图片在**同一条**消息里，机器人侧不下载。"""
    module = sys.modules[_PLUGIN_NAME]
    segments = [
        Segment(type="text", text="正文"),
        Segment(type="image", image_url="https://assets2.lxns.net/chunithm/jacket/464.png"),
    ]

    message = module.to_message(segments)

    assert len(message) == 2
    assert message[0].type == "text"
    assert message[1].type == "image"
    assert message[1].data["file"] == "https://assets2.lxns.net/chunithm/jacket/464.png"


def test_to_message_prefers_inline_data(plugin) -> None:
    """有内联数据时优先用 data URI（OneBot 实现访问不了外网的场景）。"""
    module = sys.modules[_PLUGIN_NAME]
    segments = [
        Segment(type="text", text="正文"),
        Segment(
            type="image",
            image_url="http://x/1.png",
            image_data_uri="data:image/png;base64,cG5n",
        ),
    ]

    message = module.to_message(segments)

    assert message[1].data["file"].startswith("data:image/png;base64,")


def test_to_message_empty_text_segment(plugin) -> None:
    module = sys.modules[_PLUGIN_NAME]
    message = module.to_message([Segment(type="text")])
    assert message[0].data["text"] == ""


def test_reply_messages_matches_plugin_expectation(plugin) -> None:
    """插件发出的段与命令层声明的完全一致——页面与群聊因此不会分叉。"""
    from fastchu.services.commands import Reply
    from fastchu.services.jacket import JacketImage

    module = sys.modules[_PLUGIN_NAME]
    reply = Reply(
        kind="detail",
        texts=["正文"],
        jacket=JacketImage(song_id=1, url="http://x/1.png"),
    )

    messages = reply.messages()
    assert len(messages) == 1
    message = module.to_message(messages[0])
    assert [segment.type for segment in message] == ["text", "image"]


# --------------------------------------------------------------------------- #
# 冷却器：配置变化必须生效
# --------------------------------------------------------------------------- #


def test_cooldown_is_lazy(plugin) -> None:
    """冷却器不该在导入期就读配置（NoneBot 启动顺序上不安全）。"""
    module = sys.modules[_PLUGIN_NAME]
    assert module._cooldown is None or hasattr(module._cooldown, "check")
    assert hasattr(module._get_cooldown(), "check")


def test_cooldown_rebuilds_when_config_changes(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归测试：``reset_settings_cache()`` 之后冷却配置必须真的生效。

    原先实现把 Cooldown 永久缓存，导致运行时改 ``FASTCHU_QUERY_COOLDOWN``
    （热重载 / 测试）完全不起作用，也让测试出现「先跑哪个套件」的顺序依赖。
    """
    from fastchu.config import reset_settings_cache

    module = sys.modules[_PLUGIN_NAME]

    monkeypatch.setenv("FASTCHU_QUERY_COOLDOWN", "11")
    reset_settings_cache()
    assert module._get_cooldown().seconds == pytest.approx(11.0)

    monkeypatch.setenv("FASTCHU_QUERY_COOLDOWN", "0")
    reset_settings_cache()
    assert module._get_cooldown().seconds == pytest.approx(0.0)

    # 同一配置下不应反复重建（保留已累积的冷却状态）
    first = module._get_cooldown()
    assert module._get_cooldown() is first

    reset_settings_cache()


def test_superuser_bypasses_cooldown(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    """管理员不受冷却限制。

    直接改 driver 配置而不是依赖 ``nonebot.init(superusers=...)``：
    nonebug 的 session 级 autouse fixture 已经先初始化过 NoneBot，
    再 init 一次不会覆盖 superusers。
    """
    from types import SimpleNamespace

    from nonebot import get_driver

    module = sys.modules[_PLUGIN_NAME]
    monkeypatch.setattr(get_driver().config, "superusers", {"10001"}, raising=False)

    assert module._is_superuser(SimpleNamespace(user_id="10001")) is True
    assert module._is_superuser(SimpleNamespace(user_id="99999")) is False


def test_to_message_type(plugin) -> None:
    module = sys.modules[_PLUGIN_NAME]
    assert isinstance(module.to_message([Segment(type="text", text="x")]), Message)
    assert isinstance(MessageSegment.text("x"), MessageSegment)
