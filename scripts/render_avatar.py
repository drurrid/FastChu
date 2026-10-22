"""把头像 HTML 渲染成 PNG（QQ 头像 / 插件 logo 用）。

用无头 Chrome / Edge 截图，不引入额外依赖。

用法::

    python scripts/render_avatar.py                    # 默认输出 64 / 512 / 1024
    python scripts/render_avatar.py --sizes 128 256
    python scripts/render_avatar.py --browser "C:\\path\\to\\chrome.exe"
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = ROOT / "assets" / "avatar" / "fastchu-avatar.html"
OUT_DIR = ROOT / "assets" / "avatar"

#: 常见浏览器位置（Windows）
BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit if pathlib.Path(explicit).is_file() else None
    for candidate in BROWSER_CANDIDATES:
        if pathlib.Path(candidate).is_file():
            return candidate
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render(browser: str, size: int) -> pathlib.Path:
    out = OUT_DIR / f"fastchu-avatar-{size}.png"
    url = HTML.resolve().as_uri()
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--screenshot={out}",
        f"--window-size={size},{size}",
        url,
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=120)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="把 FastChu 头像 HTML 渲染成 PNG")
    parser.add_argument("--sizes", type=int, nargs="+", default=[64, 512, 1024])
    parser.add_argument("--browser", default=None, help="浏览器可执行文件路径")
    args = parser.parse_args()

    if not HTML.is_file():
        print(f"找不到 {HTML}", file=sys.stderr)
        return 1

    browser = find_browser(args.browser)
    if browser is None:
        print(
            "找不到 Chrome / Edge，无法无头截图。\n"
            "可以手动用浏览器打开 HTML 后截图，或用 --browser 指定路径。",
            file=sys.stderr,
        )
        return 1

    print(f"浏览器：{browser}")
    for size in args.sizes:
        out = render(browser, size)
        if not out.is_file() or out.stat().st_size < 500:
            print(f"  [!] {size}px 渲染失败（文件为空）", file=sys.stderr)
            return 1
        print(f"  {size:>4}px → {out.relative_to(ROOT)}（{out.stat().st_size:,} B）")

    print("\n完成。其中 fastchu-avatar-512.png 会作为插件 logo.png 一起打包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
