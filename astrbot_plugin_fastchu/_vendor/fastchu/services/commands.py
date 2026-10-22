"""命令层：把「一条命令」变成「要发出的若干条消息」。

这一层**不依赖 NoneBot**，因此：

* QQ 插件只负责「解析事件 → 调这里 → 把 Reply 发出去」；
* 本地测试页（``tools/testpage``）调同一套函数并原样展示。

两者共用一份逻辑，测试页看到什么、群里就发什么——不会各自漂移。

命令层还负责把「渲染 + 分页」串起来，所以插件侧没有任何格式代码。
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from ..config import BotSettings
from ..errors import ChunithmBotError, SongNotFoundError
from ..models import LevelIndex, Song, Trophy
from ..render import (
    HELP_TEXT,
    format_difficulty,
    render_candidate_page,
    render_help_card,
    render_notes,
    render_region_candidates,
    render_region_song_detail,
    render_song_candidates,
    render_song_detail,
    render_title_candidates,
    render_title_detail,
    render_title_list,
    render_title_stats,
    split_message,
)
from .jacket import JacketImage
from .regions import RegionSong
from .search import normalize
from .selection import (
    PendingSelection,
    SelectionItem,
    SelectionStore,
    is_cancel,
    is_next_page,
    is_prev_page,
    parse_page_target,
    parse_selection_index,
)
from .song_service import SongService

__all__ = [
    "Reply",
    "ReplyKind",
    "Segment",
    "EMPTY_KEYWORD_HINT",

    "RESERVED_COMMAND_WORDS",
    "MISS_HINT",
    "MISS_TITLE_HINT",
    "GENERIC_ERROR",
    "CANCEL_HINT",
    "OUT_OF_RANGE_HINT",
    "OUT_OF_RANGE_PAGE_HINT",
    "cooldown_hint",
    "run_lookup",
    "run_query",
    "run_title",
    "run_title_query",
    "run_title_condition",
    "run_random_title",
    "run_rainbow_titles",
    "run_chart_author",
    "run_add_alias",
    "split_chart_difficulty",
    "run_help",
    "run_auth_start",
    "run_auth_code",
    "run_auth_status",
    "run_auth_revoke",
]

logger = logging.getLogger(__name__)

ReplyKind = Literal[
    "detail",
    "notes",
    "candidates",
    "title",
    "author",
    "title_candidates",
    "title_stats",
    "region_detail",
    "region_candidates",
    "filter",
    "alias",
    "hint",
    "help",
    "auth",
    "error",
]

#: 这些词是命令，不能被当成歌名去查（NoneBot / AstrBot 两个入口共用）
RESERVED_COMMAND_WORDS: frozenset[str] = frozenset(
    {
        "查歌",
        "song",
        "查曲",
        "称号条件",
        "条件",
        "谁写的",
        "谱师",
        "author",
        "随机称号",
        "随机",
        "random",
        "虹称号",
        "称号",
        "title",
        "查称号",
        "查歌帮助",
        "帮助",
        "help",
        "fastchu",
        "添加别名",
        "加别名",
        "addalias",
        "授权",
        "授权状态",
        "授权查询",
        "取消授权",
        "绑定落雪",
        "解绑落雪",
        "auth",
        "authstatus",
        "unbind",
        "oauth",
    }
)

#: 不带参数时的提示
EMPTY_KEYWORD_HINT = "查哪首？例：/Rebellion"
#: 搜不到时的提示
MISS_HINT = "没找到，试试别名或 /#曲目ID"
#: 称号搜不到时的提示
MISS_TITLE_HINT = "没找到这个称号"
#: 未预期错误的兜底文案
GENERIC_ERROR = "查询出错了，请稍后再试；如果一直失败请联系管理员。"
#: 用户取消两步式查询
CANCEL_HINT = "已取消，重新查歌直接发歌名就行。"
#: 两步式查询里序号超出范围
OUT_OF_RANGE_HINT = "序号超出范围，请回复列表里的数字（比如 1~{total}）。"
#: 精准查询后缀：``/cr c`` —— 只认完全等于 ``cr`` 的曲名/别名
_EXACT_SUFFIX = re.compile(r"[\s　]+[cCｃＣ]$")


def split_exact(text: str) -> tuple[str, bool]:
    """把 ``cr c`` 拆成 ``("cr", True)``；没有后缀就是普通模糊查询。"""
    raw = (text or "").strip()
    if _EXACT_SUFFIX.search(raw):
        return _EXACT_SUFFIX.sub("", raw).strip(), True
    return raw, False


def _song_sort_key(item: SelectionItem) -> float:
    """候选排序键：难度定数越高越靠前（日服曲目取日服定数）。"""
    song = item.song
    if song is not None:
        return float(song.max_constant or -1)
    region = item.region
    if region is not None:
        consts = [
            chart.jp_const
            for chart in region.charts.values()
            if chart.jp_const is not None
        ]
        return float(max(consts)) if consts else -1.0
    return -1.0


def _title_sort_key(trophy: "Trophy") -> tuple[int, int, int, str]:
    """称号候选排序键：颜色稀有度降序 → 层级 → 名字。"""
    from .titles import COLOR_ORDER, tier

    return (
        1 if trophy.color == "unknown" else 0,
        -COLOR_ORDER.get(trophy.color, 0),
        tier(trophy),
        trophy.name,
    )


def _is_character_title(trophy: Trophy, title_catalog: object) -> bool:
    """角色养成相关称号。

    日服限定称号没有结构化条件，角色 RANK 模板行只能从条件文本识别。
    """
    from .titles import TrophyCategory

    if title_catalog.category_of(trophy).category == TrophyCategory.CHARACTER_RANK:
        return True
    description = (trophy.description or "").upper().replace(" ", "")
    return "キャラクター" in description and "RANK200" in description


OUT_OF_RANGE_PAGE_HINT = "只有 {pages} 页，发 p1 ~ p{pages}"


def cooldown_hint(seconds: float) -> str:
    return f"查得有点快，请等 {seconds:.0f} 秒。"


@dataclass(frozen=True)
class Segment:
    """消息段。

    这是「要发出去的到底是什么」的**框架无关**表示：
    NoneBot 侧把它转成 ``MessageSegment``，测试页把它渲染成聊天气泡。
    两边用同一个结构，就不会出现「页面看着对、群里发出来不一样」。
    """

    type: Literal["text", "image"]
    text: str | None = None
    image_url: str | None = None
    image_data_uri: str | None = None

    @property
    def image_payload(self) -> str:
        """图片段的实际载荷：优先内联数据，否则用地址。"""
        return self.image_data_uri or self.image_url or ""


@dataclass
class Reply:
    """一次命令调用的结果。

    ``texts`` 是要**依次发出**的消息；只有一条时才把曲绘一并带上
    （分页时配图会错位，也没必要）。用 :meth:`messages` 拿到最终的消息段结构。
    """

    kind: ReplyKind
    texts: list[str] = field(default_factory=list)
    jacket: JacketImage | None = None
    #: 整条回复渲染成一张 PNG（帮助卡等）；有值时只发图片，texts 仅作回退/日志
    card_png: bytes | None = None
    #: 唯一命中的曲目（详情时有值）
    song: Song | None = None
    #: 命中的称号（称号详情时有值）
    trophy: Trophy | None = None
    #: 命中数量（候选/称号候选时有值）
    matched_count: int = 0
    #: 耗时，便于测试页展示
    elapsed_ms: float = 0.0
    #: 两步式查询：多选时列出的候选（序号从 1 开始，与正文里的编号一一对应）
    candidates: list[SelectionItem] = field(default_factory=list)
    #: 曲绘是否单独发一条（QQ 里图文混排的图片会显得小）
    separate_jacket: bool = False
    #: 其它给调试用的信息
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(self.texts)

    @property
    def char_count(self) -> int:
        return sum(len(t) for t in self.texts)

    @property
    def is_error(self) -> bool:
        return self.kind == "error"

    @property
    def is_single_message(self) -> bool:
        return len(self.texts) <= 1

    def messages(self) -> list[list[Segment]]:
        """最终要依次发出的消息，每条由其消息段组成。

        **这是「发几条、每条带什么」的唯一事实来源**：
        NoneBot 插件按它构造 ``Message``，本地测试页按它渲染聊天气泡。

        规则很简单，但必须只有一处：

        * 有卡片图 → 只发这一张图（文字仅作渲染失败时的回退）；
        * 只有一条正文 → 一条消息（正文 + 曲绘，如果有）；
        * 多条正文（分页）→ 每条正文各成一条消息，且**不带**曲绘。
        """
        if self.card_png is not None:
            encoded = base64.b64encode(self.card_png).decode("ascii")
            return [
                [
                    Segment(
                        type="image",
                        image_data_uri=f"data:image/png;base64,{encoded}",
                    )
                ]
            ]

        if not self.texts:
            return []

        if self.is_single_message:
            segments = [Segment(type="text", text=self.texts[0])]
            if self.jacket is not None:
                image = Segment(
                    type="image",
                    image_url=self.jacket.url,
                    image_data_uri=self.jacket.data_uri if self.jacket.has_data else None,
                )
                if self.separate_jacket:
                    return [segments, [image]]
                segments.append(image)
            return [segments]

        return [[Segment(type="text", text=text)] for text in self.texts]


def _paginate(text: str, settings: BotSettings) -> list[str]:
    return split_message(text, settings.max_message_length)


#: 日服曲绘的内存缓存（同一首重复查询不再下载）
_JP_JACKET_CACHE: dict[str, bytes] = {}


async def _jp_related(
    service: SongService, settings: BotSettings, song: "RegionSong"
) -> list[Trophy]:
    """日服独有曲目的相关称号（按曲名反查，拿不到就不带）。"""
    try:
        titles = await service.titles()
        return titles.for_song_title(song.title)
    except Exception as exc:  # noqa: BLE001 - 称号是增强信息
        logger.warning("日服曲目称号反查失败 %s: %s", song.title, exc)
        return []


async def _jp_jacket(
    service: SongService, settings: BotSettings, song: "RegionSong"
) -> JacketImage | None:
    """日服独有曲目的曲绘。

    国服资源库没有这些歌，所以另用 db.chunirec.net 的图
    （``lmg/music/jkt/{img}.jpg``，190×190）。``base64`` 模式下由机器人下载内联。
    """
    base = getattr(settings, "jp_jacket_base", "") or ""
    if not settings.jacket_enabled or not base or not song.jp_img:
        return None

    name = song.jp_img if song.jp_img.lower().endswith(".jpg") else f"{song.jp_img}.jpg"
    url = f"{base.rstrip('/')}/{name}"
    song_id = song.jp_idx or 0

    # 直接把这个 URL 丢给 QQ 是不行的：unidb 对**没有 Referer 的请求**统一返回占位图
    # （就是那张粉紫色「呪文」）。所以日服曲绘一律由机器人带 Referer 下载后内联。
    cached = _JP_JACKET_CACHE.get(song.jp_img)
    if cached is not None:
        return JacketImage(song_id=song_id, url=url, data=cached)

    try:
        data = await service.client.download(
            url, max_bytes=2_000_000, headers={"Referer": "https://db.chunirec.net/"}
        )
    except Exception as exc:  # noqa: BLE001 - 取图失败就干脆不配图，别发占位图
        logger.warning("日服曲绘下载失败 %s: %s", song.title, exc)
        return None

    if len(_JP_JACKET_CACHE) < 512:
        _JP_JACKET_CACHE[song.jp_img] = data
    return JacketImage(song_id=song_id, url=url, data=data)


def _is_we_only(song: Song) -> bool:
    """只由 WORLD'S END 谱面组成的曲目（111 首同名 WE 版本）。"""
    charts = song.charts_by_descending_constant()
    return bool(charts) and all(
        chart.difficulty is LevelIndex.WORLDS_END for chart in charts
    )


def _song_item(
    song: Song,
    catalog: "SongCatalog",
    *,
    author: bool = False,
    author_level: str = "",
) -> SelectionItem:
    """候选条目：``label`` 是曲名，``detail`` 是最难难度的标级/定数（WE 不算）。"""
    detail = ""
    # WE 谱面不进候选摘要（没有定数、平时不看）
    normal = [
        chart
        for chart in song.charts_by_descending_constant()
        if chart.difficulty is not LevelIndex.WORLDS_END
    ]
    if normal:
        detail = format_difficulty(normal[0])
    return SelectionItem(
        kind="song",
        label=song.title,
        detail=detail,
        song=song,
        author=author,
        author_level=author_level,
    )


def _region_item(
    song: object, *, author: bool = False, author_level: str = ""
) -> SelectionItem:
    """日服独有曲目的候选条目。"""
    artist = getattr(song, "artist", "") or "—"
    return SelectionItem(
        kind="region",
        label=song.title,
        detail=str(artist),
        region=song,
        author=author,
        author_level=author_level,
    )


def _trophy_item(trophy: Trophy, title_catalog: object) -> SelectionItem:
    """把称号变成候选条目：一行「颜色 + 名字」，不带类别（省一行也省噪音）。"""
    return SelectionItem(
        kind="title",
        label=f"[{trophy.color_label}] {trophy.name}",
        trophy=trophy,
    )


def _title_item(hit: object) -> SelectionItem:
    """称号候选条目：名字 + 分类。"""
    trophy = hit.trophy
    return SelectionItem(
        kind="title",
        label=f"[{trophy.color_label}] {trophy.name}",
        trophy=trophy,
    )


# --------------------------------------------------------------------------- #
# /查歌
# --------------------------------------------------------------------------- #


async def run_lookup(
    service: SongService,
    settings: BotSettings,
    keyword: str,
    *,
    want_notes: bool = False,
) -> Reply:
    """搜歌并把结果渲染成回复。

    与插件里的顺序保持一致：先取曲库、再找歌；找不到给提示，命中多首给候选，
    唯一命中给详情（或物量），详情场景附带曲绘。
    """
    started = time.perf_counter()
    keyword, exact = split_exact(keyword)

    if not keyword.strip():
        return Reply(kind="hint", texts=[EMPTY_KEYWORD_HINT])

    # 只包住可能失败的服务调用——渲染与后续步骤不受影响
    try:
        catalog = await service.catalog()
        result = await service.find(
            keyword, limit=settings.search_max_results, exact=exact
        )
    except SongNotFoundError:
        # 显式 ID 在国服不存在 → 可能是一首日服独有的曲目
        jp_reply = await _lookup_jp_only(
            service,
            settings,
            keyword,
            exact=exact,
            elapsed=(time.perf_counter() - started) * 1000,
        )
        if jp_reply is not None:
            return jp_reply
        return Reply(kind="hint", texts=[MISS_HINT])
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001 - 未预期错误统一兜底
        logger.exception("查歌失败 keyword=%r", keyword)
        return Reply(kind="error", texts=[GENERIC_ERROR])

    elapsed = (time.perf_counter() - started) * 1000

    # 国服恰好有同号曲目时，纯数字仍可能是日服独有曲目的别名。
    # 别名的意图更明确，所以优先出别名那首；#123 仍显式查 ID。
    if result.source == "id" and keyword.strip().isdecimal():
        jp_alias = await _lookup_jp_only(
            service,
            settings,
            keyword,
            exact=exact,
            aliases_only=True,
            elapsed=(time.perf_counter() - started) * 1000,
        )
        if jp_alias is not None:
            return jp_alias

    if result.is_empty:
        # 国服没有 → 看看是不是日服独有的曲目
        jp_reply = await _lookup_jp_only(
            service,
            settings,
            keyword,
            exact=exact,
            elapsed=(time.perf_counter() - started) * 1000,
        )
        if jp_reply is not None:
            return jp_reply
        return Reply(kind="hint", texts=[MISS_HINT])

    if not result.is_unique:
        # WE 版本不进候选（和原曲同名，只是多一行噪音）
        matches = [match for match in result.matches if not _is_we_only(match.song)]
        if not matches:
            matches = list(result.matches)
        items = [_song_item(match.song, catalog) for match in matches]
        # 别名命中多首时按难度降序，先看到最难的那首
        items.sort(key=_song_sort_key, reverse=True)
        text = render_candidate_page(
            keyword,
            items,
            page=0,
            per_page=settings.search_result_limit,
            unit="首",
        )
        return Reply(
            kind="candidates",
            texts=_paginate(text, settings),
            matched_count=len(result.matches),
            candidates=items,
            elapsed_ms=elapsed,
        )

    song = result.require_song()
    return await _song_detail_reply(
        service, settings, song, elapsed=elapsed, want_notes=want_notes
    )


async def _song_detail_reply(
    service: SongService,
    settings: BotSettings,
    song: Song,
    *,
    elapsed: float = 0.0,
    want_notes: bool = False,
) -> Reply:
    """唯一命中一首歌时的回复（详情 / 物量）。

    单独抽出来，是因为两步式查询里「用户回序号选中某一首」也要走同一套渲染。
    """
    if want_notes:
        # 物量是纯数据视图，不配图
        return Reply(
            kind="notes",
            texts=[render_notes(song, await service.catalog())],
            song=song,
            elapsed_ms=elapsed,
        )

    catalog = await service.catalog()

    # 相关特殊称号：称号库拿不到时详情照常出，只是不带称号行
    related: list[Trophy] = []
    try:
        # for_song 默认已排除：常规 AJ、角色养成、版本全完成称号
        related = (await service.titles()).for_song(song.id)
    except Exception as exc:  # noqa: BLE001 - 称号是增强信息，不是必需品
        logger.warning("读取称号库失败，详情不含称号: %s", exc)

    # 国服/日服定数对照：数据缺失时为 None，详情照常
    region = None
    try:
        region_catalog = await service.regions()
        if region_catalog is not None:
            region = region_catalog.for_cn_id(song.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取区域对照失败: %s", exc)

    jacket: JacketImage | None = None
    if settings.jacket_enabled:
        try:
            jacket = await service.jacket(song.id)
        except Exception as exc:  # noqa: BLE001 - 图片失败不影响文字
            logger.warning("获取曲绘失败 song_id=%s: %s", song.id, exc)

    return Reply(
        kind="detail",
        texts=[
            render_song_detail(
                song,
                catalog,
                settings=settings,
                related=related,
                region=region,
            )
        ],
        jacket=jacket,
        separate_jacket=settings.jacket_separate,
        song=song,
        elapsed_ms=elapsed,
        extra={"related_titles": len(related)},
    )


async def _lookup_jp_only(
    service: SongService,
    settings: BotSettings,
    keyword: str,
    *,
    exact: bool = False,
    aliases_only: bool = False,
    elapsed: float,
) -> Reply | None:
    """国服搜不到时，尝试在**日服独有**曲目里找。

    日服曲库比国服大（实测多 277 首普通曲），用户想查的歌有可能只在日服。
    这类曲目没有国服 ID，但**日服自己有谱面 ID**（与国服同一套编号），
    所以支持 ``#123`` 这种按 ID 查，详情里也照常显示 ``#ID``。

    :param aliases_only: 只返回数字别名的命中；没有就返回 ``None``。
        用于「国服恰好有同号 ID」时仍让纯数字别名优先。
    """
    try:
        region_catalog = await service.regions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取区域对照失败: %s", exc)
        return None
    if region_catalog is None:
        return None

    raw = keyword.strip()
    id_text = raw[1:].strip() if raw.startswith("#") else raw

    # --- 纯数字别名优先于谱面 ID（ID 仍可用 #123 显式指定）---
    alias_hits = (
        region_catalog.find_alias_exact(raw, jp_only=True) if raw.isdecimal() else []
    )
    if alias_hits:
        if len(alias_hits) == 1:
            song = alias_hits[0]
            return Reply(
                kind="region_detail",
                texts=[render_region_song_detail(song, related=await _jp_related(service, settings, song), title_limit=settings.related_title_limit)],
                jacket=await _jp_jacket(service, settings, song),
                separate_jacket=settings.jacket_separate,
                matched_count=1,
                elapsed_ms=elapsed,
                extra={"regions": song.regions, "kind": song.kind},
            )
        items = [_region_item(song) for song in alias_hits]
        return Reply(
            kind="region_candidates",
            texts=_paginate(
                render_candidate_page(
                    keyword,
                    items,
                    page=0,
                    per_page=settings.search_result_limit,
                    unit="首（仅日服）",
                ),
                settings,
            ),
            matched_count=len(alias_hits),
            candidates=items,
            elapsed_ms=elapsed,
        )
    if aliases_only:
        return None

    # --- 按谱面 ID 查（日服独有的曲目也能用 #ID）---
    if id_text.isdigit():
        by_id = region_catalog.find_by_id(int(id_text))
        if by_id is not None:
            return Reply(
                kind="region_detail",
                texts=[render_region_song_detail(by_id, related=await _jp_related(service, settings, by_id), title_limit=settings.related_title_limit)],
                jacket=await _jp_jacket(service, settings, by_id),
                separate_jacket=settings.jacket_separate,
                matched_count=1,
                elapsed_ms=elapsed,
                extra={"regions": by_id.regions, "kind": by_id.kind},
            )

    if exact:
        # 精准模式下不做模糊：只认曲名 / 别名完全相等的
        exact_hits = region_catalog.find_exact(keyword, jp_only=True)
        if len(exact_hits) == 1:
            song = exact_hits[0]
            return Reply(
                kind="region_detail",
                texts=[render_region_song_detail(song)],
                jacket=await _jp_jacket(service, settings, song),
                separate_jacket=settings.jacket_separate,
                matched_count=1,
                elapsed_ms=elapsed,
                extra={"regions": song.regions, "kind": song.kind},
            )
        return None

    hits = region_catalog.find(keyword, jp_only=True, limit=10)
    if not hits:
        return None

    if len(hits) == 1:
        song = hits[0]
        return Reply(
            kind="region_detail",
            texts=[render_region_song_detail(song, related=await _jp_related(service, settings, song), title_limit=settings.related_title_limit)],
            jacket=await _jp_jacket(service, settings, song),
            separate_jacket=settings.jacket_separate,
            matched_count=1,
            elapsed_ms=elapsed,
            extra={"regions": song.regions, "kind": song.kind},
        )

    # 完全同名（或同别名）只可能是一首 → 直接出详情
    exact_hits = region_catalog.find_exact(keyword, jp_only=True)
    if len(exact_hits) == 1:
        song = exact_hits[0]
        return Reply(
            kind="region_detail",
            texts=[render_region_song_detail(song, related=await _jp_related(service, settings, song), title_limit=settings.related_title_limit)],
            jacket=await _jp_jacket(service, settings, song),
            separate_jacket=settings.jacket_separate,
            matched_count=1,
            elapsed_ms=elapsed,
            extra={"regions": song.regions, "kind": song.kind},
        )

    items = [_region_item(song) for song in hits]
    return Reply(
        kind="region_candidates",
        texts=_paginate(
            render_candidate_page(
                keyword,
                items,
                page=0,
                per_page=settings.search_result_limit,
                unit="首（仅日服）",
            ),
            settings,
        ),
        matched_count=len(hits),
        candidates=items,
        elapsed_ms=elapsed,
    )


# --------------------------------------------------------------------------- #
# /添加别名
# --------------------------------------------------------------------------- #

ADD_ALIAS_HINT = (
    "用法：/添加别名 <#ID|歌名> <别名>\n"
    "例：/添加别名 #2802 黑味增 / 跨神话\n"
    "一次最多 5 个；别名之间用空格、/ 或逗号分隔。"
)

#: 单条别名长度上限（与导入脚本一致）
_ALIAS_MAX_LENGTH = 40
#: 一次投稿的别名数量上限
_ALIAS_MAX_PER_REQUEST = 5
_ALIAS_SPLIT = re.compile(r"[\s\u3000、，,/／|]+")
_ALIAS_TARGET_SEPARATORS = ("→", "=", "＝")


@dataclass(frozen=True)
class _AliasTarget:
    identity: tuple[str, int]
    label: str
    title: str
    song: Song | None = None
    region: RegionSong | None = None
    existing: tuple[str, ...] = ()


def _cn_alias_target(song: Song, catalog: Any) -> _AliasTarget:
    return _AliasTarget(
        identity=("cn", song.id),
        label=f"#{song.id} {song.title}",
        title=song.title,
        song=song,
        existing=tuple(catalog.aliases_of(song.id)),
    )


def _jp_alias_target(song: RegionSong) -> _AliasTarget:
    number = song.jp_idx
    prefix = f"#{number} " if number is not None else ""
    return _AliasTarget(
        identity=("jp", number if number is not None else -1),
        label=f"{prefix}{song.title}（仅日服）",
        title=song.title,
        region=song,
        existing=tuple(song.aliases),
    )


async def _resolve_alias_target(
    service: SongService, text: str
) -> tuple[_AliasTarget | None, str]:
    """把 ID / 曲名 / 别名解析成唯一曲目。"""
    raw = text.strip()
    if not raw:
        return None, "缺少曲目"

    catalog = await service.catalog()
    region_catalog = await service.regions()

    id_text = raw[1:].strip() if raw.startswith("#") else raw
    if id_text.isdigit():
        song = catalog.get(int(id_text))
        if song is not None:
            return _cn_alias_target(song, catalog), ""
        if region_catalog is not None:
            region = region_catalog.find_by_id(int(id_text))
            if region is not None and region.jp_idx is not None:
                if region.cn_id is not None:
                    cn_song = catalog.get(region.cn_id)
                    if cn_song is not None:
                        return _cn_alias_target(cn_song, catalog), ""
                return _jp_alias_target(region), ""
        if raw.startswith("#"):
            return None, f"曲库里没有 ID {id_text}"

    index = await service.search_index()
    cn_exact = index.search_exact(raw)
    jp_exact = (
        region_catalog.find_exact(raw, jp_only=True) if region_catalog is not None else []
    )
    if len(cn_exact) == 1 and not jp_exact:
        return _cn_alias_target(cn_exact[0].song, catalog), ""
    if not cn_exact and len(jp_exact) == 1:
        return _jp_alias_target(jp_exact[0]), ""
    if len(cn_exact) + len(jp_exact) > 1:
        return None, f"「{raw}」命中多首，请用 #ID 指定"

    result = await service.find(raw)
    if result.is_unique and result.song is not None:
        return _cn_alias_target(result.song, catalog), ""
    if region_catalog is not None:
        jp_hits = region_catalog.find(raw, jp_only=True, limit=10)
        if len(jp_hits) == 1:
            return _jp_alias_target(jp_hits[0]), ""
    return None, f"没找到「{raw}」"


async def _alias_owners(
    service: SongService,
) -> dict[str, tuple[tuple[str, int], str]]:
    """归一化别名 → (曲目标识, 展示名)，用于投稿前的冲突检查。"""
    owners: dict[str, tuple[tuple[str, int], str]] = {}

    catalog = await service.catalog()
    for song in catalog.songs:
        identity = ("cn", song.id)
        label = f"#{song.id} {song.title}"
        for alias in catalog.aliases_of(song.id):
            key = normalize(alias)
            if key:
                owners.setdefault(key, (identity, label))

    region_catalog = await service.regions()
    if region_catalog is not None:
        for song in region_catalog.songs:
            if song.jp_idx is None:
                continue
            identity = ("jp", song.jp_idx)
            label = f"#{song.jp_idx} {song.title}（仅日服）"
            for alias in song.aliases:
                key = normalize(alias)
                if key:
                    owners.setdefault(key, (identity, label))
    return owners


def _split_alias_values(text: str) -> list[str]:
    return [part for part in _ALIAS_SPLIT.split(text or "") if part]


async def run_add_alias(service: SongService, text: str) -> Reply:
    """``/添加别名``：解析目标 → 校验 → 写入本地 overlay → 刷新内存索引。"""
    raw = (text or "").strip()
    if not raw:
        return Reply(kind="hint", texts=[ADD_ALIAS_HINT])

    target: _AliasTarget | None = None
    raw_aliases: list[str] = []

    for separator in _ALIAS_TARGET_SEPARATORS:
        if separator in raw:
            target_text, rest = raw.split(separator, 1)
            target, error = await _resolve_alias_target(service, target_text.strip())
            if target is None:
                return Reply(kind="hint", texts=[error, ADD_ALIAS_HINT])
            raw_aliases = _split_alias_values(rest)
            break
    else:
        tokens = raw.split()
        if len(tokens) < 2:
            return Reply(kind="hint", texts=[ADD_ALIAS_HINT])
        # 曲名可能带空格：从长到短尝试，找到能唯一解析的前缀
        for end in range(len(tokens) - 1, 0, -1):
            candidate = " ".join(tokens[:end])
            resolved, _error = await _resolve_alias_target(service, candidate)
            if resolved is not None:
                target = resolved
                raw_aliases = _split_alias_values(" ".join(tokens[end:]))
                break
        if target is None:
            return Reply(kind="hint", texts=["没找到要添加别名的曲目。", ADD_ALIAS_HINT])

    if not raw_aliases:
        return Reply(kind="hint", texts=[ADD_ALIAS_HINT])

    title_key = normalize(target.title)
    cleaned: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for value in raw_aliases:
        alias = value.strip()
        key = normalize(alias)
        if not alias or not key:
            continue
        if len(alias) > _ALIAS_MAX_LENGTH:
            rejected.append(f"「{alias}」太长（上限 {_ALIAS_MAX_LENGTH} 字）")
            continue
        if len(alias) == 1 and not alias.isascii():
            rejected.append(f"「{alias}」是单字，太容易误命中")
            continue
        if key == title_key:
            rejected.append(f"「{alias}」与曲名相同")
            continue
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(alias)

    if len(cleaned) > _ALIAS_MAX_PER_REQUEST:
        return Reply(
            kind="hint",
            texts=[f"一次最多添加 {_ALIAS_MAX_PER_REQUEST} 个别名。"],
        )
    if not cleaned:
        return Reply(kind="hint", texts=rejected + [ADD_ALIAS_HINT])

    existing_keys = {normalize(alias) for alias in target.existing}
    owners = await _alias_owners(service)
    accepted: list[str] = []
    skipped: list[str] = []
    for alias in cleaned:
        key = normalize(alias)
        if key in existing_keys:
            skipped.append(f"「{alias}」已经是它的别名")
            continue
        owner = owners.get(key)
        if owner is not None and owner[0] != target.identity:
            skipped.append(f"「{alias}」已被 {owner[1]} 使用")
            continue
        accepted.append(alias)

    if not accepted:
        return Reply(
            kind="hint",
            texts=[f"{target.label} 没有新增别名。", *skipped],
        )

    try:
        if target.song is not None:
            added = await service.add_aliases(target.song, accepted)
        elif target.region is not None:
            added = await service.add_region_aliases(target.region, accepted)
        else:  # pragma: no cover - _AliasTarget 保证二者有其一
            added = []
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])

    if not added:
        return Reply(kind="hint", texts=[f"{target.label} 没有新增别名。", *skipped])

    lines = [f"已给 {target.label} 添加别名：{' / '.join(added)}"]
    lines.append(f"现在可以直接发 /{added[0]} 查这首歌。")
    if any(alias.isdecimal() for alias in added):
        lines.append("纯数字别名会优先按别名查询；回候选序号时请用 #数字（如 #1）。")
    if skipped:
        lines.append("未添加：\n" + "\n".join(f"· {reason}" for reason in skipped))
    return Reply(kind="alias", texts=lines, song=target.song)


# --------------------------------------------------------------------------- #
# /称号
# --------------------------------------------------------------------------- #


async def run_title(service: SongService, settings: BotSettings, keyword: str) -> Reply:
    """查称号。

    * 无参数 → 用法提示（``/称号条件`` 已覆盖按条件检索）
    * 唯一命中 → 称号详情（颜色、分类、条件、关联曲目）
    * 命中多首 → 候选列表
    """
    started = time.perf_counter()

    try:
        title_catalog = await service.titles()
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("读取称号库失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    keyword, exact = split_exact(keyword)

    if not keyword.strip():
        # 不带参数不再给分类菜单（/称号条件 已经完全覆盖了这个用途）
        return Reply(
            kind="hint",
            texts=["/称号 <关键词> 查称号 · /称号条件 <条件> 按条件查 · /随机称号"],
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    if exact:
        exact_hits = title_catalog.search_exact(keyword)
        if len(exact_hits) == 1:
            return Reply(
                kind="title",
                texts=[render_title_detail(exact_hits[0], title_catalog)],
                trophy=exact_hits[0],
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        if exact_hits:
            items = [_trophy_item(t, title_catalog) for t in exact_hits]
            items.sort(key=lambda item: _title_sort_key(item.trophy), reverse=False)
            return Reply(
                kind="title_candidates",
                texts=_paginate(
                    render_candidate_page(
                        keyword,
                        items,
                        page=0,
                        per_page=settings.search_result_limit,
                        unit="个称号",
                    ),
                    settings,
                ),
                matched_count=len(items),
                candidates=items,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        return Reply(kind="hint", texts=[MISS_TITLE_HINT])

    hits = title_catalog.search(keyword, limit=settings.search_max_results)
    if not hits:
        return Reply(kind="hint", texts=[MISS_TITLE_HINT])

    best = title_catalog.find(keyword)
    elapsed = (time.perf_counter() - started) * 1000
    if best is not None:
        return Reply(
            kind="title",
            texts=[render_title_detail(best.trophy, title_catalog)],
            trophy=best.trophy,
            elapsed_ms=elapsed,
        )

    items = [_title_item(hit) for hit in hits]
    # 别名命中多个称号时按稀有度降序
    items.sort(key=lambda item: _title_sort_key(item.trophy))
    return Reply(
        kind="title_candidates",
        texts=_paginate(
            render_candidate_page(
                keyword,
                items,
                page=0,
                per_page=settings.search_result_limit,
                unit="个称号",
            ),
            settings,
        ),
        matched_count=len(hits),
        candidates=items,
        elapsed_ms=elapsed,
    )


# --------------------------------------------------------------------------- #
# 两步式查询
# --------------------------------------------------------------------------- #


async def _has_numeric_alias(service: SongService, raw: str) -> bool:
    """纯数字输入是否精确命中某首歌的别名。

    数字同时可能是曲目 ID、候选序号、日服谱面 ID；别名是用户
    最明确的叫法，所以命中别名时应当优先查询而不是当成序号。
    """
    if not raw.isdecimal():
        return False
    try:
        index = await service.search_index()
        if index.search_alias_exact(raw):
            return True
        region_catalog = await service.regions()
    except Exception as exc:  # noqa: BLE001 - 别名检查失败不该影响选序号
        logger.warning("检查数字别名失败: %s", exc)
        return False
    return bool(
        region_catalog is not None
        and region_catalog.find_alias_exact(raw, jp_only=True)
    )


async def _handle_pending(
    service: SongService,
    settings: BotSettings,
    raw: str,
    *,
    selections: SelectionStore | None,
    key: str | None,
    prefer_alias: bool = False,
) -> Reply | None:
    """有挂起的候选时，看看这次输入是「翻页」「选第几个」还是「取消」。

    返回 ``None`` 表示不是这几件事，调用方继续走普通查询。
    :param prefer_alias: 纯数字精确命中别名时跳过序号的解释。
        只给查歌入口用——称号候选没有别名，不能被曲目别名抢序号。
    """
    if selections is None or not key:
        return None
    pending = selections.peek(key)
    if pending is None:
        return None

    if is_cancel(raw):
        selections.clear(key)
        return Reply(kind="hint", texts=[CANCEL_HINT])

    # 页码直达：``p2`` 直接跳到第 2 页
    target = parse_page_target(raw)
    if target is not None:
        if not pending.goto(target):
            return Reply(
                kind="hint",
                texts=[OUT_OF_RANGE_PAGE_HINT.format(pages=pending.total_pages)],
            )
        return _candidate_page_reply(pending, settings)

    if is_next_page(raw) or is_prev_page(raw):
        moved = pending.next_page() if is_next_page(raw) else pending.prev_page()
        if not moved:
            return Reply(
                kind="hint",
                texts=["已经是第 1 页了" if pending.page == 0 else "已经是最后一页"],
            )
        return _candidate_page_reply(pending, settings)

    index = parse_selection_index(raw)
    if index is None:
        return None

    # 纯数字别名优先于序号；明确要选序号请用 #N。
    if prefer_alias and await _has_numeric_alias(service, raw):
        return None

    item = pending.pick(index)
    if item is None:
        return Reply(
            kind="hint",
            texts=[
                OUT_OF_RANGE_HINT.format(total=len(pending.page_items))
                + "\n（也可以发「下一页」翻页，或 #曲目ID 精确查）"
            ],
        )

    selections.clear(key)
    reply = await _resolve_selection(service, settings, item)
    return await _attach_song_selection(
        service, settings, reply, selections=selections, key=key
    )


async def _attach_song_selection(
    service: SongService,
    settings: BotSettings,
    reply: Reply,
    *,
    selections: SelectionStore | None,
    key: str | None,
) -> Reply:
    """称号详情后，把它的关联曲目挂成待选：回 ``1`` 直接查那首曲子。

    称号本身只能带出「曲名」文本，用户往往想顺手看谱面/物量，
    所以详情末尾提示一行「回 1 查看曲目」，并把候选存进同一个待选槽位。
    """
    if (
        selections is None
        or not key
        or reply.kind != "title"
        or reply.trophy is None
    ):
        return reply

    song_ids = reply.trophy.song_ids
    if not song_ids:
        return reply

    try:
        catalog = await service.catalog()
    except Exception as exc:  # noqa: BLE001 - 曲目只是增强信息
        logger.warning("读取曲库失败，无法挂曲目候选: %s", exc)
        return reply

    region_catalog = None
    try:
        region_catalog = await service.regions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取区域对照失败，日服曲目候选可能缺失: %s", exc)

    items: list[SelectionItem] = []
    for req in reply.trophy.required:
        for entry in req.songs:
            song = catalog.get(entry.id) if entry.id else None
            if song is not None:
                items.append(_song_item(song, catalog))
                continue
            # 日服独有曲目：先用谱面 ID 找，再退回曲名
            region_song = None
            if region_catalog is not None:
                if entry.id:
                    region_song = region_catalog.find_by_id(entry.id)
                if region_song is None and entry.title:
                    folded = entry.title.strip().casefold()
                    region_song = next(
                        (
                            candidate
                            for candidate in region_catalog.songs
                            if candidate.title.strip().casefold() == folded
                        ),
                        None,
                    )
            if region_song is not None:
                items.append(_region_item(region_song))
    if not items:
        return reply

    selections.remember(key, reply.trophy.name, items, per_page=settings.search_result_limit)
    hint = "回 1 查看曲目" if len(items) == 1 else f"回 1-{len(items)} 查看曲目"
    if reply.texts:
        reply.texts = [reply.texts[0] + "\n" + hint, *reply.texts[1:]]
    return reply


def _candidate_page_reply(pending: PendingSelection, settings: BotSettings) -> Reply:
    """按 ``pending`` 当前页重新渲染候选列表。"""
    kind: ReplyKind = (
        "title_candidates" if pending.items and pending.items[0].kind == "title" else "candidates"
    )
    unit = {"title": "个称号", "region": "首（仅日服）"}.get(
        pending.items[0].kind if pending.items else "", "首"
    )
    text = render_candidate_page(
        pending.keyword,
        pending.items,
        page=pending.page,
        per_page=pending.per_page,
        unit=unit,
    )
    return Reply(
        kind=kind,
        texts=_paginate(text, settings),
        matched_count=len(pending.items),
        candidates=pending.items,
    )


async def _resolve_selection(
    service: SongService, settings: BotSettings, item: SelectionItem
) -> Reply:
    """把用户选中的那一项渲染成回复。"""
    started = time.perf_counter()

    if item.author:
        return await _author_for_item(
            service, item, elapsed=(time.perf_counter() - started) * 1000
        )

    if item.song is not None:
        return await _song_detail_reply(
            service,
            settings,
            item.song,
            elapsed=(time.perf_counter() - started) * 1000,
        )

    if item.region is not None:
        return Reply(
            kind="region_detail",
            texts=[render_region_song_detail(item.region, related=await _jp_related(service, settings, item.region), title_limit=settings.related_title_limit)],
            jacket=await _jp_jacket(service, settings, item.region),
            separate_jacket=settings.jacket_separate,
            matched_count=1,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            extra={"regions": item.region.regions, "kind": item.region.kind},
        )

    if item.trophy is not None:
        title_catalog = await service.titles()
        return Reply(
            kind="title",
            texts=[render_title_detail(item.trophy, title_catalog)],
            trophy=item.trophy,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    return Reply(kind="hint", texts=[MISS_HINT])  # pragma: no cover - 不会发生


async def _author_for_item(
    service: SongService, item: SelectionItem, *, elapsed: float = 0.0
) -> Reply:
    """两步式查询的第二步：用户选中候选后，查这首在那个难度的谱师。"""
    level = LevelIndex.parse(item.author_level) if item.author_level else None

    if item.song is not None:
        region_catalog = None
        try:
            region_catalog = await service.regions()
        except Exception as exc:  # noqa: BLE001 - 并集只是补充信息
            logger.warning("读取区域对照失败，谱师可能缺少日服数据: %s", exc)
        region_song = None
        if region_catalog is not None:
            region_song = region_catalog.for_cn_id(item.song.id)
            if region_song is None:
                region_song = _find_region_song(region_catalog, item.song.title)
        picked = _merged_chart_for_level(item.song, region_song, level)
        if picked is None:
            return Reply(kind="hint", texts=["这首没有这个难度的谱面"], song=item.song)
        short, designer = picked
        return _author_reply(item.song, short, designer, elapsed)

    if item.region is not None:
        picked = _region_chart_for_level(item.region, level)
        if picked is None:
            return Reply(kind="hint", texts=["这首没有这个难度的谱面"])
        short, designer = picked
        return _author_reply(None, short, designer, elapsed, title=item.region.title)

    return Reply(kind="hint", texts=[MISS_HINT])  # pragma: no cover - 不会发生


async def run_query(
    service: SongService,
    settings: BotSettings,
    text: str,
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """查歌入口（支持两步式查询）。

    * 空输入（例如只 @ 了机器人）→ 回预载菜单，让人知道能干什么；
    * 若 ``key`` 有挂起的候选，且这次输入是范围内的序号 → 直接出那首的详情；
    * 否则正常搜索；命中多首时把候选记进 ``selections``，等用户回序号。
    """
    raw = (text or "").strip()


    picked = await _handle_pending(
        service, settings, raw, selections=selections, key=key, prefer_alias=True
    )
    if picked is not None:
        return picked

    reply = await run_lookup(service, settings, raw)

    if selections is not None and key:
        if reply.candidates:
            selections.remember(key, raw, reply.candidates, per_page=settings.search_result_limit)
        else:
            # 这一轮不是多选，旧候选作废
            selections.clear(key)
    return reply


async def run_title_query(
    service: SongService,
    settings: BotSettings,
    text: str,
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """查称号入口（同样支持两步式）。"""
    raw = (text or "").strip()

    picked = await _handle_pending(service, settings, raw, selections=selections, key=key)
    if picked is not None:
        return picked

    reply = await run_title(service, settings, raw)

    if selections is not None and key:
        if reply.candidates:
            selections.remember(key, raw, reply.candidates, per_page=settings.search_result_limit)
        else:
            selections.clear(key)
            # 称号详情 → 挂上关联曲目，回 1 就能看曲子
            reply = await _attach_song_selection(
                service, settings, reply, selections=selections, key=key
            )
    return reply


# --------------------------------------------------------------------------- #
# /称号条件、/随机称号
# --------------------------------------------------------------------------- #


async def run_title_condition(
    service: SongService,
    settings: BotSettings,
    keyword: str,
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """按**获得条件**检索称号。

    条件文本里写着曲名、难度、评级等，用户往往记得条件却记不住称号名。
    """
    started = time.perf_counter()
    raw = (keyword or "").strip()

    picked = await _handle_pending(service, settings, raw, selections=selections, key=key)
    if picked is not None:
        return picked

    if not raw:
        return Reply(
            kind="hint",
            texts=["发个条件关键词，例如 /称号条件 RANK SSS、/称号条件 ALL JUSTICE"],
        )

    try:
        title_catalog = await service.titles()
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("按条件检索称号失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    hits = title_catalog.search_condition(raw, limit=settings.search_max_results)
    if not hits:
        return Reply(kind="hint", texts=[MISS_TITLE_HINT])

    items = [_trophy_item(t, title_catalog) for t in hits]
    text = render_candidate_page(
        raw,
        items,
        page=0,
        per_page=settings.search_result_limit,
        unit="个称号",
    )
    if selections is not None and key:
        selections.remember(key, raw, items, per_page=settings.search_result_limit)

    return Reply(
        kind="title_candidates",
        texts=_paginate(text, settings),
        matched_count=len(items),
        candidates=items,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


async def run_rainbow_titles(
    service: SongService,
    settings: BotSettings,
    keyword: str = "",
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """列出所有非角色称号的特殊虹称号（隐藏命令，不写进帮助）。"""
    started = time.perf_counter()
    raw = (keyword or "").strip()

    picked = await _handle_pending(service, settings, raw, selections=selections, key=key)
    if picked is not None:
        return picked

    try:
        title_catalog = await service.titles()
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("读取称号库失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    trophies = sorted(
        (
            trophy
            for trophy in title_catalog.special
            if trophy.color == "rainbow" and not _is_character_title(trophy, title_catalog)
        ),
        key=_title_sort_key,
    )
    if not trophies:
        return Reply(kind="hint", texts=["没有特殊虹称号"])

    items = [_trophy_item(trophy, title_catalog) for trophy in trophies]
    body = render_title_list(
        trophies,
        title_catalog,
        header=f"特殊虹称号 · 共 {len(trophies)} 个（回序号看详情）",
        limit=len(trophies),
    )
    if selections is not None and key:
        selections.remember(key, "虹称号", items, per_page=len(items))

    return Reply(
        kind="title_candidates",
        texts=_paginate(body, settings),
        matched_count=len(items),
        candidates=items,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


#: 难度后缀 → LevelIndex（``/谁写的 四次元紫`` 里的「紫」）
_DIFFICULTY_SUFFIXES: tuple[tuple[tuple[str, ...], LevelIndex], ...] = (
    (("MASTER", "MAS", "紫"), LevelIndex.MASTER),
    (("EXPERT", "EXP", "赤", "红"), LevelIndex.EXPERT),
    (("ULTIMA", "ULT", "黑"), LevelIndex.ULTIMA),
    (("ADVANCED", "ADV", "黄"), LevelIndex.ADVANCED),
    (("BASIC", "BAS", "绿"), LevelIndex.BASIC),
)


def split_chart_difficulty(text: str) -> tuple[str, LevelIndex | None]:
    """把 ``四次元紫`` / ``四次元 MAS`` 拆成 ``("四次元", MASTER)``。

    空格可有可无；拆完如果剩下的关键词找不到歌，调用方应当退回原文。
    """
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return "", None
    upper = compact.upper()
    for suffixes, level in _DIFFICULTY_SUFFIXES:
        for suffix in suffixes:
            if len(compact) > len(suffix) and upper.endswith(suffix):
                return compact[: -len(suffix)], level
    return compact, None


def _merged_chart_for_level(
    song: Song, region_song, level: LevelIndex | None
) -> tuple[str, str] | None:
    """国服谱面 + 日服并集合并后的谱师。

    国服曲目经常只到 MASTER——ULTIMA 只在日服有，谱师也只记在并集里
    （``charts[..]["jp"]["designer"]``），只看国服数据会误报「没有这个难度」。
    不指定难度时取「定数最高且有谱师记录」的那个。
    """
    merged: dict[str, dict[str, object]] = {}
    for difficulty in song.difficulties:
        if difficulty.difficulty is LevelIndex.WORLDS_END:
            continue
        merged[difficulty.difficulty.short] = {
            "const": difficulty.level_value if difficulty.has_constant else None,
            "designer": (difficulty.note_designer or "").strip(),
        }
    if region_song is not None:
        for name, chart in getattr(region_song, "charts", {}).items():
            if name == "WE":
                continue
            slot = merged.get(name)
            if slot is None:
                slot = {"const": None, "designer": ""}
                merged[name] = slot
            if slot["const"] is None:
                slot["const"] = chart.jp_const if chart.jp_const is not None else chart.cn_const
            if not slot["designer"]:
                slot["designer"] = (chart.cn_designer or chart.jp_designer or "").strip()

    if not merged:
        return None
    if level is not None:
        slot = merged.get(level.short)
        if slot is None:
            return None
        return level.short, str(slot["designer"])
    ordered = sorted(
        merged.items(),
        key=lambda item: (item[1]["const"] is not None, item[1]["const"] or 0.0),
        reverse=True,
    )
    for name, slot in ordered:
        if slot["designer"]:
            return name, str(slot["designer"])
    name, slot = ordered[0]
    return name, str(slot["designer"])


async def run_chart_author(
    service: SongService,
    settings: BotSettings,
    text: str,
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """``/谁写的``：查某个难度的谱师（不给难度就取最高难度）。

    国服曲目用落雪的 ``note_designer``；日服独有曲目用 wiki「譜面制作者一覧」
    抓来的数据（并集里存在 ``charts[..]["jp"]["designer"]``）。

    命中多首时和查歌一样先列候选（回序号再出谱师）：候选记进同一个
    ``selections`` 槽位，所以「回 2」由查歌那条路径接住，选完直接出谱师。
    """
    started = time.perf_counter()
    raw = (text or "").strip()
    if not raw:
        return Reply(
            kind="hint",
            texts=["发「歌名 + 难度」，例如 /谁写的 四次元紫、/谁写的 四次元MAS"],
        )

    keyword, level = split_chart_difficulty(raw)
    # (关键词, 难度)：先按「拆掉难度后缀」的结果查，查不到再按原文查一次
    attempts: list[tuple[str, LevelIndex | None]] = [(keyword, level)]
    if (keyword, level) != (raw, None):
        attempts.append((raw, None))

    level_short = level.short if level is not None else ""
    if selections is not None and key:
        # 新查询作废旧候选；命中多首时下面会重新记上
        selections.clear(key)

    try:
        region_catalog = await service.regions()
        elapsed = (time.perf_counter() - started) * 1000

        for attempt_keyword, attempt_level in attempts:
            result = await service.find(attempt_keyword, limit=settings.search_result_limit)

            # ---- 国服多首命中：和查歌一样列候选，回序号再查谱师 ----
            if result.song is None and len(result.matches) > 1:
                catalog = await service.catalog()
                matches = [match for match in result.matches if not _is_we_only(match.song)]
                if not matches:
                    matches = list(result.matches)
                items = [
                    _song_item(
                        match.song,
                        catalog,
                        author=True,
                        author_level=level_short,
                    )
                    for match in matches
                ]
                items.sort(key=_song_sort_key, reverse=True)
                page_text = render_candidate_page(
                    attempt_keyword,
                    items,
                    page=0,
                    per_page=settings.search_result_limit,
                    unit="首",
                )
                # 候选是「查谱师」的半步，结尾提示换成看谱师
                page_text = page_text.replace("回序号看详情", "回序号看谱师")
                if selections is not None and key:
                    selections.remember(
                        key,
                        attempt_keyword,
                        items,
                        per_page=settings.search_result_limit,
                    )
                return Reply(
                    kind="candidates",
                    texts=_paginate(page_text, settings),
                    matched_count=len(result.matches),
                    candidates=items,
                    elapsed_ms=elapsed,
                )

            # ---- 国服曲目（难度/谱师缺记录时用日服并集补）----
            song = result.song or (result.matches[0].song if result.matches else None)
            if song is not None:
                region_song = None
                if region_catalog is not None:
                    region_song = region_catalog.for_cn_id(song.id)
                    if region_song is None:
                        region_song = _find_region_song(region_catalog, song.title)
                picked = _merged_chart_for_level(song, region_song, attempt_level)
                if picked is None:
                    return Reply(
                        kind="hint", texts=["这首没有这个难度的谱面"], song=song
                    )
                short, designer = picked
                return _author_reply(song, short, designer, elapsed)

            # ---- 日服独有曲目 ----
            region_song = _find_region_song(region_catalog, attempt_keyword, jp_only=True)
            if region_song is not None:
                picked = _region_chart_for_level(region_song, attempt_level)
                if picked is None:
                    return Reply(kind="hint", texts=["这首没有这个难度的谱面"])
                short, designer = picked
                return _author_reply(None, short, designer, elapsed, title=region_song.title)
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("查谱师失败 text=%r", raw)
        return Reply(kind="error", texts=[GENERIC_ERROR])

    return Reply(kind="hint", texts=[MISS_HINT])


def _author_reply(song, short: str, designer: str, elapsed: float, *, title: str = "") -> Reply:
    name = song.title if song is not None else title
    if not designer:
        return Reply(
            kind="hint",
            texts=[f"{name} {short} 没有谱师信息"],
            song=song,
            elapsed_ms=elapsed,
        )
    return Reply(
        kind="author",
        texts=[f"{designer}写的"],
        song=song,
        elapsed_ms=elapsed,
        extra={"difficulty": short, "designer": designer, "title": name},
    )


def _find_region_song(region_catalog, keyword: str, *, jp_only: bool = False):
    """按曲名或别名找并集里的日服曲目。

    和查歌共用 :meth:`RegionCatalog.find` 的排序（曲名精确 > 别名精确 >
    前缀 > 包含），否则 `/谁写的 左下爱` 这类「只记得别名」的日服曲目
    会死在只认曲名前缀的老实现上。
    """
    if region_catalog is None or not keyword:
        return None
    hits = region_catalog.find(keyword, jp_only=jp_only, limit=2)
    return hits[0] if hits else None


def _region_chart_for_level(region_song, level):
    """日服曲目：取指定难度的谱师；没指定就取最高难度。"""
    charts = sorted(
        [
            chart
            for chart in getattr(region_song, "charts", {}).values()
            if chart.name != "WE" and (chart.jp_const is not None or chart.jp_designer)
        ],
        key=lambda chart: chart.jp_const if chart.jp_const is not None else -1,
        reverse=True,
    )
    if not charts:
        return None
    if level is not None:
        chart = region_song.charts.get(level.short)
        if chart is None:
            return None
        if chart.jp_designer or chart.cn_designer:
            return level.short, (chart.jp_designer or chart.cn_designer)
        return level.short, ""
    # 没指定难度：从高到低找**第一个有谱师信息**的（否则默认会撞上「最难的谱面没记录」）
    for chart in charts:
        designer = chart.jp_designer or chart.cn_designer
        if designer:
            return chart.name, designer
    return charts[0].name, ""


async def run_random_title(
    service: SongService,
    settings: BotSettings,
    *,
    selections: SelectionStore | None = None,
    key: str | None = None,
) -> Reply:
    """随机抽一个特殊称号（同样支持「回 N 查看曲目」）。"""
    started = time.perf_counter()
    try:
        title_catalog = await service.titles()
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("随机称号失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    trophy = title_catalog.random_special()
    if trophy is None:  # pragma: no cover - 称号库为空
        return Reply(kind="hint", texts=["称号库还没准备好"])

    reply = Reply(
        kind="title",
        texts=[render_title_detail(trophy, title_catalog)],
        trophy=trophy,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    if selections is not None and key:
        selections.clear(key)
        reply = await _attach_song_selection(
            service, settings, reply, selections=selections, key=key
        )
    return reply


# --------------------------------------------------------------------------- #
# /帮助
# --------------------------------------------------------------------------- #


async def run_help(service: SongService, settings: BotSettings) -> Reply:
    """帮助就是命令表，不加载曲库；能出图时发一张卡片。"""
    return Reply(kind="help", texts=[HELP_TEXT], card_png=render_help_card())


# --------------------------------------------------------------------------- #
# /授权
# --------------------------------------------------------------------------- #


def _bound_user_text(record: Any) -> str:
    name = (getattr(record, "player_name", "") or "").strip()
    friend = (getattr(record, "friend_code", "") or "").strip()
    if name and friend:
        return f"{name}（好友码 {friend}）"
    return name or (f"好友码 {friend}" if friend else "落雪账号")


async def run_auth_start(
    service: SongService, settings: BotSettings, actor_id: str
) -> Reply:
    """发起落雪 OAuth 绑定（无回调模式，用户把授权码发回来）。"""
    if not (actor_id or "").strip():
        return Reply(kind="hint", texts=["没能识别你的账号，请稍后再试。"])

    existing = service.personal.status(actor_id)
    try:
        pending = await service.personal.start(actor_id)
    except ChunithmBotError as exc:
        return Reply(kind="hint", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("发起落雪授权失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    minutes = max(1, settings.lxns_oauth_pending_ttl // 60)
    lines: list[str] = []
    if existing is not None:
        lines.append(
            f"你已经绑定了 {_bound_user_text(existing)}；重新绑定会覆盖旧授权。"
        )
        lines.append("")
    lines.extend(
        [
            "【落雪账号绑定】",
            "1. 打开下面的链接，登录落雪并同意授权（只需 read_player）",
            pending.authorize_url,
            "2. 授权完成后，页面会显示一串一次性授权码（形如 JVJ6-VPTM-MGHZ）",
            "3. 私聊机器人发送：/授权 <授权码>",
            "",
            f"授权码相当于登录凭证，请不要发到群里；链接 {minutes} 分钟内有效。",
        ]
    )
    return Reply(kind="auth", texts=["\n".join(lines)])


async def run_auth_code(
    service: SongService, settings: BotSettings, actor_id: str, code: str
) -> Reply:
    """用授权页显示的一次性授权码完成绑定。"""
    if not (actor_id or "").strip():
        return Reply(kind="hint", texts=["没能识别你的账号，请稍后再试。"])
    if not (code or "").strip():
        return Reply(
            kind="hint",
            texts=["用法：/授权 <授权码>；还没开始的话先发 /授权 获取链接。"],
        )

    try:
        record = await service.personal.bind(actor_id, code)
    except ChunithmBotError as exc:
        return Reply(kind="error", texts=[exc.user_message])
    except Exception:  # noqa: BLE001
        logger.exception("提交落雪授权码失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    lines = [f"绑定成功：{_bound_user_text(record)}"]
    lines.append("以后查歌会显示你的各难度成绩，查称号会显示是否已获得。")
    lines.append("访问令牌会自动续期；想解绑发 /取消授权。")
    return Reply(kind="auth", texts=["\n".join(lines)])


async def run_auth_status(
    service: SongService, settings: BotSettings, actor_id: str
) -> Reply:
    """查看绑定状态（不显示任何令牌内容）。"""
    record = service.personal.status(actor_id) if actor_id else None
    if record is None:
        return Reply(
            kind="hint",
            texts=["还没有绑定落雪账号。发 /授权 开始绑定。"],
        )

    now = time.time()
    lines = ["【落雪账号绑定】", _bound_user_text(record)]
    if record.expires_at > now:
        minutes = max(1, round((record.expires_at - now) / 60))
        lines.append(f"访问令牌：有效（约 {minutes} 分钟后自动续期）")
    else:
        lines.append("访问令牌：已过期，下次查询时自动刷新")
    if record.refresh_expires_at:
        days = max(0, round((record.refresh_expires_at - now) / 86400))
        lines.append(f"刷新令牌：约剩 {days} 天")
    if record.scope:
        lines.append(f"权限：{record.scope}")
    lines.append("解绑：/取消授权")
    return Reply(kind="auth", texts=["\n".join(lines)])


async def run_auth_revoke(
    service: SongService, settings: BotSettings, actor_id: str
) -> Reply:
    """删除本机保存的授权。"""
    if not actor_id:
        return Reply(kind="hint", texts=["没能识别你的账号，请稍后再试。"])
    try:
        existed = await service.personal.revoke(actor_id)
    except Exception:  # noqa: BLE001
        logger.exception("解绑落雪账号失败")
        return Reply(kind="error", texts=[GENERIC_ERROR])

    if not existed:
        return Reply(kind="hint", texts=["你还没有绑定落雪账号。"])
    return Reply(
        kind="auth",
        texts=["已解绑落雪账号。以后查歌/查称号不会再附加个人数据。"],
    )
