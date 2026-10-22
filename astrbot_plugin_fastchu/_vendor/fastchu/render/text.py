"""轻量级文字渲染。

设计取向是**尽可能轻量**：

* 单条消息、少行数、短行宽（手机上一行不折行）；
* 只给真正会被用到的信息，**不打印 ``?`` 占位**——没有的字段直接不显示；
* 详情里跳过低难度的 BASIC / ADVANCED（查歌场景没人关心），最多展示 4 个难度；
* 列表每条压成一行，只留「曲名 + 最高难度定数 + 版本」这类去歧义信息；
* 不加装饰性分隔线与表情，避免刷屏。

超长内容（筛选结果、候选列表）交给 :func:`split_message` 分条发送。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import BotSettings, get_settings
from ..models import DIFFICULTY_LABELS, LevelIndex, Song, SongDifficulty, Trophy
from ..services.catalog import SongCatalog
from ..services.query import SongFilter
from ..services.search import SongMatch

if TYPE_CHECKING:  # 仅用于类型标注，避免运行时循环导入
    from ..services.regions import RegionSong
    from ..services.titles import TitleSearchHit, TrophyCatalog

__all__ = [
    "HELP_SECTIONS",
    "HELP_TEXT",
    "HelpItem",
    "HelpSection",
    "render_song_detail",
    "render_song_candidates",
    "render_filter_result",
    "render_notes",
    "render_help",
    "render_catalog_summary",
    "render_region_song_detail",
    "render_region_candidates",
    "render_title_detail",
    "render_title_candidates",
    "render_title_stats",
    "render_title_list",
    "split_message",
]

#: 详情里展示的难度：跳过低难度
#: 详情里展示的难度名（与 _DETAIL_LEVELS 对应）
_DETAIL_NAMES: tuple[str, ...] = ("EXP", "MAS", "ULT")

#: 详情只列这三级；WORLD'S END 不进谱面列表
_DETAIL_LEVELS: tuple[LevelIndex, ...] = (
    LevelIndex.EXPERT,
    LevelIndex.MASTER,
    LevelIndex.ULTIMA,
)

#: 块之间的空行。
#: 普通 QQ 号（NapCat）渲染纯文本时行距比「机器人卡片」紧，
#: 原来一个空行看起来挤成一团，这里改成两个空行补偿。
_BLOCK_SEP = "\n\n"

#: 作者名过长时截断
_ARTIST_MAX = 40
#: 列表里曲名过长时截断
_TITLE_MAX = 34
#: 称号条件过长时截断（一行别撑爆手机屏）
_CONDITION_MAX = 60
#: 曲目详情里称号条件单独占一行。**不做截断**——条件被砍掉一半比折行更糟
_RELATED_CONDITION_MAX = 200


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short_version(catalog: SongCatalog, version: int | None) -> str:
    """版本简称：去掉 ``CHUNITHM `` 前缀，省版面。"""
    if version is None:
        return "未知版本"
    title = catalog.version_title(version)
    return title.removeprefix("CHUNITHM ").strip() or f"v{version}"


def format_difficulty(difficulty: SongDifficulty) -> str:
    """``MAS 15.4``（直接用定数，不再用 ``15+(15.4)`` 这种括号写法）。

    WORLD'S END 没有数值定数，仍然写 ``WE ★3``。
    """
    short = difficulty.difficulty.short
    if difficulty.has_constant:
        return f"{short} {difficulty.constant_text}"
    return f"{short} {difficulty.display_level}"


def _difficulty_values(song: Song, region: "RegionSong | None" = None) -> list[str]:
    """每个难度一项（手机上逐行排，避免一行塞三个难度导致折行）。

    除了国服已实装的难度，还会补上**日服追加、国服还没有**的难度
    （例如 JP 先行实装的 ULTIMA），标注 ``（日服）``——
    否则这类谱面在查歌时永远看不到。
    """
    from ..services.regions import format_const

    values: list[str] = []
    for level in _DETAIL_LEVELS:
        name = DIFFICULTY_LABELS.get(level, "")
        chart = region.charts.get(name) if region is not None else None
        difficulty = song.chart(level)

        if difficulty is None:
            # 国服没有这个难度 → 看日服有没有
            if chart is not None and chart.jp_const is not None:
                const = format_const(chart.jp_const)
                if chart.jp_const_unknown:
                    const += "?"
                values.append(f"{chart.name} {const}（日服）")
            continue

        text = format_difficulty(difficulty)
        if chart is not None and difficulty.has_constant and chart.cn_const is not None:
            const = chart.const_display()
            if const and const != "—":
                text = f"{difficulty.difficulty.short} {const}"
        values.append(text)
    return values


def _difficulty_line(song: Song, region: "RegionSong | None" = None) -> str:
    """难度行。

    有区域对照时，**定数不同的难度会把两服的定数都标出来**，
    例如 ``MAS 15.5→15.6``；定数相同时保持简洁。
    """
    parts: list[str] = []
    for level in _DETAIL_LEVELS:
        difficulty = song.chart(level)
        if difficulty is None:
            continue

        text = format_difficulty(difficulty)
        # WE 用 ★ 难度、没有数值定数，绝不能因为区域对照又冒出 ``(0)``
        if region is not None and difficulty.has_constant:
            chart = region.charts.get(DIFFICULTY_LABELS.get(level, ""))
            if chart is not None and chart.cn_const is not None:
                const = chart.const_display()
                if const and const != "—":
                    # 与 format_difficulty 保持一致：只写「缩写 定数」
                    text = f"{difficulty.difficulty.short} {const}"
        parts.append(text)
    return " · ".join(parts)


def _notes_detail(
    pairs: list[tuple[str, int]], *, drop_zero: bool, separated: bool = False
) -> str:
    """明细部分：去掉「总物量」那一项（行首已经写过），只留分项。

    ``drop_zero`` 用于详情行——``FLICK0`` 这类零值在概览里是纯噪音；
    命令行 ``fastchu notes`` 这种聚焦数据的场景则保留零值（「这首没有 flick」本身是信息）。

    ``separated=True`` 时用 `` / `` 分隔（详情里更易读），否则用空格（省字数）。
    """
    items = [
        f"{label}{value}"
        for label, value in pairs
        if label != "总物量" and not (drop_zero and value == 0)
    ]
    return (" / " if separated else " ").join(items)


def _notes_line(
    song: Song,
    *,
    difficulty: SongDifficulty | None = None,
    mode: str = "full",
) -> str:
    """物量行。

    ``mode`` 三档：

    * ``off``     —— 不出这一行；
    * ``compact`` —— ``物量 MAS 1626``（只给总物量，排版清爽）；
    * ``full``    —— ``物量 MAS 1626（TAP893 / HOLD118 …）``
    """
    if mode == "off":
        return ""

    target = difficulty
    if target is None or target.notes is None:
        # WE 谱面不进列表，物量也只看 EXP/MAS/ULT
        target = next(
            (
                d
                for d in song.charts_by_descending_constant()
                if d.notes is not None and d.difficulty is not LevelIndex.WORLDS_END
            ),
            None,
        )
    if target is None or target.notes is None:
        return ""

    head = f"物量 {target.difficulty.short}"
    if target.notes.total is not None:
        head += f" {target.notes.total}"

    if mode != "full":
        return head

    detail = _notes_detail(target.notes.as_pairs(), drop_zero=True, separated=True)
    return f"{head}（{detail}）" if detail else head


# --------------------------------------------------------------------------- #
# 曲目详情
# --------------------------------------------------------------------------- #

#: 标签与值之间的间隔。
#:
#: 用**全角空格**而不是 ASCII 空格：QQ / 微信都是比例字体，
#: ``BPM`` 这类拉丁字母比两个汉字窄，按字符数补 ASCII 空格永远对不齐；
#: 而所有标签都是两个汉字、间隔是一个全角空格，任何字体下都齐。
_INFO_GAP = "\u3000"

#: 详情里信息行的标签都是两个汉字，否则对不齐（所以用「曲速」而不是 BPM）


def _info_line(label: str, value: str) -> str:
    """一行信息：``曲师　eoll``；多行值时续行用同样的缩进。"""
    indent = _INFO_GAP * (len(label) + 1)
    return f"{label}{_INFO_GAP}" + value.replace("\n", "\n" + indent)


def render_song_detail(
    song: Song,
    catalog: SongCatalog,
    *,
    settings: BotSettings | None = None,
    notes_mode: str | None = None,
    related: Sequence[Trophy] | None = None,
    region: "RegionSong | None" = None,
) -> str:
    """曲目详情。

    排版按「块」组织，块之间空一行：

    ::

        Crossmythos Rhapsodia
        原创 · VERSE · #2802

        曲师  Acotto vs Potwi
        BPM   198
        难度  EXP 14+(14.6) · MAS 15+(15.7)
        物量  MAS 3688（TAP1848 / HOLD452 / …）

        特殊称号
        [虹] 少女が夢見た空の果て — Crossmythos RhapsodiaのUNLOCK…

    :param notes_mode: ``off`` / ``compact`` / ``full``；``None`` 时跟随配置。
    :param related: 该曲涉及的特殊称号。常规 AJ、角色养成、版本全完成称号
        由 :meth:`TrophyCatalog.for_song` 预先排除。
    """
    settings = settings or get_settings()
    mode = notes_mode or settings.notes_mode

    blocks: list[str] = []

    # ---- 头部：#ID - 曲名 / 分类 · 版本 ----
    blocks.append(f"#{song.id} - {song.title}")
    head_meta = [part for part in (song.genre, short_version(catalog, song.version)) if part]
    blocks.append(" · ".join(head_meta))

    # ---- 信息块：标签对齐（全角标签 + 全角空格）----
    info: list[tuple[str, str]] = []
    if song.artist:
        info.append(("曲师", _shorten(str(song.artist), _ARTIST_MAX)))
    if song.bpm:
        info.append(("曲速", str(song.bpm)))
    if difficulty_values := _difficulty_values(song, region):
        # 每个难度一行，续行缩进由 _info_line 处理
        info.append(("难度", "\n".join(difficulty_values)))
    if notes_line := _notes_line(song, mode=mode):
        info.append(
            ("物量", notes_line[len("物量 ") :] if notes_line.startswith("物量 ") else notes_line)
        )

    if info:
        blocks.append("\n".join(_info_line(label, value) for label, value in info))

    # ---- 特殊称号块 ----
    if related:
        blocks.append(
            "\n".join(
                _related_title_lines(
                    related,
                    song.title,
                    limit=settings.related_title_limit,
                )
            )
        )

    return _BLOCK_SEP.join(blocks)


def _title_tag(trophy: Trophy) -> str:
    """称号的紧凑标签：``[金] パカラー``。"""
    return f"[{trophy.color_label}] {trophy.name}"


def _related_title_lines(
    related: Sequence[Trophy],
    song_title: str,
    *,
    limit: int = 8,
) -> list[str]:
    """相关特殊称号：**每个称号一行**，带颜色标记与所需条件。

    输出形如::

        特殊称号 2：
        [究极] ジングルベル · ULTIMA/ALL JUSTICE達成
        [银] シングルヘル · EXPERT/1人/RANK SSS達成

    条件里的曲名前缀会被裁掉（在曲目详情里是冗余信息），单行过长再截断。
    """
    from ..services.titles import condition_text_for_song

    shown = list(related[:limit])
    if not shown:  # pragma: no cover - 调用方已判空
        return []

    rendered = [
        f"· {_title_tag(trophy)}\n"
        f"  {_shorten(condition_text_for_song(trophy, song_title), _RELATED_CONDITION_MAX)}"
        for trophy in shown
    ]

    if len(shown) == 1:
        return [f"特殊称号\n{rendered[0]}"]

    lines = [f"特殊称号 {len(related)}"]
    lines.extend(rendered)
    if len(related) > len(shown):
        lines.append(f"…还有 {len(related) - len(shown)} 个")
    return lines


def render_notes(song: Song, catalog: SongCatalog) -> str:
    """物量详情：每个有物量的难度各占两行。

    只列 EXP 及以上难度（与详情一致，跳过低难度）——查歌场景没人关心 BASIC 物量。
    保留零值分项：在「分析谱面构成」这个用途下，「没有 flick」本身是信息。

    没有物量数据时给出**可操作的说明**，而不是空列表——
    常见原因是曲库缓存是用 ``notes=false`` 抓的。
    """
    lines = [f"{song.title} · 物量"]

    rendered = False
    for level in _DETAIL_LEVELS:
        difficulty = song.chart(level)
        if difficulty is None or difficulty.notes is None:
            continue
        rendered = True
        pairs = difficulty.notes.as_pairs()
        head = format_difficulty(difficulty)
        if difficulty.notes.total is not None:
            head += f" {difficulty.notes.total}"
        lines.append(head)
        detail = _notes_detail(pairs, drop_zero=False)
        if detail:
            lines.append(f"  {detail}")

    if not rendered:
        if catalog.has_note_data:
            lines.append("这首歌没有物量数据。")
        else:
            lines.append(
                "曲库里没有物量数据。请运行 `python scripts/fetch_reference_data.py` "
                "重新抓取（需要带 notes）。"
            )
    lines.append(f"#{song.id}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 搜索与筛选
# --------------------------------------------------------------------------- #


def _match_line(index: int, song: Song, catalog: SongCatalog) -> str:
    """列表里的一行：``1. 曲名 · MAS15+(15.4) · X-VERSE``"""
    chunks = [_shorten(song.title, _TITLE_MAX)]

    if top := song.charts_by_descending_constant():
        chunks.append(format_difficulty(top[0]))
    if genre := song.genre:
        chunks.append(genre)
    chunks.append(short_version(catalog, song.version))
    return f"{index}. " + " · ".join(chunks)


def render_song_candidates(
    keyword: str,
    matches: list[SongMatch],
    catalog: SongCatalog,
    *,
    settings: BotSettings | None = None,
) -> str:
    """命中多首时的候选列表（轻量）。"""
    settings = settings or get_settings()
    limit = settings.search_result_limit
    shown = matches[:limit]

    lines = [f"`{keyword}` 命中 {len(matches)} 首" + (f"，显示前 {len(shown)} 首：" if len(matches) > len(shown) else "：")]
    for index, match in enumerate(shown, start=1):
        lines.append(_match_line(index, match.song, catalog))
    if len(matches) > len(shown):
        lines.append(f"…还有 {len(matches) - len(shown)} 首，缩小关键词或用 #曲目ID 精确查。")
    elif len(shown) > 1:
        lines.append(f"回复序号 1~{len(shown)} 直接看详情，也可以用 #曲目ID。")
    return "\n".join(lines)


def render_filter_result(
    spec: SongFilter,
    songs: list[Song],
    catalog: SongCatalog,
    *,
    settings: BotSettings | None = None,
) -> str:
    """筛选结果列表（轻量，按最高定数降序）。"""
    settings = settings or get_settings()
    limit = settings.filter_result_limit
    shown = songs[:limit]

    header = spec.describe(catalog)
    if not songs:
        return f"{header}\n没有符合条件的曲目。"

    lines = [f"{header} · {len(songs)} 首" + (f"（显示前 {len(shown)} 首）" if len(songs) > len(shown) else "")]
    for index, song in enumerate(shown, start=1):
        lines.append(_match_line(index, song, catalog))
    if len(songs) > len(shown):
        lines.append(f"…还有 {len(songs) - len(shown)} 首，加条件缩小范围。")
    return "\n".join(lines)


def render_catalog_summary(
    catalog: SongCatalog, *, extra: str = "", jp_latest: str | None = None
) -> str:
    """曲库概览（``/帮助`` 里用来给用户建立预期）。"""
    current = catalog.current_version()
    lines = [
        f"FastChu 曲库：{len(catalog)} 首",
        f"分类：{' / '.join(catalog.genre_names())}",
        f"国服当前版本：{short_version(catalog, current)}（{current}）",
    ]
    if jp_latest:
        lines.append(f"日服最新版本：{jp_latest}")
    if not catalog.has_note_data:
        lines.append("⚠ 曲库没有物量数据，物量查询前请重新抓取。")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 日服独有曲目
# --------------------------------------------------------------------------- #


def render_region_song_detail(
    song: "RegionSong",
    *,
    related: Sequence[Trophy] | None = None,
    title_limit: int = 8,
) -> str:
    """日服独有曲目的详情（国服没有这首歌）。

    没有国服曲目 ID，用日服编号标注。
    排版与国服曲目一致：标题 / 分类版本 / 信息块，避免手机上一行折行。
    """
    from ..services.regions import format_const

    blocks: list[str] = []

    head_meta = [
        part
        for part in (
            song.genres.get("cn") or song.genres.get("jp"),
            song.jp_version,
        )
        if part
    ]
    prefix = f"#{song.jp_idx} - " if song.jp_idx is not None else ""
    blocks.append(f"{prefix}{song.title} · 仅日服")
    blocks.append(" · ".join(str(part) for part in head_meta))

    info: list[tuple[str, str]] = []
    if song.artist:
        info.append(("曲师", _shorten(str(song.artist), _ARTIST_MAX)))
    if song.bpm:
        info.append(("曲速", str(song.bpm)))

    # 与国服详情同一口径：只列 EXP 及以上（BAS/ADV 两服都不显示）
    charts = [
        chart
        for chart in song.ordered_charts()
        if chart.jp_const is not None and chart.name in _DETAIL_NAMES
    ]
    if charts:
        # 整首歌都是日服的，不必每个难度都加「日」；推定值仍然带 ? 提醒
        info.append(
            (
                "难度",
                "\n".join(
                    f"{chart.name} {format_const(chart.jp_const)}"
                    f"{'?' if chart.jp_const_unknown else ''}"
                    for chart in charts
                ),
            )
        )

    if info:
        blocks.append("\n".join(_info_line(label, value) for label, value in info))

    if related:
        blocks.append(
            "\n".join(_related_title_lines(related, song.title, limit=title_limit))
        )

    if song.is_we:
        blocks.append("WORLD'S END（国服未收录）")

    return _BLOCK_SEP.join(blocks)


def render_region_candidates(keyword: str, songs: Sequence["RegionSong"]) -> str:
    """日服独有曲目的候选列表。"""
    lines = [f"`{_shorten(keyword, _TITLE_MAX)}` 在日服曲库命中 {len(songs)} 首（国服无）："]
    for index, song in enumerate(songs[:6], start=1):
        charts = [c for c in song.ordered_charts() if c.jp_const is not None]
        hardest = charts[-1] if charts else None
        suffix = ""
        if hardest is not None:
            suffix = f" · {hardest.name} {hardest.render(prefer_jp=True)}"
        lines.append(f"{index}. {_shorten(song.title, _TITLE_MAX)} · {song.artist or '—'}{suffix}")
    if len(songs) > 6:
        lines.append(f"…还有 {len(songs) - 6} 首，输入更完整的曲名。")
    elif len(songs) > 1:
        lines.append(f"回复序号 1~{min(len(songs), 6)} 直接看详情。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 候选列表（分页）
# --------------------------------------------------------------------------- #


def render_candidate_page(
    keyword: str,
    items: Sequence[object],
    *,
    page: int = 0,
    per_page: int = 8,
    unit: str = "首",
) -> str:
    """候选列表的一页。

    每个候选**占两行**：第一行序号 + 名字，第二行缩进给难度/分类。
    并成一行在手机上必然折行，折在中间很难看——宁可多一行。

    ``items`` 只需要有 ``label`` / ``detail`` 两个属性
    （即 :class:`fastchu.services.selection.SelectionItem`）。
    """
    total_items = len(items)
    total_pages = max(1, (total_items + max(1, per_page) - 1) // max(1, per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = list(items)[start : start + per_page]

    pager = f"（{page + 1}/{total_pages} 页）" if total_pages > 1 else ""
    lines = [f"`{_shorten(keyword, _TITLE_MAX)}` 命中 {total_items} {unit}{pager}"]

    for index, item in enumerate(chunk, start=1):
        label = str(getattr(item, "label", "") or "")
        detail = str(getattr(item, "detail", "") or "")
        lines.append(f"{index}. {label}")
        if detail:
            lines.append(f"   {detail}")

    tips: list[str] = []
    if total_pages > 1:
        # 页码直达：页数少就全列出来，多了只提示相邻页
        if total_pages <= 5:
            tips.append("翻页 " + " ".join(f"p{i}" for i in range(1, total_pages + 1)))
        else:
            if page > 0:
                tips.append(f"p{page} 上一页")
            if page + 1 < total_pages:
                tips.append(f"p{page + 2} 下一页")
    tips.append("回序号看详情")
    lines.append(" · ".join(tips))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 称号
# --------------------------------------------------------------------------- #


def render_title_detail(
    trophy: Trophy,
    title_catalog: "TrophyCatalog",
    *,
    song_limit: int = 3,
) -> str:
    """单个称号详情（轻量，4 行以内）。"""
    # 类别不再展示：一行里塞「颜色 + 类别 + ID」信息密度低还显乱
    lines = [trophy.name]
    lines.append(f"[{trophy.color_label}] · #{trophy.id}")

    condition = trophy.condition_text()
    lines.append(f"条件 {_shorten(condition, 160)}")

    if songs := trophy.song_ids:
        titles = []
        seen = set()
        for req in trophy.required:
            for song in req.songs:
                if song.id in seen:
                    continue
                seen.add(song.id)
                titles.append(song.title or str(song.id))
        shown = " / ".join(_shorten(name, _TITLE_MAX) for name in titles[:song_limit])
        suffix = f" …共{len(songs)}首" if len(songs) > song_limit else ""
        lines.append(f"曲目 {shown}{suffix}")

    return "\n".join(lines)


def render_title_candidates(
    keyword: str,
    hits: Sequence["TitleSearchHit"],
    *,
    settings: BotSettings | None = None,
    limit: int | None = None,
) -> str:
    """称号搜索的多条候选，每条一行。"""
    settings = settings or get_settings()
    effective = limit if limit is not None else min(settings.search_result_limit, 10)
    shown = hits[:effective]

    lines = [f"`{_shorten(keyword, _TITLE_MAX)}` 命中 {len(hits)} 个称号："]
    for index, hit in enumerate(shown, start=1):
        trophy = hit.trophy
        lines.append(
            f"{index}. [{trophy.color_label}] {_shorten(trophy.name, _TITLE_MAX)}"
        )
    if len(hits) > len(shown):
        lines.append(f"…还有 {len(hits) - len(shown)} 个，换个更精确的关键词。")
    elif len(shown) > 1:
        lines.append(f"回复序号 1~{len(shown)} 直接看详情。")
    return "\n".join(lines)


def render_title_stats(title_catalog: "TrophyCatalog") -> str:
    """称号库概览：给「盘点视频」一个可用的总览。"""
    from ..models import TROPHY_COLOR_LABELS
    from ..services.titles import (
        CATEGORY_LABELS,
        COLOR_ORDER,
        EXCLUDED_CATEGORIES,
        TrophyCategory,
    )

    total = len(title_catalog)
    special = len(title_catalog.special)
    excluded = len(title_catalog.excluded)
    categories = title_catalog.category_counts()

    aj = categories.get(TrophyCategory.PER_SONG_AJ, 0)
    character = categories.get(TrophyCategory.CHARACTER_RANK, 0)

    lines = [
        f"FastChu 称号库：{total} 个",
        f"特殊称号 {special} · 已排除固定称号 {excluded}（常规AJ {aj} / 角色养成 {character}）",
    ]

    colors = title_catalog.color_counts(special_only=True)
    color_text = " / ".join(
        f"{TROPHY_COLOR_LABELS.get(color, color)} {count}"
        for color, count in sorted(
            colors.items(), key=lambda kv: -COLOR_ORDER.get(kv[0], 0)
        )
    )
    lines.append(f"特殊称号分色：{color_text}")

    ranked = [
        (category, count)
        for category, count in categories.items()
        if category not in EXCLUDED_CATEGORIES
    ]
    top = " / ".join(f"{CATEGORY_LABELS.get(c, c)} {n}" for c, n in ranked[:6])
    lines.append(f"主要分类：{top}")

    lines.append("用法：/称号 <关键词>   例：/称号 Legend")
    return "\n".join(lines)


def render_title_list(
    titles: Sequence[Trophy],
    title_catalog: "TrophyCatalog",
    *,
    header: str = "",
    limit: int = 20,
) -> str:
    """按条件列出的称号清单（每条一行）。"""
    shown = list(titles[:limit])
    lines: list[str] = []
    if header:
        lines.append(header)
    for index, trophy in enumerate(shown, start=1):
        info = title_catalog.category_of(trophy)
        lines.append(
            f"{index}. [{trophy.color_label}] {_shorten(trophy.name, _TITLE_MAX)}"
            f" · {info.label}"
        )
    if len(titles) > len(shown):
        lines.append(f"…还有 {len(titles) - len(shown)} 个（用 --limit 调整）")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 帮助
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class HelpItem:
    """一条命令，以及一句最短说明。"""

    command: str
    description: str


@dataclass(frozen=True)
class HelpSection:
    """帮助卡里的一组命令。"""

    title: str
    items: tuple[HelpItem, ...]


#: 帮助只列命令，不写自我介绍、不解释内部过滤规则。
#: 这是唯一数据源：文字回退和图片卡片都从这里生成。
HELP_SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        title="查歌",
        items=(
            HelpItem("/歌曲名或别名", "直接查曲目；末尾加 c 精准匹配"),
            HelpItem("/#曲目ID", "按曲目 ID 精确查询"),
        ),
    ),
    HelpSection(
        title="称号",
        items=(
            HelpItem("/称号 <关键词>", "查称号的颜色、条件与分类"),
            HelpItem("/称号条件 <条件>", "按获得条件反查称号"),
            HelpItem("/随机称号", "随机抽一个称号"),
        ),
    ),
    HelpSection(
        title="工具",
        items=(
            HelpItem("/谁写的 <歌名> [难度]", "查谱师；默认取最高难度"),
            HelpItem("/添加别名 <曲目> <别名>", "给曲目补充别名，群友可投稿"),
            HelpItem("/授权", "绑定落雪账号，查歌/称号显示个人数据"),
            HelpItem("/帮助", "随时回到这张图"),
        ),
    ),
)

HELP_TEXT = "\n\n".join(
    f"【{section.title}】\n"
    + "\n".join(f"{item.command}  {item.description}" for item in section.items)
    for section in HELP_SECTIONS
)


def render_help(catalog: SongCatalog | None = None, **_kwargs: object) -> str:
    """帮助文本（图片渲染不可用时的回退）。

    曾经这里会附上「曲库 N 首 / 国服版本 / 日服版本」这些概况，以及过滤规则的说明。
    按「足够轻量」的要求全删了：帮助就是一张命令表。
    ``**_kwargs`` 保留旧调用签名（``jp_latest=`` 等）。
    """
    return HELP_TEXT


# --------------------------------------------------------------------------- #
# 分页
# --------------------------------------------------------------------------- #


def split_message(text: str, limit: int = 1800) -> list[str]:
    """把长文本按行切成若干条消息。

    列表类输出天然是「一行一条」，所以按行切既能保证不超长，也不会切断某一条曲目。
    ``limit`` 过小时按 200 兜底。
    """
    limit = max(200, int(limit))
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            flush()
        if line_len - 1 > limit:
            # 单行超长（几乎不可能）：硬切
            flush()
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            continue
        current.append(line)
        current_len += line_len

    flush()
    return chunks
