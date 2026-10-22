"""FastChu 本地测试页。

在浏览器里试查歌。**与 QQ 机器人共用同一套逻辑**
（:mod:`fastchu.services.commands`），所以页面上看到的消息条数、分段、
曲绘位置，就是群里会发出来的样子。

启动::

    python tools/testpage/server.py
    # 然后打开 http://127.0.0.1:8899

只监听本机回环地址，不对外暴露。
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import sys
from collections.abc import AsyncIterator
from typing import Any, Literal

# 允许直接 `python tools/testpage/server.py` 运行而不必先 pip install -e .
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field
except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失时给可操作提示
    print(
        f"缺少依赖 {exc.name}。测试页需要 fastapi / uvicorn：\n"
        '  pip install -e ".[dev]"      或      pip install "nonebot2[fastapi]"',
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from fastchu import __version__
from fastchu.config import get_settings
from fastchu.services.commands import (
    Reply,
    run_help,
    run_query,
    run_title_query,
)
from fastchu.services.selection import SelectionStore
from fastchu.services.song_service import aclose_service, get_service

HERE = pathlib.Path(__file__).resolve().parent


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """退出时关掉曲库服务的 httpx 连接池。"""
    yield
    await aclose_service()


app = FastAPI(title="FastChu 测试页", version=__version__, lifespan=lifespan)
Command = Literal["song", "title", "help"]

#: 两步式查询：测试页按会话记住候选
_SELECTIONS = SelectionStore()


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #


def _segment_to_dict(segment: Any) -> dict[str, Any]:
    return {
        "type": segment.type,
        "text": segment.text,
        "url": segment.image_url,
        "data_uri": segment.image_data_uri,
    }


def _reply_to_dict(reply: Reply) -> dict[str, Any]:
    messages = reply.messages()  # 只算一次：base64 模式下会做编码
    song = reply.song
    trophy = reply.trophy
    return {
        "kind": reply.kind,
        "texts": reply.texts,
        "text": reply.text,
        "char_count": reply.char_count,
        "message_count": len(messages),
        "elapsed_ms": round(reply.elapsed_ms, 1),
        "matched_count": reply.matched_count,
        "is_error": reply.is_error,
        "song": (
            {
                "id": song.id,
                "title": song.title,
                "artist": song.artist,
                "genre": song.genre,
                "bpm": song.bpm,
                "version": song.version,
            }
            if song is not None
            else None
        ),
        "trophy": (
            {
                "id": trophy.id,
                "name": trophy.name,
                "color": trophy.color,
                "color_label": trophy.color_label,
            }
            if trophy is not None
            else None
        ),
        # 与 QQ 侧完全一致的消息段结构
        "messages": [[_segment_to_dict(s) for s in message] for message in messages],
    }


async def _collect_catalog() -> tuple[Any, dict[str, Any]]:
    settings = get_settings()
    service = get_service()
    catalog = await service.catalog()
    current = catalog.current_version()
    info: dict[str, Any] = {
        "songs": len(catalog),
        "aliases": len(catalog.aliases),
        "genres": catalog.genre_names(),
        "versions": len(catalog.versions),
        "current_version": current,
        "current_version_title": catalog.version_title(current),
        "declared_version": catalog.declared_version(),
        "has_notes": catalog.has_note_data,
        "cache_path": str(settings.song_cache_path),
        "cache_exists": settings.song_cache_path.is_file(),
    }
    try:
        title_catalog = await service.titles()
    except Exception as exc:  # noqa: BLE001 - 称号库挂了不该让页面打不开
        info["titles"] = None
        info["titles_error"] = f"{type(exc).__name__}: {exc}"
    else:
        info["titles"] = {
            "total": len(title_catalog),
            "special": len(title_catalog.special),
            "excluded": len(title_catalog.excluded),
            "categories": title_catalog.category_counts(),
            "colors": title_catalog.color_counts(special_only=True),
            "cache_path": str(settings.trophy_cache_path),
        }
    return catalog, info


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((HERE / "index.html").read_text(encoding="utf-8"))


@app.get("/api/meta")
async def api_meta() -> JSONResponse:
    settings = get_settings()
    try:
        _catalog, catalog_info = await _collect_catalog()
    except Exception as exc:  # noqa: BLE001 - 页面要能显示"曲库没准备好"
        return JSONResponse(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "settings": _settings_dict(settings),
                "version": __version__,
            }
        )
    return JSONResponse(
        {
            "ok": True,
            "version": __version__,
            "catalog": catalog_info,
            "settings": _settings_dict(settings),
        }
    )


def _settings_dict(settings: Any) -> dict[str, Any]:
    return {
        "jacket_mode": settings.jacket_mode,
        "max_message_length": settings.max_message_length,
        "search_result_limit": settings.search_result_limit,
        "filter_result_limit": settings.filter_result_limit,
        "notes_mode": settings.notes_mode,
        "query_cooldown": settings.query_cooldown,
        "fetch_notes": settings.fetch_notes,
    }


class RunRequest(BaseModel):
    command: Command = "song"
    text: str = Field(default="", description="命令参数")
    notes_mode: str | None = Field(
        default=None,
        description="覆盖 notes_mode（off / compact / full）；None 表示用配置值",
    )
    session: str = Field(default="testpage", description="两步式查询的会话标识")


@app.post("/api/run")
async def api_run(request: RunRequest) -> JSONResponse:
    settings = get_settings()
    if request.notes_mode in ("off", "compact", "full"):
        settings = settings.model_copy(update={"notes_mode": request.notes_mode})

    service = get_service()
    command = request.command
    text = request.text

    if command == "help":
        reply = await run_help(service, settings)
    elif command == "title":
        reply = await run_title_query(
            service, settings, text, selections=_SELECTIONS, key=request.session
        )
    else:  # song
        reply = await run_query(
            service, settings, text, selections=_SELECTIONS, key=request.session
        )

    return JSONResponse({"ok": True, "result": _reply_to_dict(reply)})


@app.post("/api/refresh")
async def api_refresh() -> JSONResponse:
    """强制重新抓取曲库与称号（版本更新后用）。"""
    try:
        service = get_service()
        await service.catalog(force_refresh=True)
        await service.titles(force_refresh=True)
        _catalog, info = await _collect_catalog()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return JSONResponse({"ok": True, "catalog": info})


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="FastChu 本地测试页")
    parser.add_argument("--host", default="127.0.0.1", help="默认只监听本机")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--reload", action="store_true", help="改动代码后自动重载")
    args = parser.parse_args()

    print(f"FastChu 测试页 → http://{args.host}:{args.port}")
    uvicorn.run(
        "server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(HERE)] if args.reload else None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
