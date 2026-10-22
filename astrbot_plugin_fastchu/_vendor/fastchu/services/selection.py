"""两步式查询的「待选列表」。

命中多首时，命令层会把候选记在这里；用户下一次**只回复一个序号**，
就直接出对应曲目的详情，不用再敲一遍歌名。

设计要点：

* **框架无关**：只依赖内存字典，NoneBot / AstrBot / 测试页共用；
* **带 TTL**：候选列表默认 90 秒后失效——用户过一会儿再发 ``2`` 不会被旧的候选状态劫持；
* **会被新查询覆盖**：用户直接发新歌名时，旧候选自然作废；
* **只存对象引用**：候选里的 ``Song`` / ``RegionSong`` / ``Trophy`` 都来自已经加载好的目录，
  不额外占内存，也不需要序列化。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from ..models import Song, Trophy
from .regions import RegionSong

__all__ = [
    "CANCEL_HINTS",
    "SelectionItem",
    "SelectionKind",
    "SelectionStore",
    "PendingSelection",
    "is_cancel",
    "is_next_page",
    "is_prev_page",
    "parse_page_target",
    "parse_selection_index",
]

SelectionKind = Literal["song", "region", "title", "category"]

#: 默认候选有效期（秒）
DEFAULT_TTL = 90.0

#: 表示「取消」的输入
CANCEL_HINTS = frozenset({"取消", "取消查询", "算了", "cancel", "esc"})

#: 翻页输入
PAGE_NEXT_WORDS = frozenset({"下一页", "下页", "next", "n", ">"})
PAGE_PREV_WORDS = frozenset({"上一页", "上页", "prev", "p", "<"})

#: 直接跳页：``p1`` / ``P2`` / 全角 ``ｐ３``
_PAGE_TARGET_PATTERN = re.compile(r"^[pPｐＰ]([0-9０-９]{1,3})$")

#: 序号：支持 ``3``、``#3``、全角 ``３``
_INDEX_PATTERN = re.compile(r"^[#＃]?([0-9０-９]{1,3})$")


@dataclass(frozen=True)
class SelectionItem:
    """候选里的「第 N 项」指向的东西。

    三者只会有一个非空：国服曲目 / 日服独有曲目 / 称号。

    ``label`` / ``detail`` 是**预先渲染好**的两行内容（曲名 / 难度·版本），
    翻页时直接拼出来即可，不必再回头查曲库——这样翻页是纯内存操作。
    """

    kind: SelectionKind
    label: str
    detail: str = ""
    #: 分类浏览时记录分类 key（kind="category" 才有值）
    category: str = ""
    song: Song | None = None
    region: RegionSong | None = None
    trophy: Trophy | None = None
    #: 这一项是「/谁写的」的候选：选中后查谱师而不是曲目详情
    author: bool = False
    #: ``author`` 候选要查的难度短名（``MAS``/``ULT``…）；空 = 最高难度
    author_level: str = ""


@dataclass
class PendingSelection:
    """某个会话 + 用户当前挂着的候选列表（带翻页状态）。"""

    keyword: str
    items: list[SelectionItem]
    per_page: int = 8
    page: int = 0
    created_at: float = field(default_factory=time.monotonic)

    @property
    def total_pages(self) -> int:
        size = max(1, self.per_page)
        return max(1, (len(self.items) + size - 1) // size)

    @property
    def has_next(self) -> bool:
        return self.page + 1 < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 0

    @property
    def page_items(self) -> list[SelectionItem]:
        """当前页的条目（序号从这里从 1 重新数）。"""
        size = max(1, self.per_page)
        start = self.page * size
        return self.items[start : start + size]

    def goto(self, page: int) -> bool:
        """跳到第 ``page`` 页（0 基）。越界返回 ``False``。"""
        if not 0 <= page < self.total_pages:
            return False
        self.page = page
        return True

    def next_page(self) -> bool:
        if not self.has_next:
            return False
        self.page += 1
        return True

    def prev_page(self) -> bool:
        if not self.has_prev:
            return False
        self.page -= 1
        return True

    def pick(self, index: int) -> SelectionItem | None:
        """取**当前页**的第 ``index`` 项（从 1 开始）。越界返回 ``None``。"""
        chunk = self.page_items
        if 1 <= index <= len(chunk):
            return chunk[index - 1]
        return None


def parse_page_target(text: str) -> int | None:
    """把 ``p2`` 这类输入解析成**0 基**页码；不是跳页指令时返回 ``None``。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    match = _PAGE_TARGET_PATTERN.match(cleaned)
    if match is None:
        return None
    try:
        return int(unicodedata.normalize("NFKC", match.group(1))) - 1
    except ValueError:  # pragma: no cover - 正则已保证是数字
        return None


def parse_selection_index(text: str) -> int | None:
    """把用户输入解析成序号；不是纯序号时返回 ``None``。

    只接受**很短**的数字串：``3`` / ``#3`` / 全角 ``３``。
    曲目 ID（如 ``464``）也符合这个形式，是否按序号处理由调用方决定
    （有待选列表且序号在范围内才当序号）。若数字精确命中了某首歌的别名，
    调用方会优先按别名查询；``#3`` 这种写法仍用于明确选序号。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    match = _INDEX_PATTERN.match(cleaned)
    if match is None:
        return None
    raw = match.group(1)
    # 全角数字转半角
    normalized = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    try:
        return int(normalized)
    except ValueError:  # pragma: no cover - 正则已保证是数字
        return None


def is_next_page(text: str) -> bool:
    return (text or "").strip().casefold() in PAGE_NEXT_WORDS


def is_prev_page(text: str) -> bool:
    return (text or "").strip().casefold() in PAGE_PREV_WORDS


def is_cancel(text: str) -> bool:
    return (text or "").strip().casefold() in CANCEL_HINTS


class SelectionStore:
    """按 key（会话 + 用户）保存待选列表的内存存储。"""

    def __init__(self, ttl: float = DEFAULT_TTL, max_entries: int = 1024) -> None:
        self.ttl = max(0.0, float(ttl))
        self.max_entries = max(8, int(max_entries))
        self._items: dict[str, PendingSelection] = {}

    # ------------------------------------------------------------------ 写
    def remember(
        self, key: str, keyword: str, items: list[SelectionItem], *, per_page: int = 8
    ) -> None:
        """记住一次多选结果。``items`` 为空时等价于清除。"""
        if not key:
            return
        if not items:
            self._items.pop(key, None)
            return
        self._purge()
        self._items[key] = PendingSelection(
            keyword=keyword, items=list(items), per_page=max(1, int(per_page))
        )
        if len(self._items) > self.max_entries:
            # 简单按写入顺序淘汰最旧的
            for stale in list(self._items)[: len(self._items) - self.max_entries]:
                self._items.pop(stale, None)

    def clear(self, key: str) -> None:
        self._items.pop(key, None)

    def reset(self) -> None:
        self._items.clear()

    # ------------------------------------------------------------------ 读
    def peek(self, key: str) -> PendingSelection | None:
        """取待选列表但**不**清除（选错序号时还能再选）。"""
        pending = self._items.get(key)
        if pending is None:
            return None
        if time.monotonic() - pending.created_at > self.ttl:
            self._items.pop(key, None)
            return None
        return pending

    def take(self, key: str) -> PendingSelection | None:
        """取待选列表并清除（选中之后就不该再挂着）。"""
        pending = self.peek(key)
        self._items.pop(key, None)
        return pending

    # ------------------------------------------------------------------ 杂项
    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, pending in self._items.items()
            if now - pending.created_at > self.ttl
        ]
        for key in expired:
            self._items.pop(key, None)

    def __len__(self) -> int:
        self._purge()
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return self.peek(key) is not None
