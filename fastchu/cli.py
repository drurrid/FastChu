"""命令行入口。

QQ 机器人不方便调试，所以查歌/查称号的能力都能从命令行跑：

* ``python -m fastchu search <关键词>``    搜歌（候选或详情）
* ``python -m fastchu song <关键词|#ID>``  曲目详情（含相关特殊称号）
* ``python -m fastchu notes <关键词|#ID>`` 谱面物量
* ``python -m fastchu titles [关键词]``    称号列表 / 搜索
* ``python -m fastchu title <关键词>``     称号详情
* ``python -m fastchu catalog``            曲库与称号库概况
* ``python -m fastchu refresh``            强制重新抓取曲库与称号
* ``python -m fastchu config``             查看当前配置
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import get_settings
from .errors import ChunithmBotError
from .models import TROPHY_COLOR_LABELS
from .render import (
    render_help,
    render_notes,
    render_song_candidates,
    render_song_detail,
    render_title_candidates,
    render_title_detail,
    render_title_list,
    render_title_stats,
)
from .services.commands import run_lookup
from .services.song_service import SongService
from .services.titles import COLOR_ORDER, TrophyCategory, tier, tier_label

__all__ = ["main"]


def _force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，输出中日文会乱码或报错，这里强制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def _make_service() -> SongService:
    return SongService(get_settings())


async def _resolve(service: SongService, keyword: str):
    """把关键词解析成唯一曲目；不唯一时打印候选并返回 ``None``。"""
    catalog = await service.catalog()
    result = await service.find(keyword)

    if result.is_empty:
        print(f"没有找到 `{keyword}`。可以只打个片段、或用别名试试。")
        return None
    if not result.is_unique:
        print(render_song_candidates(keyword, result.matches, catalog))
        return None
    return catalog, result.require_song()


async def _print_song_detail(service: SongService, catalog, song) -> None:
    """打印曲目详情（含相关特殊称号）。"""
    settings = get_settings()
    try:
        related = (await service.titles()).for_song(song.id)
    except ChunithmBotError as exc:
        print(f"（称号库不可用，详情不含称号：{exc}）", file=sys.stderr)
        related = []

    region = None
    try:
        region_catalog = await service.regions()
        if region_catalog is not None:
            region = region_catalog.for_cn_id(song.id)
    except ChunithmBotError as exc:
        print(f"（区域对照不可用：{exc}）", file=sys.stderr)

    print(
        render_song_detail(
            song, catalog, settings=settings, related=related, region=region
        )
    )


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #


async def _print_reply(service: SongService, keyword: str) -> int:
    """走与 QQ 机器人**同一套**命令层逻辑（含日服独有曲回退、定数双标）。"""
    reply = await run_lookup(service, get_settings(), keyword)
    print(reply.text)
    return 0 if reply.kind not in ("hint", "error") else 1


async def _cmd_search(args: argparse.Namespace) -> int:
    service = _make_service()
    try:
        return await _print_reply(service, args.keyword)
    finally:
        await service.aclose()


async def _cmd_song(args: argparse.Namespace) -> int:
    service = _make_service()
    try:
        code = await _print_reply(service, args.keyword)
        if args.jacket:
            reply = await run_lookup(service, get_settings(), args.keyword)
            if reply.song is not None:
                print(f"\n曲绘 {get_settings().jacket_url(reply.song.id)}")
        return code
    finally:
        await service.aclose()


async def _cmd_notes(args: argparse.Namespace) -> int:
    service = _make_service()
    try:
        resolved = await _resolve(service, args.keyword)
        if resolved is None:
            return 1
        catalog, song = resolved
        print(render_notes(song, catalog))
        return 0
    finally:
        await service.aclose()


async def _cmd_titles(args: argparse.Namespace) -> int:
    """称号列表 / 搜索。"""
    service = _make_service()
    try:
        title_catalog = await service.titles()

        # 没有关键词但有过滤条件 → 直接列（做盘点取材时最常用）
        if not args.keyword:
            if args.color or args.category or args.special_only or args.tier is not None:
                all_titles = title_catalog.listing(
                    special_only=args.special_only,
                    color=args.color,
                    category=args.category,
                    tier_value=args.tier,
                    limit=10_000,
                )
                if not all_titles:
                    print("没有符合条件的称号。")
                    return 1
                parts = []
                if args.special_only:
                    parts.append("特殊称号")
                if args.color:
                    parts.append(f"颜色 {TROPHY_COLOR_LABELS.get(args.color, args.color)}")
                if args.category:
                    parts.append(f"分类 {args.category}")
                if args.tier is not None:
                    parts.append(f"分层 {tier_label(args.tier)}")
                header = " · ".join(parts) + f" · 共 {len(all_titles)} 个"
                print(
                    render_title_list(
                        all_titles, title_catalog, header=header, limit=args.limit
                    )
                )
                return 0

            print(render_title_stats(title_catalog))
            return 0

        hits = title_catalog.search(
            args.keyword,
            special_only=args.special_only,
            color=args.color,
            category=args.category,
            limit=args.limit,
        )
        if not hits:
            print(f"没有找到称号 `{args.keyword}`。")
            return 1
        print(render_title_candidates(args.keyword, hits, settings=get_settings()))
        return 0
    finally:
        await service.aclose()


async def _cmd_title(args: argparse.Namespace) -> int:
    """称号详情。"""
    service = _make_service()
    try:
        title_catalog = await service.titles()
        hits = title_catalog.search(args.keyword, limit=20)
        if not hits:
            print(f"没有找到称号 `{args.keyword}`。")
            return 1

        best = title_catalog.find(args.keyword)
        if best is None:
            print(render_title_candidates(args.keyword, hits, settings=get_settings()))
            return 1

        trophy = best.trophy
        info = best.category
        print(render_title_detail(trophy, title_catalog))
        print()
        print(
            f"稀有度分层 {tier_label(tier(trophy, info))}"
            f" · {'特殊称号' if info.is_special else '固定称号（' + info.excluded + '）'}"
        )
        return 0
    finally:
        await service.aclose()


async def _cmd_catalog(args: argparse.Namespace) -> int:
    service = _make_service()
    try:
        settings = get_settings()
        catalog = await service.catalog(force_refresh=args.refresh)
        print(render_help(catalog))
        print()
        print(catalog.summary())
        current = catalog.current_version()
        declared = catalog.declared_version()
        print(f"当前稼动版本（实际有曲目的最高版本）: {current} ({catalog.version_title(current)})")
        if declared != current:
            print(
                f"版本列表声明的最高版本: {declared} ({catalog.version_title(declared)})"
                " —— 该版本还没有曲目数据"
            )
        print(f"曲绘模式: {settings.jacket_mode} · 物量数据: {'有' if catalog.has_note_data else '无'}")

        try:
            title_catalog = await service.titles(force_refresh=args.refresh)
        except ChunithmBotError as exc:
            print(f"称号库不可用: {exc}")
        else:
            print()
            print(render_title_stats(title_catalog))
            print()
            print("=== 称号分类明细 ===")
            counts = title_catalog.category_counts()
            for category, count in counts.items():
                print(f"  {category:<20} {count:>5}")
        return 0
    finally:
        await service.aclose()


async def _cmd_refresh(args: argparse.Namespace) -> int:
    service = _make_service()
    try:
        catalog = await service.catalog(force_refresh=True)
        print(f"已刷新曲库：{catalog.summary()}")
        print(f"物量数据: {'有' if catalog.has_note_data else '无'}")
        try:
            title_catalog = await service.titles(force_refresh=True)
        except ChunithmBotError as exc:
            print(f"称号库刷新失败: {exc}", file=sys.stderr)
            return 1
        print(
            f"已刷新称号库：{len(title_catalog)} 个"
            f"（特殊 {len(title_catalog.special)}）"
        )
        return 0
    finally:
        await service.aclose()


async def _cmd_config(args: argparse.Namespace) -> int:
    settings = get_settings()
    print("当前配置：")
    for name, value in sorted(settings.model_dump().items()):
        print(f"  FASTCHU_{name.upper()} = {value!r}")
    print("\n说明：本项目只使用公共接口，因此没有任何密钥配置项。")
    return 0


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastchu",
        description="FastChu —— 中二节奏查歌 / 查称号（数据源：落雪查分器公共接口 maimai.lxns.net）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="搜歌")
    p_search.add_argument("keyword")
    p_search.set_defaults(func=_cmd_search)

    p_song = sub.add_parser("song", help="曲目详情")
    p_song.add_argument("keyword", help="关键词或 #曲目ID")
    p_song.add_argument("--jacket", action="store_true", help="同时打印曲绘地址")
    p_song.set_defaults(func=_cmd_song)

    p_notes = sub.add_parser("notes", help="谱面物量")
    p_notes.add_argument("keyword", help="关键词或 #曲目ID")
    p_notes.set_defaults(func=_cmd_notes)

    p_titles = sub.add_parser("titles", help="称号列表 / 搜索（不带关键词则显示概览）")
    p_titles.add_argument("keyword", nargs="?", default="", help="称号关键词")
    p_titles.add_argument("--color", choices=sorted(TROPHY_COLOR_LABELS), help="按颜色过滤")
    p_titles.add_argument("--category", help="按分类过滤，例如 all_songs")
    p_titles.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="按稀有度分层过滤")
    p_titles.add_argument("--special-only", action="store_true", help="只列特殊称号")
    p_titles.add_argument("--limit", type=int, default=20, help="最多显示条数")
    p_titles.set_defaults(func=_cmd_titles)

    p_title = sub.add_parser("title", help="称号详情")
    p_title.add_argument("keyword")
    p_title.set_defaults(func=_cmd_title)

    p_catalog = sub.add_parser("catalog", help="曲库与称号库概况")
    p_catalog.add_argument("--refresh", action="store_true", help="强制重新抓取")
    p_catalog.set_defaults(func=_cmd_catalog)

    p_refresh = sub.add_parser("refresh", help="强制重新抓取曲库与称号")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_config = sub.add_parser("config", help="查看当前配置")
    p_config.set_defaults(func=_cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("\n已取消。", file=sys.stderr)
        return 130
    except ChunithmBotError as exc:
        print(f"❌ {exc.user_message}\n   ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
