"""生成称号测试夹具（``tests/data/trophy_list.json``）。

真实称号列表有 5600+ 条、约 3.8 MB，直接进测试会让每次跑测试都慢一截。
这里按分类**抽样**成一个几百条的小夹具，覆盖全部 17 个分类 + 颜文字，
既能测试分类逻辑，又不拖慢测试。

用法::

    python scripts/build_title_fixture.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastchu.services.titles import TrophyCatalog, TrophyCategory, is_kaomoji, load_trophies_from_file

SOURCE = ROOT / "data" / "cache" / "trophy_list.json"
TARGET = ROOT / "tests" / "data" / "trophy_list.json"

#: 每个分类最多保留多少条；``None`` 表示全保留
PER_CATEGORY: dict[str, int | None] = {
    TrophyCategory.ALL_SONGS: None,  # 全曲系必须全留：曲目反查要用
    TrophyCategory.PER_SONG_AJ_ULTIMA: 20,
    TrophyCategory.PER_SONG_AJ_SPECIAL: 25,
    TrophyCategory.MULTIPLAYER: 25,
    TrophyCategory.PER_SONG_RANK: 25,
    TrophyCategory.PER_SONG_OTHER: 12,
    TrophyCategory.MATCHING_PLAYER: None,
    TrophyCategory.PLAYER_LEVEL: None,
    TrophyCategory.RATING: 10,
    TrophyCategory.LOGIN_DAYS: 10,
    TrophyCategory.MAP_CLEAR: 5,
    TrophyCategory.ONE_CREDIT: 5,
    TrophyCategory.DANGER_SKILL: None,
    TrophyCategory.EVENT: 10,
    TrophyCategory.OTHER: 10,
    TrophyCategory.INHERITED: 5,
    # 被排除的两个分类也要留样本，用来验证「确实被排除」
    TrophyCategory.PER_SONG_AJ: 20,
    TrophyCategory.CHARACTER_RANK: 20,
}


def main() -> int:
    if not SOURCE.is_file():
        print(f"找不到 {SOURCE}，先运行 python -m fastchu refresh", file=sys.stderr)
        return 1

    trophies = load_trophies_from_file(SOURCE)
    catalog = TrophyCatalog(trophies)

    kept: dict[int, None] = {}

    # 每个分类按上限取
    for category, cap in PER_CATEGORY.items():
        group = catalog.of_category(category)
        for trophy in group if cap is None else group[:cap]:
            kept[trophy.id] = None

    # 颜文字称号全部保留，方便测试颜文字识别
    for trophy in catalog.kaomoji():
        kept[trophy.id] = None

    selected = [t for t in trophies if t.id in kept]
    selected.sort(key=lambda t: t.id)

    payload = {"trophies": [t.model_dump(mode="json", exclude_none=True) for t in selected]}
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    subset = TrophyCatalog(selected)
    print(f"源称号 {len(trophies)} → 夹具 {len(selected)} 条")
    print(f"  特殊 {len(subset.special)} · 排除 {len(subset.excluded)}")
    print(f"  颜文字 {len(subset.kaomoji())}")
    print("  分类覆盖：")
    counts = subset.category_counts()
    missing = [c for c in PER_CATEGORY if c not in counts]
    for category, count in counts.items():
        print(f"    {category:<20} {count:>4}")
    if missing:
        print(f"  ⚠ 缺少分类: {missing}")
        return 1

    print(f"\n写入 {TARGET.relative_to(ROOT)}  ({TARGET.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
