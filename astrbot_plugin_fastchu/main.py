"""FastChu —— 中二节奏（CHUNITHM）查歌 / 查称号（AstrBot 插件）。

用法（群里直接发）：

* ``/歌曲名``  或 ``/别名``   — 例如 ``/Rebellion``、``/世征``、``/wv``、``/#464``、``/464``
* ``/称号 <关键词>``          — 查称号（颜色、获得条件、是否特殊）
* ``/称号``                   — 用法提示（查称号 / 按条件查 / 随机）
* ``/添加别名 <曲目> <别名>``  — 给曲目补充别名（群友投稿）
* ``/授权``                   — 绑定落雪账号，查歌/查称号时附带个人数据
* ``/授权状态`` / ``/取消授权`` — 查看或删除绑定
* ``/帮助``                   — 命令卡片（图片）

曲目详情包含：曲师 / 分类 / BPM / 版本 / 相关特殊称号（带颜色与所需条件），
并会在国服与日服定数不同时同时标注（``15.5国/15.6日``）。绑定落雪账号后，
曲目详情会多出「各难度最高分」，称号详情会多出「已获得/未获得」。

实现说明：查歌逻辑全部来自 FastChu 项目的**框架无关核心层**
（``fastchu.services`` / ``fastchu.render``），随插件以 ``_vendor/`` 形式附带，
因此**不需要 pip 安装、也不会往 AstrBot 的 venv 里塞依赖**。
本文件只做三件事：解析消息 → 调命令层 → 把回复发出去。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# 路径引导（必须在 import fastchu 之前跑完）
# --------------------------------------------------------------------------- #


def _paths_project_dir() -> Path | None:
    """构建时写入的项目根目录（``_paths.json``），或环境变量显式指定。"""
    explicit = os.environ.get("FASTCHU_PROJECT_DIR")
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("FASTCHU_DATA_DIR")
    if data_dir:
        return Path(data_dir).parent

    paths_file = _PLUGIN_DIR / "_paths.json"
    if paths_file.is_file():
        try:
            project = json.loads(paths_file.read_text(encoding="utf-8")).get("project_dir")
            if project:
                return Path(project)
        except (OSError, ValueError):
            pass
    return None


def _load_project_env(project: Path) -> None:
    """把项目 ``.env`` 里的 ``FASTCHU_*`` 读进环境变量。

    AstrBot 的工作目录不是 FastChu 项目目录，pydantic-settings 默认的
    ``.env`` 因此读不到。这里只加载 ``FASTCHU_`` 前缀，避免污染 AstrBot
    自己的配置；真实环境变量优先（``setdefault``）。
    """
    env_file = project / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - pydantic-settings 依赖 python-dotenv
        return
    try:
        values = dotenv_values(env_file)
    except Exception:  # noqa: BLE001 - 配置读不出来时按默认配置运行
        return
    for key, value in values.items():
        if not key or value is None:
            continue
        if str(key).upper().startswith("FASTCHU_"):
            os.environ.setdefault(str(key), str(value))


def _project_data_dir() -> Path | None:
    """找到 FastChu 项目的数据目录（复用已经抓好的缓存，避免重复下载约 6 MB）。"""
    explicit = os.environ.get("FASTCHU_DATA_DIR")
    if explicit:
        return Path(explicit)

    project = _paths_project_dir()
    if project is not None and (project / "data" / "cache" / "song_list.json").is_file():
        return project / "data"
    return None


def _bootstrap() -> None:
    vendor = _PLUGIN_DIR / "_vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))

    project = _paths_project_dir()
    if project is not None:
        _load_project_env(project)

    data_dir = _project_data_dir()
    if data_dir is None:
        data_dir = (project / "data") if project is not None else (_PLUGIN_DIR / "data")

    os.environ.setdefault("FASTCHU_SONG_CACHE_PATH", str(data_dir / "cache" / "song_list.json"))
    os.environ.setdefault("FASTCHU_ALIAS_CACHE_PATH", str(data_dir / "cache" / "alias_list.json"))
    os.environ.setdefault(
        "FASTCHU_COMMUNITY_ALIAS_PATH", str(data_dir / "aliases" / "community.json")
    )
    os.environ.setdefault(
        "FASTCHU_LOCAL_ALIAS_PATH", str(data_dir / "aliases" / "local.json")
    )
    os.environ.setdefault("FASTCHU_TROPHY_CACHE_PATH", str(data_dir / "cache" / "trophy_list.json"))
    os.environ.setdefault("FASTCHU_REGION_CACHE_PATH", str(data_dir / "songs" / "songs.union.json"))
    #: 日服 Wiki 补充的称号条件（build_title_report.py 生成）
    os.environ.setdefault(
        "FASTCHU_CONDITION_OVERRIDES_PATH", str(data_dir / "titles" / "conditions.json")
    )
    #: 日服限定称号（wiki 有、落雪没有）
    os.environ.setdefault(
        "FASTCHU_JP_ONLY_TITLES_PATH", str(data_dir / "titles" / "jp_only.json")
    )
    os.environ.setdefault("FASTCHU_JACKET_CACHE_DIR", str(data_dir / "assets" / "jacket"))
    #: 落雪 OAuth 授权记录（含令牌，位于项目的 data/auth，不入库）
    os.environ.setdefault(
        "FASTCHU_LXNS_OAUTH_TOKEN_PATH", str(data_dir / "auth" / "oauth_tokens.json")
    )


_bootstrap()


from astrbot.api import logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.message_components import At, Image, Plain  # noqa: E402
from astrbot.api.star import Context, Star  # noqa: E402

from fastchu.config import get_settings  # noqa: E402
from fastchu.services.commands import (  # noqa: E402
    Reply,
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
from fastchu.services.cooldown import Cooldown  # noqa: E402
from fastchu.services.selection import (  # noqa: E402
    SelectionStore,
    is_cancel,
    is_next_page,
    is_prev_page,
    parse_page_target,
    parse_selection_index,
)
from fastchu.services.song_service import aclose_service, get_service  # noqa: E402

logger = logging.getLogger("astrbot")

#: 这些词是插件/框架自己的命令，不能被当成歌名
_RESERVED = frozenset(
    RESERVED_COMMAND_WORDS
)

#: 命令起始符（AstrBot 默认唤醒前缀是 ``/``）
_PREFIXES = ("/", "／")

#: 物量显示三档（与 _conf_schema.json 的 options 一致）
_NOTES_MODES = ("off", "compact", "full")

#: 两步式查询：按「会话 + 用户」记住候选，用户回序号即可选
_SELECTIONS = SelectionStore()


def _session_key(event: AstrMessageEvent) -> str:
    """两步式查询的键：会话 + 发送者。

    带上发送者，避免群里 A 刚查完候选、B 发个「2」被当成选择。
    """
    try:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"
    except Exception:  # pragma: no cover - 理论上不会失败
        return str(id(event))


def _is_pending_index(event: AstrMessageEvent, text: str) -> bool:
    """这句话是不是「在候选列表里选一项」。"""
    if parse_selection_index(text) is None:
        return False
    return _SELECTIONS.peek(_session_key(event)) is not None


def _is_pending_action(event: AstrMessageEvent, text: str) -> bool:
    """选序号 / 翻页 / 取消——有待选列表时都不需要 @ 也能接。"""
    if _is_pending_index(event, text):
        return True
    if _SELECTIONS.peek(_session_key(event)) is None:
        return False
    return (
        is_next_page(text)
        or is_prev_page(text)
        or is_cancel(text)
        or parse_page_target(text) is not None
    )


def strip_prefix(text: str) -> str:
    """去掉开头的命令起始符与空白。"""
    out = (text or "").strip()
    while out and out[0] in _PREFIXES:
        out = out[1:].lstrip()
    return out.strip()


def _registered_commands() -> set[str]:
    """AstrBot 里已经注册的命令名（含别名）。

    用来避免把 ``/reset``、``/help`` 这类框架命令当成歌名去查。
    """
    names: set[str] = set(_RESERVED)
    try:
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:  # pragma: no cover - AstrBot 内部结构变动时降级
        return names

    for handler in star_handlers_registry:
        for event_filter in getattr(handler, "event_filters", []) or []:
            if isinstance(event_filter, CommandFilter):
                if event_filter.command_name:
                    names.add(str(event_filter.command_name).casefold())
                for alias in event_filter.alias or set():
                    names.add(str(alias).casefold())
    return names


#: 「谁写的」的触发前缀（允许与参数连写）
AUTHOR_PREFIXES = ("谁写的", "谱师", "author")


class _AuthorFilter(filter.CustomFilter):
    """``/谁写的四次元紫``、``/谁写的 四次元紫`` 都要触发。"""

    def filter(self, event: AstrMessageEvent, cfg) -> bool:  # noqa: ARG002
        text = strip_prefix(event.get_message_str() or "")
        if not text:
            return False
        lowered = text.casefold()
        if not any(lowered.startswith(p) for p in AUTHOR_PREFIXES):
            return False
        return bool(event.is_at_or_wake_command)


class _BareSongFilter(filter.CustomFilter):  # type: ignore[name-defined]
    """「被叫到就把后面的内容当歌名查」的入口。

    注意 AstrBot 的唤醒检查会**先**处理前缀：

    * ``/End Time``  → wake_prefix 被剥掉，handler 拿到的是 ``End Time``；
    * ``@机器人 End Time`` / 私聊 → 根本不带任何前缀，``message_str`` 保持原样。

    所以这里**不能**要求文本以 ``/`` 开头（否则在 QQ 官方 bot 的 @ 唤醒场景下永远不触发）。
    判定改为：已唤醒 + 去掉可能残留的 ``/`` 后非空 + 首个词不是任何已注册命令。
    """

    def filter(self, event: AstrMessageEvent, cfg) -> bool:  # noqa: ARG002
        body = strip_prefix(event.get_message_str() or "")
        if not body:
            return False

        # 两步式：刚列过候选时，用户回一个纯序号（哪怕没 @）也算选择，
        # 这样 QQ 官方 bot 里「@机器人 → 列表 → 直接回 2」就能走通。
        if _is_pending_action(event, body):
            return True

        # 群里只认「被 @」的裸查询（@机器人 公交车）：
        # 别的 bot / 普通聊天都会无脑触发，太吵。私聊维持原样。
        if not event.is_private_chat():
            if not _is_at_me(event):
                return False
        elif not event.is_at_or_wake_command:
            return False

        # 「谁写的…」交给 author 处理器，别当歌名
        lowered = body.casefold()
        if any(lowered.startswith(prefix) for prefix in AUTHOR_PREFIXES):
            return False

        first = body.split()[0].casefold()
        return first not in _registered_commands()


def _is_at_me(event: AstrMessageEvent) -> bool:
    """这条消息有没有 **@ 机器人本体**（At 消息段且 qq 等于自己）。

    只看 @：群里的唤醒前缀（``/``）不该让裸查询生效——否则别人家 bot 的
    正常输出也会被当成歌名。
    """
    self_id = str(event.get_self_id() or "")
    if not self_id:
        return False
    for comp in event.get_messages() or []:
        if isinstance(comp, At) and str(getattr(comp, "qq", "")) == self_id:
            return True
    return False


def _image_component(segment) -> object | None:
    """把 FastChu 的图片段转成 AstrBot 的 Image 组件。"""
    data_uri = getattr(segment, "image_data_uri", None)
    if data_uri:
        return Image.fromBase64(data_uri.split(",", 1)[1])
    payload = getattr(segment, "image_payload", "") or ""
    if payload.startswith("http"):
        return Image.fromURL(payload)
    if payload:
        return Image(file=payload)
    return None


class FastChuPlugin(Star):
    """中二节奏查歌 / 查称号。

    直接发 ``/歌曲名`` 或 ``/别名`` 即可；``/称号`` 查称号；``/帮助`` 看用法。
    曲库/称号来自落雪公共接口，不需要密钥；可选的 ``/授权`` 需要管理员
    在项目 ``.env`` 里配置落雪 OAuth Client ID。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._cooldown: Cooldown | None = None
        self._cooldown_seconds: float | None = None

    # ------------------------------------------------------------------ 配置
    def _settings(self):
        """把面板配置叠加到环境配置上。

        面板里改 ``notes_mode`` 需要立刻生效，而 ``get_settings()`` 是带缓存的，
        所以这里用 ``model_copy`` 派生一份，不动全局缓存。
        """
        base = get_settings()
        config = self.config or {}

        updates = {}
        mode = config.get("notes_mode")
        if mode in _NOTES_MODES and mode != base.notes_mode:
            updates["notes_mode"] = mode

        if isinstance(config.get("jacket_separate"), bool) and (
            config["jacket_separate"] != base.jacket_separate
        ):
            updates["jacket_separate"] = config["jacket_separate"]

        try:
            limit = int(config.get("related_title_limit", 0))
        except (TypeError, ValueError):
            limit = 0
        if 1 <= limit <= 20 and limit != base.related_title_limit:
            updates["related_title_limit"] = limit

        return base.model_copy(update=updates) if updates else base

    # ------------------------------------------------------------------ 冷却
    def _cooldown_for(self) -> Cooldown:
        seconds = get_settings().query_cooldown
        if self._cooldown is None or self._cooldown_seconds != seconds:
            self._cooldown = Cooldown(seconds)
            self._cooldown_seconds = seconds
        return self._cooldown

    def _wait_seconds(self, event: AstrMessageEvent) -> float:
        try:
            user_id = int(event.get_sender_id())
        except (TypeError, ValueError):
            return 0.0
        return self._cooldown_for().check(user_id)

    # ------------------------------------------------------------------ 回复
    async def _emit(self, event: AstrMessageEvent, reply: Reply):
        """把命令层的结果发出去：一条消息里正文 + 曲绘（分页时逐条发）。

        注意：``stop_event()`` **必须放在 yield 之后**。
        AstrBot 的 ``call_handler`` 会对每个 yield 出来的结果调用 ``event.set_result()``，
        而 ``set_result()`` 会整个替换掉 ``_result``——先 stop 再 yield 会把 stop 标记冲掉，
        结果是内容可能丢失、且后续还会再走一遍 LLM 多回一条。
        放在最后则会把这个标记打在**已发出的结果**上，内容保留、传播终止。
        """
        try:
            reply = await get_service().personal.enrich(
                reply, str(event.get_sender_id())
            )
        except Exception:  # noqa: BLE001 - 个人数据是增强，绝不能挡住查询
            logger.warning("个人数据增强失败，已跳过", exc_info=True)

        messages = reply.messages()
        if not messages:  # pragma: no cover
            yield event.plain_result("（空回复）")
            event.stop_event()
            return

        for index, segments in enumerate(messages):
            chain = []
            for segment in segments:
                if segment.type == "image":
                    image = _image_component(segment)
                    if image is not None:
                        chain.append(image)
                else:
                    chain.append(Plain(segment.text or ""))
            yield event.chain_result(chain)
            if index < len(messages) - 1:
                await asyncio.sleep(0.3)
        event.stop_event()

    async def _lookup(self, event: AstrMessageEvent, keyword: str):
        # 选序号是 UI 动作，不该被查询冷却挡住
        if not _is_pending_action(event, keyword):
            wait = self._wait_seconds(event)
            if wait > 0:
                yield event.plain_result(cooldown_hint(wait))
                event.stop_event()
                return
        reply = await run_query(
            get_service(),
            self._settings(),
            keyword,
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    async def _titles(self, event: AstrMessageEvent, keyword: str):
        if not _is_pending_action(event, keyword):
            wait = self._wait_seconds(event)
            if wait > 0:
                yield event.plain_result(cooldown_hint(wait))
                event.stop_event()
                return
        reply = await run_title_query(
            get_service(),
            self._settings(),
            keyword,
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    # ------------------------------------------------------------------ 命令
    @filter.command("查歌", alias={"song", "查曲"})
    async def song_cmd(self, event: AstrMessageEvent):
        """按曲名/别名查歌（``/查歌 <关键词>``，是 ``/歌曲名`` 的显式写法）。"""
        keyword = strip_prefix(event.get_message_str())
        for prefix_word in ("查歌", "song", "查曲"):
            if keyword.casefold().startswith(prefix_word):
                keyword = keyword[len(prefix_word) :].strip()
                break
        async for result in self._lookup(event, keyword):
            yield result

    @filter.command("称号", alias={"title", "查称号"})
    async def title_cmd(self, event: AstrMessageEvent):
        """查称号（``/称号 <关键词>``；不带参数给用法提示）。"""
        keyword = strip_prefix(event.get_message_str())
        for prefix_word in ("查称号", "称号", "title"):
            if keyword.casefold().startswith(prefix_word):
                keyword = keyword[len(prefix_word) :].strip()
                break
        async for result in self._titles(event, keyword):
            yield result


    @filter.command("称号条件", alias={"条件"})
    async def condition_cmd(self, event: AstrMessageEvent):
        """按获得条件检索称号（``/称号条件 RANK SSS``）。"""
        keyword = strip_prefix(event.get_message_str())
        for word in ("称号条件", "条件"):
            if keyword.casefold().startswith(word):
                keyword = keyword[len(word) :].strip()
                break
        if keyword and not _is_pending_action(event, keyword):
            wait = self._wait_seconds(event)
            if wait > 0:
                yield event.plain_result(cooldown_hint(wait))
                event.stop_event()
                return
        reply = await run_title_condition(
            get_service(),
            self._settings(),
            keyword,
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("随机称号", alias={"随机", "random"})
    async def random_cmd(self, event: AstrMessageEvent):
        """随机抽一个称号。"""
        wait = self._wait_seconds(event)
        if wait > 0:
            yield event.plain_result(cooldown_hint(wait))
            event.stop_event()
            return
        reply = await run_random_title(
            get_service(),
            self._settings(),
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("虹称号")
    async def rainbow_titles_cmd(self, event: AstrMessageEvent):
        """隐藏命令：列出所有非角色称号的特殊虹称号。"""
        keyword = strip_prefix(event.get_message_str())
        if keyword.startswith("虹称号"):
            keyword = keyword[len("虹称号") :].strip()
        if keyword and not _is_pending_action(event, keyword):
            wait = self._wait_seconds(event)
            if wait > 0:
                yield event.plain_result(cooldown_hint(wait))
                event.stop_event()
                return
        reply = await run_rainbow_titles(
            get_service(),
            self._settings(),
            keyword,
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("添加别名", alias={"加别名", "addalias"})
    async def add_alias_cmd(self, event: AstrMessageEvent):
        """给曲目补充别名（``/添加别名 <#ID|歌名> <别名>``）。"""
        keyword = strip_prefix(event.get_message_str())
        for word in ("添加别名", "加别名", "addalias"):
            if keyword.casefold().startswith(word):
                keyword = keyword[len(word) :].strip()
                break
        wait = self._wait_seconds(event)
        if wait > 0:
            yield event.plain_result(cooldown_hint(wait))
            event.stop_event()
            return
        reply = await run_add_alias(get_service(), keyword)
        async for result in self._emit(event, reply):
            yield result

    @filter.custom_filter(_AuthorFilter)
    async def author_cmd(self, event: AstrMessageEvent):
        """查某个难度的谱师（不给难度就取最高难度）。

        用 custom_filter 而不是 filter.command：命令过滤器要求「命令 + 空格」，
        ``/谁写的四次元紫`` 这种连写形式根本匹配不上。
        """
        keyword = strip_prefix(event.get_message_str())
        for word in ("谁写的", "谱师", "author"):
            if keyword.casefold().startswith(word):
                keyword = keyword[len(word) :].strip()
                break
        wait = self._wait_seconds(event)
        if wait > 0:
            yield event.plain_result(cooldown_hint(wait))
            event.stop_event()
            return
        reply = await run_chart_author(
            get_service(),
            self._settings(),
            keyword,
            selections=_SELECTIONS,
            key=_session_key(event),
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("授权", alias={"绑定落雪", "auth", "oauth"})
    async def auth_cmd(self, event: AstrMessageEvent):
        """绑定落雪账号：不带参数发起，``/授权 <授权码>`` 完成绑定。"""
        raw = strip_prefix(event.get_message_str() or "")
        for word in ("绑定落雪", "授权", "auth", "oauth"):
            if raw.casefold().startswith(word):
                raw = raw[len(word) :].strip()
                break
        lowered = raw.casefold()
        if lowered in {"状态", "查询", "status"}:
            reply = await run_auth_status(
                get_service(), self._settings(), str(event.get_sender_id())
            )
        elif lowered in {"取消", "解绑", "revoke", "unbind"}:
            reply = await run_auth_revoke(
                get_service(), self._settings(), str(event.get_sender_id())
            )
        elif raw:
            reply = await run_auth_code(
                get_service(), self._settings(), str(event.get_sender_id()), raw
            )
        else:
            reply = await run_auth_start(
                get_service(), self._settings(), str(event.get_sender_id())
            )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("授权状态", alias={"授权查询", "authstatus"})
    async def auth_status_cmd(self, event: AstrMessageEvent):
        """查看落雪账号绑定状态。"""
        reply = await run_auth_status(
            get_service(), self._settings(), str(event.get_sender_id())
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("取消授权", alias={"解绑落雪", "unbind"})
    async def auth_revoke_cmd(self, event: AstrMessageEvent):
        """删除本机保存的落雪授权。"""
        reply = await run_auth_revoke(
            get_service(), self._settings(), str(event.get_sender_id())
        )
        async for result in self._emit(event, reply):
            yield result

    @filter.command("帮助", alias={"help", "查歌帮助"})
    async def help_cmd(self, event: AstrMessageEvent):
        """显示 FastChu 的命令表。"""
        wait = self._wait_seconds(event)
        if wait > 0:
            yield event.plain_result(cooldown_hint(wait))
            event.stop_event()
            return
        reply = await run_help(get_service(), self._settings())
        async for result in self._emit(event, reply):
            yield result

    @filter.custom_filter(_BareSongFilter)  # 传类：AstrBot 内部会自己实例化
    async def bare_song(self, event: AstrMessageEvent):
        """``/歌曲名`` 直查。"""
        keyword = strip_prefix(event.get_message_str())
        async for result in self._lookup(event, keyword):
            yield result

    # ------------------------------------------------------------------ 生命周期
    async def terminate(self) -> None:
        """插件被禁用/卸载时关掉 HTTP 连接池。"""
        try:
            await aclose_service()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 FastChu 服务失败: %s", exc)
