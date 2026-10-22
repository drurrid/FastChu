"""共用测试夹具。

测试**完全离线、可复现**：不联网、不读项目根的 ``.env``、不依赖 ``data/cache``
（那是运行期缓存），统一用随包夹具 ``tests/data/``。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from fastchu.config import BotSettings
from fastchu.services.catalog import SongCatalog, load_catalog_from_files
from fastchu.services.search import SongSearch
from fastchu.services.titles import TrophyCatalog, load_trophies_from_file

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "data"
SONG_FIXTURE = FIXTURE_DIR / "song_list.json"
ALIAS_FIXTURE = FIXTURE_DIR / "alias_list.json"
TROPHY_FIXTURE = FIXTURE_DIR / "trophy_list.json"

#: 真实运行期缓存（本地可能存在，测试**不依赖**它）
RUN_CACHE = PROJECT_ROOT / "data" / "cache" / "song_list.json"


def make_settings(tmp_path: pathlib.Path | None = None, **overrides: Any) -> BotSettings:
    """构造隔离配置：不读 .env、曲库与称号指向随包夹具、默认不发曲绘。"""
    defaults: dict[str, Any] = {
        "_env_file": None,  # 关掉 .env 自动加载
        "song_cache_path": SONG_FIXTURE,
        "alias_cache_path": ALIAS_FIXTURE,
        # 补充别名默认也隔离：真实 community.json 会改变别名命中结果
        "community_alias_path": (tmp_path or FIXTURE_DIR) / "_no_community_aliases.json",
        "local_alias_path": (tmp_path or FIXTURE_DIR) / "_no_local_aliases.json",
        "trophy_cache_path": TROPHY_FIXTURE,
        # 区域对照默认不加载：默认路径指向真实并集数据（2.5 MB），
        # 那样测试既慢又会掺进「国/日」定数标注。需要时用例单独指定。
        "region_cache_path": (tmp_path or FIXTURE_DIR) / "_no_regions.json",
        # 条件补充同理：默认路径是真实的 data/titles/conditions.json，
        # 不隔离的话测试会掺进日服 Wiki 的条件。
        "condition_overrides_path": (tmp_path or FIXTURE_DIR) / "_no_conditions.json",
        # 日服限定称号同理：默认路径是真实数据，测试里要隔离掉
        "jp_only_titles_path": (tmp_path or FIXTURE_DIR) / "_no_jp_only.json",
        # 夹具视为"永久新鲜"：TTL=0 会让 CatalogLoader 认为缓存过期而走网络，
        # 那样测试就不再离线了。用足够大的值保证只读盘。
        "song_cache_ttl": 315_360_000,
        "jacket_mode": "off",  # 测试默认不碰网络
        "query_cooldown": 0.0,
        "jacket_cache_dir": (tmp_path or FIXTURE_DIR) / "_jacket_cache",
        # OAuth 默认关闭并隔离授权文件：普通测试既不联网也不碰真实绑定
        "oauth_enabled": False,
        "lxns_oauth_token_path": (tmp_path or FIXTURE_DIR) / "_oauth_tokens.json",
    }
    defaults.update(overrides)
    return BotSettings(**defaults)


@pytest.fixture
def settings(tmp_path: pathlib.Path) -> BotSettings:
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def catalog() -> SongCatalog:
    """整份曲库（会话级，避免反复解析 1.3 MB JSON）。"""
    if not SONG_FIXTURE.is_file():
        pytest.skip(f"缺少测试夹具 {SONG_FIXTURE}，请运行 scripts/build_test_fixture.py")
    return load_catalog_from_files(SONG_FIXTURE, ALIAS_FIXTURE)


@pytest.fixture(scope="session")
def search(catalog: SongCatalog) -> SongSearch:
    return SongSearch(catalog)


@pytest.fixture(scope="session")
def title_catalog() -> TrophyCatalog:
    """称号夹具（会话级）。"""
    if not TROPHY_FIXTURE.is_file():
        pytest.skip(f"缺少测试夹具 {TROPHY_FIXTURE}，请运行 scripts/build_title_fixture.py")
    return TrophyCatalog(load_trophies_from_file(TROPHY_FIXTURE))


@pytest.fixture(scope="session")
def raw_bundle() -> dict:
    return json.loads(SONG_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def raw_aliases() -> dict:
    return json.loads(ALIAS_FIXTURE.read_text(encoding="utf-8"))
