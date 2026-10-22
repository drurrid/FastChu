"""落雪账号绑定流程。

负责三件事：

1. **发起授权**：生成 PKCE verifier / state，拼出无回调授权链接；
2. **提交授权码**：用 pending 里的 verifier 换取令牌，并保存玩家信息；
3. **取用令牌**：访问令牌将过期时自动刷新。

刷新令牌是**一次性轮换**的（每次刷新都会作废旧 refresh_token），所以：

* 同一用户的刷新必须串行（每个用户一把 ``asyncio.Lock``）；
* 新令牌拿到后必须立刻持久化，不能等进程退出再写。

绑定记录里没有账号密码，只有 OAuth 令牌；令牌不会出现在日志或回复里。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..api.oauth import (
    LxnsOAuthClient,
    build_authorize_url,
    code_challenge_s256,
    extract_authorization_code,
    generate_code_verifier,
    generate_state,
)
from ..config import BotSettings
from ..errors import (
    OAuthError,
    OAuthNotConfigured,
    OAuthPendingMissing,
    OAuthReauthRequired,
)
from .token_store import StoredAuthorization, TokenStore

__all__ = [
    "PendingAuthorization",
    "OAuthAuthorizationManager",
    "REFRESH_TOKEN_LIFETIME",
]

logger = logging.getLogger(__name__)

#: 落雪文档：refresh_token 有效期 30 天（仅用于状态展示）
REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600

#: 提前刷新访问令牌的安全余量（秒）
_ACCESS_SKEW = 60.0


@dataclass(frozen=True)
class PendingAuthorization:
    """一次等待用户提交授权码的授权会话。"""

    actor_id: str
    state: str
    authorize_url: str
    code_verifier: str
    scopes: str
    created_at: float

    def expired(self, now: float, ttl: float) -> bool:
        return now - self.created_at > ttl


class OAuthAuthorizationManager:
    """绑定、刷新、解绑。"""

    def __init__(
        self,
        settings: BotSettings,
        *,
        store: TokenStore | None = None,
        client: LxnsOAuthClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.store = (
            store
            if store is not None
            else TokenStore(settings.lxns_oauth_token_path)
        )
        self._clock = clock
        self._owns_client = client is None
        self.client = client or LxnsOAuthClient(
            client_id=settings.lxns_oauth_client_id,
            client_secret=settings.lxns_oauth_client_secret.get_secret_value(),
            token_url=settings.lxns_oauth_token_url,
            userinfo_url=settings.lxns_oauth_userinfo_url,
            timeout=settings.personal_timeout,
            retries=min(settings.http_retries, 2),
            backoff=settings.http_backoff,
        )
        self._pending: dict[str, PendingAuthorization] = {}
        self._pending_lock = asyncio.Lock()
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ 状态
    @property
    def configured(self) -> bool:
        return self.settings.lxns_oauth_configured

    def _scope_text(self) -> str:
        return " ".join(sorted(self.settings.lxns_oauth_scope_set))

    def _lock_for(self, actor_id: str) -> asyncio.Lock:
        lock = self._refresh_locks.get(actor_id)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[actor_id] = lock
        return lock

    async def _cleanup_pending(self, now: float) -> None:
        ttl = float(self.settings.lxns_oauth_pending_ttl)
        async with self._pending_lock:
            expired = [
                actor_id
                for actor_id, pending in self._pending.items()
                if pending.expired(now, ttl)
            ]
            for actor_id in expired:
                self._pending.pop(actor_id, None)

    # ------------------------------------------------------------------ 发起
    async def start(self, actor_id: str) -> PendingAuthorization:
        """生成授权链接并记住这次授权会话。"""
        if not self.configured:
            raise OAuthNotConfigured("落雪 OAuth 未配置")

        now = self._clock()
        await self._cleanup_pending(now)

        verifier = generate_code_verifier()
        state = generate_state()
        scopes = self._scope_text()
        authorize_url = build_authorize_url(
            authorize_url=self.settings.lxns_oauth_authorize_url,
            client_id=self.settings.lxns_oauth_client_id.strip(),
            scope=scopes,
            state=state,
            code_challenge=code_challenge_s256(verifier),
        )
        pending = PendingAuthorization(
            actor_id=str(actor_id),
            state=state,
            authorize_url=authorize_url,
            code_verifier=verifier,
            scopes=scopes,
            created_at=now,
        )
        async with self._pending_lock:
            self._pending[str(actor_id)] = pending
        return pending

    async def pending(self, actor_id: str) -> PendingAuthorization | None:
        """取当前待提交的授权会话（测试/状态展示用）。"""
        await self._cleanup_pending(self._clock())
        async with self._pending_lock:
            return self._pending.get(str(actor_id))

    # ------------------------------------------------------------------ 提交
    async def complete(self, actor_id: str, code: str) -> StoredAuthorization:
        """用授权码换取令牌并持久化。"""
        if not self.configured:
            raise OAuthNotConfigured("落雪 OAuth 未配置")

        normalized = extract_authorization_code(code)
        if not normalized:
            raise OAuthPendingMissing("授权码为空")

        actor = str(actor_id)
        async with self._lock_for(actor):
            now = self._clock()
            await self._cleanup_pending(now)
            async with self._pending_lock:
                pending = self._pending.get(actor)
            if pending is None:
                raise OAuthPendingMissing("没有待提交的授权会话")
            ttl = float(self.settings.lxns_oauth_pending_ttl)
            if pending.expired(now, ttl):
                async with self._pending_lock:
                    self._pending.pop(actor, None)
                raise OAuthPendingMissing("授权会话已过期")

            token = await self.client.exchange_code(
                normalized, pending.code_verifier
            )

            # 授权码是一次性的：换到令牌后这次会话就结束了。
            # 无论后续保存是否成功，都不该让用户拿同一个码再试一次。
            async with self._pending_lock:
                if self._pending.get(actor) is pending:
                    self._pending.pop(actor, None)

            userinfo = None
            try:
                userinfo = await self.client.get_userinfo(token.access_token)
            except OAuthError as exc:
                # 用户信息只用于展示；拿不到不影响令牌已经下发的事实
                logger.warning("读取落雪用户信息失败（绑定仍继续）：%s", exc)

            now = self._clock()
            record = StoredAuthorization(
                actor_id=actor,
                access_token=token.access_token,
                refresh_token=token.refresh_token or "",
                token_type=token.token_type,
                scope=token.scope or pending.scopes,
                expires_at=now + max(30.0, token.expires_in - 30.0),
                refresh_expires_at=now + REFRESH_TOKEN_LIFETIME,
                player_name=(userinfo.name if userinfo else "").strip(),
                friend_code=(
                    str(userinfo.friend_code)
                    if userinfo and userinfo.friend_code not in (None, "")
                    else ""
                ),
                created_at=now,
                updated_at=now,
            )
            try:
                self.store.put(record)
            except OSError as exc:
                raise OAuthError(
                    f"保存授权记录失败: {exc}",
                    user_message="授权成功但保存失败，请重新发 /授权 再试。",
                ) from exc
            return record

    # ------------------------------------------------------------------ 取令牌
    async def access_token(self, actor_id: str) -> str | None:
        """取可用的访问令牌；未绑定返回 ``None``。

        快过期时自动刷新。刷新失败属于授权层错误，会向上抛出
        :class:`OAuthReauthRequired`，由上层决定是否提示用户重新授权。
        """
        actor = str(actor_id)
        record = self.store.get(actor)
        if record is None:
            return None

        now = self._clock()
        if not record.needs_refresh(now, skew=_ACCESS_SKEW):
            return record.access_token

        async with self._lock_for(actor):
            # 等锁期间别的请求可能已经刷新过，必须重新读
            record = self.store.get(actor)
            if record is None:
                return None
            now = self._clock()
            if not record.needs_refresh(now, skew=_ACCESS_SKEW):
                return record.access_token

            if not record.refresh_token:
                self.store.delete(actor)
                raise OAuthReauthRequired("授权记录缺少 refresh_token")
            if record.refresh_expires_at and record.refresh_expires_at <= now:
                self.store.delete(actor)
                raise OAuthReauthRequired("刷新令牌已过期")

            try:
                token = await self.client.refresh(record.refresh_token)
            except OAuthError as exc:
                if exc.invalid_grant or exc.status in {400, 401}:
                    # refresh_token 已作废（可能被别处刷新/过期），只能重新授权
                    self.store.delete(actor)
                    raise OAuthReauthRequired(
                        "刷新令牌已失效", status=exc.status
                    ) from exc
                raise

            now = self._clock()
            record.access_token = token.access_token
            # 刷新令牌轮换：新值必须落盘，否则下次刷新会失败
            record.refresh_token = token.refresh_token or record.refresh_token
            record.token_type = token.token_type
            if token.scope:
                record.scope = token.scope
            record.expires_at = now + max(30.0, token.expires_in - 30.0)
            record.refresh_expires_at = now + REFRESH_TOKEN_LIFETIME
            try:
                self.store.put(record)
            except OSError as exc:
                # 内存里的新令牌仍然可用；只是进程重启后可能要重新授权
                logger.error("保存刷新后的令牌失败（进程内仍可用）：%s", exc)
            return record.access_token

    # ------------------------------------------------------------------ 查询/解绑
    def status(self, actor_id: str) -> StoredAuthorization | None:
        return self.store.get(str(actor_id))

    async def revoke(self, actor_id: str) -> bool:
        actor = str(actor_id)
        async with self._pending_lock:
            self._pending.pop(actor, None)
        return self.store.delete(actor)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
