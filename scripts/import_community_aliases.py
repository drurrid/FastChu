"""把社区别名库整理成 FastChu 的 overlay。

数据源是 ``AmethystTim/ChunithmUtil`` 仓库里的两份用户投稿数据：

* ``data/alias.json`` —— 现行格式，``cid`` 为日服曲目编号；
* ``backup/alias_legacy.json`` —— 旧格式，``cid`` 是曲名。

这一步只做**数据清洗与 ID 归属**，不联网拉落雪数据：

* 按 ``songs.union.json`` 把条目映射到国服 ``cn_id`` 或日服 ``jp_idx``；
* 丢掉空别名、超长别名、与曲名相同的别名；
* 丢掉单字非 ASCII 别名（「屎」「爱」这类检索噪声太大）；
* 丢掉「同时挂在超过 N 首曲子上」的歧义别名（默认 5）；
* 同一首曲子的别名去重（大小写/全半角不敏感）。

输出 ``data/aliases/community.json``::

    {
      "meta": {...},
      "cn": {"3": ["bbkkbkk", "bk"], ...},
      "jp": {"2494": ["星野爱", "idol", "偶像"], ...}
    }

之后由运行时与落雪别名合并，落雪缓存刷新不会把社区数据冲掉。

用法::

    python scripts/import_community_aliases.py
    python scripts/import_community_aliases.py --fresh alias.json --legacy alias_legacy.json
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNION_PATH = ROOT / "data" / "songs" / "songs.union.json"
OUT_PATH = ROOT / "data" / "aliases" / "community.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastchu.services.search import normalize  # noqa: E402

FRESH_URL = "https://github.com/AmethystTim/ChunithmUtil/blob/master/data/alias.json"
LEGACY_URL = "https://github.com/AmethystTim/ChunithmUtil/blob/master/backup/alias_legacy.json"
API_BASE = "https://api.github.com/repos/AmethystTim/ChunithmUtil/contents"

#: 单条别名的长度上限。社区数据里偶尔有整句说明，不该进检索。
MAX_ALIAS_LENGTH = 40

#: 同时挂在超过这个数量曲目上的别名视为歧义（例如「屎」）。
MAX_COLLISIONS = 5


def fetch_github_json(path: str, *, attempts: int = 2) -> tuple[Any, str | None]:
    """从 GitHub contents API 取 JSON（返回内容与 blob sha）。"""
    url = f"{API_BASE}/{path}?ref=master"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "FastChu/0.1 (+https://maimai.lxns.net)",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            raw = base64.b64decode(payload["content"])
            return json.loads(raw.decode("utf-8-sig")), payload.get("sha")
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt)
    raise RuntimeError(f"拉取 GitHub 数据失败: {path}: {last_error}")


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def source_songs(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        return list(payload.get("songs") or [])
    return list(payload or [])


def build_song_index(union: list[dict]) -> tuple[dict[int, dict], dict[str, dict]]:
    """``jp_idx -> song`` 与 ``标题 -> song``。"""
    by_jp: dict[int, dict] = {}
    by_title: dict[str, dict] = {}
    for song in union:
        if song.get("jp_idx") is not None:
            by_jp.setdefault(int(song["jp_idx"]), song)
        title = (song.get("title") or "").strip()
        if title:
            by_title.setdefault(title, song)
    return by_jp, by_title


def resolve_song(raw_cid: Any, by_jp: dict[int, dict], by_title: dict[str, dict]) -> dict | None:
    text = str(raw_cid).strip()
    if text.isdigit():
        # 社区 cid 的语义是日服编号；不能拿它去匹配国服 ID——
        # 两套编号区间有重叠，未收录的新曲会被错误归到别的歌上。
        return by_jp.get(int(text))
    return by_title.get(text)


def song_key(song: dict) -> tuple[str, int] | None:
    """曲目的稳定标识：优先国服 ID，日服独有用日服编号。

    不能按曲名归并——union 里有 49 组同名但不同的曲目（如 ``B.B.K.K.B.K.K.``），
    按标题归并会把它们的别名混到第一首上。
    """
    if song.get("cn_id") is not None:
        return ("cn", int(song["cn_id"]))
    if song.get("jp_idx") is not None:
        return ("jp", int(song["jp_idx"]))
    return None


def collect_aliases(
    fresh: list[dict],
    legacy: list[dict],
    by_jp: dict[int, dict],
    by_title: dict[str, dict],
) -> tuple[dict[tuple[str, int], list[tuple[str, dict]]], int, int]:
    """把两份数据合并成 ``曲目标识 -> [(别名, 曲目)]``。

    返回 ``(per_song, raw_entries, raw_aliases)``。同一首曲子的别名按
    出现顺序去重（以 ``normalize`` 后的结果为准）。
    """
    per_song: dict[tuple[str, int], list[tuple[str, dict]]] = collections.defaultdict(list)
    seen: dict[tuple[str, int], set[str]] = collections.defaultdict(set)
    raw_entries = 0
    raw_aliases = 0

    def add(song: dict, aliases: list[Any]) -> None:
        nonlocal raw_aliases
        key = song_key(song)
        if key is None:
            return
        title = (song.get("title") or "").strip()
        for value in aliases:
            raw_aliases += 1
            alias = str(value).strip()
            if not alias or len(alias) > MAX_ALIAS_LENGTH:
                continue
            if len(alias) == 1 and not alias.isascii():
                continue
            normalized = normalize(alias)
            if not normalized or normalized == normalize(title):
                continue
            if normalized in seen[key]:
                continue
            seen[key].add(normalized)
            per_song[key].append((alias, song))

    for entry in fresh:
        raw_entries += 1
        song = resolve_song(entry.get("cid"), by_jp, by_title)
        if song is not None:
            add(song, entry.get("aliases") or [])

    for entry in legacy:
        raw_entries += 1
        song = by_title.get(str(entry.get("cid") or "").strip())
        if song is not None:
            add(song, entry.get("aliases") or [])

    return per_song, raw_entries, raw_aliases


def build_overlay(
    per_song: dict[tuple[str, int], list[tuple[str, dict]]], *, max_collisions: int
) -> tuple[dict[str, list[str]], dict[str, list[str]], int]:
    """按曲目归属输出 ``cn`` / ``jp`` 两个映射。"""
    alias_songs: dict[str, set[tuple[str, int]]] = collections.defaultdict(set)
    for key, items in per_song.items():
        for alias, _song in items:
            alias_songs[normalize(alias)].add(key)

    cn: dict[str, list[str]] = {}
    jp: dict[str, list[str]] = {}
    kept = 0
    for (kind, song_id), items in per_song.items():
        aliases = [
            alias
            for alias, _song in items
            if len(alias_songs[normalize(alias)]) <= max_collisions
        ]
        if not aliases:
            continue
        kept += len(aliases)
        (cn if kind == "cn" else jp)[str(song_id)] = aliases

    def sort_map(mapping: dict[str, list[str]]) -> dict[str, list[str]]:
        return {key: mapping[key] for key in sorted(mapping, key=int)}

    return sort_map(cn), sort_map(jp), kept


def main() -> int:
    parser = argparse.ArgumentParser(description="整理社区别名库为 FastChu overlay")
    parser.add_argument("--fresh", type=pathlib.Path, default=None, help="本地 alias.json；省略则联网拉取")
    parser.add_argument("--legacy", type=pathlib.Path, default=None, help="本地 alias_legacy.json；省略则联网拉取")
    parser.add_argument("--no-legacy", action="store_true", help="不使用旧版按曲名索引的数据")
    parser.add_argument("--out", type=pathlib.Path, default=OUT_PATH)
    parser.add_argument("--max-collisions", type=int, default=MAX_COLLISIONS)
    args = parser.parse_args()

    union_payload = load_json(UNION_PATH)
    union = union_payload.get("songs") if isinstance(union_payload, dict) else union_payload
    union = list(union or [])
    by_jp, by_title = build_song_index(union)

    fresh_sha: str | None = None
    legacy_sha: str | None = None
    input_mode = "network"
    if args.fresh is not None:
        fresh_payload = load_json(args.fresh)
        input_mode = "local"
    else:
        fresh_payload, fresh_sha = fetch_github_json("data/alias.json")

    if args.no_legacy:
        legacy_payload = []
    elif args.legacy is not None:
        legacy_payload = load_json(args.legacy)
        input_mode = "local"
    else:
        legacy_payload, legacy_sha = fetch_github_json("backup/alias_legacy.json")

    per_song, raw_entries, raw_aliases = collect_aliases(
        source_songs(fresh_payload),
        source_songs(legacy_payload),
        by_jp,
        by_title,
    )
    cn, jp, kept = build_overlay(per_song, max_collisions=args.max_collisions)

    payload = {
        "meta": {
            "source": FRESH_URL,
            "legacy_source": LEGACY_URL,
            "fresh_sha": fresh_sha,
            "legacy_sha": legacy_sha,
            "input_mode": input_mode,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "raw_entries": raw_entries,
            "raw_aliases": raw_aliases,
            "kept_aliases": kept,
            "cn_songs": len(cn),
            "jp_songs": len(jp),
            "max_collisions": args.max_collisions,
        },
        "cn": cn,
        "jp": jp,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[ OK ] 写入 {args.out.relative_to(ROOT)}")
    print(
        f"       原始 {raw_entries} 条 / {raw_aliases} 个别名；保留 {kept} 个；"
        f"国服 {len(cn)} 首，日服独有 {len(jp)} 首"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
