"""在真实 NoneBot 运行时里驱动命令的端到端测试。

覆盖：命令命中、查歌（含相关称号）、查称号、错误路径、帮助。
这里**不碰网络**：曲库与称号都走随包夹具，曲绘关闭。

按需求「只保留直接用歌曲查询」，``/筛选`` 与 ``/物量`` 已从机器人移除，
对应的用例改为断言「命令不再注册」，避免它们被悄悄加回来。

注意期望值的类型：插件的 ``_reply`` 统一发 ``Message`` 对象
（单条时把曲绘拼进同一条，分页时逐条发），所以期望值一律用 :func:`msg` 包一层。
"""

from __future__ import annotations

import pytest
from nonebug import App
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender

from fastchu.config import get_settings, reset_settings_cache
from fastchu.render import (
    render_song_candidates,
    render_song_detail,
    render_title_detail,
    render_title_stats,
)
from fastchu.services.commands import (
    EMPTY_KEYWORD_HINT,
    MISS_HINT,
    MISS_TITLE_HINT,
    run_help,
    run_title,
    run_lookup,
)
from fastchu.services.song_service import get_service, reset_service
from tests.plugin_bootstrap import ensure_plugin_loaded
from tests_e2e.conftest import E2E_ENV, TROPHY_FIXTURE

PLUGIN = "fastchu.plugins.song"

GROUP_ID = 20002
USER_ID = 10001


def msg(text: str) -> Message:
    """把文本包成 ``Message``，与插件实际发出的对象类型一致。"""
    return Message(MessageSegment.text(text))


@pytest.fixture(scope="module", autouse=True)
def _load_plugin():
    ensure_plugin_loaded(PLUGIN)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch):
    """每个用例都：设好配置 → 重新读配置 → 丢弃服务单例；退出时自动还原。"""
    for key, value in E2E_ENV.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    reset_service()
    yield
    reset_service()
    reset_settings_cache()


def make_event(text: str, *, user_id: int = USER_ID, group_id: int = GROUP_ID) -> GroupMessageEvent:
    message = Message(text)
    return GroupMessageEvent(
        time=1_700_000_000,
        self_id=99999,
        post_type="message",
        message_type="group",
        sub_type="normal",
        user_id=user_id,
        message_id=1,
        message=message,
        original_message=message,
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id),
        group_id=group_id,
    )


async def expected_detail(keyword: str) -> str:
    """用真实管线算出「应该发出去的详情文本」（含相关称号）。"""
    service = get_service()
    catalog = await service.catalog()
    title_catalog = await service.titles()
    result = await service.find(keyword)
    song = result.require_song()
    return render_song_detail(
        song,
        catalog,
        settings=get_settings(),
        related=title_catalog.for_song(song.id),
    )


needs_trophies = pytest.mark.skipif(
    not TROPHY_FIXTURE.is_file(), reason="缺少称号夹具"
)


# --------------------------------------------------------------------------- #
# 命令命中
# --------------------------------------------------------------------------- #


async def test_song_command_matches(app: App) -> None:
    """``/查歌`` 必须能命中命令规则——这条只有跑真实 Matcher 才验证得到。"""
    import fastchu.plugins.song as plugin

    expected = await expected_detail("World Vanquisher")

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 World Vanquisher")
        ctx.receive_event(bot, event)
        ctx.should_pass_rule(plugin.song_cmd)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


async def test_plain_text_does_not_trigger(app: App) -> None:
    """普通聊天内容不该误触发命令。"""
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        ctx.receive_event(bot, make_event("今天打什么歌好"))
        ctx.should_not_pass_rule(plugin.song_cmd)


async def test_command_without_prefix_does_not_trigger(app: App) -> None:
    """没有前缀的 ``查歌`` 不该触发（默认 COMMAND_START 是 ``/``）。"""
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        ctx.receive_event(bot, make_event("查歌 world"))
        ctx.should_not_pass_rule(plugin.song_cmd)


async def test_alias_command_triggers(app: App) -> None:
    """别名 ``song`` 也应命中同一条命令。"""
    import fastchu.plugins.song as plugin

    expected = await expected_detail("Rebellion")

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/song Rebellion")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


def test_removed_commands_are_gone() -> None:
    """``/筛选``、``/物量`` 已按需求移除；该用例防止它们被悄悄加回来。"""
    import fastchu.plugins.song as plugin

    assert not hasattr(plugin, "filter_cmd")
    assert not hasattr(plugin, "notes_cmd")


# --------------------------------------------------------------------------- #
# 查歌
# --------------------------------------------------------------------------- #


async def test_exact_song_gives_detail(app: App) -> None:
    import fastchu.plugins.song as plugin

    expected = await expected_detail("World Vanquisher")
    assert "World Vanquisher" in expected.splitlines()[0]

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 World Vanquisher")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


async def test_alias_search_gives_detail(app: App) -> None:
    """别名精确命中应当直出详情。"""
    import fastchu.plugins.song as plugin

    expected = await expected_detail("wv")
    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 wv")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


async def test_song_id_lookup(app: App) -> None:
    import fastchu.plugins.song as plugin

    service = get_service()
    catalog = await service.catalog()
    title_catalog = await service.titles()
    song = catalog.require(464)
    expected = render_song_detail(
        song,
        catalog,
        settings=get_settings(),
        related=title_catalog.for_song(song.id),
    )

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 #464")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


async def test_ambiguous_query_gives_candidates(app: App) -> None:
    import fastchu.plugins.song as plugin

    service = get_service()
    result = await service.find("world")
    assert not result.is_unique, "world 应当命中多首"
    expected = (await run_lookup(service, get_settings(), "world")).texts[0]

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 world")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.song_cmd)


async def test_missing_song_gives_hint(app: App) -> None:
    """回归测试：搜不到时必须回「没找到」的提示，而不是通用报错。

    这条曾经真的挂过——``matcher.finish()`` 被写在 ``try/except Exception`` 里，
    而 NoneBot 的 ``FinishedException`` 也是 ``Exception`` 子类。
    """
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌 完全不存在zzz")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(MISS_HINT), None)
        ctx.should_finished(plugin.song_cmd)


async def test_empty_argument_gives_hint(app: App) -> None:
    """``/查歌`` 不带参数 → 一句提示。"""
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.song_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(EMPTY_KEYWORD_HINT), None)
        ctx.should_finished(plugin.song_cmd)


# --------------------------------------------------------------------------- #
# 查称号
# --------------------------------------------------------------------------- #


@needs_trophies
async def test_title_command_matches(app: App) -> None:
    import fastchu.plugins.song as plugin

    service = get_service()
    title_catalog = await service.titles()
    target = next(t for t in title_catalog.special if len(t.name) > 8)
    expected = render_title_detail(target, title_catalog)

    async with app.test_matcher(plugin.title_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event(f"/称号 {target.name}")
        ctx.receive_event(bot, event)
        ctx.should_pass_rule(plugin.title_cmd)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.title_cmd)


@needs_trophies
async def test_title_without_argument_gives_hint(app: App) -> None:
    """``/称号`` 不带参数 → 用法提示（分类菜单已移除）。"""
    import fastchu.plugins.song as plugin

    expected = (await run_title(get_service(), get_settings(), "")).texts[0]

    async with app.test_matcher(plugin.title_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/称号")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.title_cmd)


@needs_trophies
async def test_title_not_found_gives_hint(app: App) -> None:
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.title_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/称号 绝对不存在zzz")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(MISS_TITLE_HINT), None)
        ctx.should_finished(plugin.title_cmd)


@needs_trophies
async def test_title_command_alias(app: App) -> None:
    """``/title`` 是 ``/称号`` 的别名。"""
    import fastchu.plugins.song as plugin

    expected = (await run_title(get_service(), get_settings(), "")).texts[0]

    async with app.test_matcher(plugin.title_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/title")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.title_cmd)


# --------------------------------------------------------------------------- #
# 帮助
# --------------------------------------------------------------------------- #


async def test_help_command(app: App) -> None:
    import fastchu.plugins.song as plugin

    service = get_service()
    reply = await run_help(service, get_settings())
    expected = plugin.to_message(reply.messages()[0])

    async with app.test_matcher(plugin.help_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/查歌帮助")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, None)
        ctx.should_finished(plugin.help_cmd)


# --------------------------------------------------------------------------- #
# 「/歌曲名」直查
#
# 用户要的是「直接输入 /xxx（xxx 是歌曲/别名）」，而不是先打命令词。
# 这条路径是 on_message + 自定义 Rule，最容易在重构里被漏掉，所以单独覆盖。
# --------------------------------------------------------------------------- #


async def test_bare_slash_song_lookup(app: App) -> None:
    """``/Rebellion`` 直接出详情。"""
    import fastchu.plugins.song as plugin

    expected = await expected_detail("Rebellion")

    async with app.test_matcher(plugin.bare_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/Rebellion")
        ctx.receive_event(bot, event)
        ctx.should_pass_rule(plugin.bare_cmd)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.bare_cmd)


async def test_bare_slash_alias(app: App) -> None:
    """``/wv`` 用别名直查。"""
    import fastchu.plugins.song as plugin

    expected = await expected_detail("wv")

    async with app.test_matcher(plugin.bare_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/wv")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected), None)
        ctx.should_finished(plugin.bare_cmd)


async def test_bare_slash_id(app: App) -> None:
    """``/#464`` 与 ``/464`` 都按 ID 直查。"""
    import fastchu.plugins.song as plugin

    service = get_service()
    catalog = await service.catalog()
    title_catalog = await service.titles()
    song = catalog.require(464)
    expected = render_song_detail(
        song,
        catalog,
        settings=get_settings(),
        related=title_catalog.for_song(song.id),
    )

    for text in ("/#464", "/464"):
        async with app.test_matcher(plugin.bare_cmd) as ctx:
            bot = ctx.create_bot()
            event = make_event(text)
            ctx.receive_event(bot, event)
            ctx.should_call_send(event, msg(expected), None)
            ctx.should_finished(plugin.bare_cmd)


async def test_bare_slash_unknown_song_gives_hint(app: App) -> None:
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.bare_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/绝对不存在的曲子zzz")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(MISS_HINT), None)
        ctx.should_finished(plugin.bare_cmd)


async def test_bare_slash_ignores_plain_text(app: App) -> None:
    """没有 ``/`` 前缀的普通聊天不该被当成歌名。"""
    import fastchu.plugins.song as plugin

    async with app.test_matcher(plugin.bare_cmd) as ctx:
        bot = ctx.create_bot()
        ctx.receive_event(bot, make_event("Rebellion"))
        ctx.should_not_pass_rule(plugin.bare_cmd)


def test_bare_rule_rejects_reserved_words() -> None:
    """保留命令词不能被当成歌名——否则 ``/称号`` 会被拿去查歌。"""
    import fastchu.plugins.song as plugin

    for word in ("称号", "查歌", "帮助", "title", "help", "song"):
        event = make_event(f"/{word} 随便什么")
        assert plugin._looks_like_bare_song(event) is False, word

    for word in ("Rebellion", "wv", "#464", "464", "世征"):
        event = make_event(f"/{word}")
        assert plugin._looks_like_bare_song(event) is True, word


def test_strip_command_start() -> None:
    import fastchu.plugins.song as plugin

    assert plugin.strip_command_start("/Rebellion") == "Rebellion"
    assert plugin.strip_command_start("//Rebellion") == "Rebellion"
    assert plugin.strip_command_start("  /  wv  ") == "wv"
    assert plugin.strip_command_start("Rebellion") == "Rebellion"
    assert plugin.strip_command_start("/") == ""


async def test_help_alias(app: App) -> None:
    """``/帮助`` 应命中帮助命令，而不是被当成歌名。"""
    import fastchu.plugins.song as plugin

    service = get_service()
    reply = await run_help(service, get_settings())
    expected = plugin.to_message(reply.messages()[0])

    async with app.test_matcher(plugin.help_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("/帮助")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, None)
        ctx.should_finished(plugin.help_cmd)


async def test_author_two_step_selection(app: App) -> None:
    """``/谁写的`` 多候选后回序号，应出谱师而不是曲目详情。"""
    import fastchu.plugins.song as plugin
    from fastchu.services.commands import run_chart_author, run_query
    from fastchu.services.selection import SelectionStore

    service = get_service()
    settings = get_settings()
    keyword = "a"
    first_event = make_event(f"/谁写的 {keyword}")
    selection_key = plugin._session_key(first_event)
    plugin._selections.clear(selection_key)

    # 用独立 store 算出候选列表的预期文本
    reference = SelectionStore()
    listed = await run_chart_author(
        service, settings, keyword, selections=reference, key="expected"
    )
    assert listed.kind == "candidates"

    async with app.test_matcher(plugin.author_cmd) as ctx:
        bot = ctx.create_bot()
        ctx.receive_event(bot, first_event)
        ctx.should_call_send(first_event, msg(listed.text), None)
        ctx.should_finished(plugin.author_cmd)

    pending = plugin._selections.peek(selection_key)
    assert pending is not None

    # 复制插件挂上的候选，算出回「1」的预期回复
    reference.remember(
        "expected", pending.keyword, pending.items, per_page=pending.per_page
    )
    expected = await run_query(
        service, settings, "1", selections=reference, key="expected"
    )
    assert expected.kind == "author"

    async with app.test_matcher(plugin.bare_cmd) as ctx:
        bot = ctx.create_bot()
        event = make_event("1")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, msg(expected.text), None)
        ctx.should_finished(plugin.bare_cmd)
