"""抓日服曲绘的真实文件名。

背景：日服曲绘不在国服资源库里，只能去 unidb（db.chunirec.net）取。
但 chunirec 数据里的 ``img`` 字段**不是图片文件名**——用它会拿到同一张
占位图（粉紫色「呪文」图）。真正的文件名在曲目页 HTML 里：

    https://db.chunirec.net/music/{urlencode(title)}/{jp_id}
    → /lmg/music/jkt/xxxxxxxx=.jpg

所以这里逐页抓、缓存到 ``data/cache/jp_jackets.json``，
再由 ``build_song_regions.py`` 写进并集的 ``jp_img`` 字段。

用法::

    python scripts/fetch_jp_jackets.py            # 抓缺失的（有缓存就跳过）
    python scripts/fetch_jp_jackets.py --force    # 全量重抓
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNION_PATH = ROOT / "data" / "songs" / "songs.union.json"
CACHE_PATH = ROOT / "data" / "cache" / "jp_jackets.json"

BASE = "https://db.chunirec.net/music/"
USER_AGENT = "Mozilla/5.0 (compatible; FastChu/0.1; +https://maimai.lxns.net)"

#: 曲绘文件名里可能出现特殊点号（U+2024），不能只匹配 ``.jpg``
_IMAGE_RE = re.compile(r"/lmg/music/jkt/([^\"'\s]+?)jpg", re.IGNORECASE)

#: 请求间隔，别把人家站点打崩
DELAY = 0.35


def _clean_name(raw: str) -> str:
    """去掉文件名尾部可能混进来的特殊点号，补回 ``.jpg``。"""
    name = raw.strip()
    for dot in ("\u2024", "\u2027", "\uff0e"):
        if name.endswith(dot):
            name = name[: -len(dot)]
    name = name.rstrip(".")
    if not name.lower().endswith(".jpg"):
        name += ".jpg"
    return name


def fetch_jacket(song: dict) -> str | None:
    """抓一首日服曲目的曲绘文件名；没有就返回 ``None``。"""
    title = urllib.parse.quote(song["title"], safe="")
    url = f"{BASE}{title}/{song['jp_id']}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"    [!] {song['title'][:28]}: {exc}", file=sys.stderr)
        return None

    match = _IMAGE_RE.search(html)
    return _clean_name(match.group(1)) if match else None


def load_union() -> list[dict]:
    if not UNION_PATH.is_file():
        print(f"缺少并集数据：{UNION_PATH}", file=sys.stderr)
        print("先跑 python scripts/build_song_regions.py", file=sys.stderr)
        return []
    payload = json.loads(UNION_PATH.read_text(encoding="utf-8"))
    return payload.get("songs", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="抓日服曲绘文件名")
    parser.add_argument("--force", action="store_true", help="忽略缓存，全量重抓")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 首（调试用）")
    args = parser.parse_args()

    songs = [
        song
        for song in load_union()
        if song.get("jp_id") and song.get("regions") == ["jp"]
    ]
    if not songs:
        print("并集里没有日服独有曲目，先跑 build_song_regions.py")
        return 1

    cache: dict[str, str] = {}
    if not args.force and CACHE_PATH.is_file():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}

    if args.limit:
        songs = songs[: args.limit]

    todo = [s for s in songs if s["jp_id"] not in cache]
    print(f"日服独有 {len(songs)} 首，待抓 {len(todo)} 首（缓存 {len(cache)} 条）")

    found = 0
    for index, song in enumerate(todo, start=1):
        name = fetch_jacket(song)
        if name:
            cache[song["jp_id"]] = name
            found += 1
        if index % 25 == 0 or index == len(todo):
            print(f"  {index}/{len(todo)}  已找到 {found}")
        time.sleep(DELAY)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"完成：缓存 {len(cache)} 条 → {CACHE_PATH.relative_to(ROOT)}")
    print("接着跑 python scripts/build_song_regions.py 把文件名写进并集")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
