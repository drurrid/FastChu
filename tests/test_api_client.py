"""公共接口客户端测试：信封拆解、错误映射、重试、资源下载。

本项目**不做鉴权**，所以这里没有密钥相关用例；取而代之的是
「免鉴权也必须稳定」的那些性质：重试、限流识别、响应解析容错。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from fastchu.api.client import LxnsClient
from fastchu.errors import LxnsError, LxnsNotFoundError, LxnsRateLimitError, LxnsServerError

BASE = "https://maimai.lxns.net/api/v0"
JACKET = "https://assets2.lxns.net/chunithm/jacket/464.png"


def make_client(**kwargs: object) -> LxnsClient:
    defaults: dict[str, object] = {
        "base_url": BASE,
        "min_interval": 0.0,
        "backoff": 0.0,
        "retries": 0,
    }
    defaults.update(kwargs)
    return LxnsClient(**defaults)  # type: ignore[arg-type]


def envelope(data: object, *, success: bool = True, code: int = 200, message: str | None = None) -> dict:
    return {"success": success, "code": code, "message": message, "data": data}


# --------------------------------------------------------------------------- #
# 曲目接口
# --------------------------------------------------------------------------- #


@respx.mock
async def test_get_song_list_parses_bundle() -> None:
    route = respx.get(f"{BASE}/chunithm/song/list").mock(
        return_value=httpx.Response(
            200,
            json=envelope(
                {
                    "songs": [
                        {
                            "id": 1,
                            "title": "T",
                            "difficulties": [{"difficulty": 3, "level": "14", "level_value": 14.0}],
                        }
                    ],
                    "genres": [{"id": 1, "genre": "原创"}],
                    "versions": [{"id": 1, "title": "CHUNITHM", "version": 10000}],
                }
            ),
        )
    )
    async with make_client() as client:
        bundle = await client.get_song_list(notes=True)

    assert len(bundle.songs) == 1
    assert bundle.songs[0].title == "T"
    assert bundle.genres[0].genre == "原创"
    assert route.calls[0].request.url.params["notes"] == "true"


@respx.mock
async def test_get_song_list_notes_flag() -> None:
    route = respx.get(f"{BASE}/chunithm/song/list").mock(
        return_value=httpx.Response(200, json=envelope({"songs": [], "genres": [], "versions": []}))
    )
    async with make_client() as client:
        await client.get_song_list(notes=False)
    assert route.calls[0].request.url.params["notes"] == "false"


@respx.mock
async def test_get_song_by_id() -> None:
    respx.get(f"{BASE}/chunithm/song/464").mock(
        return_value=httpx.Response(200, json=envelope({"id": 464, "title": "World Vanquisher"}))
    )
    async with make_client() as client:
        song = await client.get_song(464)
    assert song.title == "World Vanquisher"


@respx.mock
async def test_none_version_param_is_dropped() -> None:
    route = respx.get(f"{BASE}/chunithm/song/list").mock(
        return_value=httpx.Response(200, json=envelope({"songs": [], "genres": [], "versions": []}))
    )
    async with make_client() as client:
        await client.get_song_list(version=None)
    assert "version" not in route.calls[0].request.url.params


@respx.mock
async def test_alias_list_accepts_dict_and_list() -> None:
    for payload in (
        envelope({"aliases": [{"song_id": 1, "aliases": ["a"]}]}),
        envelope([{"song_id": 1, "aliases": ["a"]}]),
    ):
        respx.get(f"{BASE}/chunithm/alias/list").mock(return_value=httpx.Response(200, json=payload))
        async with make_client() as client:
            aliases = await client.get_alias_list()
        assert aliases[0].aliases == ["a"]


@respx.mock
async def test_collection_list() -> None:
    respx.get(f"{BASE}/chunithm/character/list").mock(
        return_value=httpx.Response(200, json=envelope({"characters": [{"id": 1, "name": "x"}]}))
    )
    async with make_client() as client:
        items = await client.get_collection_list("character")
    assert items[0]["id"] == 1


async def test_collection_list_rejects_unknown_type() -> None:
    async with make_client() as client:
        with pytest.raises(ValueError, match="不支持的收藏品类型"):
            await client.get_collection_list("nonsense")


# --------------------------------------------------------------------------- #
# 信封与错误
# --------------------------------------------------------------------------- #


@respx.mock
async def test_accepts_bare_payload_without_envelope() -> None:
    respx.get(f"{BASE}/chunithm/song/list").mock(
        return_value=httpx.Response(200, json={"songs": [], "genres": [], "versions": []})
    )
    async with make_client() as client:
        bundle = await client.get_song_list()
    assert bundle.songs == []


@respx.mock
async def test_success_false_raises_by_code() -> None:
    respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(
            200, json=envelope(None, success=False, code=404, message="song not found")
        )
    )
    async with make_client() as client:
        with pytest.raises(LxnsNotFoundError):
            await client.get_song(1)


@pytest.mark.parametrize(
    ("status", "expected"), [(404, LxnsNotFoundError), (400, LxnsError), (403, LxnsError)]
)
@respx.mock
async def test_http_error_mapping(status: int, expected: type[LxnsError]) -> None:
    respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(
            status, json=envelope(None, success=False, code=status, message="boom")
        )
    )
    async with make_client() as client:
        with pytest.raises(expected):
            await client.get_song(1)


@respx.mock
async def test_non_json_response_raises() -> None:
    respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(200, text="<html>oops</html>")
    )
    async with make_client() as client:
        with pytest.raises(LxnsError, match="不是合法 JSON"):
            await client.get_song(1)


@respx.mock
async def test_rate_limit_retries_then_raises() -> None:
    route = respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(429, json=envelope(None, success=False, code=429))
    )
    async with make_client(retries=1) as client:
        with pytest.raises(LxnsRateLimitError):
            await client.get_song(1)
    assert route.call_count == 2


@respx.mock
async def test_server_error_retries_then_raises() -> None:
    route = respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(503, json=envelope(None, success=False, code=503))
    )
    async with make_client(retries=2) as client:
        with pytest.raises(LxnsServerError):
            await client.get_song(1)
    assert route.call_count == 3


@respx.mock
async def test_retries_on_429_then_succeeds() -> None:
    route = respx.get(f"{BASE}/chunithm/song/1").mock(
        side_effect=[
            httpx.Response(429, json=envelope(None, success=False, code=429)),
            httpx.Response(200, json=envelope({"id": 1, "title": "ok"})),
        ]
    )
    async with make_client(retries=1) as client:
        song = await client.get_song(1)
    assert song.title == "ok"
    assert route.call_count == 2


@respx.mock
async def test_transport_error_retries_then_succeeds() -> None:
    route = respx.get(f"{BASE}/chunithm/song/1").mock(
        side_effect=[
            httpx.ConnectTimeout("timeout"),
            httpx.Response(200, json=envelope({"id": 1, "title": "ok"})),
        ]
    )
    async with make_client(retries=1) as client:
        song = await client.get_song(1)
    assert song.title == "ok"
    assert route.call_count == 2


@respx.mock
async def test_transport_error_without_retry() -> None:
    respx.get(f"{BASE}/chunithm/song/1").mock(side_effect=httpx.ConnectError("refused"))
    async with make_client(retries=0) as client:
        with pytest.raises(LxnsError, match="网络错误"):
            await client.get_song(1)


@respx.mock
async def test_4xx_is_not_retried() -> None:
    route = respx.get(f"{BASE}/chunithm/song/1").mock(
        return_value=httpx.Response(404, json=envelope(None, success=False, code=404))
    )
    async with make_client(retries=3) as client:
        with pytest.raises(LxnsNotFoundError):
            await client.get_song(1)
    assert route.call_count == 1


# --------------------------------------------------------------------------- #
# 资源下载
# --------------------------------------------------------------------------- #


@respx.mock
async def test_download_returns_bytes() -> None:
    respx.get(JACKET).mock(return_value=httpx.Response(200, content=b"\x89PNG-fake"))
    async with make_client() as client:
        data = await client.download(JACKET)
    assert data == b"\x89PNG-fake"


@respx.mock
async def test_download_respects_max_bytes() -> None:
    respx.get(JACKET).mock(return_value=httpx.Response(200, content=b"x" * 100))
    async with make_client() as client:
        with pytest.raises(LxnsError, match="超过大小上限"):
            await client.download(JACKET, max_bytes=10)


@respx.mock
async def test_download_404() -> None:
    respx.get(JACKET).mock(return_value=httpx.Response(404))
    async with make_client() as client:
        with pytest.raises(LxnsNotFoundError):
            await client.download(JACKET)


@respx.mock
async def test_download_retries_on_503() -> None:
    route = respx.get(JACKET).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"png")]
    )
    async with make_client(retries=1) as client:
        data = await client.download(JACKET)
    assert data == b"png"
    assert route.call_count == 2


@respx.mock
async def test_download_does_not_expect_json() -> None:
    """资源不是 JSON，走独立分支，不能套信封解析。"""
    respx.get(JACKET).mock(return_value=httpx.Response(200, content=b"not json at all"))
    async with make_client() as client:
        assert await client.download(JACKET) == b"not json at all"


@respx.mock
async def test_no_auth_header_is_sent() -> None:
    """公共接口不该带任何鉴权头。"""
    route = respx.get(f"{BASE}/chunithm/song/list").mock(
        return_value=httpx.Response(200, json=envelope({"songs": [], "genres": [], "versions": []}))
    )
    async with make_client() as client:
        await client.get_song_list()
    request = route.calls[0].request
    assert "Authorization" not in request.headers
    assert "X-User-Token" not in request.headers
