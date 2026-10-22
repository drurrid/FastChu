"""配置。

配置来源优先级（pydantic-settings 语义）：**环境变量 > ``.env`` 文件 > 代码默认值**。

所有字段统一加前缀 ``FASTCHU_``，因此可以和 NoneBot 自己的 ``.env``
共存——NoneBot 的 ``DRIVER`` / ``SUPERUSERS`` 等键会被忽略（``extra="ignore"``）。

查歌与称号使用落雪查分器的**公共接口**，不需要密钥；可选的账号绑定
（``/授权``）使用 OAuth 2.0 + PKCE，需要管理员自行配置 Client ID/Secret。
密钥只从环境变量或 ``.env`` 读取，不写入插件面板配置。

示例（``.env``）::

    FASTCHU_JACKET_MODE=url
    FASTCHU_SEARCH_RESULT_LIMIT=8
    FASTCHU_LXNS_OAUTH_CLIENT_ID=...
    FASTCHU_LXNS_OAUTH_CLIENT_SECRET=...
"""

from __future__ import annotations

import functools
import pathlib
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "BotSettings",
    "NotesMode",
    "PROJECT_ROOT",
    "get_settings",
    "reset_settings_cache",
]


#: 项目根目录（``pyproject.toml`` 所在目录）。
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 物量显示档位
NotesMode = Literal["off", "compact", "full"]

#: 曲绘发送方式
JacketMode = Literal["base64", "url", "off"]


class BotSettings(BaseSettings):
    """机器人配置。"""

    model_config = SettingsConfigDict(
        env_prefix="FASTCHU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ 接口
    lxns_base_url: str = "https://maimai.lxns.net/api/v0"
    #: 游戏资源基础 URL（曲绘等）。官方提示访问频率有限制，因此默认本地缓存。
    assets_base_url: str = "https://assets2.lxns.net/chunithm"
    #: 日服曲绘地址前缀（国服资源没有日服独有曲目，只能另找源）
    jp_jacket_base: str = "https://db.chunirec.net/lmg/music/jkt"
    http_timeout: float = Field(default=15.0, gt=0)
    http_retries: int = Field(default=2, ge=0, le=6, description="429/5xx/网络错误的重试次数")
    http_backoff: float = Field(default=0.5, ge=0, description="重试退避基数（秒），指数增长")
    min_request_interval: float = Field(
        default=0.15, ge=0, description="两次请求最小间隔（秒），用于规避 429"
    )

    # ------------------------------------------------------------------ 落雪 OAuth
    #: 总开关：关掉后 /授权 会提示未启用，但其他查询完全不受影响。
    oauth_enabled: bool = Field(default=True, description="是否允许用户绑定落雪账号")
    lxns_oauth_client_id: str = Field(default="", description="落雪 OAuth 应用 Client ID")
    lxns_oauth_client_secret: SecretStr = Field(
        default=SecretStr(""), description="落雪 OAuth 应用 Client Secret（可选，PKCE 公共客户端可留空）"
    )
    lxns_oauth_scopes: str = Field(
        default="read_player", description="申请的权限范围（空格分隔，必须包含 read_player）"
    )
    lxns_oauth_authorize_url: str = "https://maimai.lxns.net/oauth/authorize"
    lxns_oauth_token_url: str = "https://maimai.lxns.net/api/v0/oauth/token"
    lxns_oauth_userinfo_url: str = "https://maimai.lxns.net/api/v0/oauth/userinfo"
    #: OAuth 授权记录（含令牌），务必不要提交到仓库
    lxns_oauth_token_path: pathlib.Path = (
        PROJECT_ROOT / "data" / "auth" / "oauth_tokens.json"
    )
    lxns_oauth_pending_ttl: int = Field(
        default=600, ge=60, le=3600, description="从打开授权链接到提交授权码的最长等待（秒）"
    )
    #: 个人成绩/称号的服务端缓存时间：避免每次查歌都打一次落雪接口
    personal_score_ttl: int = Field(default=300, ge=0, description="个人成绩缓存（秒）")
    personal_title_ttl: int = Field(default=600, ge=0, description="已获得称号缓存（秒）")
    personal_timeout: float = Field(
        default=8.0, gt=0, description="个人数据请求超时（秒）；超时不影响基础查询"
    )

    # ------------------------------------------------------------------ 曲库缓存
    song_cache_path: pathlib.Path = PROJECT_ROOT / "data" / "cache" / "song_list.json"
    alias_cache_path: pathlib.Path = PROJECT_ROOT / "data" / "cache" / "alias_list.json"
    #: 社区别名补充（scripts/import_community_aliases.py 生成）
    community_alias_path: pathlib.Path = (
        PROJECT_ROOT / "data" / "aliases" / "community.json"
    )
    #: 群友通过 /添加别名 投稿的别名（运行时写入，不入库）
    local_alias_path: pathlib.Path = PROJECT_ROOT / "data" / "aliases" / "local.json"
    trophy_cache_path: pathlib.Path = PROJECT_ROOT / "data" / "cache" / "trophy_list.json"
    region_cache_path: pathlib.Path = PROJECT_ROOT / "data" / "songs" / "songs.union.json"
    #: 日服限定称号（wiki 有、落雪没有），由 build_title_report.py 生成
    jp_only_titles_path: pathlib.Path = (
        PROJECT_ROOT / "data" / "titles" / "jp_only.json"
    )
    #: 称号条件的补充（日服 Wiki 考据），由 build_title_report.py 生成
    condition_overrides_path: pathlib.Path = (
        PROJECT_ROOT / "data" / "titles" / "conditions.json"
    )
    song_cache_ttl: int = Field(default=86_400, ge=0, description="曲库缓存有效期（秒）")
    allow_stale_cache: bool = Field(
        default=True, description="拉取失败时是否允许使用过期缓存（离线可用性）"
    )
    fetch_notes: bool = Field(
        default=True, description="拉取曲库时是否带上物量（物量查询需要，代价是响应更大）"
    )

    # ------------------------------------------------------------------ 业务
    query_cooldown: float = Field(default=3.0, ge=0, description="同一用户两次查询的最小间隔（秒）")
    max_message_length: int = Field(default=1800, ge=200, description="单条消息最大长度")
    search_result_limit: int = Field(
        default=8, ge=1, le=30, description="候选列表**每页**条数（超出可翻页）"
    )
    search_max_results: int = Field(
        default=48,
        ge=8,
        le=300,
        description="候选池上限：最多收集多少条候选供分页浏览",
    )
    filter_result_limit: int = Field(
        default=30, ge=1, le=100, description="筛选/列表类结果默认展示条数"
    )
    related_title_limit: int = Field(
        default=8, ge=1, le=20, description="曲目详情里最多列出几个相关特殊称号"
    )
    jacket_separate: bool = Field(
        default=False,
        description=(
            "曲绘是否单独发一条消息。QQ 里「图文混排」的图片往往显示得偏小，"
            "开启后正文与曲绘分两条发，图片通常更大（代价是多一条消息）。"
        ),
    )
    notes_mode: NotesMode = Field(
        default="compact",
        description=(
            "曲目详情里物量的显示档位："
            "off = 不显示；"
            "compact = 只给总物量（默认，排版清爽）；"
            "full = 总物量 + TAP/HOLD/SLIDE/AIR/FLICK 分项"
        ),
    )

    # ------------------------------------------------------------------ 曲绘
    jacket_mode: JacketMode = Field(
        default="url",
        description=(
            "url = 把资源地址交给 OneBot 实现自行下载（**默认，最轻量**：机器人侧不下载、不占缓存）；"
            "base64 = 下载并本地缓存后内联发送（适合 OneBot 实现访问不了外网的场景，"
            "消息体积会膨胀约 1/3）；"
            "off = 只发文字。"
        ),
    )
    jacket_cache_dir: pathlib.Path = PROJECT_ROOT / "data" / "assets" / "jacket"
    jacket_max_bytes: int = Field(
        default=4_000_000, gt=0, description="单张曲绘的大小上限，超过则放弃（防异常大文件）"
    )

    # ------------------------------------------------------------------ 校验
    @field_validator(
        "lxns_base_url",
        "assets_base_url",
        "lxns_oauth_authorize_url",
        "lxns_oauth_token_url",
        "lxns_oauth_userinfo_url",
    )
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return value

    # ------------------------------------------------------------------ 便捷属性
    @property
    def lxns_oauth_scope_set(self) -> frozenset[str]:
        return frozenset(
            part
            for part in self.lxns_oauth_scopes.replace(",", " ").split()
            if part
        )

    @property
    def lxns_oauth_configured(self) -> bool:
        """账号绑定是否可用：开关打开、有 Client ID、且申请了 read_player。"""
        return bool(
            self.oauth_enabled
            and self.lxns_oauth_client_id.strip()
            and "read_player" in self.lxns_oauth_scope_set
        )

    @property
    def alias_overlay_paths(self) -> tuple[pathlib.Path, ...]:
        """补充别名的合并顺序：社区数据在前，本机投稿在后。"""
        return (self.community_alias_path, self.local_alias_path)

    @property
    def jacket_enabled(self) -> bool:
        return self.jacket_mode != "off"

    def jacket_url(self, song_id: int) -> str:
        """曲绘地址。官方路径：``/jacket/{song_id}.png``。"""
        return f"{self.assets_base_url}/jacket/{int(song_id)}.png"

    def music_url(self, song_id: int) -> str:
        """试听音频地址（预留，当前未使用）。"""
        return f"{self.assets_base_url}/music/{int(song_id)}.mp3"


@functools.lru_cache(maxsize=1)
def get_settings() -> BotSettings:
    """取全局配置（带缓存）。改完环境变量记得调 :func:`reset_settings_cache`。"""
    return BotSettings()


def reset_settings_cache() -> None:
    """清空配置缓存，供测试与热重载使用。"""
    get_settings.cache_clear()
