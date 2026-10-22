"""把绑定用户的个人成绩/称号叠加到查询回复上。

原则：**个人数据是增强，不是依赖**。

* 没绑定、OAuth 没配置、网络失败、超时 → 原回复原样返回；
* 令牌失效 → 在回复末尾补一句「重新授权」，而不是让整条查询失败；
* 个人接口有缓存（成绩 5 分钟、已获得称号 10 分钟），避免每次查歌都请求落雪；
* 只在「曲目详情」「称号详情」上叠加，候选列表不查个人接口。

查歌叠加的是**各难度最高分**；查称号叠加的是**是否已获得**。
日服限定称号（ID ≥ 900000）在落雪没有记录，会明确写「无法判断」，
而不是误报「未获得」。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Sequence

from ..api.user import LxnsUserClient, OwnedTrophy, PersonalScore
from ..config import BotSettings
from ..errors import (
    ChunithmBotError,
    LxnsError,
    OAuthReauthRequired,
    OAuthScopeMissing,
)
from ..models import LevelIndex, RANK_LABELS, Song, Trophy
from .oauth_auth import OAuthAuthorizationManager, PendingAuthorization
from .token_store import StoredAuthorization, TokenStore
from .titles import JP_ONLY_ID_BASE

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免与 commands 循环导入
    from .commands import Reply

__all__ = [
    "PersonalService",
    "format_song_scores",
    "format_title_status",
]

logger = logging.getLogger(__name__)

#: 连击状态 → 展示文本
_COMBO_LABELS: dict[str, str] = {
    "fullcombo": "FC",
    "alljustice": "AJ",
    "alljusticecritical": "AJC",
    "fullchain": "FULL CHAIN",
    "fullchain2": "FULL CHAIN 2",
}


def _rank_for_score(score: int) -> str:
    """落雪没给 rank 时按 CHUNITHM 分数线补一个。"""
    if score >= 1_009_000:
        return "SSS+"
    if score >= 1_007_500:
        return "SSS"
    if score >= 1_005_000:
        return "SS+"
    if score >= 1_000_000:
        return "SS"
    if score >= 990_000:
        return "S+"
    if score >= 975_000:
        return "S"
    if score >= 950_000:
        return "AAA"
    if score >= 925_000:
        return "AA"
    if score >= 900_000:
        return "A"
    if score >= 875_000:
        return "BBB"
    if score >= 850_000:
        return "BB"
    if score >= 825_000:
        return "B"
    if score >= 800_000:
        return "C"
    return "D"


def _difficulty_label(level_index: int | None) -> str:
    if level_index is None:
        return "难度?"
    try:
        return LevelIndex(int(level_index)).short
    except (TypeError, ValueError):
        return f"难度{level_index}"


def _chart_level(song: Song, level_index: int | None) -> str:
    if level_index is None:
        return ""
    try:
        level = LevelIndex(int(level_index))
    except (TypeError, ValueError):
        return ""
    chart = song.chart(level)
    return chart.display_level if chart is not None else ""


def _combo_text(score: PersonalScore) -> str:
    combo = (score.full_combo or "").strip().lower()
    if combo in _COMBO_LABELS:
        return _COMBO_LABELS[combo]
    chain = (score.full_chain or "").strip().lower()
    return _COMBO_LABELS.get(chain, "")


def format_song_scores(song: Song, scores: Iterable[PersonalScore]) -> str:
    """把个人成绩整理成紧凑的「各难度最高分」。

    同一难度可能有多条记录（不同版本/重复上传），只保留分数最高的一条。
    """
    best: dict[int | None, PersonalScore] = {}
    for score in scores:
        if score.id != song.id:
            continue
        if score.score is None or score.score <= 0:
            continue
        previous = best.get(score.level_index)
        if previous is None or (score.score or 0) > (previous.score or 0):
            best[score.level_index] = score

    if not best:
        return "【你的成绩】\n还没有这首歌的成绩。"

    lines = ["【你的成绩】"]
    def sort_key(item: tuple[int | None, PersonalScore]) -> tuple[int, int]:
        level_index = item[0]
        return (1 if level_index is None else 0, -(level_index or 0))
    for level_index, score in sorted(best.items(), key=sort_key):
        label = _difficulty_label(level_index)
        level_text = score.level or _chart_level(song, level_index)
        score_text = f"{score.score:,}" if score.score is not None else "-"
        rank_text = RANK_LABELS.get(
            (score.rank or "").lower(), (score.rank or "").upper()
        )
        if not rank_text and score.score is not None:
            rank_text = _rank_for_score(score.score)
        combo_text = _combo_text(score)
        head = f"{label} {level_text}".strip()
        parts = [part for part in (head, score_text, rank_text, combo_text) if part]
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def format_title_status(trophy: Trophy, owned_ids: set[int]) -> str:
    """称号获得情况（日服限定称号无法从落雪判断）。"""
    if trophy.id >= JP_ONLY_ID_BASE:
        return "【你的状态】日服限定，落雪无记录，无法判断"
    if trophy.id in owned_ids:
        return "【你的状态】已获得"
    return "【你的状态】未获得"


@dataclass
class _CacheEntry:
    fetched_at: float
    value: Any


class PersonalService:
    """授权管理 + 个人数据缓存 + 回复增强。"""

    def __init__(
        self,
        settings: BotSettings,
        *,
        store: TokenStore | None = None,
        auth: OAuthAuthorizationManager | None = None,
        user_client: LxnsUserClient | None = None,
    ) -> None:
        self.settings = settings
        # TokenStore 实现了 __len__，空存储是假值，不能用 `store or ...`
        if store is not None:
            self.store = store
        elif auth is not None:
            self.store = auth.store
        else:
            self.store = TokenStore(settings.lxns_oauth_token_path)
        self._owns_auth = auth is None
        self.auth = auth or OAuthAuthorizationManager(settings, store=self.store)
        self._owns_user_client = user_client is None
        self.user_client = user_client or LxnsUserClient(
            base_url=settings.lxns_base_url,
            timeout=settings.personal_timeout,
            retries=min(settings.http_retries, 2),
            backoff=settings.http_backoff,
        )
        self._score_cache: dict[str, _CacheEntry] = {}
        self._trophy_cache: dict[str, _CacheEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def configured(self) -> bool:
        return self.settings.lxns_oauth_configured

    # ------------------------------------------------------------------ 授权
    async def start(self, actor_id: str) -> PendingAuthorization:
        return await self.auth.start(actor_id)

    async def bind(self, actor_id: str, code: str) -> StoredAuthorization:
        record = await self.auth.complete(actor_id, code)
        self.invalidate(actor_id)
        return record

    def status(self, actor_id: str) -> StoredAuthorization | None:
        return self.auth.status(actor_id)

    async def revoke(self, actor_id: str) -> bool:
        existed = await self.auth.revoke(actor_id)
        self.invalidate(actor_id)
        return existed

    def invalidate(self, actor_id: str) -> None:
        actor = str(actor_id)
        self._score_cache.pop(actor, None)
        self._trophy_cache.pop(actor, None)

    # ------------------------------------------------------------------ 缓存
    def _cached(self, cache: dict[str, _CacheEntry], actor_id: str, ttl: float) -> Any:
        entry = cache.get(actor_id)
        if entry is None:
            return None
        if ttl <= 0 or (time.monotonic() - entry.fetched_at) > ttl:
            cache.pop(actor_id, None)
            return None
        return entry.value

    def _lock_for(self, actor_id: str, kind: str) -> asyncio.Lock:
        key = (actor_id, kind)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _fetch_cached(
        self,
        *,
        cache: dict[str, _CacheEntry],
        actor_id: str,
        ttl: float,
        kind: str,
        fetcher: Callable[[], Awaitable[Any]],
    ) -> Any:
        cached = self._cached(cache, actor_id, ttl)
        if cached is not None:
            return cached
        async with self._lock_for(actor_id, kind):
            cached = self._cached(cache, actor_id, ttl)
            if cached is not None:
                return cached
            value = await fetcher()
            cache[actor_id] = _CacheEntry(fetched_at=time.monotonic(), value=value)
            return value

    async def _scores(self, actor_id: str, token: str) -> Sequence[PersonalScore]:
        return await self._fetch_cached(
            cache=self._score_cache,
            actor_id=actor_id,
            ttl=float(self.settings.personal_score_ttl),
            kind="scores",
            fetcher=lambda: self.user_client.get_scores(token),
        )

    async def _owned_trophies(self, actor_id: str, token: str) -> Sequence[OwnedTrophy]:
        return await self._fetch_cached(
            cache=self._trophy_cache,
            actor_id=actor_id,
            ttl=float(self.settings.personal_title_ttl),
            kind="trophies",
            fetcher=lambda: self.user_client.get_owned_trophies(token),
        )

    # ------------------------------------------------------------------ 增强
    async def enrich(self, reply: "Reply", actor_id: str | None) -> "Reply":
        """给详情回复追加个人数据；任何失败都返回原回复。"""
        if not actor_id or not self.configured:
            return reply
        kind = getattr(reply, "kind", "")
        if kind == "detail" and getattr(reply, "song", None) is not None:
            pass
        elif kind == "title" and getattr(reply, "trophy", None) is not None:
            pass
        else:
            return reply

        try:
            block = await asyncio.wait_for(
                self._build_block(reply, str(actor_id)),
                timeout=float(self.settings.personal_timeout),
            )
        except asyncio.TimeoutError:
            logger.warning("读取落雪个人数据超时，已跳过")
            return reply
        except OAuthReauthRequired as exc:
            self.invalidate(str(actor_id))
            _append_text(reply, f"提示：{exc.user_message}")
            return reply
        except OAuthScopeMissing as exc:
            logger.warning("落雪个人接口权限不足：%s", exc)
            _append_text(reply, f"提示：{exc.user_message}")
            return reply
        except LxnsError as exc:
            logger.warning("读取落雪个人数据失败，已跳过：%s", exc)
            return reply
        except ChunithmBotError as exc:
            logger.warning("落雪个人数据不可用，已跳过：%s", exc)
            return reply
        except Exception:  # noqa: BLE001 - 个人数据绝不能影响基础查询
            logger.exception("个人数据增强出现未预期错误，已跳过")
            return reply

        if block:
            _append_text(reply, block)
        return reply

    async def _build_block(self, reply: "Reply", actor_id: str) -> str:
        token = await self.auth.access_token(actor_id)
        if not token:
            return ""
        if getattr(reply, "kind", "") == "detail":
            song = reply.song
            assert song is not None
            scores = await self._scores(actor_id, token)
            return format_song_scores(song, scores)
        trophy = reply.trophy
        assert trophy is not None
        owned = await self._owned_trophies(actor_id, token)
        return format_title_status(trophy, {item.id for item in owned})

    # ------------------------------------------------------------------ 生命周期
    async def aclose(self) -> None:
        if self._owns_user_client:
            await self.user_client.aclose()
        if self._owns_auth:
            await self.auth.aclose()


def _append_text(reply: "Reply", block: str) -> None:
    if not block:
        return
    if not reply.texts:
        reply.texts = [block]
        return
    reply.texts[-1] = reply.texts[-1].rstrip() + "\n\n" + block
