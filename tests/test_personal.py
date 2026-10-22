"""个人数据增强测试：成绩排版、称号状态、降级与授权命令。

核心约束：**个人数据只是增强**。未绑定、未配置、接口失败、超时都不能
改变基础查询的结果；令牌内容永远不出现在用户可见文本里。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from fastchu.api.user import OwnedTrophy, PersonalScore
from fastchu.errors import (
    LxnsError,
    OAuthError,
    OAuthNotConfigured,
    OAuthReauthRequired,
    OAuthScopeMissing,
)
from fastchu.models import LevelIndex, Song, SongDifficulty, Trophy
from fastchu.services.commands import (
    Reply,
    run_auth_code,
    run_auth_revoke,
    run_auth_start,
    run_auth_status,
)
from fastchu.services.personal import (
    PersonalService,
    format_song_scores,
    format_title_status,
)
from fastchu.services.token_store import StoredAuthorization, TokenStore

from .conftest import make_settings


def make_song(song_id: int = 159) -> Song:
    return Song(
        id=song_id,
        title="Test Song",
        difficulties=[
            SongDifficulty(difficulty=LevelIndex.EXPERT, level="10"),
            SongDifficulty(difficulty=LevelIndex.MASTER, level="14+"),
            SongDifficulty(difficulty=LevelIndex.ULTIMA, level="15"),
        ],
    )


# --------------------------------------------------------------------------- #
# 成绩排版
# --------------------------------------------------------------------------- #


def test_scores_keep_best_per_difficulty_and_sort_desc() -> None:
    song = make_song()
    scores = [
        PersonalScore(
            id=159,
            level_index=3,
            level="14+",
            score=1_005_000,
            rank="ss",
            full_combo="fullcombo",
        ),
        PersonalScore(
            id=159,
            level_index=3,
            level="14+",
            score=1_008_765,
            rank="sss",
            full_combo="alljusticecritical",
        ),
        PersonalScore(
            id=159,
            level_index=2,
            level="10",
            score=990_000,
            rank="sp",
            full_chain="fullchain",
        ),
        # 别的曲子、零分记录都不参与
        PersonalScore(id=160, level_index=3, score=1_009_000),
        PersonalScore(id=159, level_index=4, score=0),
    ]

    assert format_song_scores(song, scores) == (
        "【你的成绩】\n"
        "MAS 14+ · 1,008,765 · SSS · AJC\n"
        "EXP 10 · 990,000 · S+ · FULL CHAIN"
    )


def test_scores_fall_back_to_chart_level_and_rank() -> None:
    song = make_song()
    line = format_song_scores(
        song, [PersonalScore(id=159, level_index=3, score=1_007_500)]
    )
    assert line.splitlines()[1] == "MAS 14+ · 1,007,500 · SSS"


def test_unknown_level_index_is_listed_last() -> None:
    song = make_song()
    text = format_song_scores(
        song,
        [
            PersonalScore(id=159, level_index=None, score=900_000, rank="a"),
            PersonalScore(id=159, level_index=3, score=950_000, rank="aaa"),
        ],
    )
    assert text.splitlines()[1:] == [
        "MAS 14+ · 950,000 · AAA",
        "难度? · 900,000 · A",
    ]


def test_scores_empty_message() -> None:
    song = make_song()
    assert format_song_scores(song, []) == "【你的成绩】\n还没有这首歌的成绩。"
    assert (
        format_song_scores(song, [PersonalScore(id=1, level_index=3, score=1_000_000)])
        == "【你的成绩】\n还没有这首歌的成绩。"
    )


def test_title_status_owned_and_not_owned() -> None:
    trophy = Trophy(id=7, name="Test")
    assert format_title_status(trophy, set()) == "【你的状态】未获得"
    assert format_title_status(trophy, {7}) == "【你的状态】已获得"


def test_jp_only_title_is_unknown() -> None:
    trophy = Trophy(id=900_001, name="JP only")
    assert format_title_status(trophy, set()) == (
        "【你的状态】日服限定，落雪无记录，无法判断"
    )


# --------------------------------------------------------------------------- #
# 增强流程
# --------------------------------------------------------------------------- #


class FakeAuth:
    def __init__(self, token: str | None = "access-1", error: Exception | None = None):
        self.token = token
        self.error = error
        self.calls = 0

    async def access_token(self, actor_id: str) -> str | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.token

    def status(self, actor_id: str):
        return None

    async def revoke(self, actor_id: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class FakeUserClient:
    def __init__(
        self,
        scores: list[PersonalScore] | None = None,
        trophies: list[OwnedTrophy] | None = None,
    ):
        self.scores = scores or []
        self.trophies = trophies or []
        self.score_calls = 0
        self.trophy_calls = 0
        self.error: Exception | None = None

    async def get_scores(self, token: str) -> list[PersonalScore]:
        self.score_calls += 1
        if self.error is not None:
            raise self.error
        return self.scores

    async def get_owned_trophies(self, token: str) -> list[OwnedTrophy]:
        self.trophy_calls += 1
        if self.error is not None:
            raise self.error
        return self.trophies

    async def aclose(self) -> None:
        return None


def enabled_settings(tmp_path, **overrides: object):
    values: dict[str, object] = {
        "oauth_enabled": True,
        "lxns_oauth_client_id": "client-1",
        "lxns_oauth_token_path": tmp_path / "tokens.json",
    }
    values.update(overrides)
    return make_settings(tmp_path, **values)


def make_personal(tmp_path, *, auth=None, user_client=None, **overrides):
    settings = enabled_settings(tmp_path, **overrides)
    store = TokenStore(settings.lxns_oauth_token_path)
    auth = auth or FakeAuth()
    user_client = user_client or FakeUserClient()
    service = PersonalService(
        settings, store=store, auth=auth, user_client=user_client
    )
    return service, auth, user_client


async def test_enrich_detail_appends_scores(tmp_path) -> None:
    song = make_song()
    score = PersonalScore(
        id=159,
        level_index=3,
        level="14+",
        score=1_008_765,
        rank="sss",
        full_combo="alljusticecritical",
    )
    user_client = FakeUserClient(scores=[score])
    service, _auth, _client = make_personal(tmp_path, user_client=user_client)
    reply = Reply(kind="detail", texts=["曲目信息"], song=song)

    result = await service.enrich(reply, "42")

    assert result is reply
    assert result.texts == [
        "曲目信息\n\n【你的成绩】\nMAS 14+ · 1,008,765 · SSS · AJC"
    ]
    assert user_client.score_calls == 1


async def test_enrich_title_appends_owned_status(tmp_path) -> None:
    user_client = FakeUserClient(trophies=[OwnedTrophy(id=7)])
    service, _auth, _client = make_personal(tmp_path, user_client=user_client)
    reply = Reply(kind="title", texts=["称号信息"], trophy=Trophy(id=7, name="T"))

    result = await service.enrich(reply, "42")

    assert result.texts[0].endswith("【你的状态】已获得")
    assert user_client.trophy_calls == 1
    assert user_client.score_calls == 0


async def test_enrich_is_skipped_when_unbound(tmp_path) -> None:
    service, auth, user_client = make_personal(tmp_path, auth=FakeAuth(token=None))
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    result = await service.enrich(reply, "42")

    assert result.texts == ["曲目信息"]
    assert auth.calls == 1
    assert user_client.score_calls == 0


async def test_enrich_is_skipped_when_disabled(tmp_path) -> None:
    service, auth, user_client = make_personal(tmp_path)
    service.settings = make_settings(tmp_path, oauth_enabled=False)
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    result = await service.enrich(reply, "42")

    assert result.texts == ["曲目信息"]
    assert auth.calls == 0
    assert user_client.score_calls == 0


async def test_enrich_ignores_other_reply_kinds(tmp_path) -> None:
    service, auth, user_client = make_personal(tmp_path)
    reply = Reply(kind="search", texts=["候选"])

    result = await service.enrich(reply, "42")

    assert result.texts == ["候选"]
    assert auth.calls == 0
    assert user_client.score_calls == 0


async def test_enrich_without_actor_is_noop(tmp_path) -> None:
    service, auth, _client = make_personal(tmp_path)
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    assert await service.enrich(reply, None) is reply
    assert auth.calls == 0


async def test_reauth_failure_appends_hint_and_keeps_reply(tmp_path) -> None:
    service, _auth, _client = make_personal(
        tmp_path, auth=FakeAuth(error=OAuthReauthRequired("expired"))
    )
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    result = await service.enrich(reply, "42")

    assert result.texts[0].startswith("曲目信息")
    assert "提示：" in result.texts[0]
    assert "重新发 /授权" in result.texts[0]


async def test_scope_failure_appends_hint(tmp_path) -> None:
    service, _auth, _client = make_personal(
        tmp_path, auth=FakeAuth(error=OAuthScopeMissing("no scope"))
    )
    reply = Reply(kind="title", texts=["称号信息"], trophy=Trophy(id=7, name="T"))

    result = await service.enrich(reply, "42")

    assert "read_player" in result.texts[0]


async def test_network_failure_leaves_reply_unchanged(tmp_path) -> None:
    user_client = FakeUserClient()
    user_client.error = LxnsError("boom")
    service, _auth, _client = make_personal(tmp_path, user_client=user_client)
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    result = await service.enrich(reply, "42")

    assert result.texts == ["曲目信息"]


async def test_timeout_leaves_reply_unchanged(tmp_path) -> None:
    service, _auth, _client = make_personal(tmp_path, personal_timeout=0.01)

    async def slow(_reply, _actor):
        await asyncio.sleep(0.05)
        return "迟到"

    service._build_block = slow  # type: ignore[method-assign]
    reply = Reply(kind="detail", texts=["曲目信息"], song=make_song())

    result = await service.enrich(reply, "42")

    assert result.texts == ["曲目信息"]


async def test_personal_data_is_cached_per_actor(tmp_path) -> None:
    score = PersonalScore(id=159, level_index=3, score=1_000_000, rank="ss")
    user_client = FakeUserClient(scores=[score])
    service, _auth, _client = make_personal(tmp_path, user_client=user_client)
    song = make_song()

    await service.enrich(Reply(kind="detail", texts=["a"], song=song), "42")
    await service.enrich(Reply(kind="detail", texts=["b"], song=song), "42")
    assert user_client.score_calls == 1

    service.invalidate("42")
    await service.enrich(Reply(kind="detail", texts=["c"], song=song), "42")
    assert user_client.score_calls == 2


async def test_title_cache_is_separate_from_scores(tmp_path) -> None:
    user_client = FakeUserClient(
        scores=[PersonalScore(id=159, level_index=3, score=1_000_000)],
        trophies=[OwnedTrophy(id=7)],
    )
    service, _auth, _client = make_personal(tmp_path, user_client=user_client)

    await service.enrich(
        Reply(kind="detail", texts=["a"], song=make_song()), "42"
    )
    await service.enrich(
        Reply(kind="title", texts=["b"], trophy=Trophy(id=7, name="T")), "42"
    )

    assert user_client.score_calls == 1
    assert user_client.trophy_calls == 1


# --------------------------------------------------------------------------- #
# 授权命令
# --------------------------------------------------------------------------- #


class FakePersonal:
    def __init__(self) -> None:
        self.record = None
        self.pending_url = "https://maimai.lxns.net/oauth/authorize?client_id=c"
        self.bind_result = None
        self.start_error: Exception | None = None
        self.bind_error: Exception | None = None
        self.revoke_error: Exception | None = None
        self.revoke_result = True
        self.completed_code = ""

    def status(self, actor_id: str):
        return self.record

    async def start(self, actor_id: str):
        if self.start_error is not None:
            raise self.start_error
        return SimpleNamespace(authorize_url=self.pending_url)

    async def bind(self, actor_id: str, code: str):
        if self.bind_error is not None:
            raise self.bind_error
        self.completed_code = code
        return self.bind_result

    async def revoke(self, actor_id: str) -> bool:
        if self.revoke_error is not None:
            raise self.revoke_error
        return self.revoke_result


def service_with(personal: FakePersonal):
    return SimpleNamespace(personal=personal)


async def test_auth_start_returns_no_callback_instructions(tmp_path) -> None:
    personal = FakePersonal()
    reply = await run_auth_start(
        service_with(personal), make_settings(tmp_path), "42"
    )

    assert reply.kind == "auth"
    text = reply.texts[0]
    assert personal.pending_url in text
    assert "read_player" in text
    assert "10 分钟内有效" in text
    assert "请不要发到群里" in text


async def test_auth_start_warns_before_overwriting_existing_binding(tmp_path) -> None:
    personal = FakePersonal()
    personal.record = StoredAuthorization(
        actor_id="42", access_token="A", player_name="Tester", friend_code="1234"
    )
    reply = await run_auth_start(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert "你已经绑定了 Tester（好友码 1234）" in reply.texts[0]


async def test_auth_start_reports_unconfigured(tmp_path) -> None:
    personal = FakePersonal()
    personal.start_error = OAuthNotConfigured("no oauth")
    reply = await run_auth_start(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert reply.kind == "hint"
    assert "还没配置好" in reply.texts[0]


async def test_auth_start_without_actor(tmp_path) -> None:
    reply = await run_auth_start(
        service_with(FakePersonal()), make_settings(tmp_path), ""
    )
    assert reply.kind == "hint"


async def test_auth_code_binds_and_never_echoes_tokens(tmp_path) -> None:
    personal = FakePersonal()
    personal.bind_result = StoredAuthorization(
        actor_id="42",
        access_token="SECRET-ACCESS",
        refresh_token="SECRET-REFRESH",
        player_name="Tester",
        friend_code="12345678",
    )
    reply = await run_auth_code(
        service_with(personal), make_settings(tmp_path), "42", "CODE-1"
    )

    assert reply.kind == "auth"
    text = reply.texts[0]
    assert personal.completed_code == "CODE-1"
    assert "绑定成功：Tester（好友码 12345678）" in text
    assert "SECRET-ACCESS" not in text
    assert "SECRET-REFRESH" not in text


async def test_auth_code_maps_invalid_grant(tmp_path) -> None:
    personal = FakePersonal()
    personal.bind_error = OAuthError("bad code", error="invalid_grant", status=400)
    reply = await run_auth_code(
        service_with(personal), make_settings(tmp_path), "42", "BAD"
    )

    assert reply.kind == "error"
    assert "重新发 /授权" in reply.texts[0]


async def test_auth_code_requires_code(tmp_path) -> None:
    reply = await run_auth_code(
        service_with(FakePersonal()), make_settings(tmp_path), "42", "  "
    )
    assert reply.kind == "hint"
    assert "/授权" in reply.texts[0]


async def test_auth_status_hides_tokens(tmp_path) -> None:
    personal = FakePersonal()
    personal.record = StoredAuthorization(
        actor_id="42",
        access_token="SECRET-ACCESS",
        refresh_token="SECRET-REFRESH",
        player_name="Tester",
        friend_code="12345678",
        expires_at=time.time() + 600,
        refresh_expires_at=time.time() + 10 * 86400,
        scope="read_player",
    )
    reply = await run_auth_status(
        service_with(personal), make_settings(tmp_path), "42"
    )

    assert reply.kind == "auth"
    text = reply.texts[0]
    assert "Tester（好友码 12345678）" in text
    assert "访问令牌：有效" in text
    assert "刷新令牌：约剩 10 天" in text
    assert "权限：read_player" in text
    assert "SECRET-ACCESS" not in text
    assert "SECRET-REFRESH" not in text


async def test_auth_status_expired_access(tmp_path) -> None:
    personal = FakePersonal()
    personal.record = StoredAuthorization(
        actor_id="42",
        access_token="A",
        expires_at=time.time() - 1,
        refresh_expires_at=time.time() + 86400,
    )
    reply = await run_auth_status(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert "访问令牌：已过期，下次查询时自动刷新" in reply.texts[0]


async def test_auth_status_unbound(tmp_path) -> None:
    reply = await run_auth_status(
        service_with(FakePersonal()), make_settings(tmp_path), "42"
    )
    assert reply.kind == "hint"
    assert "/授权" in reply.texts[0]


async def test_auth_revoke_variants(tmp_path) -> None:
    personal = FakePersonal()
    reply = await run_auth_revoke(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert reply.kind == "auth"
    assert "已解绑" in reply.texts[0]

    personal.revoke_result = False
    reply = await run_auth_revoke(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert reply.kind == "hint"
    assert "还没有绑定" in reply.texts[0]

    personal.revoke_error = OSError("disk")
    reply = await run_auth_revoke(
        service_with(personal), make_settings(tmp_path), "42"
    )
    assert reply.kind == "error"
