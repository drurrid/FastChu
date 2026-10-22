"""曲目搜索。

查歌 bot 的核心就一句话：**用户随便打个词，能不能一次找到那首曲子。**
质量取决于三件事：

1. **归一化**——全角转半角、大小写、去掉空白与常见标点。
   中二曲名大量带 ``【】`` ``！`` ``～`` ``B.B.K.K.B.K.K.`` 这类符号，
   用户不会照着打。
2. **别名**——中二曲子有大量通用叫法（例如把某首叫「脑天直击」），
   只匹配曲名会漏掉一大半真实搜索。
3. **排序**——精确 > 前缀 > 包含；曲名优先于艺术家；同档内**短标题优先**
   （「Rebellion」比「Rebellion of the ...」更贴近用户意图）。

排序是这套实现里最容易被忽视的部分：如果只按"命中"返回，用户搜
``World`` 会先看到一堆标题更长的曲子，体验很差。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..models import Song
from .catalog import SongCatalog

__all__ = ["SongMatch", "SongSearch", "normalize", "normalize_words", "fold_diacritics", "MATCH_REASONS"]

#: 需要从匹配键里抹掉的空白与标点（覆盖中日英常见符号）
_PUNCTUATION = re.compile(
    r"[\s\u3000\-_~～ー!！?？.,，。、:：;；'\"“”‘’`()（）\[\]【】{}<>《》〈〉/\\|+＋&＆*＊#＃@＠$＄%％^＝=]+"
)


def normalize(text: str) -> str:
    """归一化成匹配键：全半角统一 + 大小写归一 + 去空白标点。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    return _PUNCTUATION.sub("", text)


def normalize_words(text: str) -> list[str]:
    """归一化后**保留词边界**的切词结果。

    用来区分「整词命中」与「词内片段命中」：搜 ``world`` 时，
    ``World Vanquisher`` 是整词命中，而 ``Worlds End`` 只是 ``worlds`` 的前缀。
    两者都算命中，但前者显然更贴近用户意图。
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    # 把标点与空白统一替换成单个空格，再切词
    return _PUNCTUATION.sub(" ", text).split()


def fold_diacritics(text: str) -> str:
    """再去掉变音符号，作为**次级**匹配键。

    日文浊音（``ガ`` → ``カ``）与欧洲重音（``é`` → ``e``）都在此列。
    用户经常打不准这些符号，所以留一档低分兜底，而不是把它当主键
    （否则容易误命中）。
    """
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped)


#: 命中原因——给用户看「为什么这首歌被搜出来」，也便于测试断言
MATCH_REASONS = {
    "song_id": "曲目 ID",
    "title_exact": "曲名完全匹配",
    "alias_exact": "别名完全匹配",
    "title_first_word": "曲名首词匹配",
    "title_prefix": "曲名前缀",
    "title_word": "曲名含该词",
    "alias_prefix": "别名前缀",
    "alias_word": "别名含该词",
    "title_contains": "曲名包含",
    "alias_contains": "别名包含",
    "artist_exact": "艺术家匹配",
    "artist_contains": "艺术家包含",
    "loose": "模糊匹配（忽略变音符号）",
}

# 打分档位。档位间距远大于末位微调，确保「更精确的匹配方式」永远压过「更短的名字」。
#
# 为什么把「首词」「前缀」「非首位整词」拆成三档：
# 搜 ``world`` 时，``World Vanquisher``（首词）与 ``Rule the World!!``（词在末尾）
# 都是整词命中，但前者显然更可能是用户想要的；而 ``Worlds End`` 只是
# ``worlds`` 的前缀——它应当排在整词命中之后、片段包含之前。
# 不做这个区分，就只能靠标题长度碰运气，排序会明显反直觉。
_SCORE = {
    "song_id": 10_000,
    "title_exact": 1_000,
    "alias_exact": 950,
    "title_first_word": 940,
    "title_prefix": 880,
    "title_word": 860,
    "alias_prefix": 800,
    "alias_word": 780,
    "title_contains": 640,
    "alias_contains": 570,
    "artist_exact": 400,
    "artist_contains": 320,
    "loose": 200,
}

#: 末位微调的封顶长度，保证它只是「同档内排序」而不影响档位
_TITLE_LENGTH_CAP = 60


@dataclass(frozen=True)
class SongMatch:
    """一条搜索结果。"""

    song: Song
    score: float
    reason: str

    @property
    def reason_label(self) -> str:
        return MATCH_REASONS.get(self.reason, self.reason)


@dataclass(frozen=True)
class _Indexed:
    song: Song
    title_key: str
    title_loose: str
    title_words: tuple[str, ...]
    artist_key: str
    artist_loose: str
    alias_keys: tuple[str, ...]
    alias_loose: tuple[str, ...]
    alias_words: tuple[str, ...]


class SongSearch:
    """曲目搜索索引。

    构造时把归一化结果缓存下来（1458 首曲子的一次性开销可以忽略），
    之后每次查询只做字符串比较。**不加倒排索引**：曲库规模下没必要，
    保持代码可读性更重要。
    """

    def __init__(self, catalog: SongCatalog) -> None:
        self.catalog = catalog
        self._entries: list[_Indexed] = []
        for song in catalog.songs:
            aliases = catalog.aliases_of(song.id)
            alias_keys = tuple(key for a in aliases if (key := normalize(a)))
            words: list[str] = []
            for alias in aliases:
                words.extend(normalize_words(alias))
            self._entries.append(
                _Indexed(
                    song=song,
                    title_key=normalize(song.title),
                    title_loose=fold_diacritics(normalize(song.title)),
                    title_words=tuple(normalize_words(song.title)),
                    artist_key=normalize(song.artist or ""),
                    artist_loose=fold_diacritics(normalize(song.artist or "")),
                    alias_keys=alias_keys,
                    alias_loose=tuple(fold_diacritics(k) for k in alias_keys),
                    alias_words=tuple(words),
                )
            )

    def __len__(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ 查询
    def search_exact(self, keyword: str) -> list[SongMatch]:
        """精确匹配：曲名或别名**完全等于**关键词（归一化后），不含模糊。

        用于 ``/歌名 c`` 这种「我知道全名，别给我一堆带这两个字母的歌」的场景。
        """
        query = normalize(keyword)
        if not query:
            return []
        hits: list[SongMatch] = []
        for entry in self._entries:
            if entry.title_key == query:
                hits.append(SongMatch(song=entry.song, score=1000.0, reason="title_exact"))
            elif query in entry.alias_keys:
                hits.append(SongMatch(song=entry.song, score=900.0, reason="alias_exact"))
        return hits

    def search_alias_exact(self, keyword: str) -> list[SongMatch]:
        """只按别名做精确匹配（不含曲名）。

        纯数字输入既可能是曲目 ID，也可能是用户给歌起的数字叫法。
        别名更具体，所以调用方用这个结果优先于 ID；ID 仍可用 ``#123`` 显式指定。
        """
        query = normalize(keyword)
        if not query:
            return []
        return [
            SongMatch(song=entry.song, score=900.0, reason="alias_exact")
            for entry in self._entries
            if query in entry.alias_keys
        ]

    def search(self, keyword: str, *, limit: int | None = None) -> list[SongMatch]:
        """搜歌。返回按相关度降序的匹配；找不到返回空列表。"""
        query = normalize(keyword)
        if not query:
            return []

        matches: list[SongMatch] = []

        # 纯数字先当曲目 ID——这是最精确、用户最可能期望的解释
        stripped = keyword.strip()
        if stripped.isdigit():
            song = self.catalog.get(int(stripped))
            if song is not None:
                matches.append(SongMatch(song=song, score=_SCORE["song_id"], reason="song_id"))

        loose_query = fold_diacritics(query)

        for entry in self._entries:
            best = self._score_entry(entry, query, loose_query)
            if best is not None:
                reason, score = best
                # 同档内短标题优先：更具体的匹配更可能是用户想要的。
                # 封顶避免长标题跨档位「越级」。
                score -= min(len(entry.song.title), _TITLE_LENGTH_CAP) * 0.1
                matches.append(SongMatch(song=entry.song, score=score, reason=reason))

        matches.sort(key=lambda m: (-m.score, m.song.id))
        if limit is not None:
            return matches[:limit]
        return matches

    @staticmethod
    def _score_entry(entry: _Indexed, query: str, loose_query: str) -> tuple[str, float] | None:
        """给单个曲目打分，返回最高档位。"""
        title = entry.title_key
        words = entry.title_words
        if title:
            if title == query:
                return "title_exact", _SCORE["title_exact"]
            if words and words[0] == query:
                return "title_first_word", _SCORE["title_first_word"]
            if title.startswith(query):
                return "title_prefix", _SCORE["title_prefix"]
            if query in words:
                return "title_word", _SCORE["title_word"]
            if query in title:
                return "title_contains", _SCORE["title_contains"]

        for alias in entry.alias_keys:
            if alias == query:
                return "alias_exact", _SCORE["alias_exact"]
        for alias in entry.alias_keys:
            if alias.startswith(query):
                return "alias_prefix", _SCORE["alias_prefix"]
        if query in entry.alias_words:
            return "alias_word", _SCORE["alias_word"]
        for alias in entry.alias_keys:
            if query in alias:
                return "alias_contains", _SCORE["alias_contains"]

        artist = entry.artist_key
        if artist:
            if artist == query:
                return "artist_exact", _SCORE["artist_exact"]
            if query in artist:
                return "artist_contains", _SCORE["artist_contains"]

        # 最后一档：忽略变音符号。只在长度 >= 2 时启用，避免单字符误命中一片
        if len(loose_query) >= 2:
            if loose_query in entry.title_loose or any(
                loose_query in alias for alias in entry.alias_loose
            ):
                return "loose", _SCORE["loose"]

        return None

    # ------------------------------------------------------------------ 便捷
    def best(self, keyword: str) -> SongMatch | None:
        """唯一/最优命中。用于「能确定是哪首就直出详情」的场景。"""
        matches = self.search(keyword, limit=2)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        # 榜首与次席差距明显时也认为可以确定
        if matches[0].score - matches[1].score >= 120:
            return matches[0]
        return None
