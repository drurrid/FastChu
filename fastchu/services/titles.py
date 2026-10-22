"""称号（Trophy）分类与检索。

中二节奏共有 5600+ 个称号，但其中绝大多数是「固定」的：
每首歌的每个难度达成 ALL JUSTICE 就自动获得，一次性几百上千个，
对查歌/盘点毫无信息量。这个模块的核心工作就是**把它们认出来并排除**。

分类依据是官方接口给的**结构化条件**（``required``）加上 ``description``，
不是靠猜测。实测 5620 个称号的真实分布见 ``data/titles/README.md``。

关于「特殊」的定义（与用户需求一致）：

* **排除** 常规 AJ 称号（单曲 ALL JUSTICE / FULL COMBO）——即「固定的普通 aj 称号」；
* **排除** 角色养成称号（``キャラクターランク N``，共 1209 个）；
* **保留** 其余所有：全曲系、单曲评级、多人联机、登录天数、评级段位、
  玩家等级突破、活动限定等。

ULTIMA 谱面的单曲 AJ 单独归类（``per_song_aj_ultima``），因为「ULTIMA 全AJ」
本身是稀有成就，不属于「普通 aj 称号」。
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..models import LevelIndex, Trophy

logger = logging.getLogger(__name__)

__all__ = [
    "TrophyCategory",
    "CategoryInfo",
    "TrophyCatalog",
    "classify",
    "is_kaomoji",
    "condition_text_for_song",
    "tier",
    "tier_label",
    "load_trophies_from_file",
    "load_jp_only_titles",
    "JP_ONLY_ID_BASE",
    "load_condition_overrides",
    "apply_condition_overrides",
    "songs_mentioned_in",
    "COLOR_ORDER",
    "CATEGORY_LABELS",
    "EXCLUDED_CATEGORIES",
    "ALL_SONGS_THRESHOLD",
]


class TrophyCategory:
    """称号分类常量。

    每个名字都对应一条**可解释**的判定规则，见 :func:`classify`。
    """

    # —— 会被排除的（固定称号 / 养成称号）
    PER_SONG_AJ = "per_song_aj"
    CHARACTER_RANK = "character_rank"

    # —— 保留的（特殊获得条件）
    PER_SONG_AJ_SPECIAL = "per_song_aj_special"
    PER_SONG_AJ_ULTIMA = "per_song_aj_ultima"
    PER_SONG_RANK = "per_song_rank"
    PER_SONG_CHAIN = "per_song_chain"
    PER_SONG_OTHER = "per_song_other"
    #: 单曲 FULL COMBO（不是 AJ，别混一起）
    PER_SONG_FC = "per_song_fc"
    #: 究极（ULTIMA）ALL JUSTICE：称号名就是曲名，属于固定称号
    ULTIMA_AJ = "ultima_aj"
    #: 日服限定（wiki 有、落雪没有）
    JP_ONLY = "jp_only"
    MULTIPLAYER = "multiplayer"
    ALL_SONGS = "all_songs"
    RATING = "rating"
    MAP_CLEAR = "map_clear"
    PLAYER_LEVEL = "player_level"
    LOGIN_DAYS = "login_days"
    DANGER_SKILL = "danger_skill"
    ONE_CREDIT = "one_credit"
    MATCHING_PLAYER = "matching_player"
    INHERITED = "inherited"
    EVENT = "event"
    OTHER = "other"


#: 常驻的「排除」类
EXCLUDED_CATEGORIES: frozenset[str] = frozenset(
    {
        TrophyCategory.ULTIMA_AJ,
        TrophyCategory.PER_SONG_AJ,
        TrophyCategory.CHARACTER_RANK,
    }
)

CATEGORY_LABELS: dict[str, str] = {
    "per_song_fc": "单曲 FC 特殊称号",
    "ultima_aj": "究极固定称号",

    "jp_only": "日服限定",
    TrophyCategory.PER_SONG_AJ: "常规 AJ 称号（固定）",
    TrophyCategory.PER_SONG_AJ_SPECIAL: "单曲 AJ 特殊称号",
    TrophyCategory.PER_SONG_AJ_ULTIMA: "ULTIMA 全 AJ",
    TrophyCategory.CHARACTER_RANK: "角色养成称号（固定）",
    TrophyCategory.PER_SONG_RANK: "单曲评级",
    TrophyCategory.PER_SONG_CHAIN: "单曲 FULL CHAIN",
    TrophyCategory.PER_SONG_OTHER: "单曲其他条件",
    TrophyCategory.MULTIPLAYER: "多人联机",
    TrophyCategory.ALL_SONGS: "全曲系（版本全谱面）",
    TrophyCategory.RATING: "评级段位",
    TrophyCategory.MAP_CLEAR: "地图制霸",
    TrophyCategory.PLAYER_LEVEL: "玩家等级突破",
    TrophyCategory.LOGIN_DAYS: "登录天数",
    TrophyCategory.DANGER_SKILL: "DANGER SKILL",
    TrophyCategory.ONE_CREDIT: "一道内连打",
    TrophyCategory.MATCHING_PLAYER: "匹配特定称号玩家",
    TrophyCategory.INHERITED: "旧作引继",
    TrophyCategory.EVENT: "活动/其他限定",
    TrophyCategory.OTHER: "未归类",
}

#: 颜色排序（越稀有越靠前）
COLOR_ORDER: dict[str, int] = {
    "normal": 0,
    "copper": 1,
    "silver": 2,
    "gold": 3,
    "platina": 4,
    "platinum": 4,
    "ultima": 5,
    "rainbow": 6,
    "image": 7,
}

#: 全曲系判定阈值：涉及曲目数达到这个量级就是「某个版本的全部谱面」
ALL_SONGS_THRESHOLD = 40

#: 日服限定称号的合成 ID 起点（与落雪 ID 不冲突）
JP_ONLY_ID_BASE = 900_000

#: 日服独有曲目的合成 ID 起点（让「曲目 → 称号」文本反查也能挂到它们）
JP_SONG_ID_BASE = 700_000
#: 单曲系判定阈值：涉及曲目数不超过这个量级才算「针对某首歌」
PER_SONG_THRESHOLD = 4

# --------------------------------------------------------------------------- #
# 颜文字识别
# --------------------------------------------------------------------------- #

#: 这些字符基本只出现在颜文字里，不会出现在正常的日文/英文标题中。\n#: 刻意**不含** ``・``/``･``（中点）：``BOW AND ARROW （アニメ・オープニングver.）``\n#: 这种正常括号说明里也有，会误判。
_STRONG_FACE_CHARS = set("ω∀▽△∇◡⌒‿´｀ﾟヮﾜｦ≧≦дДㅂ⌓﹏艸◕◔◉✪◎○◯●≖＾")
#: 括号对（半角/全角/方括号），内容长度受限，避免匹配到括号里的长解释
_BRACKET_RE = re.compile(r"[（(\[][^（()）\[\]\n]{0,24}[）)\]]")
#: 无括号的 ASCII 颜文字，例如 ``:-)`` ``;)``
_ASCII_KAOMOJI_RE = re.compile(r"[:;][-^']?[\)\]\(\[/\\]|[\)\]\(\[/\\][-^']?[:;]")
#: ASCII 表情里常见的符号
_ASCII_FACE_CHARS = set("^_-=~.<>;:'\"!oO0vVuU/\\@#$%&*+")
#: 连续 3 个以上拉丁字母 → 是单词，不是表情
_HAS_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _is_ascii_face(inner: str) -> bool:
    """判断括号内的内容是不是 ASCII 表情，例如 ``(^o^)`` ``(/>_<)`` ``(=_=)``。

    关键在于排除 ``(Reborn)`` ``(about you)`` ``(CRYSTAL_ACCESS)``
    这类"括号里是正常单词"的情况——早期版本用「含 o/0 就算」来判断，
    结果把它们全误判成了颜文字。
    """
    if not inner or len(inner) > 12:
        return False
    if _HAS_WORD_RE.search(inner):
        return False
    return sum(1 for ch in inner if ch in _ASCII_FACE_CHARS) >= 2


def is_kaomoji(name: str) -> bool:
    """判断标题是否为颜文字。

    **颜文字必须有括号**——这是把它和普通符号标题区分开的关键。

    早期版本还有一条「强特征字符 + 肢体符号」的规则（不要求括号），
    结果把 ``星咲 あかり／マーメイド・ランデブー`` 这类**角色名**判成了颜文字：
    ``・``（中点）被当成强特征字符、``／``（全角斜杠）被当成肢体符号，
    而这两个在日文标题里只是普通标点。那一版一次误判了 74 个角色养成称号。
    该规则已移除。

    现在的规则：

    1. 括号内含**强特征**颜文字字符：``(´･ω･｀)`` ``(*｀・▽・´*)`` ``щ(ﾟДﾟщ)``；
    2. 括号内是 ASCII 表情：``(^o^)`` ``(/>_<)`` ``(=_=)``；
    3. 无括号的 ASCII 颜文字：``:-)``。

    也不能用「符号密度」之类的统计量：``D.E.A.D.L.Y.``、``合格！！``
    的符号密度同样很高，会大面积误判。
    """
    if not name:
        return False

    for match in _BRACKET_RE.finditer(name):
        inner = match.group()
        if any(ch in _STRONG_FACE_CHARS for ch in inner):
            return True
        if _is_ascii_face(inner[1:-1]):
            return True

    if _ASCII_KAOMOJI_RE.search(name):
        return True

    return False


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CategoryInfo:
    """分类结果。"""

    category: str
    #: 排除原因（为空表示属于「特殊称号」）
    excluded: str = ""

    @property
    def is_special(self) -> bool:
        return not self.excluded

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


def _has(reqs, field: str) -> bool:
    return any(getattr(r, field) for r in reqs)


#: 各种「破折号 / 横线」字符——wiki 与落雪写法不同，查重/反查时必须归一
_DASH_MAP = {
    ord(char): "-"
    for char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d\u2500\u2501"
}

#: 弯引号 / 修饰符撇号与直引号视为同一字符（wiki 与国服数据常有差异）
_QUOTE_MAP = {
    **{ord(char): "'" for char in "\u2018\u2019\u201b\u02bc\u2032\uff07"},
    **{ord(char): '"' for char in "\u201c\u201d\u201e\u301d\u301e\uff02"},
}

def _name_key(text: str) -> str:
    """称号名/曲名的比较键：全半角统一 + 破折号/引号归一 + 去空白 + 大小写归一。"""
    folded = (
        unicodedata.normalize("NFKC", text or "")
        .translate(_DASH_MAP)
        .translate(_QUOTE_MAP)
    )
    return "".join(folded.split()).casefold()


#: wiki 表格会把脚注编号/「※」一起抓进标题，例如 ``Nice boat.*21``
_FOOTNOTE_SUFFIX_RE = re.compile(r"\s*(?:\*\d+|※)\s*$")


def _clean_wiki_name(name: str) -> str:
    """去掉 wiki 标题末尾的脚注标记（``*12`` / ``※``）。"""
    cleaned = (name or "").strip()
    while True:
        stripped = _FOOTNOTE_SUFFIX_RE.sub("", cleaned).strip()
        if not stripped or stripped == cleaned:
            return cleaned
        cleaned = stripped


#: 国服写「2人以上で店内マッチング」，wiki 只写「2人以上」，视作同一条件
_MATCHING_PHRASES = ("で店内マッチング", "店内マッチング")


def _condition_key(text: str) -> str:
    """获得条件的比较键：名称归一 + 去掉多人匹配的冗余修饰。"""
    key = _name_key(text)
    for phrase in _MATCHING_PHRASES:
        key = key.replace(phrase, "")
    return key


def _closest_trophy(name: str, candidates: list[Trophy]) -> Trophy:
    """条件命中多个同名档位时，挑名字最接近的那条做别名目标。"""
    if len(candidates) == 1:
        return candidates[0]
    key = _name_key(name)
    return max(
        candidates,
        key=lambda t: difflib.SequenceMatcher(None, key, _name_key(t.name)).ratio(),
    )


def _name_matches_song(trophy: Trophy) -> bool:
    """称号名是不是**就是曲名**。

    这是区分「固定 AJ 称号」和「特殊 AJ 称号」的**唯一判据**：

    * 名字就是曲名 → 每首歌每个难度都有一个，纯属固定授予，排除；
    * 名字另有其名 → 一律算特殊称号（``賢者タイム`` 是 ``End Time`` 的全难度 AJ、
      ``FOUR DIMENSIONS`` 是 ``FREEDOM DiVE`` 的全难度 AJ、``パカラー`` 是
      ``Blue Noise`` / ``JET`` 的 EXPERT AJ）。

    不按难度数量、不按是否全难度来判断——那样既难解释，也会误伤。
    """
    song_titles = {
        _name_key(song.title) for req in trophy.required for song in req.songs
    }
    return _name_key(trophy.name) in song_titles


def _is_ultima(trophy: Trophy) -> bool:
    """是否与 ULTIMA 难度相关。"""
    if trophy.color == "ultima":
        return True
    return any(LevelIndex.ULTIMA in req.difficulties for req in trophy.required)


def _classify_no_required(trophy: Trophy) -> CategoryInfo:
    """没有结构化条件的称号，靠 ``description`` 归类。"""
    desc = (trophy.description or "").strip()

    if "キャラクターランク" in desc:
        # 角色养成：把某个角色升到指定等级
        return CategoryInfo(TrophyCategory.CHARACTER_RANK, "角色养成称号")
    if "プレイヤーレベル" in desc:
        return CategoryInfo(TrophyCategory.PLAYER_LEVEL)
    if "引き継ぎ" in desc:
        return CategoryInfo(TrophyCategory.INHERITED)
    if "ログイン" in desc:
        return CategoryInfo(TrophyCategory.LOGIN_DAYS)
    if "マップ制覇" in desc:
        return CategoryInfo(TrophyCategory.MAP_CLEAR)
    if "デンジャースキル" in desc:
        return CategoryInfo(TrophyCategory.DANGER_SKILL)
    if "クレジット中" in desc:
        return CategoryInfo(TrophyCategory.ONE_CREDIT)
    if "店内マッチング" in desc or "フレンドマッチング" in desc:
        return CategoryInfo(TrophyCategory.MATCHING_PLAYER)
    if re.search(r"RATING \d", desc, re.IGNORECASE):
        return CategoryInfo(TrophyCategory.RATING)
    if not desc or desc == "-":
        # 大部分是活动/比赛/联名限定，官方没给条件
        return CategoryInfo(TrophyCategory.EVENT)
    return CategoryInfo(TrophyCategory.OTHER)


def classify(trophy: Trophy) -> CategoryInfo:
    """判定称号属于哪一类、是否应当被排除。

    判定顺序很重要：先按「涉及曲目数」分流，再看条件字段。
    """
    # 究极（ULTIMA）称号的名字**就是曲名**——实测 94/94 全是，
    # 和常规 AJ 一样属于「每首歌都有的固定称号」，直接排除。
    if trophy.color == "ultima":
        return CategoryInfo(TrophyCategory.ULTIMA_AJ, "究极固定称号")

    if not trophy.required:
        return _classify_no_required(trophy)

    song_count = trophy.song_count
    desc = trophy.description or ""

    # ---- 全曲系：某个版本的全部谱面达成 S/SSS/AJ ----
    if song_count >= ALL_SONGS_THRESHOLD:
        return CategoryInfo(TrophyCategory.ALL_SONGS)

    # ---- 单曲系 ----
    if 1 <= song_count <= PER_SONG_THRESHOLD:
        # 联机相关优先（FULL CHAIN 基本都要求店内联机）
        if "マッチング" in desc:
            return CategoryInfo(TrophyCategory.MULTIPLAYER)

        # 先看条件类型，再看难度——否则「ULTIMA 单曲评级」会被误当成 ULTIMA 全 AJ
        if _has(trophy.required, "full_chain"):
            return CategoryInfo(TrophyCategory.PER_SONG_CHAIN)

        if _has(trophy.required, "rank"):
            return CategoryInfo(TrophyCategory.PER_SONG_RANK)

        if _has(trophy.required, "full_combo"):
            # 只有连击要求、没有评级要求。
            # **必须看取值**：``alljustice`` 才是 AJ，``fullcombo`` 是 FC，别混为一谈。
            values = {
                str(req.full_combo).strip().casefold()
                for req in trophy.required
                if req.full_combo
            }

            if values and values <= {"alljustice"}:
                # ULTIMA 谱面的全 AJ 单独归类：它本身是稀有成就
                if _is_ultima(trophy):
                    return CategoryInfo(TrophyCategory.PER_SONG_AJ_ULTIMA)

                # 判据只有一个：**称号名是不是曲名**。
                #   名字 == 曲名 → 每首歌每个难度都有的固定称号，排除；
                #   名字另有其名 → 专属成就称号（含「MASTER以下全て」这类全难度 AJ），保留。
                if _name_matches_song(trophy):
                    return CategoryInfo(TrophyCategory.PER_SONG_AJ, "常规 AJ 称号")
                return CategoryInfo(TrophyCategory.PER_SONG_AJ_SPECIAL)

            # 纯 FULL COMBO → 单独一类（不是 AJ）
            return CategoryInfo(TrophyCategory.PER_SONG_FC)

        # 只剩「打一遍」这种（例：MASTER以下全て/プレイ）
        return CategoryInfo(TrophyCategory.PER_SONG_OTHER)

    # ---- 中间量级（5~39 首）：数据里目前没有，留作兜底 ----
    if _has(trophy.required, "rank"):
        return CategoryInfo(TrophyCategory.PER_SONG_RANK)
    return CategoryInfo(TrophyCategory.PER_SONG_OTHER)


# --------------------------------------------------------------------------- #
# 称号目录
# --------------------------------------------------------------------------- #


@dataclass
class TitleSearchHit:
    trophy: Trophy
    category: CategoryInfo
    score: float


def _normalize(text: str) -> str:
    """全半角统一 + 引号归一 + 小写，用于检索。"""
    folded = unicodedata.normalize("NFKC", text or "").translate(_QUOTE_MAP)
    return folded.casefold().strip()


#: 曲名后面跟这些字符，才算「这句话在说这首歌」
_MENTION_FOLLOWERS = frozenset("のをでがはに、。」』！？!?)]） 　/／・：:（(")

#: 曲名前面出现这些，说明在说**别的东西**（版本名 / 角色名），不是曲目
_MENTION_EXCLUDE_PREFIXES = ("chunithm", "x-", "x ", "x-verse", "キャラクター『", "キャラクター「")

#: 太短的曲名容易误伤（STAR / AIR 之类），至少这么长才参与文本反查
_MENTION_MIN_LENGTH = 4


def songs_mentioned_in(text: str, song_titles: Mapping[str, int]) -> list[int]:
    """从一段说明文本里反查出提到的曲目。

    落雪对「非单曲条件」的称号（例如 UNLOCK CHALLENGE）不给 ``required.songs``，
    但 ``description`` 里常常直接写着曲名，例如::

        少女が夢見た空の果て
            Crossmythos RhapsodiaのUNLOCK CHALLENGEを最も厳しい条件でクリアした証です。

    为了不误伤，只认「曲名 + 助词/引号」这种明确语境，并排除版本语境
    （``『CHUNITHM STAR』`` 里的 STAR 是版本名，不是曲目《STAR》）。
    """
    if not text:
        return []

    lowered = text.casefold()
    consumed: list[tuple[int, int]] = []
    hits: list[tuple[int, int]] = []

    # 长名优先，避免《Change Our MIRAI！》抢掉《Change Our MIRAI！ (Our 7 Lights)》
    for title in sorted(song_titles, key=len, reverse=True):
        if len(title) < _MENTION_MIN_LENGTH:
            continue
        needle = title.casefold()
        start = lowered.find(needle)
        while start != -1:
            end = start + len(needle)
            prev_char = lowered[start - 1] if start > 0 else ""
            prefix = lowered[max(0, start - 12) : start].strip()

            ok = True
            # 后面必须是助词/引号/空白/结尾
            if end < len(lowered) and lowered[end] not in _MENTION_FOLLOWERS:
                ok = False
            # 前面紧贴**ASCII** 字母数字或连字符 → 多半是别的词的一部分
            # （X-VERSE 里的 VERSE）；日文助词不当成阻断
            if ok and prev_char and (
                (prev_char.isascii() and prev_char.isalnum()) or prev_char in "-_"
            ):
                ok = False
            # 版本语境
            if ok and any(prefix.endswith(p) for p in _MENTION_EXCLUDE_PREFIXES):
                ok = False
            # 已被更长的标题占用的区间让位
            if ok and any(start < used_end and end > used_start for used_start, used_end in consumed):
                ok = False

            if ok:
                consumed.append((start, end))
                hits.append((start, song_titles[title]))
                break
            start = lowered.find(needle, start + 1)

    return [song_id for _, song_id in hits]


class TrophyCatalog:
    """称号集合，带分类与索引。

    * :meth:`search` 按名字检索；
    * :meth:`for_song` 反查某首歌涉及的特殊称号；
    * :meth:`special` / :attr:`excluded` 给出过滤后的两个集合。
    """

    def __init__(
        self,
        trophies: list[Trophy],
        *,
        song_titles: Mapping[str, int] | None = None,
    ) -> None:
        # 先分类：查重需要知道哪些国服条目属于「特殊称号」
        categories: dict[int, CategoryInfo] = {}
        for trophy in trophies:
            if trophy.id >= JP_ONLY_ID_BASE:
                # wiki 补充的日服限定称号没有结构化条件，按 ID 段直接归类
                categories[trophy.id] = CategoryInfo(TrophyCategory.JP_ONLY)
            else:
                categories[trophy.id] = classify(trophy)

        # 日服限定条目按名称/条件兜底查重：wiki 与落雪可能只差弯引号、
        # 脚注（*12）或汉字/片假名写法（ゲキ vs 激）。
        # 国服条目优先保留（有结构化条件、ID 稳定），重名的合成条目丢弃；
        # 被丢弃的名字记成别名，保证用日服写法仍能搜到国服条目。
        cn_names: dict[str, int] = {}
        cn_conditions: dict[str, list[Trophy]] = {}
        for trophy in trophies:
            if trophy.id >= JP_ONLY_ID_BASE:
                continue
            cn_names.setdefault(_name_key(trophy.name), trophy.id)
            if not categories[trophy.id].is_special:
                continue
            condition_key = _condition_key(trophy.description)
            if condition_key:
                cn_conditions.setdefault(condition_key, []).append(trophy)

        aliases: dict[int, set[str]] = {}

        def remember_alias(target_id: int, name: str) -> None:
            key = _normalize(name)
            if key:
                aliases.setdefault(target_id, set()).add(key)

        seen_jp: dict[tuple[str, str], int] = {}
        unique: list[Trophy] = []
        for original in trophies:
            if original.id < JP_ONLY_ID_BASE:
                unique.append(original)
                continue

            trophy = original.model_copy(update={"name": _clean_wiki_name(original.name)})
            name_key = _name_key(trophy.name)
            condition_key = _condition_key(trophy.description)

            target = cn_names.get(name_key)
            if target is None:
                # 同一份 wiki 里重复抓到的同条件条目（脚注差异）只留一条
                target = seen_jp.get((name_key, condition_key))
            if target is None and condition_key in cn_conditions:
                # 条件相同 = 同一项成就，只是 wiki 用了另一种写法
                target = _closest_trophy(trophy.name, cn_conditions[condition_key]).id
            if target is not None:
                remember_alias(target, trophy.name)
                logger.debug("忽略重复的日服限定称号: %s", original.name)
                continue

            seen_jp[(name_key, condition_key)] = trophy.id
            unique.append(trophy)
        self._aliases = aliases
        self.trophies = unique
        trophies = unique
        #: 曲名（归一化）→ 曲目 ID，供日服曲目按标题反查
        self._song_id_by_title: dict[str, int] = {
            _name_key(title): song_id for title, song_id in (song_titles or {}).items()
        }
        self._by_id = {t.id: t for t in trophies}
        self._categories = {t.id: categories[t.id] for t in trophies}
        self._normalized: dict[int, str] = {}

        # 曲目 → 特殊称号 反向索引
        self._by_song: dict[int, list[int]] = {}

        for trophy in trophies:
            info = self._categories[trophy.id]
            self._normalized[trophy.id] = _normalize(trophy.name)
            if not info.is_special:
                continue

            linked = trophy.song_ids
            if not linked and song_titles:
                # 落雪对「非单曲条件」的称号（如 UNLOCK CHALLENGE）不给 songs，
                # 但 description 里常常直接写着曲名，这里补一层文本反查。
                linked = songs_mentioned_in(trophy.description, song_titles)
            for song_id in linked:
                self._by_song.setdefault(song_id, []).append(trophy.id)

        self._special = [t for t in trophies if self._categories[t.id].is_special]
        self._excluded = [t for t in trophies if not self._categories[t.id].is_special]

    def __len__(self) -> int:
        return len(self.trophies)

    # ---- 取数 ----

    def get(self, trophy_id: int) -> Trophy | None:
        return self._by_id.get(trophy_id)

    def category_of(self, trophy: Trophy) -> CategoryInfo:
        return self._categories[trophy.id]

    @property
    def special(self) -> list[Trophy]:
        """排除常规 AJ 与角色养成后的称号。"""
        return self._special

    @property
    def excluded(self) -> list[Trophy]:
        """被排除的固定称号。"""
        return self._excluded

    def of_category(self, category: str) -> list[Trophy]:
        return [t for t in self.trophies if self._categories[t.id].category == category]

    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for info in self._categories.values():
            counts[info.category] = counts.get(info.category, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def color_counts(self, *, special_only: bool = False) -> dict[str, int]:
        source = self._special if special_only else self.trophies
        counts: dict[str, int] = {}
        for trophy in source:
            counts[trophy.color] = counts.get(trophy.color, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    # ---- 检索 ----

    def search(
        self,
        keyword: str,
        *,
        special_only: bool = False,
        color: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[TitleSearchHit]:
        """按名字检索称号。

        打分沿用搜歌的思路：完全匹配 > 前缀 > 包含，得分相同时颜色更稀有的靠前。
        """
        key = _normalize(keyword)
        if not key:
            return []

        hits: list[TitleSearchHit] = []
        for trophy in self._special if special_only else self.trophies:
            info = self._categories[trophy.id]
            if color and trophy.color != color:
                continue
            if category and info.category != category:
                continue

            names = (self._normalized[trophy.id], *self._aliases.get(trophy.id, ()))
            score = 0.0
            for name in names:
                if key == name:
                    score = max(score, 1000.0)
                elif name.startswith(key):
                    score = max(score, 880.0)
                elif key in name:
                    score = max(score, 640.0)
            if not score:
                continue

            # 同分时：颜色越稀有越靠前，名字越短越靠前
            score += COLOR_ORDER.get(trophy.color, 0) * 2
            score -= min(len(trophy.name), 60) * 0.1
            hits.append(TitleSearchHit(trophy=trophy, category=info, score=score))

        hits.sort(key=lambda hit: -hit.score)
        return hits[:limit]

    def find(self, keyword: str, **kwargs) -> TitleSearchHit | None:
        """取唯一最佳命中；分差不够就返回 ``None``（交给上层列候选）。"""
        hits = self.search(keyword, limit=20, **kwargs)
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        if hits[0].score - hits[1].score >= 120:
            return hits[0]
        return None

    # ---- 曲目反查 ----

    def search_exact(self, keyword: str) -> list[Trophy]:
        """称号名**完全等于**关键词（归一化后）。"""
        needle = _name_key(keyword or "")
        if not needle:
            return []
        normalized = _normalize(keyword or "")
        hits = [
            t
            for t in self.trophies
            if _name_key(t.name) == needle
            or normalized in self._aliases.get(t.id, ())
        ]
        hits.sort(
            key=lambda t: (
                1 if t.color == "unknown" else 0,
                -COLOR_ORDER.get(t.color, 0),
                tier(t),
                t.name,
            )
        )
        return hits

    def search_condition(self, keyword: str, *, limit: int = 50) -> list[Trophy]:
        """按**获得条件**检索称号。

        只看条件文本（``description``，已含日服 Wiki 补充），不看称号名——
        名字检索是 :meth:`search` 的事。
        """
        needle = _normalize(keyword or "")
        if not needle:
            return []
        hits: list[Trophy] = []
        for trophy in self.special:
            # 「图片」称号是曲绘系奖励，与条件检索的意图无关，噪音大 → 跳过
            if trophy.color == "image":
                continue
            # 与曲目同名的称号 = 每首歌都有的固定成就，检索条件时也是噪音
            if _name_matches_song(trophy):
                continue
            if needle in _normalize(trophy.description or ""):
                hits.append(trophy)
        # 稀有度优先（虹/究极/铂金…），同档再按名字，避免「先出现的就是最普通的」
        # 先按颜色稀有度（虹 > 究极 > 铂金 > 金 > 银 > 普通），再按层级和名字；
        # 「未标注」（wiki 补充、没有颜色信息）排在最后
        hits.sort(
            key=lambda t: (
                1 if t.color == "unknown" else 0,
                -COLOR_ORDER.get(t.color, 0),
                tier(t),
                t.name,
            )
        )
        return hits[: max(1, limit)]

    def random_special(self) -> Trophy | None:
        """随机抽一个特殊称号（盘点/整活用）。

        排除「未标注」的日服限定称号——它们没有颜色、来源是 wiki，
        抽出来大多是不认识的杂项，体验很差。
        """
        import random

        pool = [t for t in self._special if t.color != "unknown"]
        return random.choice(pool) if pool else None
    def for_song_title(self, title: str) -> list[Trophy]:
        """按**曲名**反查相关特殊称号。

        日服独有曲目没有国服 ID，只能靠标题匹配；索引在建目录时就已按标题建好。
        """
        song_id = self._song_id_by_title.get(_name_key(title or ""))
        if song_id is None:
            return []
        return self.for_song(song_id)
    def for_song(self, song_id: int, *, include_all_songs: bool = False) -> list[Trophy]:
        """某首歌涉及的**特殊**称号。

        默认已经去掉三类没有信息量的称号：

        * 常规 AJ（每个谱面一个，固定称号）；
        * 角色养成（和曲子无关）；
        * **版本全完成称号**（``Spirit/Tribute/Legend of X``）——同一个版本里
          每首歌都会命中同一组，挂在单曲详情上纯属噪音。

        需要完整反查（例如做「这首歌计入哪些版本成就」）时传
        ``include_all_songs=True``。
        """
        ids = self._by_song.get(song_id, [])
        trophies: list[Trophy] = []
        for trophy_id in ids:
            trophy = self._by_id.get(trophy_id)
            if trophy is None:
                continue
            if (
                not include_all_songs
                and self._categories[trophy.id].category == TrophyCategory.ALL_SONGS
            ):
                continue
            trophies.append(trophy)

        # 颜色更稀有的排前面，同色按 id
        return sorted(trophies, key=lambda t: (-COLOR_ORDER.get(t.color, 0), t.id))

    def kaomoji(self, *, special_only: bool = False) -> list[Trophy]:
        return [t for t in (self._special if special_only else self.trophies) if is_kaomoji(t.name)]

    def listing(
        self,
        *,
        special_only: bool = False,
        color: str | None = None,
        category: str | None = None,
        tier_value: int | None = None,
        limit: int = 50,
    ) -> list[Trophy]:
        """不按名字、只按条件列出称号。

        按「稀有度 → 颜色 → id」排序，方便盘点取材（例如列出所有虹色特殊称号）。
        """
        out: list[Trophy] = []
        for trophy in self._special if special_only else self.trophies:
            info = self._categories[trophy.id]
            if color and trophy.color != color:
                continue
            if category and info.category != category:
                continue
            if tier_value is not None and tier(trophy, info) != tier_value:
                continue
            out.append(trophy)

        out.sort(
            key=lambda t: (
                tier(t, self._categories[t.id]),
                -COLOR_ORDER.get(t.color, 0),
                t.id,
            )
        )
        return out[:limit]


def condition_text_for_song(trophy: Trophy, song_title: str | None = None) -> str:
    """把称号条件转成「挂在某首歌下面」时更短的文本。

    官方 ``description`` 常常以曲名开头（``ジングルベル/ULTIMA/ALL JUSTICE達成``），
    在曲目详情里曲名是冗余的，去掉后每行能短一大截。
    只有确实以该曲名开头时才裁，多曲条件保持原文。
    """
    text = trophy.condition_text()
    if song_title:
        prefix = f"{song_title}/"
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def load_trophies_from_file(path: str | Path) -> list[Trophy]:
    """从缓存文件读取称号列表，兼容三种信封形态。"""
    import json

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if "trophies" in raw:
            items = raw["trophies"]
        elif "data" in raw and isinstance(raw["data"], dict):
            items = raw["data"].get("trophies", [])
        else:
            items = []
    else:
        items = raw
    return [Trophy.model_validate(item) for item in items]


def load_condition_overrides(path: str | Path) -> dict[int, str]:
    """读取「条件补充」文件（日服 Wiki 考据）。

    ``build_title_report.py`` 会把它写成 ``data/titles/conditions.json``::

        {"conditions": {"9719": {"condition": "...", "source": "wiki"}}}

    文件不存在或损坏时返回空表——**没有它也能正常查称号**，只是条件少了日服那层。
    """
    import json

    target = Path(path)
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - 损坏时降级
        logger.warning("条件补充文件读取失败，忽略: %s", exc)
        return {}

    if isinstance(raw, dict):
        items = raw.get("conditions", raw)
    else:  # pragma: no cover - 只会是 dict
        return {}

    overrides: dict[int, str] = {}
    for key, value in items.items():
        try:
            trophy_id = int(key)
        except (TypeError, ValueError):
            continue
        text = value.get("condition") if isinstance(value, dict) else value
        if isinstance(text, str) and text.strip():
            overrides[trophy_id] = text.strip()
    return overrides


def apply_condition_overrides(
    trophies: list[Trophy], overrides: dict[int, str]
) -> list[Trophy]:
    """用补充条件替换称号的 ``description``。

    落雪对一部分称号只给「CHUNITHMから引き継ぎ」这种没信息量的说明，
    日服 Wiki 那边有真正的达成条件——这里把它盖上去。
    """
    if not overrides:
        return trophies
    enriched: list[Trophy] = []
    for trophy in trophies:
        text = overrides.get(trophy.id)
        enriched.append(trophy.model_copy(update={"description": text}) if text else trophy)
    return enriched


# --------------------------------------------------------------------------- #
# 稀有度分层（仅供盘点排序，不是官方数据）
# --------------------------------------------------------------------------- #

TIER_LABELS: dict[int, str] = {
    1: "S · 最难",
    2: "A · 高难",
    3: "B · 长期/特定条件",
    4: "C · 活动/其他",
}


def tier_label(value: int) -> str:
    return TIER_LABELS.get(value, TIER_LABELS[4])


def tier(trophy: Trophy, info: CategoryInfo | None = None) -> int:
    """给称号评一个 1~4 的稀有度层级。

    依据是「类别 + 颜色」这种可解释的组合，不是主观打分：

    * **S**：全曲系（某版本全部谱面 ACHIEVE）、虹色、图片称号；
    * **A**：ULTIMA 全 AJ、多人联机、玩家等级突破、匹配特定称号玩家；
    * **B**：单曲评级、单曲 AJ 特殊称号（名字不是曲名的那些）、评级段位、\n      登录天数、地图制霸、一道内连打；
    * **C**：活动/引继/未归类。
    """
    info = info or classify(trophy)
    category = info.category

    if category == TrophyCategory.ALL_SONGS or trophy.color in ("rainbow", "image"):
        return 1
    if category in (
        TrophyCategory.PER_SONG_AJ_ULTIMA,
        TrophyCategory.MULTIPLAYER,
        TrophyCategory.PLAYER_LEVEL,
        TrophyCategory.MATCHING_PLAYER,
    ):
        return 2
    if category in (
        TrophyCategory.PER_SONG_AJ_SPECIAL,
        TrophyCategory.PER_SONG_RANK,
        TrophyCategory.PER_SONG_CHAIN,
        TrophyCategory.RATING,
        TrophyCategory.LOGIN_DAYS,
        TrophyCategory.MAP_CLEAR,
        TrophyCategory.ONE_CREDIT,
        TrophyCategory.DANGER_SKILL,
    ):
        return 3
    return 4


# --------------------------------------------------------------------------- #
# 称号分类（``/称号`` 不带参数时的入口）
# --------------------------------------------------------------------------- #

#: 谱面速度相关的条件标记（称号名里通常没有这些词，要看条件）
_SPEED_MARKERS: dict[str, tuple[str, ...]] = {
    "speed1": ("SPEED 1", "SPEED1"),
    "speedsonic": ("SPEED SONIC", "SPEEDSONIC"),
}


def load_jp_only_titles(path: str | Path) -> list[Trophy]:
    """读取「日服限定」称号（wiki 有、落雪没有）。

    文件不存在时返回空列表——没有它也能正常查称号，只是少了日服那一批。
    """
    import json

    target = Path(path)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - 损坏时降级
        logger.warning("日服限定称号读取失败，忽略: %s", exc)
        return []

    items = payload.get("trophies", payload) if isinstance(payload, dict) else payload
    out: list[Trophy] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            trophy_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = _clean_wiki_name(str(item.get("name") or ""))
        if not name:
            continue
        out.append(
            Trophy(
                id=trophy_id,
                name=name,
                # wiki 没给颜色 → 明确标「未标注」；人工核对过的用 json 里的 color
                color=str(item.get("color") or "unknown"),
                description=str(item.get("condition") or "").strip(),
            )
        )
    return out