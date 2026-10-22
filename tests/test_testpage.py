"""本地测试页（``tools/testpage``）的接口测试。

这是个开发工具，但用户会直接用它，所以值得锁住两件事：

1. 页面与 API 能起来（别因为重构把导入弄坏）；
2. API 返回的**消息段结构**与 ``Reply.messages()`` 一致——
   测试页显示的就是群里会发的，这条不能漂。

用 ASGI 直连（不绑定端口），因此不会和真实服务冲突。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import httpx
import pytest

from fastchu.services.commands import Reply, run_lookup
from fastchu.services.song_service import SongService

from .conftest import PROJECT_ROOT, TROPHY_FIXTURE, make_settings

SERVER_PATH = PROJECT_ROOT / "tools" / "testpage" / "server.py"

needs_trophies = pytest.mark.skipif(not TROPHY_FIXTURE.is_file(), reason="缺少称号夹具")


@pytest.fixture(scope="module")
def page_module():
    """按路径导入测试页服务模块（它不在包里）。

    必须先注册进 ``sys.modules``：pydantic 需要能按模块名解析模型的前向引用，
    否则 FastAPI 的依赖注入会在运行时报 "not fully defined"。
    """
    if not SERVER_PATH.is_file():  # pragma: no cover
        pytest.skip(f"找不到 {SERVER_PATH}")
    spec = importlib.util.spec_from_file_location("fastchu_testpage_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def client(page_module, tmp_path: pathlib.Path):
    """直连 ASGI，不绑端口。"""
    settings = make_settings(tmp_path, jacket_mode="url")
    service = SongService(settings)
    # 让页面服务使用这个受控服务与配置
    page_module.get_service = lambda: service
    page_module.get_settings = lambda: settings

    transport = httpx.ASGITransport(app=page_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    await service.aclose()


async def test_index_page_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "FastChu 测试页" in response.text
    # 页面必须声明与后端一致的命令
    for command in ("查歌", "称号", "查歌帮助"):
        assert command in response.text
    # 已移除的命令不该残留在页面里
    for removed in ('data-cmd="filter"', 'data-cmd="notes"'):
        assert removed not in response.text


async def test_meta(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/meta")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["catalog"]["songs"] > 1000
    assert data["settings"]["jacket_mode"] == "url"


@needs_trophies
async def test_meta_includes_titles(client: httpx.AsyncClient) -> None:
    data = (await client.get("/api/meta")).json()
    titles = data["catalog"]["titles"]
    assert titles is not None
    assert titles["total"] > 0
    assert titles["special"] > 0
    assert titles["total"] == titles["special"] + titles["excluded"]
    assert titles["colors"]


async def test_run_song_detail(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/run", json={"command": "song", "text": "#464", "notes_mode": "full"}
    )
    assert response.status_code == 200
    result = response.json()["result"]

    assert result["kind"] == "detail"
    assert result["message_count"] == 1
    # 单条消息 = 正文 + 曲绘，与 QQ 侧一致
    assert [seg["type"] for seg in result["messages"][0]] == ["text", "image"]
    assert result["messages"][0][1]["url"].endswith("/jacket/464.png")


async def test_run_segments_match_reply_contract(client: httpx.AsyncClient, page_module) -> None:
    """API 返回的段结构必须与 ``Reply.messages()`` 完全一致。"""
    settings = page_module.get_settings()
    reply = await run_lookup(page_module.get_service(), settings, "#464")

    response = await client.post("/api/run", json={"command": "song", "text": "#464"})
    result = response.json()["result"]

    expected = [[segment.type for segment in message] for message in reply.messages()]
    actual = [[segment["type"] for segment in message] for message in result["messages"]]
    assert actual == expected


async def test_run_notes_mode_toggle(client: httpx.AsyncClient) -> None:
    without = await client.post(
        "/api/run", json={"command": "song", "text": "#464", "notes_mode": "off"}
    )
    with_notes = await client.post(
        "/api/run", json={"command": "song", "text": "#464", "notes_mode": "full"}
    )
    assert "物量" not in without.json()["result"]["texts"][0]
    assert "物量" in with_notes.json()["result"]["texts"][0]


async def test_run_candidates_and_help(client: httpx.AsyncClient) -> None:
    cases = [
        ("song", "world", "candidates"),
        ("help", "", "help"),
    ]
    for command, text, expected_kind in cases:
        response = await client.post("/api/run", json={"command": command, "text": text})
        result = response.json()["result"]
        assert result["kind"] == expected_kind, (command, result["kind"])
        assert result["texts"], command
        if command == "help":
            first = result["messages"][0][0]
            assert first["type"] in {"image", "text"}
            if first["type"] == "image":
                assert first["data_uri"].startswith("data:image/png;base64,")


@needs_trophies
async def test_run_title_without_keyword(client: httpx.AsyncClient) -> None:
    """``/称号`` 不带参数 → 用法提示。"""
    response = await client.post("/api/run", json={"command": "title", "text": ""})
    result = response.json()["result"]
    assert result["kind"] == "hint"
    assert "称号条件" in result["texts"][0]


@needs_trophies
async def test_run_title_detail(client: httpx.AsyncClient, page_module) -> None:
    from fastchu.render import render_title_detail

    service = page_module.get_service()
    title_catalog = await service.titles()
    target = next(t for t in title_catalog.special if len(t.name) > 8)

    response = await client.post("/api/run", json={"command": "title", "text": target.name})
    result = response.json()["result"]

    assert result["kind"] == "title"
    assert result["trophy"]["id"] == target.id
    assert result["trophy"]["color_label"] == target.color_label
    assert result["texts"][0] == render_title_detail(target, title_catalog)


@needs_trophies
async def test_run_title_not_found(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/run", json={"command": "title", "text": "绝对不存在zzz"}
    )
    result = response.json()["result"]
    assert result["kind"] == "hint"


async def test_unknown_command_rejected(client: httpx.AsyncClient) -> None:
    """已移除的命令不该还能调用。"""
    response = await client.post("/api/run", json={"command": "filter", "text": "分类=原创"})
    assert response.status_code == 422


def test_paginated_reply_has_no_image() -> None:
    """分页时每条只发文字——配图会错位。"""
    reply = Reply(kind="filter", texts=["第一段", "第二段"])
    assert len(reply.messages()) == 2
    assert all(len(message) == 1 for message in reply.messages())
