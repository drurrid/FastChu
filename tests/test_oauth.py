"""落雪 OAuth 授权测试：PKCE、换令牌、刷新轮换与本地存储。

全部离线：令牌端点用 ``respx`` 模拟，授权管理器用假客户端和可控时钟。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from fastchu.api.oauth import (
    OAuthToken,
    OAuthUserInfo,
    LxnsOAuthClient,
    build_authorize_url,
    code_challenge_s256,
    extract_authorization_code,
    generate_code_verifier,
    generate_state,
)
from fastchu.errors import (
    OAuthError,
    OAuthNotConfigured,
    OAuthPendingMissing,
    OAuthReauthRequired,
    OAuthScopeMissing,
)
from fastchu.services.oauth_auth import OAuthAuthorizationManager
from fastchu.services.token_store import StoredAuthorization, TokenStore

from .conftest import make_settings

TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
USERINFO_URL = "https://maimai.lxns.net/api/v0/oauth/userinfo"


# --------------------------------------------------------------------------- #
# PKCE / 授权地址
# --------------------------------------------------------------------------- #


def test_pkce_matches_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifier_and_state_are_random_url_safe() -> None:
    first = generate_code_verifier()
    second = generate_code_verifier()
    assert first != second
    assert 43 <= len(first) <= 128
    assert all(ch.isalnum() or ch in "-_" for ch in first)
    assert generate_state() != generate_state()


def test_authorize_url_has_pkce_and_no_redirect() -> None:
    url = build_authorize_url(
        client_id="client-1",
        scope="read_player",
        state="state-1",
        code_challenge="challenge-1",
    )
    parts = urlsplit(url)
    params = parse_qs(parts.query)

    assert parts.scheme == "https"
    assert parts.netloc == "maimai.lxns.net"
    assert parts.path == "/oauth/authorize"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-1"]
    assert params["scope"] == ["read_player"]
    assert params["state"] == ["state-1"]
    assert params["code_challenge"] == ["challenge-1"]
    assert params["code_challenge_method"] == ["S256"]
    assert "redirect_uri" not in params


def test_authorize_url_keeps_existing_query_and_redirect() -> None:
    url = build_authorize_url(
        authorize_url="https://example.com/auth?tenant=lxns",
        client_id="c",
        scope="read_player",
        redirect_uri="https://bot.example/callback",
    )
    params = parse_qs(urlsplit(url).query)
    assert params["tenant"] == ["lxns"]
    assert params["redirect_uri"] == ["https://bot.example/callback"]


def test_extract_code_variants() -> None:
    assert extract_authorization_code("  JVJ6-VPTM-MGHZ  ") == "JVJ6-VPTM-MGHZ"
    assert extract_authorization_code("JVJ6 VPTM MGHZ") == "JVJ6VPTMMGHZ"
    assert (
        extract_authorization_code("https://example.com/cb?code=abc%2Fdef&state=x")
        == "abc/def"
    )
    assert extract_authorization_code("") == ""
    assert extract_authorization_code("   ") == ""


def test_extract_code_rejects_oversized_input() -> None:
    with pytest.raises(OAuthError) as info:
        extract_authorization_code("A" * 600)
    assert "太长" in info.value.user_message


def test_oauth_configured_requires_id_and_scope(tmp_path: pathlib.Path) -> None:
    disabled = make_settings(tmp_path, oauth_enabled=False, lxns_oauth_client_id="c")
    assert not disabled.lxns_oauth_configured

    missing_id = make_settings(tmp_path, oauth_enabled=True, lxns_oauth_client_id="")
    assert not missing_id.lxns_oauth_configured

    missing_scope = make_settings(
        tmp_path, oauth_enabled=True, lxns_oauth_client_id="c", lxns_oauth_scopes="read_user"
    )
    assert not missing_scope.lxns_oauth_configured

    enabled = make_settings(
        tmp_path,
        oauth_enabled=True,
        lxns_oauth_client_id="c",
        lxns_oauth_scopes="read_player, read_user",
    )
    assert enabled.lxns_oauth_configured
    assert enabled.lxns_oauth_scope_set == frozenset({"read_player", "read_user"})


# --------------------------------------------------------------------------- #
# 令牌端点
# --------------------------------------------------------------------------- #


def token_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "access_token": "access-1",
        "token_type": "Bearer",
        "expires_in": 900,
        "refresh_token": "refresh-1",
        "scope": "read_player",
    }
    payload.update(overrides)
    return payload


def make_oauth_client(**kwargs: object) -> LxnsOAuthClient:
    defaults: dict[str, object] = {
        "client_id": "client-1",
        "token_url": TOKEN_URL,
        "userinfo_url": USERINFO_URL,
        "retries": 0,
        "backoff": 0.0,
    }
    defaults.update(kwargs)
    return LxnsOAuthClient(**defaults)  # type: ignore[arg-type]


@respx.mock
async def test_exchange_code_posts_form_and_parses_flat_response() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=token_payload())
    )
    async with make_oauth_client() as client:
        token = await client.exchange_code("code-1", "verifier-1")

    assert token.access_token == "access-1"
    assert token.refresh_token == "refresh-1"
    assert token.expires_in == 900
    assert token.scope == "read_player"

    form = parse_qs(route.calls[0].request.content.decode("utf-8"))
    assert form["grant_type"] == ["authorization_code"]
    assert form["client_id"] == ["client-1"]
    assert form["code"] == ["code-1"]
    assert form["code_verifier"] == ["verifier-1"]
    assert "redirect_uri" not in form
    assert "client_secret" not in form


@respx.mock
async def test_client_secret_is_sent_when_configured() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=token_payload())
    )
    async with make_oauth_client(client_secret="secret-1") as client:
        await client.exchange_code("code-1", "verifier-1")

    form = parse_qs(route.calls[0].request.content.decode("utf-8"))
    assert form["client_secret"] == ["secret-1"]


@respx.mock
async def test_refresh_uses_refresh_grant() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json=token_payload(access_token="access-2", refresh_token="refresh-2")
        )
    )
    async with make_oauth_client() as client:
        token = await client.refresh("refresh-1")

    form = parse_qs(route.calls[0].request.content.decode("utf-8"))
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-1"]
    assert token.access_token == "access-2"
    assert token.refresh_token == "refresh-2"


@respx.mock
async def test_invalid_grant_is_not_retried() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "code expired"},
        )
    )
    async with make_oauth_client(retries=3) as client:
        with pytest.raises(OAuthError) as info:
            await client.exchange_code("bad", "verifier")

    assert info.value.invalid_grant
    assert info.value.status == 400
    assert route.call_count == 1
    assert "重新发 /授权" in info.value.user_message


@respx.mock
async def test_server_error_is_retried() -> None:
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(500, json={"error": "server_error"}),
            httpx.Response(200, json=token_payload()),
        ]
    )
    async with make_oauth_client(retries=1) as client:
        token = await client.refresh("refresh-1")

    assert token.access_token == "access-1"
    assert route.call_count == 2


@respx.mock
async def test_missing_access_token_in_payload_is_an_error() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"token_type": "Bearer"})
    )
    async with make_oauth_client() as client:
        with pytest.raises(OAuthError):
            await client.exchange_code("code-1", "verifier-1")


@respx.mock
async def test_userinfo_returns_profile() -> None:
    route = respx.get(USERINFO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "code": 200,
                "data": {"name": "Tester", "friend_code": 12345678, "rating": 16.2},
            },
        )
    )
    async with make_oauth_client() as client:
        info = await client.get_userinfo("access-1")

    assert info == OAuthUserInfo(name="Tester", friend_code=12345678, rating=16.2)
    assert route.calls[0].request.headers["Authorization"] == "Bearer access-1"


@respx.mock
async def test_userinfo_401_requires_reauth() -> None:
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(401, json={}))
    async with make_oauth_client() as client:
        with pytest.raises(OAuthReauthRequired):
            await client.get_userinfo("expired")


@respx.mock
async def test_userinfo_403_means_missing_scope() -> None:
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(403, json={}))
    async with make_oauth_client() as client:
        with pytest.raises(OAuthScopeMissing):
            await client.get_userinfo("token")


# --------------------------------------------------------------------------- #
# 本地存储
# --------------------------------------------------------------------------- #


def stored(actor_id: str = "42", **overrides: object) -> StoredAuthorization:
    values: dict[str, object] = {
        "actor_id": actor_id,
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "scope": "read_player",
        "expires_at": 2_000_000_000.0,
        "refresh_expires_at": 2_100_000_000.0,
        "player_name": "Tester",
        "friend_code": "12345678",
    }
    values.update(overrides)
    return StoredAuthorization(**values)  # type: ignore[arg-type]


def test_token_store_roundtrip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "nested" / "tokens.json"
    store = TokenStore(path)
    assert store.get("42") is None

    store.put(stored())
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["users"]["42"]["access_token"] == "access-1"
    assert payload["users"]["42"]["friend_code"] == "12345678"
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".oauth_tokens-")]

    reloaded = TokenStore(path)
    record = reloaded.get("42")
    assert record is not None
    assert record.player_name == "Tester"
    assert record.refresh_token == "refresh-1"
    assert len(reloaded) == 1
    assert [item.actor_id for item in reloaded.all()] == ["42"]

    assert reloaded.delete("42") is True
    assert reloaded.delete("42") is False
    assert TokenStore(path).get("42") is None


def test_token_store_tolerates_corrupt_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text("{not json", encoding="utf-8")

    store = TokenStore(path)
    assert store.get("42") is None
    assert len(store) == 0

    store.put(stored())
    assert TokenStore(path).get("42") is not None


def test_token_store_repairs_malformed_entries(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "users": {
                    "broken": {"refresh_token": "only-refresh"},
                    "bad-time": {
                        "access_token": "access-1",
                        "expires_at": "not-a-number",
                        "created_at": "not-a-number",
                    },
                    "ok": {"access_token": "access-1", "friend_code": 12345678},
                }
            }
        ),
        encoding="utf-8",
    )
    store = TokenStore(path)
    assert store.get("broken") is None
    repaired = store.get("bad-time")
    assert repaired is not None
    assert repaired.access_token == "access-1"
    assert repaired.expires_at == 0.0
    assert repaired.created_at == 0.0
    record = store.get("ok")
    assert record is not None
    assert record.friend_code == "12345678"


def test_needs_refresh_uses_skew() -> None:
    record = stored(expires_at=1000.0)
    assert not record.needs_refresh(939.0, skew=60.0)
    assert record.needs_refresh(940.0, skew=60.0)
    assert record.needs_refresh(1001.0, skew=60.0)


# --------------------------------------------------------------------------- #
# 授权管理器
# --------------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeOAuthClient:
    def __init__(self) -> None:
        self.exchange_calls: list[tuple[str, str]] = []
        self.refresh_calls: list[str] = []
        self.userinfo_calls: list[str] = []
        self.exchange_token = OAuthToken(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_in=900,
            scope="read_player",
        )
        self.refresh_result = OAuthToken(
            access_token="access-2",
            refresh_token="refresh-2",
            expires_in=900,
            scope="read_player",
        )
        self.userinfo = OAuthUserInfo(name="Tester", friend_code=12345678)
        self.exchange_error: Exception | None = None
        self.refresh_error: Exception | None = None
        self.userinfo_error: Exception | None = None
        self.refresh_delay = 0.0

    async def exchange_code(
        self, code: str, code_verifier: str, *, redirect_uri: str | None = None
    ) -> OAuthToken:
        if self.exchange_error is not None:
            raise self.exchange_error
        self.exchange_calls.append((code, code_verifier))
        return self.exchange_token

    async def refresh(self, refresh_token: str) -> OAuthToken:
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refresh_calls.append(refresh_token)
        return self.refresh_result

    async def get_userinfo(self, access_token: str) -> OAuthUserInfo:
        if self.userinfo_error is not None:
            raise self.userinfo_error
        self.userinfo_calls.append(access_token)
        return self.userinfo

    async def aclose(self) -> None:
        return None


def configured_settings(tmp_path: pathlib.Path, **overrides: object):
    values: dict[str, object] = {
        "oauth_enabled": True,
        "lxns_oauth_client_id": "client-1",
        "lxns_oauth_client_secret": "secret-1",
        "lxns_oauth_token_path": tmp_path / "tokens.json",
    }
    values.update(overrides)
    return make_settings(tmp_path, **values)


def make_manager(
    tmp_path: pathlib.Path, *, settings=None, client=None, clock=None
):
    settings = settings or configured_settings(tmp_path)
    client = client or FakeOAuthClient()
    clock = clock or Clock()
    store = TokenStore(settings.lxns_oauth_token_path)
    manager = OAuthAuthorizationManager(
        settings, store=store, client=client, clock=clock
    )
    return manager, client, store, clock


async def test_start_requires_configuration(tmp_path: pathlib.Path) -> None:
    manager, _client, _store, _clock = make_manager(
        tmp_path, settings=make_settings(tmp_path, oauth_enabled=False)
    )
    with pytest.raises(OAuthNotConfigured):
        await manager.start("42")
    with pytest.raises(OAuthNotConfigured):
        await manager.complete("42", "code")


async def test_start_builds_pending_authorization(tmp_path: pathlib.Path) -> None:
    manager, _client, _store, _clock = make_manager(tmp_path)
    pending = await manager.start("42")

    params = parse_qs(urlsplit(pending.authorize_url).query)
    assert params["client_id"] == ["client-1"]
    assert params["scope"] == ["read_player"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [pending.state]
    assert code_challenge_s256(pending.code_verifier) == params["code_challenge"][0]
    assert await manager.pending("42") is pending


async def test_complete_exchanges_code_and_persists(tmp_path: pathlib.Path) -> None:
    manager, client, store, _clock = make_manager(tmp_path)
    await manager.start("42")

    record = await manager.complete("42", "  CODE-1  ")

    assert client.exchange_calls[0][0] == "CODE-1"
    assert record.player_name == "Tester"
    assert record.friend_code == "12345678"
    assert record.scope == "read_player"
    assert store.get("42") is not None
    assert (tmp_path / "tokens.json").is_file()
    assert await manager.pending("42") is None
    assert manager.status("42") is not None


async def test_complete_without_pending_or_code(tmp_path: pathlib.Path) -> None:
    manager, _client, _store, _clock = make_manager(tmp_path)
    with pytest.raises(OAuthPendingMissing):
        await manager.complete("42", "CODE")

    await manager.start("42")
    with pytest.raises(OAuthPendingMissing):
        await manager.complete("42", "   ")


async def test_complete_survives_userinfo_failure(tmp_path: pathlib.Path) -> None:
    manager, client, store, _clock = make_manager(tmp_path)
    client.userinfo_error = OAuthError("userinfo down")
    await manager.start("42")

    record = await manager.complete("42", "CODE")
    assert record.player_name == ""
    assert store.get("42") is not None


async def test_pending_expires(tmp_path: pathlib.Path) -> None:
    manager, _client, _store, clock = make_manager(tmp_path)
    await manager.start("42")
    clock.now += manager.settings.lxns_oauth_pending_ttl + 1

    assert await manager.pending("42") is None
    with pytest.raises(OAuthPendingMissing):
        await manager.complete("42", "CODE")


async def test_access_token_returns_none_for_unbound(tmp_path: pathlib.Path) -> None:
    manager, _client, _store, _clock = make_manager(tmp_path)
    assert await manager.access_token("42") is None


async def test_access_token_uses_valid_token_without_refresh(tmp_path: pathlib.Path) -> None:
    manager, client, _store, _clock = make_manager(tmp_path)
    await manager.start("42")
    await manager.complete("42", "CODE")

    assert await manager.access_token("42") == "access-1"
    assert client.refresh_calls == []


async def test_access_token_refreshes_and_rotates_refresh_token(
    tmp_path: pathlib.Path,
) -> None:
    manager, client, store, clock = make_manager(tmp_path)
    await manager.start("42")
    await manager.complete("42", "CODE")
    clock.now += 900

    assert await manager.access_token("42") == "access-2"
    assert client.refresh_calls == ["refresh-1"]

    record = store.get("42")
    assert record is not None
    assert record.access_token == "access-2"
    assert record.refresh_token == "refresh-2"

    on_disk = json.loads((tmp_path / "tokens.json").read_text(encoding="utf-8"))
    assert on_disk["users"]["42"]["refresh_token"] == "refresh-2"


async def test_concurrent_access_requests_refresh_once(tmp_path: pathlib.Path) -> None:
    manager, client, _store, clock = make_manager(tmp_path)
    await manager.start("42")
    await manager.complete("42", "CODE")
    clock.now += 900
    client.refresh_delay = 0.02

    results = await asyncio.gather(
        manager.access_token("42"),
        manager.access_token("42"),
        manager.access_token("42"),
    )

    assert results == ["access-2", "access-2", "access-2"]
    assert client.refresh_calls == ["refresh-1"]


async def test_invalid_grant_deletes_binding(tmp_path: pathlib.Path) -> None:
    manager, client, store, clock = make_manager(tmp_path)
    await manager.start("42")
    await manager.complete("42", "CODE")
    clock.now += 900
    client.refresh_error = OAuthError(
        "refresh rejected", error="invalid_grant", status=400
    )

    with pytest.raises(OAuthReauthRequired):
        await manager.access_token("42")

    assert store.get("42") is None


async def test_expired_refresh_token_deletes_binding(tmp_path: pathlib.Path) -> None:
    manager, _client, store, clock = make_manager(tmp_path)
    await manager.start("42")
    record = await manager.complete("42", "CODE")
    record.expires_at = clock.now
    record.refresh_expires_at = clock.now - 1
    store.put(record)

    with pytest.raises(OAuthReauthRequired):
        await manager.access_token("42")

    assert store.get("42") is None


async def test_missing_refresh_token_deletes_binding(tmp_path: pathlib.Path) -> None:
    manager, _client, store, clock = make_manager(tmp_path)
    await manager.start("42")
    record = await manager.complete("42", "CODE")
    record.expires_at = clock.now
    record.refresh_token = ""
    store.put(record)

    with pytest.raises(OAuthReauthRequired):
        await manager.access_token("42")

    assert store.get("42") is None


async def test_revoke_clears_pending_and_record(tmp_path: pathlib.Path) -> None:
    manager, _client, store, _clock = make_manager(tmp_path)
    await manager.start("42")
    await manager.complete("42", "CODE")

    assert await manager.revoke("42") is True
    assert manager.status("42") is None
    assert await manager.pending("42") is None
    assert await manager.revoke("42") is False

    await manager.start("42")
    assert await manager.revoke("42") is False
    assert await manager.pending("42") is None
    assert store.get("42") is None
