"""把 FastChu 打包成 AstrBot 插件。

FastChu 的核心层（``models`` / ``api`` / ``services`` / ``render``）**不依赖 NoneBot**，
所以可以把它们原样复制进插件目录（``_vendor/``），让插件：

* 不需要 ``pip install``；
* 不会往 AstrBot 的 venv 里塞 nonebot 相关依赖（避免与 AstrBot 的 pydantic/httpx 冲突）。

复制的是**生成产物**：源仍是项目里的 ``fastchu/``，改代码后重新跑本脚本即可。

用法::

    python scripts/build_astrbot_plugin.py                     # 只生成 _vendor
    python scripts/build_astrbot_plugin.py --install           # 再装进 AstrBot 的 plugins 目录
    python scripts/build_astrbot_plugin.py --install --target <plugins 目录>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fastchu"
PLUGIN_DIR = ROOT / "astrbot_plugin_fastchu"
VENDOR_DIR = PLUGIN_DIR / "_vendor" / "fastchu"

#: 头像（同时用作 AstrBot 插件列表里的 logo.png）
AVATAR_PNG = ROOT / "assets" / "avatar" / "fastchu-avatar-512.png"
PLUGIN_LOGO = PLUGIN_DIR / "logo.png"

#: AstrBot Launcher 的实例根目录（每个实例下是 core/data/plugins）
INSTANCES_ROOT = pathlib.Path.home() / ".astrbot_launcher" / "instances"

#: 只复制框架无关的核心层。``plugins/``（NoneBot 胶水）、``cli.py``、``__main__.py``
#: 都依赖框架或命令行参数，插件里不需要。
COPY_FILES = ["__init__.py", "models.py", "errors.py", "config.py"]
COPY_DIRS = ["api", "services", "render"]

#: 复制时排除的内容
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

def discover_plugin_dirs() -> list[pathlib.Path]:
    """列出所有 AstrBot 实例的 data/plugins 目录。"""
    if not INSTANCES_ROOT.is_dir():
        return []
    found = []
    for instance in sorted(INSTANCES_ROOT.iterdir()):
        plugins = instance / "core" / "data" / "plugins"
        if plugins.is_dir():
            found.append(plugins)
    return found


def copy_core() -> int:
    """复制核心层到插件目录，返回复制的文件数。"""
    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    for name in COPY_FILES:
        src = SOURCE / name
        if not src.is_file():
            print(f"  [!] 缺少 {src}", file=sys.stderr)
            continue
        shutil.copy2(src, VENDOR_DIR / name)
        count += 1

    for name in COPY_DIRS:
        src = SOURCE / name
        if not src.is_dir():
            print(f"  [!] 缺少目录 {src}", file=sys.stderr)
            continue
        shutil.copytree(src, VENDOR_DIR / name, ignore=IGNORE)
        count += sum(1 for _ in (VENDOR_DIR / name).rglob("*.py"))

    return count


def copy_logo() -> bool:
    """把头像复制成插件 logo.png（AstrBot 插件列表会显示它）。"""
    if not AVATAR_PNG.is_file():
        print(f"  [!] 找不到头像 {AVATAR_PNG}（先跑 scripts/render_avatar.py）", file=sys.stderr)
        return False
    shutil.copy2(AVATAR_PNG, PLUGIN_LOGO)
    return True


def write_paths() -> None:
    """记录项目路径，让插件能找到已经抓好的缓存（曲库/称号/并集）。"""
    (PLUGIN_DIR / "_paths.json").write_text(
        json.dumps({"project_dir": str(ROOT)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def install(target: pathlib.Path) -> int:
    """把插件目录整体复制到 AstrBot 的 plugins 目录。"""
    # AstrBot 首次启动前 data/plugins 可能还不存在，直接创建即可
    if not target.is_dir():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"  [!] 无法创建目标目录 {target}: {exc}", file=sys.stderr)
            return 1
        print(f"  已创建目录：{target}")

    dest = target / PLUGIN_DIR.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(PLUGIN_DIR, dest, ignore=IGNORE)
    print(f"  已安装到：{dest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 FastChu 打包成 AstrBot 插件")
    parser.add_argument("--install", action="store_true", help="复制到 AstrBot 的 plugins 目录")
    parser.add_argument(
        "--target",
        type=pathlib.Path,
        default=None,
        help="AstrBot 的 data/plugins 目录；只有一个实例时可省略",
    )
    args = parser.parse_args()

    if not (PLUGIN_DIR / "main.py").is_file():
        print(f"缺少插件入口 {PLUGIN_DIR / 'main.py'}", file=sys.stderr)
        return 1

    print(f"源：{SOURCE.relative_to(ROOT)}")
    count = copy_core()
    write_paths()
    print(f"  已复制核心层 {count} 个文件 → {VENDOR_DIR.relative_to(ROOT)}")
    print(f"  已写入 _paths.json（project_dir = {ROOT}）")
    if copy_logo():
        print(f"  已复制头像 → {PLUGIN_LOGO.relative_to(ROOT)}（AstrBot 插件列表会显示）")

    # 做一个环境自检用的标记：插件是否还缺文件
    missing = [
        name
        for name in ("main.py", "metadata.yaml")
        if not (PLUGIN_DIR / name).is_file()
    ]
    if missing:
        print(f"  [!] 插件缺少文件：{missing}", file=sys.stderr)
        return 1

    if args.install:
        targets: list[pathlib.Path] = []
        if args.target is not None:
            targets = [args.target]
        else:
            discovered = discover_plugin_dirs()
            if len(discovered) == 1:
                targets = discovered
            elif not discovered:
                print(f"  [!] 没找到 AstrBot 实例（{INSTANCES_ROOT}），请用 --target 指定", file=sys.stderr)
                return 1
            else:
                print("  发现多个 AstrBot 实例，请用 --target 指定要装到哪一个：", file=sys.stderr)
                for path in discovered:
                    print(f"    {path}", file=sys.stderr)
                return 1

        for target in targets:
            code = install(target)
            if code:
                return code

    print("\n完成。重启 AstrBot 或在 WebUI 里重载插件即可生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
