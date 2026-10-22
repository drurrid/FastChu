"""异常体系。

区分两类信息：``str(exc)`` 给开发者看（含状态码等技术细节），
``exc.user_message`` 给 QQ 用户看（一句话说清怎么办）。

查歌只需要**公共接口**，鉴权异常只用于可选的落雪账号绑定（``/授权``）：
授权码无效、令牌过期、权限不足等。绑定失败永远只影响个人数据，
不会影响基础查歌/查称号。
"""

from __future__ import annotations

__all__ = [
    "ChunithmBotError",
    "ConfigurationError",
    "DataSourceError",
    "LxnsError",
    "LxnsNotFoundError",
    "LxnsRateLimitError",
    "LxnsServerError",
    "SongNotFoundError",
    "AssetUnavailableError",
    "QuerySyntaxError",
    "OAuthError",
    "OAuthNotConfigured",
    "OAuthPendingMissing",
    "OAuthReauthRequired",
    "OAuthScopeMissing",
]


class ChunithmBotError(Exception):
    """本项目所有可预期异常的基类。

    ``user_message`` 是给 QQ 用户看的一句话；默认取类属性 ``default_user_message``，
    也可以用 ``user_message=`` 针对具体场景覆盖——这样既有兜底文案，
    又能在关键场景（例如条件写错）给出更准确的指引。
    """

    #: 默认给 QQ 用户看的提示
    default_user_message = "出了点问题，请稍后再试或联系管理员。"

    def __init__(self, message: str = "", *, user_message: str | None = None) -> None:
        super().__init__(message)
        self._custom_user_message = user_message

    @property
    def user_message(self) -> str:
        return self._custom_user_message or self.default_user_message


class ConfigurationError(ChunithmBotError):
    default_user_message = "机器人配置有误，请联系管理员。"


class DataSourceError(ChunithmBotError):
    default_user_message = "数据源暂时不可用，请稍后再试。"


class LxnsError(DataSourceError):
    """落雪查分器（LXNS）接口调用失败。"""

    default_user_message = "查分器接口暂时不可用，请稍后再试。"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.status = status
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        parts = [super().__str__()]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.code is not None:
            parts.append(f"code={self.code}")
        return " ".join(parts)


class LxnsNotFoundError(LxnsError):
    default_user_message = "查分器里没有找到对应的数据。"


class LxnsRateLimitError(LxnsError):
    default_user_message = "查得有点频繁被限流了，请等一会儿再试。"


class LxnsServerError(LxnsError):
    default_user_message = "查分器服务器出错了，请稍后再试。"


class SongNotFoundError(ChunithmBotError):
    default_user_message = "没有找到这首歌。换个关键词、别名或者直接用曲目 ID 试试。"


class AssetUnavailableError(ChunithmBotError):
    """曲绘等游戏资源拿不到。属于**非致命**错误：正文照常回复，只是不带图。"""

    default_user_message = "曲绘暂时取不到，先给你文字信息。"


class QuerySyntaxError(ChunithmBotError):
    """筛选条件写得不对。"""

    default_user_message = "筛选条件的写法不对，发 `/帮助` 看看支持哪些条件。"


class OAuthError(ChunithmBotError):
    """落雪 OAuth 流程失败。

    ``error`` 是服务端返回的 OAuth 错误码（如 ``invalid_grant``），
    ``status`` 是 HTTP 状态码。两者都只用于日志与分支判断，
    **不包含任何令牌内容**。
    """

    default_user_message = "落雪授权失败，请稍后重新发 /授权 再试。"

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        description: str | None = None,
        status: int | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.error = error
        self.description = description
        self.status = status

    @property
    def invalid_grant(self) -> bool:
        return self.error in {"invalid_grant", "invalid_token", "unauthorized"}

    @property
    def user_message(self) -> str:
        if self._custom_user_message:
            return self._custom_user_message
        if self.invalid_grant:
            return "授权码无效或已过期，请重新发 /授权 获取新的链接。"
        if self.status == 429:
            return "落雪接口限流了，请等一会儿再试。"
        if self.status is not None and self.status >= 500:
            return "落雪服务器出错了，请稍后再试。"
        return self.default_user_message

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        parts = [super().__str__()]
        if self.error:
            parts.append(f"error={self.error}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        return " ".join(parts)


class OAuthNotConfigured(OAuthError):
    default_user_message = (
        "落雪账号绑定还没配置好，请联系管理员设置 OAuth Client ID 与 read_player 权限。"
    )


class OAuthPendingMissing(OAuthError):
    default_user_message = "授权链接已过期或还没开始，请先发 /授权 获取绑定链接。"


class OAuthReauthRequired(OAuthError):
    default_user_message = "落雪授权已失效，请重新发 /授权 绑定。"


class OAuthScopeMissing(OAuthError):
    default_user_message = (
        "落雪授权缺少 read_player 权限，请重新发 /授权；若仍然失败请联系管理员检查应用权限。"
    )
