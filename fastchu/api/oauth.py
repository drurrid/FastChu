"""落雪 OAuth 2.0 客户端（授权码 + PKCE + 刷新令牌）。

官方文档 <https://maimai.lxns.net/docs/oauth-guide>。

设计要点：

* **公共客户端 + PKCE**：机器人不持有用户的账号密码，只拿 OAuth 令牌；
  ``client_secret`` 可选，填了就作为额外的客户端认证随表单提交。
* **无回调模式**：OAuth 应用不登记回调地址时，授权页会直接显示一次性
  授权码（形如 ``JVJ6-VPTM-MGHZ``），用户复制回机器人即可，不需要公网回调。
* **令牌响应是扁平结构**（``access_token`` 等字段在顶层），与公共接口的
  ``{success, code, message, data}`` 信封不同；错误也是扁平的
  ``{error, error_description}``。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict

from ..errors import OAuthError, OAuthReauthRequired, OAuthScopeMissing
from .client import RETRYABLE_STATUS

__all__ = [
    "OAuthToken",
    "OAuthUserInfo",
    "LxnsOAuthClient",
    "DEFAULT_AUTHORIZE_URL",
    "DEFAULT_TOKEN_URL",
    "DEFAULT_USERINFO_URL",
    "build_authorize_url",
    "generate_code_verifier",
    "generate_state",
    "code_challenge_s256",
    "extract_authorization_code",
]

DEFAULT_AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
DEFAULT_TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
DEFAULT_USERINFO_URL = "https://maimai.lxns.net/api/v0/oauth/userinfo"

#: PKCE verifier 的推荐长度（RFC 7636 允许 43~128 个字符）
_VERIFIER_BYTES = 64


def generate_code_verifier() -> str:
    """生成 PKCE ``code_verifier``（URL 安全，长度约 86）。"""
    return secrets.token_urlsafe(_VERIFIER_BYTES)


def generate_state() -> str:
    """生成 OAuth ``state``（防 CSRF 的随机串）。"""
    return secrets.token_urlsafe(32)


def code_challenge_s256(verifier: str) -> str:
    """``BASE64URL(SHA256(verifier))``，去掉 ``=`` 填充。"""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorize_url(
    *,
    authorize_url: str = DEFAULT_AUTHORIZE_URL,
    client_id: str,
    scope: str,
    state: str | None = None,
    code_challenge: str | None = None,
    redirect_uri: str | None = None,
) -> str:
    """拼授权页地址。

    ``redirect_uri`` 为空时走落雪网页的「无回调」模式：授权完成后页面
    直接显示一次性授权码，用户复制回机器人即可。
    """
    parts = urlsplit(authorize_url)
    params: dict[str, str] = {
        key: values[-1]
        for key, values in parse_qs(parts.query, keep_blank_values=True).items()
        if values
    }
    params.update(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": scope,
        }
    )
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if state:
        params["state"] = state
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


def extract_authorization_code(text: str) -> str:
    """从用户输入里取授权码。

    允许直接粘贴授权码、带空格的授权码，或完整的回调 URL（取其 ``code``）。
    这里只做清洗，不校验格式——OAuth 授权码对客户端是不透明字符串。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if "code=" in raw or "://" in raw:
        try:
            values = parse_qs(urlsplit(raw).query).get("code")
        except ValueError:
            values = None
        if values:
            raw = unquote(values[0])
    raw = "".join(raw.split())
    if len(raw) > 512:
        raise OAuthError(
            "授权码过长",
            user_message="这段内容太长了，不像是授权码。请重新复制授权页显示的授权码。",
        )
    return raw


@dataclass(frozen=True)
class OAuthToken:
    """令牌端点返回的扁平结构。"""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900
    refresh_token: str | None = None
    scope: str = ""
    id_token: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OAuthToken":
        access = str(payload.get("access_token") or "").strip()
        if not access:
            raise OAuthError(
                "令牌响应缺少 access_token",
                error=str(payload.get("error") or "") or None,
                description=str(payload.get("error_description") or "") or None,
            )
        try:
            expires_in = int(payload.get("expires_in") or 900)
        except (TypeError, ValueError):
            expires_in = 900
        scope = payload.get("scope") or ""
        if isinstance(scope, (list, tuple)):
            scope = " ".join(str(item) for item in scope)
        refresh = payload.get("refresh_token")
        return cls(
            access_token=access,
            token_type=str(payload.get("token_type") or "Bearer"),
            expires_in=max(1, expires_in),
            refresh_token=str(refresh) if refresh else None,
            scope=str(scope),
            id_token=str(payload["id_token"]) if payload.get("id_token") else None,
        )


class OAuthUserInfo(BaseModel):
    """``GET /oauth/userinfo`` 的常用字段（其余字段忽略）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    friend_code: int | str | None = None
    rating: float | None = None
    level: int | None = None


class LxnsOAuthClient:
    """落雪 OAuth 接口客户端。

    只处理 OAuth 协议本身；个人成绩/称号在 :mod:`fastchu.api.user`。
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str = "",
        token_url: str = DEFAULT_TOKEN_URL,
        userinfo_url: str = DEFAULT_USERINFO_URL,
        timeout: float = 10.0,
        retries: int = 1,
        backoff: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.client_id = client_id.strip()
        self._client_secret = (client_secret or "").strip()
        self.token_url = token_url
        self.userinfo_url = userinfo_url
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = max(0.0, backoff)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "LxnsOAuthClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ 表单
    def _client_auth(self) -> dict[str, str]:
        if not self._client_secret:
            return {}
        return {"client_secret": self._client_secret}

    async def _post_token(self, data: Mapping[str, str]) -> OAuthToken:
        last_error: OAuthError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.post(
                    self.token_url,
                    data=dict(data),
                    headers={"Accept": "application/json"},
                )
            except httpx.TransportError as exc:
                last_error = OAuthError(f"OAuth 令牌请求网络错误: {exc}")
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))
                    continue
                raise last_error from exc

            status = response.status_code
            payload = self._json(response)
            if status in RETRYABLE_STATUS and attempt < self.retries:
                await asyncio.sleep(self.backoff * (2**attempt))
                continue
            if status >= 400:
                raise self._error_from(payload, status)
            if not isinstance(payload, dict):
                raise OAuthError("OAuth 令牌响应格式不正确", status=status)
            return OAuthToken.from_payload(payload)

        raise last_error or OAuthError("OAuth 令牌请求失败")

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            text = (response.text or "").strip()
            raise OAuthError(
                f"OAuth 响应不是合法 JSON (status={response.status_code}): {text[:200]}",
                status=response.status_code,
            ) from None

    @staticmethod
    def _error_from(payload: Any, status: int) -> OAuthError:
        error = None
        description = None
        if isinstance(payload, dict):
            error = str(payload.get("error") or "") or None
            description = str(payload.get("error_description") or "") or None
        return OAuthError(
            f"OAuth 请求失败({status}): {description or error or '未知错误'}",
            error=error,
            description=description,
            status=status,
        )

    # ------------------------------------------------------------------ 授权码
    async def exchange_code(
        self, code: str, code_verifier: str, *, redirect_uri: str | None = None
    ) -> OAuthToken:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": code,
            "code_verifier": code_verifier,
            **self._client_auth(),
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        return await self._post_token(data)

    async def refresh(self, refresh_token: str) -> OAuthToken:
        """刷新访问令牌。

        落雪每次刷新都会返回**新的 refresh_token 并立刻作废旧令牌**，
        调用方必须串行化刷新并立即持久化结果。
        """
        return await self._post_token(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": refresh_token,
                **self._client_auth(),
            }
        )

    # ------------------------------------------------------------------ 用户
    async def get_userinfo(self, access_token: str) -> OAuthUserInfo:
        last_error: OAuthError | None = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.get(self.userinfo_url, headers=headers)
            except httpx.TransportError as exc:
                last_error = OAuthError(f"OAuth 用户信息网络错误: {exc}")
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))
                    continue
                raise last_error from exc

            status = response.status_code
            payload = self._json(response)
            if status in RETRYABLE_STATUS and attempt < self.retries:
                await asyncio.sleep(self.backoff * (2**attempt))
                continue
            if status == 401:
                raise OAuthReauthRequired("OAuth 访问令牌无效", status=status)
            if status == 403:
                raise OAuthScopeMissing("OAuth 令牌缺少所需权限", status=status)
            if status >= 400:
                raise self._error_from(payload, status)

            data = payload
            if isinstance(payload, dict) and "success" in payload and "data" in payload:
                if not payload.get("success"):
                    raise self._error_from(payload, status)
                data = payload.get("data")
            return OAuthUserInfo.model_validate(data or {})

        raise last_error or OAuthError("OAuth 用户信息请求失败")
