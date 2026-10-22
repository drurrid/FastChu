"""中二节奏（CHUNITHM）曲目数据模型。

字段定义严格对齐落雪查分器官方文档
<https://maimai.lxns.net/docs/api/chunithm> 的「结构体」章节。

本项目只做**查歌**，因此模型里只保留曲目侧的数据：曲目、谱面、物量、别名、
分类、版本。玩家成绩、Rating 等结构体一律不在此列——它们需要鉴权，
而查歌只依赖**公共接口**。

设计要点：

* 所有模型 ``extra="ignore"``，接口新增字段不会导致解析失败。
* ``SongDifficulty.level_value`` 就是**谱面定数**，接口直接提供，不需要本地维护定数表。
* ``difficulty`` 用 :class:`LevelIndex`；WORLD'S END 的 ``level`` 恒为 ``"0"``、
  定数为 0，真实难度由 ``star`` / ``kanji`` 表示。
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LevelIndex",
    "Notes",
    "SongDifficulty",
    "Song",
    "Alias",
    "Genre",
    "Version",
    "SongListBundle",
    "RATING_ELIGIBLE_DIFFICULTIES",
    # 称号
    "Trophy",
    "TrophyRequired",
    "TrophyRequiredSong",
    "TrophyListBundle",
    "TROPHY_COLOR_LABELS",
    "RANK_LABELS",
    "FULL_COMBO_LABELS",
    "FULL_CHAIN_LABELS",
    "DIFFICULTY_LABELS",
]


class LevelIndex(IntEnum):
    """谱面难度。"""

    BASIC = 0
    ADVANCED = 1
    EXPERT = 2
    MASTER = 3
    ULTIMA = 4
    WORLDS_END = 5

    @property
    def label(self) -> str:
        return _LEVEL_LABELS[self.value]

    @property
    def short(self) -> str:
        return _LEVEL_SHORT[self.value]

    @classmethod
    def parse(cls, value: str | int | None) -> "LevelIndex | None":
        """把用户输入解析成难度。

        接受 ``mas`` / ``MASTER`` / ``3`` / ``紫`` 这类写法（大小写不敏感）。
        无法识别时返回 ``None``，由调用方给出提示。
        """
        if value is None:
            return None
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return None
        key = str(value).strip().upper()
        if key.isdigit():
            try:
                return cls(int(key))
            except ValueError:
                return None
        return _LEVEL_ALIASES.get(key)


_LEVEL_LABELS: dict[int, str] = {
    0: "BASIC",
    1: "ADVANCED",
    2: "EXPERT",
    3: "MASTER",
    4: "ULTIMA",
    5: "WORLD'S END",
}
_LEVEL_SHORT: dict[int, str] = {0: "BAS", 1: "ADV", 2: "EXP", 3: "MAS", 4: "ULT", 5: "WE"}

#: 用户可能输入的难度写法。中文用框体配色惯例。
_LEVEL_ALIASES: dict[str, LevelIndex] = {
    "BAS": LevelIndex.BASIC,
    "BASIC": LevelIndex.BASIC,
    "绿": LevelIndex.BASIC,
    "ADV": LevelIndex.ADVANCED,
    "ADVANCED": LevelIndex.ADVANCED,
    "黄": LevelIndex.ADVANCED,
    "EXP": LevelIndex.EXPERT,
    "EXPERT": LevelIndex.EXPERT,
    "红": LevelIndex.EXPERT,
    "MAS": LevelIndex.MASTER,
    "MASTER": LevelIndex.MASTER,
    "紫": LevelIndex.MASTER,
    "ULT": LevelIndex.ULTIMA,
    "ULTIMA": LevelIndex.ULTIMA,
    "黑": LevelIndex.ULTIMA,
    "WE": LevelIndex.WORLDS_END,
    "WORLD'S END": LevelIndex.WORLDS_END,
    "WORLDSEND": LevelIndex.WORLDS_END,
}

#: 有定数、可比难度的谱面难度（WORLD'S END 定数为 0，不参与定数比较与排序）。
RATING_ELIGIBLE_DIFFICULTIES: frozenset[LevelIndex] = frozenset(
    {LevelIndex.MASTER, LevelIndex.ULTIMA}
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Notes(_Base):
    """谱面物量。"""

    total: int | None = None
    tap: int | None = None
    hold: int | None = None
    slide: int | None = None
    air: int | None = None
    flick: int | None = None

    def as_pairs(self) -> list[tuple[str, int]]:
        """``[("总物量", 1626), ("TAP", 893), ...]``，只保留有值的项。"""
        fields = (
            ("total", "总物量"),
            ("tap", "TAP"),
            ("hold", "HOLD"),
            ("slide", "SLIDE"),
            ("air", "AIR"),
            ("flick", "FLICK"),
        )
        return [
            (label, value) for field, label in fields if (value := getattr(self, field)) is not None
        ]


class SongDifficulty(_Base):
    """一个谱面。"""

    difficulty: LevelIndex
    level: str = ""
    #: 谱面定数。WORLD'S END 为 0。
    level_value: float = 0.0
    note_designer: str | None = None
    version: int | None = None
    notes: Notes | None = None
    # --- 仅 WORLD'S END ---
    origin_id: int | None = None
    kanji: str | None = None
    star: int | None = None

    @property
    def is_worlds_end(self) -> bool:
        return self.difficulty is LevelIndex.WORLDS_END

    @property
    def has_constant(self) -> bool:
        """是否有可用定数（WORLD'S END 没有）。"""
        return not self.is_worlds_end and self.level_value > 0

    @property
    def display_level(self) -> str:
        """展示用难度：WORLD'S END 用 ``★星级``，其余用标级（如 ``14+``）。"""
        if self.is_worlds_end:
            return f"★{self.star if self.star is not None else '?'}"
        return self.level or (f"{self.level_value:g}" if self.level_value else "?")

    @property
    def constant_text(self) -> str:
        """定数文本；没有定数时给 ``-``。"""
        return f"{self.level_value:g}" if self.has_constant else "-"


class Song(_Base):
    """一首曲子。"""

    id: int
    title: str
    artist: str | None = None
    genre: str | None = None
    bpm: int | None = None
    #: 曲目首次出现版本
    version: int | None = None
    map: str | None = None
    rights: str | None = None
    locked: bool = False
    disabled: bool = False
    difficulties: list[SongDifficulty] = Field(default_factory=list)

    # ------------------------------------------------------------------ 查询
    def chart(self, level_index: LevelIndex) -> SongDifficulty | None:
        for difficulty in self.difficulties:
            if difficulty.difficulty == level_index:
                return difficulty
        return None

    def charts_by_descending_constant(self) -> list[SongDifficulty]:
        """按定数从高到低；没有定数的谱面（WE）排在最后。"""
        return sorted(
            self.difficulties, key=lambda d: (d.has_constant, d.level_value), reverse=True
        )

    @property
    def max_constant(self) -> float:
        """该曲最高定数（WE 无定数，不参与）。"""
        return max((d.level_value for d in self.difficulties if d.has_constant), default=0.0)

    @property
    def has_notes(self) -> bool:
        return any(d.notes is not None for d in self.difficulties)

    @property
    def playable_difficulties(self) -> list[SongDifficulty]:
        """按难度从低到高排列的谱面。"""
        return sorted(self.difficulties, key=lambda d: d.difficulty.value)

    def difficulty_summary(self, *, only_with_constant: bool = False) -> str:
        """一行难度概览，例如 ``EXP 10 / MAS 14+ / ULT 15``。"""
        parts = []
        for difficulty in self.playable_difficulties:
            if only_with_constant and not difficulty.has_constant:
                continue
            parts.append(f"{difficulty.difficulty.short} {difficulty.display_level}")
        return " / ".join(parts)


class Alias(_Base):
    """曲目别名。"""

    song_id: int
    aliases: list[str] = Field(default_factory=list)


class Genre(_Base):
    id: int
    genre: str


class Version(_Base):
    id: int
    title: str
    version: int


class SongListBundle(_Base):
    """``GET /api/v0/chunithm/song/list`` 的响应。"""

    songs: list[Song] = Field(default_factory=list)
    genres: list[Genre] = Field(default_factory=list)
    versions: list[Version] = Field(default_factory=list)

# --------------------------------------------------------------------------- #
# 称号（Trophy）
#
# 官方文档的枚举与实际数据**不一致**，这里以实际数据为准：
#   * 颜色没有 ``copper``，实际出现 ``platina``；
#   * 多了一个文档里没写的 ``ultima``（ULTIMA 谱面 AJ 称号专用色）。
# 所以颜色用 ``str`` 而不是 Enum——宁可原样保留，也不要因为一个未知值整条解析失败。
# --------------------------------------------------------------------------- #

#: 称号颜色 → 中文标签。未收录的颜色原样返回，避免未知值导致渲染崩掉。
TROPHY_COLOR_LABELS: dict[str, str] = {
    "normal": "普通",
    "copper": "铜",
    "silver": "银",
    "gold": "金",
    "platina": "铂金",
    "platinum": "铂金",
    "rainbow": "虹",
    "ultima": "究极",
    "image": "图片",
    "unknown": "未标注",
}

#: 评级 → 显示文本
RANK_LABELS: dict[str, str] = {
    "s": "S",
    "sp": "S+",
    "ss": "SS",
    "ssp": "SS+",
    "sss": "SSS",
    "sssp": "SSS+",
}

#: 连击要求 → 显示文本
FULL_COMBO_LABELS: dict[str, str] = {
    "fullcombo": "FULL COMBO",
    "alljustice": "ALL JUSTICE",
    "alljusticecritical": "ALL JUSTICE CRITICAL",
}

#: 锁链要求 → 显示文本
FULL_CHAIN_LABELS: dict[str, str] = {
    "fullchain": "FULL CHAIN",
    "fullchain2": "FULL CHAIN 2",
}

DIFFICULTY_LABELS: dict[LevelIndex, str] = {
    LevelIndex.BASIC: "BAS",
    LevelIndex.ADVANCED: "ADV",
    LevelIndex.EXPERT: "EXP",
    LevelIndex.MASTER: "MAS",
    LevelIndex.ULTIMA: "ULT",
    LevelIndex.WORLDS_END: "WE",
}


class TrophyRequiredSong(_Base):
    """称号要求里的单个曲目。"""

    id: int
    title: str = ""
    type: str | None = None
    completed: bool | None = None
    completed_difficulties: list[LevelIndex] = Field(default_factory=list)


class TrophyRequired(_Base):
    """一个「或」条件。多个 ``TrophyRequired`` 之间满足任一即可。"""

    difficulties: list[LevelIndex] = Field(default_factory=list)
    rank: str | None = None
    full_combo: str | None = None
    full_chain: str | None = None
    songs: list[TrophyRequiredSong] = Field(default_factory=list)

    def condition_parts(self) -> list[str]:
        """把结构化条件转成人类可读的短语（与 ``description`` 互为印证）。"""
        parts: list[str] = []
        if self.difficulties:
            parts.append("/".join(DIFFICULTY_LABELS.get(d, str(d)) for d in self.difficulties))
        if self.rank:
            parts.append(f"RANK {RANK_LABELS.get(self.rank, self.rank.upper())}")
        if self.full_combo:
            parts.append(FULL_COMBO_LABELS.get(self.full_combo, self.full_combo))
        if self.full_chain:
            parts.append(FULL_CHAIN_LABELS.get(self.full_chain, self.full_chain))
        return parts


class Trophy(_Base):
    """一个称号。"""

    id: int
    name: str
    color: str = "normal"
    description: str = ""
    #: 角色等级（仅角色相关称号有值）
    level: int | None = None
    required: list[TrophyRequired] = Field(default_factory=list)

    @property
    def color_label(self) -> str:
        return TROPHY_COLOR_LABELS.get(self.color, self.color)

    @property
    def has_requirement(self) -> bool:
        return bool(self.required)

    @property
    def song_ids(self) -> list[int]:
        """该称号涉及的全部曲目 ID（去重、保序）。"""
        seen: set[int] = set()
        out: list[int] = []
        for req in self.required:
            for song in req.songs:
                if song.id not in seen:
                    seen.add(song.id)
                    out.append(song.id)
        return out

    @property
    def song_count(self) -> int:
        """涉及曲目数的最大值（``required`` 是「或」，取最大的一支）。"""
        return max((len(req.songs) for req in self.required), default=0)

    @property
    def full_combo(self) -> str | None:
        for req in self.required:
            if req.full_combo:
                return req.full_combo
        return None

    @property
    def full_chain(self) -> str | None:
        for req in self.required:
            if req.full_chain:
                return req.full_chain
        return None

    @property
    def rank(self) -> str | None:
        for req in self.required:
            if req.rank:
                return req.rank
        return None

    @property
    def difficulties(self) -> list[LevelIndex]:
        for req in self.required:
            if req.difficulties:
                return list(req.difficulties)
        return []

    def condition_text(self) -> str:
        """优先用官方 ``description``；没有的话用结构化条件拼。"""
        if self.description.strip() and self.description.strip() != "-":
            return self.description.strip()
        parts = [p for req in self.required for p in req.condition_parts()]
        return " / ".join(parts) if parts else "（无说明）"


class TrophyListBundle(_Base):
    """``GET /api/v0/chunithm/trophy/list`` 的响应。"""

    trophies: list[Trophy] = Field(default_factory=list)