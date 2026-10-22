"""抓取日服 CHUNITHM 曲库（含谱面定数）。

数据源是 [reiwa.f5.si](https://reiwa.f5.si/api.html) 提供的
``chunirec_all.json``——它镜像了 chunirec 的 ``music/showall`` 接口，
并从「譜面定数メインフレーム」补充/修正了定数。

这个接口的注意事项（来自其说明页）：

* 原本不是作为正式 API 提供的，**结构可能随时变化**；
* 数据准确性不做保证；
* 不要发过量请求。

所以只抓一次、落盘缓存，后续都读本地。

输出：``data/cache/jp_chunirec_all.json``（约 800 KB，带 UTF-8 BOM）

用法::

    python scripts/fetch_jp_reference.py
    python scripts/fetch_jp_reference.py --force
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "jp_chunirec_all.json"

DEFAULT_URL = "https://reiwa.f5.si/chunirec_all.json"
USER_AGENT = "FastChu/0.1 (+https://maimai.lxns.net)"


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取日服 CHUNITHM 曲库")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", type=pathlib.Path, default=CACHE)
    parser.add_argument("--force", action="store_true", help="即使已有缓存也重新抓取")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if args.out.is_file() and not args.force:
        size = args.out.stat().st_size
        print(f"已有缓存 {args.out.relative_to(ROOT)}（{size:,} B），跳过。加 --force 重新抓取。")
        return 0

    request = urllib.request.Request(args.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[FAIL] 抓取失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # 这个文件带 UTF-8 BOM，用 utf-8-sig 解析
    try:
        items = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] 解析失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(raw)

    genres: dict[str, int] = {}
    for item in items:
        genre = (item.get("meta") or {}).get("genre", "?")
        genres[genre] = genres.get(genre, 0) + 1

    print(f"[ OK ] 日服曲目 {len(items)} 首 → {args.out.relative_to(ROOT)}（{len(raw):,} B）")
    print("       分类:", " / ".join(f"{k} {v}" for k, v in sorted(genres.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
