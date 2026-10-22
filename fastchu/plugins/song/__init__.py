"""查歌 / 查称号命令（NoneBot 胶水层）。

* ``/查歌 <关键词|#ID>``  搜歌 → 曲目详情（曲师 / 分类 / BPM / 版本 / 相关特殊称号）
* ``/称号 <关键词>``      查称号（颜色、获得条件、是否特殊）；无参数给用法提示
* ``/添加别名 <曲目> <别名>`` 给曲目补充别名（写入本地 overlay）
* ``/帮助``               命令卡片（图片）

本模块**只做三件事**：把事件解析成参数、调 :mod:`fastchu.services.commands`
拿到 :class:`~fastchu.services.commands.Reply`、把回复发出去。
格式与业务逻辑都在命令层，所以本地测试页与群里走的是同一套逻辑。

按需求「只保留直接用歌曲查询相关信息」，原先的 ``/筛选``、``/物量``
两个命令已移除（物量仍在曲目详情里显示）。需要恢复时在命令层加回
``run_filter`` / 带 ``want_notes=True`` 的 ``run_lookup``，插件侧套用
:func:`_reply` 即可。

轻量取向的落地方式：**文字与曲绘合并成同一条消息**（而不是先文字再图片两条），
曲绘默认只带 URL 让 OneBot 实现自己取，机器人侧不下载、不缓存。
"""

from __future__ import annotations

import asyncio
import logging

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from ...config import get_settings
from ...render import HELP_TEXT  # noqa: F401  —— 保留 plugin.HELP_TEXT 便于引用
from ...services.commands import (
    MISS_HINT,  # noqa: F401  —— 保留 plugin.MISS_HINT 便于引用
    MISS_TITLE_HINT,  # noqa: F401
    Reply,
    Segment,
    cooldown_hint,
    run_help,
    run_query,
    run_chart_author,
    run_add_alias,
    run_random_title,
    run_title_condition,
    run_rainbow_titles,
    RESERVED_COMMAND_WORDS,
    run_title_query,
    run_auth_start,
    run_auth_code,
    run_auth_status,
    run_auth_revoke,
)
from ...services.cooldown import Cooldown
from ...services.selection import (
    SelectionStore,
    is_cancel,
    is_next_page,
    is_prev_page,
    parse_page_target,
    parse_selection_index,
)
from ...services.song_service import aclose_service, get_service

logger = logging.getLogger(__name__)

__plugin_meta__ = PluginMetadata(
    name="FastChu 查歌",
    description=(
        "中二节奏（CHUNITHM）查歌与查称号：直接输入 /歌曲名 或 /别名 即可查，"
        "结果含曲师/BPM/分类/版本/相关特殊称号（带颜色与获得条件）。"
        "可选绑定落雪账号，查歌/查称号时附带个人成绩与获得情况。"
    ),
    usage=(
        "/<歌曲名或别名>（如 /Rebellion、/世征、/wv、/#464）\n"
        "/称号 <关键词>\n/称号\n/添加别名 <曲目> <别名>\n"
        "/授权（绑定落雪账号）\n/帮助"
    ),
    type="application",
    homepage="https://maimai.lxns.net/docs/api/chunithm",
    supported_adapters={"~onebot.v11"},
)

#: 分页发送时的间隔，避免连发太快被风控
_CHUNK_DELAY = 0.3

#: 两步式查询：按「会话 + 用户」记住候选，用户回序号即可选
_selections = SelectionStore()

_cooldown: Cooldown | None = None
_cooldown_seconds: float | None = None


def _get_cooldown() -> Cooldown:
    """按用户冷却器。

    在 ``query_cooldown`` 变化时重建——否则 ``reset_settings_cache()`` 之后
    运行时改配置对冷却完全不起作用。
    """
    global _cooldown, _cooldown_seconds
    seconds = get_settings().query_cooldown
    if _cooldown is None or _cooldown_seconds != seconds:
        _cooldown = Cooldown(seconds)
        _cooldown_seconds = seconds
    return _cooldown


def _session_key(event: MessageEvent) -> str:
    """两步式查询的键：会话（群/私聊）+ 用户。

    带上用户，避免群里 A 查完候选、B 发个「2」被当成选择。
    """
    try:
        return f"{event.get_session_id()}:{event.user_id}"
    except Exception:  # pragma: no cover - session_id 理论上不会失败
        return str(event.user_id)


def _is_pending_index(event: MessageEvent, text: str) -> bool:
    """这句话是不是「在候选列表里选一项」。"""
    if parse_selection_index(text) is None:
        return False
    return _selections.peek(_session_key(event)) is not None


def _is_pending_nav(event: MessageEvent, text: str) -> bool:
    """这句话是不是「翻页 / 取消」——有待选列表时才算。"""
    if _selections.peek(_session_key(event)) is None:
        return False
    return (
        is_next_page(text)
        or is_prev_page(text)
        or is_cancel(text)
        or parse_page_target(text) is not None
    )


def _is_pending_action(event: MessageEvent, text: str) -> bool:
    return _is_pending_index(event, text) or _is_pending_nav(event, text)


def _is_superuser(event: MessageEvent) -> bool:
    superusers = getattr(get_driver().config, "superusers", set()) or set()
    return str(event.user_id) in {str(u) for u in superusers}


def to_message(segments: list[Segment]) -> Message:
    """把消息段转成 NoneBot 的 ``Message``。

    段结构由 :meth:`fastchu.services.commands.Reply.messages` 决定——
    **这里不做任何排版决策**，否则就与测试页各说各话了。
    """
    message = Message()
    for segment in segments:
        if segment.type == "image":
            message += MessageSegment.image(segment.image_payload)
        else:
            message += MessageSegment.text(segment.text or "")
    return message


def _check_cooldown(event: MessageEvent, user_id: int) -> float:
    """返回需要等待的秒数（0 表示放行）。"""
    if _is_superuser(event):
        return 0.0
    return _get_cooldown().check(user_id)


async def _reply(
    matcher: Matcher, reply: Reply, event: MessageEvent | None = None
) -> None:
    """把命令层的结果发出去。

    消息条数与分段完全按 :meth:`Reply.messages` 来：
    单条时把曲绘拼进**同一条**消息，分页时只发文字。
    绑定了落雪账号的用户，详情回复会先叠加个人成绩/获得情况；
    个人接口失败时原回复照常发出。
    """
    if event is not None:
        try:
            reply = await get_service().personal.enrich(reply, str(event.user_id))
        except Exception:  # noqa: BLE001 - 个人数据是增强，绝不能挡住查询
            logger.warning("个人数据增强失败，已跳过", exc_info=True)

    messages = reply.messages()
    if not messages:  # pragma: no cover - 命令层不会返回空回复
        await matcher.finish()

    if len(messages) == 1:
        await matcher.finish(to_message(messages[0]))

    for index, segments in enumerate(messages, start=1):
        await matcher.send(to_message(segments))
        if index < len(messages):
            await asyncio.sleep(_CHUNK_DELAY)
    await matcher.finish()


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #


async def _handle_lookup(matcher: Matcher, event: MessageEvent, args: Message) -> None:
    keyword = args.extract_plain_text().strip()

    # 选序号是 UI 动作，不该被查询冷却挡住
    if keyword and not _is_pending_action(event, keyword):
        wait = _check_cooldown(event, int(event.user_id))
        if wait > 0:
            await matcher.finish(cooldown_hint(wait))

    reply = await run_query(
        get_service(), get_settings(), keyword, selections=_selections, key=_session_key(event)
    )
    await _reply(matcher, reply, event)


async def _handle_title(matcher: Matcher, event: MessageEvent, args: Message) -> None:
    keyword = args.extract_plain_text().strip()

    if keyword and not _is_pending_action(event, keyword):
        wait = _check_cooldown(event, int(event.user_id))
        if wait > 0:
            await matcher.finish(cooldown_hint(wait))

    reply = await run_title_query(
        get_service(), get_settings(), keyword, selections=_selections, key=_session_key(event)
    )
    await _reply(matcher, reply, event)


async def _handle_help(matcher: Matcher, event: MessageEvent) -> None:
    reply = await run_help(get_service(), get_settings())
    await _reply(matcher, reply, event)


# --------------------------------------------------------------------------- #
# 「/歌曲名」直查
#
# 用户要的是「直接输入 /xxx（xxx 是歌曲/别名）」，而不是先打一个命令词。
# NoneBot 的命令匹配需要固定命令名，所以这里用 on_message + 自定义 Rule：
# 只要文本以命令起始符开头、且去掉起始符后不是保留命令词，就当成歌名去查。
# --------------------------------------------------------------------------- #

#: 真正的命令词，不能被当成歌名（定义在命令层，与 AstrBot 插件共用）
RESERVED_WORDS = RESERVED_COMMAND_WORDS


def _command_starts() -> set[str]:
    """读取 NoneBot 配置的命令起始符（默认只有 ``/``）。"""
    starts = getattr(get_driver().config, "command_start", None) or {"/"}
    return {str(s) for s in starts if s}


def strip_command_start(text: str) -> str:
    """去掉开头的所有命令起始符与空白，返回剩余内容。"""
    starts = _command_starts()
    out = (text or "").strip()
    while out:
        for start in starts:
            if start and out.startswith(start):
                out = out[len(start) :].lstrip()
                break
        else:
            break
    return out.strip()


def _looks_like_bare_song(event: MessageEvent) -> bool:
    """``/xxx`` 形式的消息，且 xxx 不是保留命令词。

    保留命令词交由优先级更高的 ``on_command`` 处理；这里再挡一次，
    这样即使某个命令被禁用，``/称号`` 也不会被当成歌名去查。
    """
    text = event.get_plaintext().strip()
    if not text:
        return False

    # 两步式：刚列过候选，用户直接回「2」也该接住
    if _is_pending_action(event, text):
        return True

    starts = _command_starts()
    if not any(start and text.startswith(start) for start in starts):
        return False

    body = strip_command_start(text)
    if not body:
        return False

    lowered = body.casefold()
    if any(lowered.startswith(prefix) for prefix in AUTHOR_PREFIXES):
        return False

    return body.split()[0].casefold() not in {word.casefold() for word in RESERVED_WORDS}


song_cmd = on_command("查歌", aliases={"song", "查曲"}, priority=10, block=True)
title_cmd = on_command("称号", aliases={"title", "查称号"}, priority=10, block=True)
#: 按条件检索称号（名字记不住、条件记得住时用）
condition_cmd = on_command("称号条件", aliases={"条件"}, priority=10, block=True)
#: 随机抽一个称号
random_cmd = on_command("随机称号", aliases={"随机", "random"}, priority=10, block=True)
#: 隐藏命令：所有非角色称号的特殊虹称号
rainbow_cmd = on_command("虹称号", priority=10, block=True)
#: 群友给曲目补充别名
add_alias_cmd = on_command("添加别名", aliases={"加别名", "addalias"}, priority=10, block=True)
#: 落雪账号绑定：无参数发起，带参数提交授权码
auth_cmd = on_command(
    "授权", aliases={"绑定落雪", "auth", "oauth"}, priority=10, block=True
)
auth_status_cmd = on_command(
    "授权状态", aliases={"授权查询", "authstatus"}, priority=10, block=True
)
auth_revoke_cmd = on_command(
    "取消授权", aliases={"解绑落雪", "unbind"}, priority=10, block=True
)
#: 「谁写的」允许与参数连写（``/谁写的四次元紫``），所以不能用 on_command
AUTHOR_PREFIXES = ("谁写的", "谱师", "author")


def _looks_like_author(event: MessageEvent) -> bool:
    text = strip_command_start(event.get_plaintext().strip())
    if not text:
        return False
    lowered = text.casefold()
    return any(lowered.startswith(prefix) for prefix in AUTHOR_PREFIXES)


author_cmd = on_message(rule=Rule(_looks_like_author), priority=9, block=True)
help_cmd = on_command(
    "查歌帮助",
    aliases={"fastchu", "FastChu", "帮助", "help"},
    priority=10,
    block=True,
)

#: 优先级必须**低于**上面几条具名命令，否则 ``/称号`` 会被当成歌名
bare_cmd = on_message(rule=Rule(_looks_like_bare_song), priority=20, block=True)


@song_cmd.handle()
async def _on_song(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _handle_lookup(matcher, event, args)


@title_cmd.handle()
async def _on_title(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _handle_title(matcher, event, args)


@condition_cmd.handle()
async def _on_condition(
    matcher: Matcher, event: MessageEvent, args: Message = CommandArg()
) -> None:
    keyword = args.extract_plain_text().strip()
    if keyword and not _is_pending_action(event, keyword):
        wait = _check_cooldown(event, int(event.user_id))
        if wait > 0:
            await matcher.finish(cooldown_hint(wait))
    reply = await run_title_condition(
        get_service(), get_settings(), keyword, selections=_selections, key=_session_key(event)
    )
    await _reply(matcher, reply, event)


@random_cmd.handle()
async def _on_random(matcher: Matcher, event: MessageEvent) -> None:
    wait = _check_cooldown(event, int(event.user_id))
    if wait > 0:
        await matcher.finish(cooldown_hint(wait))
    reply = await run_random_title(
        get_service(),
        get_settings(),
        selections=_selections,
        key=_session_key(event),
    )
    await _reply(matcher, reply, event)


@rainbow_cmd.handle()
async def _on_rainbow_titles(
    matcher: Matcher, event: MessageEvent, args: Message = CommandArg()
) -> None:
    keyword = args.extract_plain_text().strip()
    if keyword and not _is_pending_action(event, keyword):
        wait = _check_cooldown(event, int(event.user_id))
        if wait > 0:
            await matcher.finish(cooldown_hint(wait))
    reply = await run_rainbow_titles(
        get_service(), get_settings(), keyword, selections=_selections, key=_session_key(event)
    )
    await _reply(matcher, reply, event)


@add_alias_cmd.handle()
async def _on_add_alias(
    matcher: Matcher, event: MessageEvent, args: Message = CommandArg()
) -> None:
    keyword = args.extract_plain_text().strip()
    wait = _check_cooldown(event, int(event.user_id))
    if wait > 0:
        await matcher.finish(cooldown_hint(wait))
    reply = await run_add_alias(get_service(), keyword)
    await _reply(matcher, reply, event)


@author_cmd.handle()
async def _on_author(matcher: Matcher, event: MessageEvent) -> None:
    text = strip_command_start(event.get_plaintext().strip())
    keyword = text
    lowered = text.casefold()
    for prefix in AUTHOR_PREFIXES:
        if lowered.startswith(prefix):
            keyword = text[len(prefix) :].strip()
            break
    wait = _check_cooldown(event, int(event.user_id))
    if wait > 0:
        await matcher.finish(cooldown_hint(wait))
    reply = await run_chart_author(
        get_service(),
        get_settings(),
        keyword,
        selections=_selections,
        key=_session_key(event),
    )
    await _reply(matcher, reply, event)


@auth_cmd.handle()
async def _on_auth(
    matcher: Matcher, event: MessageEvent, args: Message = CommandArg()
) -> None:
    raw = args.extract_plain_text().strip()
    lowered = raw.casefold()
    if lowered in {"状态", "查询", "status"}:
        reply = await run_auth_status(
            get_service(), get_settings(), str(event.user_id)
        )
    elif lowered in {"取消", "解绑", "revoke", "unbind"}:
        reply = await run_auth_revoke(
            get_service(), get_settings(), str(event.user_id)
        )
    elif raw:
        reply = await run_auth_code(
            get_service(), get_settings(), str(event.user_id), raw
        )
    else:
        reply = await run_auth_start(
            get_service(), get_settings(), str(event.user_id)
        )
    await _reply(matcher, reply, event)


@auth_status_cmd.handle()
async def _on_auth_status(matcher: Matcher, event: MessageEvent) -> None:
    reply = await run_auth_status(
        get_service(), get_settings(), str(event.user_id)
    )
    await _reply(matcher, reply, event)


@auth_revoke_cmd.handle()
async def _on_auth_revoke(matcher: Matcher, event: MessageEvent) -> None:
    reply = await run_auth_revoke(
        get_service(), get_settings(), str(event.user_id)
    )
    await _reply(matcher, reply, event)


@help_cmd.handle()
async def _on_help(matcher: Matcher, event: MessageEvent) -> None:
    await _handle_help(matcher, event)


@bare_cmd.handle()
async def _on_bare_song(matcher: Matcher, event: MessageEvent) -> None:
    keyword = strip_command_start(event.get_plaintext())
    if not _is_pending_action(event, keyword):
        wait = _check_cooldown(event, int(event.user_id))
        if wait > 0:
            await matcher.finish(cooldown_hint(wait))

    reply = await run_query(
        get_service(), get_settings(), keyword, selections=_selections, key=_session_key(event)
    )
    await _reply(matcher, reply, event)


@get_driver().on_shutdown
async def _close_service() -> None:
    """退出时关掉 httpx 连接池。"""
    try:
        await aclose_service()
    except Exception:  # noqa: BLE001 - 关闭失败不该影响退出
        logger.warning("关闭曲库服务失败", exc_info=True)
