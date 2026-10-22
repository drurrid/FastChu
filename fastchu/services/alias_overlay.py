"""补充别名 overlay（社区数据 + 群友投稿）。

落雪的别名缓存只覆盖国服，而且刷新时会整体重写。社区数据与用户投稿因此放在
独立文件里，运行时再合并——落雪缓存怎么刷新都不会把它们冲掉。

两个文件的分工：

* ``community.json``：社区数据导入产物，只读，由脚本生成；
* ``local.json``：``/添加别名`` 写入，运行时累积，不入库。

文件格式相同（``local.json`` 可以没有 ``meta``）::

    {
      "meta": {...},
      "cn": {"2802": ["黑味增"]},
      "jp": {"2494": ["星野爱", "idol"]}
    }
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from ..models import Alias

__all__ = [
    "AliasOverlay",
    "load_alias_overlay",
    "merge_aliases",
    "append_local_alias",
]

logger = logging.getLogger(__name__)

AliasSection = Literal["cn", "jp"]


def _key(text: str) -> str:
    """别名去重键。延迟导入：``search`` 会反向引用 ``catalog``。"""
    from .search import normalize

    return normalize(text)


@dataclass
class AliasOverlay:
    """按曲目归属的补充别名。``cn`` 用国服 ID，``jp`` 用日服编号。"""

    cn: dict[int, list[str]] = field(default_factory=dict)
    jp: dict[int, list[str]] = field(default_factory=dict)


def _merge_section(target: dict[int, list[str]], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    for raw_id, values in raw.items():
        try:
            song_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(values, (list, tuple)):
            continue
        bucket = target.setdefault(song_id, [])
        keys = {_key(alias) for alias in bucket}
        for value in values:
            if not isinstance(value, str):
                continue
            alias = value.strip()
            key = _key(alias)
            if not alias or not key or key in keys:
                continue
            keys.add(key)
            bucket.append(alias)


def load_alias_overlay(paths: Iterable[str | pathlib.Path]) -> AliasOverlay:
    """按顺序合并若干 overlay 文件；缺失或损坏的文件跳过。"""
    overlay = AliasOverlay()
    for raw_path in paths:
        path = pathlib.Path(raw_path)
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            logger.warning("别名 overlay 读取失败，跳过 %s: %s", path, exc)
            continue
        if not isinstance(payload, dict):
            logger.warning("别名 overlay 格式不对，跳过 %s", path)
            continue
        _merge_section(overlay.cn, payload.get("cn"))
        _merge_section(overlay.jp, payload.get("jp"))
    return overlay


def merge_aliases(aliases: Sequence[Alias], overlay: AliasOverlay) -> list[Alias]:
    """把 overlay 的国服别名并进落雪别名列表（返回新列表）。"""
    merged = [Alias(song_id=item.song_id, aliases=list(item.aliases)) for item in aliases]
    by_song = {item.song_id: item for item in merged}

    for song_id, extras in overlay.cn.items():
        item = by_song.get(song_id)
        if item is None:
            item = Alias(song_id=song_id, aliases=[])
            merged.append(item)
            by_song[song_id] = item
        keys = {_key(alias) for alias in item.aliases}
        for alias in extras:
            key = _key(alias)
            if key and key not in keys:
                keys.add(key)
                item.aliases.append(alias)
    return merged


def append_local_alias(
    path: str | pathlib.Path,
    section: AliasSection,
    song_id: int,
    aliases: Sequence[str],
) -> None:
    """把别名追加到本地投稿文件（原子写入）。

    文件损坏时抛 ``ValueError``，而不是覆盖——里面可能是群友攒了很久的数据。
    """
    path = pathlib.Path(path)
    payload: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"本地别名文件读取失败: {path}") from exc
        if isinstance(loaded, dict):
            payload = loaded

    raw_section = payload.get(section)
    section_map = dict(raw_section) if isinstance(raw_section, dict) else {}

    raw_bucket = section_map.get(str(song_id))
    bucket = [str(value).strip() for value in raw_bucket] if isinstance(raw_bucket, list) else []
    keys = {_key(alias) for alias in bucket}
    for value in aliases:
        alias = str(value).strip()
        key = _key(alias)
        if not alias or not key or key in keys:
            continue
        keys.add(key)
        bucket.append(alias)
    section_map[str(song_id)] = bucket
    payload[section] = section_map

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
