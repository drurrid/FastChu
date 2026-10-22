"""抓取落雪查分器（LXNS）中二节奏的**公共**接口数据。

公共接口无需任何密钥，可以随时重新抓取：
  - 曲目列表（含物量）   GET /api/v0/chunithm/song/list
  - 曲目别名列表         GET /api/v0/chunithm/alias/list
  - 称号列表（含条件）   GET /api/v0/chunithm/trophy/list

抓取结果写入 ``data/cache/``，供曲库索引与称号分类使用
（该目录在 ``.gitignore`` 中；仓库自带的离线测试夹具在 ``tests/data/``，
由 ``scripts/build_test_fixture.py`` 与 ``scripts/build_title_fixture.py`` 生成）。

用法::

    python scripts/fetch_reference_data.py
    python scripts/fetch_reference_data.py --base-url https://maimai.lxns.net/api/v0
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"

DEFAULT_BASE_URL = "https://maimai.lxns.net/api/v0"
USER_AGENT = "FastChu/0.1 (+https://maimai.lxns.net)"


def fetch_json(url: str, timeout: float = 60.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def unwrap(payload: dict) -> dict:
    """落雪 API 统一返回 {success, code, message, data} 信封。"""
    if isinstance(payload, dict) and "data" in payload and "success" in payload:
        if not payload.get("success", False):
            raise RuntimeError(f"接口返回失败: code={payload.get('code')} message={payload.get('message')}")
        return payload["data"] or {}
    return payload


def save(name: str, payload: dict) -> pathlib.Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 LXNS 中二节奏公共接口数据")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--version", type=int, default=None, help="游戏版本号，默认使用接口默认值")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    jobs = {
        "song_list.json": "/chunithm/song/list?notes=true",
        "alias_list.json": "/chunithm/alias/list",
        "trophy_list.json": "/chunithm/trophy/list",
    }
    if args.version is not None:
        jobs["song_list.json"] += f"&version={args.version}"

    failures = 0
    for name, path in jobs.items():
        url = args.base_url.rstrip("/") + path
        try:
            data = unwrap(fetch_json(url, timeout=args.timeout))
        except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[FAIL] {name} <- {url}\n       {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue

        saved = save(name, data)
        summary = ", ".join(f"{k}={len(v) if isinstance(v, list) else v}" for k, v in data.items())
        print(f"[ OK ] {name} -> {saved.relative_to(ROOT)}  ({summary})")

    if failures:
        print(f"\n{failures} 个接口抓取失败。公共接口不需要密钥，失败通常是网络问题。", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
