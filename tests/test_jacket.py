"""曲绘测试。

三种模式的行为差别很大，而且「默认最轻量」是明确的产品要求，
所以要逐条锁住：``url`` 模式**绝不允许下载**。
"""

from __future__ import annotations

import base64
import pathlib

import httpx
import pytest
import respx

from fastchu.api.client import LxnsClient
from fastchu.errors import AssetUnavailableError
from fastchu.services.jacket import JacketImage, JacketProvider

from .conftest import make_settings

SONG_ID = 464
JACKET_URL = f"https://assets2.lxns.net/chunithm/jacket/{SONG_ID}.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def make_provider(tmp_path: pathlib.Path, **overrides) -> tuple[JacketProvider, LxnsClient]:
    settings = make_settings(tmp_path, **overrides)
    client = LxnsClient(min_interval=0.0, backoff=0.0, retries=0)
    return JacketProvider(settings, client), client


async def test_off_mode_returns_none(tmp_path: pathlib.Path) -> None:
    provider, client = make_provider(tmp_path, jacket_mode="off")
    assert await provider.get(SONG_ID) is None
    await client.aclose()


@respx.mock
async def test_url_mode_makes_no_request(tmp_path: pathlib.Path) -> None:
    """``url`` 模式是默认值，也是「轻量级」的关键：机器人侧不许下载。"""
    route = respx.route(host="assets2.lxns.net")
    provider, client = make_provider(tmp_path, jacket_mode="url")

    jacket = await provider.get(SONG_ID)

    assert jacket is not None
    assert jacket.url == JACKET_URL
    assert not jacket.has_data
    assert jacket.size_bytes == 0
    assert not route.called, "url 模式不该发起任何资源请求"
    await client.aclose()


@respx.mock
async def test_url_mode_data_uri_raises(tmp_path: pathlib.Path) -> None:
    provider, client = make_provider(tmp_path, jacket_mode="url")
    jacket = await provider.get(SONG_ID)
    assert jacket is not None
    with pytest.raises(AssetUnavailableError):
        _ = jacket.data_uri
    await client.aclose()


@respx.mock
async def test_base64_mode_downloads_and_caches(tmp_path: pathlib.Path) -> None:
    route = respx.get(JACKET_URL).mock(return_value=httpx.Response(200, content=PNG))
    provider, client = make_provider(tmp_path, jacket_mode="base64")

    first = await provider.get(SONG_ID)
    assert first is not None and first.has_data
    assert first.size_bytes == len(PNG)
    assert provider.cache_path(SONG_ID).is_file()
    assert route.call_count == 1

    # 第二次应当读缓存，不再请求
    second = await provider.get(SONG_ID)
    assert second is not None and second.data == PNG
    assert route.call_count == 1, "命中缓存后不该再下载"
    await client.aclose()


@respx.mock
async def test_base64_mode_data_uri_format(tmp_path: pathlib.Path) -> None:
    respx.get(JACKET_URL).mock(return_value=httpx.Response(200, content=PNG))
    provider, client = make_provider(tmp_path, jacket_mode="base64")
    jacket = await provider.get(SONG_ID)
    assert jacket is not None

    uri = jacket.data_uri
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == PNG
    assert jacket.filename == f"jacket_{SONG_ID}.png"
    await client.aclose()


@respx.mock
async def test_base64_download_failure_is_cached_negatively(tmp_path: pathlib.Path) -> None:
    """取不到的图要记住，别每次查询都去敲一次官方资源接口。"""
    route = respx.get(JACKET_URL).mock(return_value=httpx.Response(404))
    provider, client = make_provider(tmp_path, jacket_mode="base64")

    assert await provider.get(SONG_ID) is None
    assert route.call_count == 1

    assert await provider.get(SONG_ID) is None
    assert route.call_count == 1, "负缓存生效后不该重复请求"
    await client.aclose()


@respx.mock
async def test_negative_cache_can_be_cleared(tmp_path: pathlib.Path) -> None:
    route = respx.get(JACKET_URL).mock(return_value=httpx.Response(404))
    provider, client = make_provider(tmp_path, jacket_mode="base64")
    await provider.get(SONG_ID)

    provider.clear_missing_marker(SONG_ID)
    route.mock(return_value=httpx.Response(200, content=PNG))
    jacket = await provider.get(SONG_ID)
    assert jacket is not None and jacket.data == PNG
    await client.aclose()


@respx.mock
async def test_oversized_image_is_skipped(tmp_path: pathlib.Path) -> None:
    """异常大的文件不该把消息体积撑爆。"""
    respx.get(JACKET_URL).mock(return_value=httpx.Response(200, content=b"x" * 5000))
    provider, client = make_provider(tmp_path, jacket_mode="base64", jacket_max_bytes=1000)

    assert await provider.get(SONG_ID) is None
    assert not provider.cache_path(SONG_ID).is_file()
    await client.aclose()


@respx.mock
async def test_transport_error_does_not_raise(tmp_path: pathlib.Path) -> None:
    """曲绘失败是**非致命**的：文字照常回复。"""
    respx.get(JACKET_URL).mock(side_effect=httpx.ConnectError("refused"))
    provider, client = make_provider(tmp_path, jacket_mode="base64")
    assert await provider.get(SONG_ID) is None
    await client.aclose()


@respx.mock
async def test_corrupted_cache_is_refetched(tmp_path: pathlib.Path) -> None:
    route = respx.get(JACKET_URL).mock(return_value=httpx.Response(200, content=PNG))
    provider, client = make_provider(tmp_path, jacket_mode="base64")

    # 造一个"坏掉"的缓存：路径存在但读不出来（用目录冒充文件）
    path = provider.cache_path(SONG_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()

    jacket = await provider.get(SONG_ID)
    assert jacket is not None and jacket.data == PNG
    assert route.call_count == 1
    await client.aclose()


def test_jacket_image_properties() -> None:
    image = JacketImage(song_id=1, url="http://x/1.png")
    assert not image.has_data
    assert image.size_bytes == 0
    assert image.filename == "jacket_1.png"


def test_settings_jacket_url() -> None:
    settings = make_settings(None)
    assert settings.jacket_url(464) == "https://assets2.lxns.net/chunithm/jacket/464.png"
    assert settings.music_url(464).endswith("/music/464.mp3")
    assert not settings.jacket_enabled  # 测试默认 off
