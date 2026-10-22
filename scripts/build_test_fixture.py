"""从曲库缓存生成随包测试夹具 ``tests/data/``。

为什么提交一份**完整**曲库而不是裁剪样本：

* 测试因此**完全离线、可复现**，CI 不需要联网，也不依赖 `data/cache`；
* 搜索、筛选、物量这些功能的正确性依赖真实数据的复杂性（别名分布、
  同名曲、多难度、WORLD'S END 无定数、版本断层……），裁剪样本会掩盖问题；
* 曲库本身是公共数据，体积（约 2 MB）对仓库可接受。

只丢弃用不到的字段（``rights`` / ``map`` / ``locked``）。

用法::

    python scripts/fetch_reference_data.py     # 先抓缓存
    python scripts/build_test_fixture.py       # 再生成夹具
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_SONGS = ROOT / "data" / "cache" / "song_list.json"
CACHE_ALIASES = ROOT / "data" / "cache" / "alias_list.json"
OUT_DIR = ROOT / "tests" / "data"

#: 谱面里保留的字段
_DIFFICULTY_FIELDS = (
    "difficulty",
    "level",
    "level_value",
    "note_designer",
    "version",
    "notes",
    "origin_id",
    "kanji",
    "star",
)
#: 曲目里保留的字段
_SONG_FIELDS = ("id", "title", "artist", "genre", "bpm", "version", "disabled", "difficulties")

#: 丢弃值为空的字段，进一步压体积
def _compact(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if value not in (None, [], {}, "")}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 FastChu 的测试夹具")
    parser.add_argument("--songs", type=pathlib.Path, default=CACHE_SONGS)
    parser.add_argument("--aliases", type=pathlib.Path, default=CACHE_ALIASES)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--indent", type=int, default=None, help="默认紧凑输出（体积优先）")
    args = parser.parse_args()

    if not args.songs.is_file():
        print(f"找不到曲库缓存 {args.songs}，请先运行 scripts/fetch_reference_data.py")
        return 1

    bundle = json.loads(args.songs.read_text(encoding="utf-8"))

    songs_out: list[dict] = []
    for song in bundle.get("songs", []):
        difficulties = [
            _compact({key: chart.get(key) for key in _DIFFICULTY_FIELDS})
            for chart in song.get("difficulties", [])
        ]
        song_out = {key: song.get(key) for key in _SONG_FIELDS}
        song_out["difficulties"] = difficulties
        songs_out.append(_compact(song_out))

    payload = {
        "songs": songs_out,
        "genres": bundle.get("genres", []),
        "versions": bundle.get("versions", []),
        "_note": "由 scripts/build_test_fixture.py 生成，供离线测试使用；数据来源为落雪查分器公共接口。",
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    songs_path = args.out_dir / "song_list.json"
    songs_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=args.indent), encoding="utf-8"
    )

    aliases_path = args.out_dir / "alias_list.json"
    if args.aliases.is_file():
        aliases_raw = json.loads(args.aliases.read_text(encoding="utf-8"))
        aliases_path.write_text(
            json.dumps(aliases_raw, ensure_ascii=False, indent=args.indent), encoding="utf-8"
        )
    else:
        print(f"⚠ 别名缓存不存在，跳过：{args.aliases}")

    note_charts = sum(
        1 for song in songs_out for chart in song["difficulties"] if chart.get("notes")
    )
    print(
        f"已生成 {songs_path.relative_to(ROOT)}：{len(songs_out)} 首曲目，"
        f"{sum(len(s['difficulties']) for s in songs_out)} 个谱面（含物量 {note_charts} 个），"
        f"{songs_path.stat().st_size / 1024:.0f} KB"
    )
    if aliases_path.is_file():
        print(
            f"已生成 {aliases_path.relative_to(ROOT)}："
            f"{aliases_path.stat().st_size / 1024:.0f} KB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
