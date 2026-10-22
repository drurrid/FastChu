"""模拟 OneBot v11 客户端，对 FastChu 做一次真实的 WebSocket 往返测试。

用途：在没有 NapCat / QQ 的情况下，验证「反向 WebSocket」这条链路是通的。

它会：
1. 连上 ``ws://127.0.0.1:8080/onebot/v11/ws``
2. 发一条群消息事件（``/Rebellion``）
3. 回应机器人发来的 API 调用（``send_msg``）
4. 打印机器人实际要发出的内容

用法::

    python scripts/verify_onebot_ws.py
    python scripts/verify_onebot_ws.py --text "/End Time" --url ws://127.0.0.1:8080/onebot/v11/ws
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

DEFAULT_URL = "ws://127.0.0.1:8080/onebot/v11/ws"

SELF_ID = 10000
USER_ID = 10001
GROUP_ID = 20002


def make_event(text: str, message_id: int = 1) -> dict:
    """构造一条 OneBot v11 群消息事件（消息段用 array 格式）。"""
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": 1_700_000_000,
        "self_id": SELF_ID,
        "user_id": USER_ID,
        "group_id": GROUP_ID,
        "message_id": message_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": USER_ID, "nickname": "tester", "card": ""},
    }


def extract_text(params: dict) -> str:
    """从 API 调用参数里取出要发送的文本（兼容 array / string 两种消息格式）。"""
    message = params.get("message")
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for segment in message or []:
        if segment.get("type") == "text":
            parts.append(segment.get("data", {}).get("text", ""))
        elif segment.get("type") == "image":
            data = segment.get("data", {})
            source = data.get("file") or data.get("url") or ""
            parts.append(f"[图片 {str(source)[:60]}…]")
    return "".join(parts)


async def run(url: str, texts: list[str], timeout: float = 30.0) -> int:
    try:
        from websockets.asyncio.client import connect
    except ModuleNotFoundError as exc:  # pragma: no cover
        print(f"缺少 websockets：{exc}", file=sys.stderr)
        return 1

    # NoneBot 的反向 WS 端点要求这两个头：
    #   X-Self-ID      机器人自己的 QQ 号（OneBot 实现侧的登录号）
    #   X-Client-Role  Universal / API / Event
    # 缺了会被拒（HTTP 403）。NapCat 连过来时会自动带上，这里手工模拟。
    headers = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}

    print(f"连接 {url} ...")
    try:
        async with connect(url, open_timeout=timeout, additional_headers=headers) as ws:
            print("已连接。等待机器人的生命周期事件…")
            # OneBot v11 反向 WS：先收到 meta_event/lifecycle
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                print(f"  <- {first.get('post_type')}/{first.get('meta_event_type')}")
            except (asyncio.TimeoutError, json.JSONDecodeError):
                print("  （没收到生命周期事件，直接发消息也行）")

            replies = 0
            for index, text in enumerate(texts, start=1):
                event = make_event(text, message_id=index)
                await ws.send(json.dumps(event, ensure_ascii=False))
                print(f"\n>> 发送：{text}")

                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    payload = json.loads(raw)

                    # 机器人发来的 API 调用（send_msg / send_group_msg …）
                    if "action" in payload:
                        action = payload["action"]
                        params = payload.get("params") or {}
                        echo = payload.get("echo")
                        print(f"  <- 机器人要发送（{action}）：")
                        for line in extract_text(params).splitlines():
                            print(f"       {line}")
                        # 回一个成功响应
                        await ws.send(
                            json.dumps(
                                {
                                    "status": "ok",
                                    "retcode": 0,
                                    "data": {"message_id": 9000 + index},
                                    "echo": echo,
                                },
                                ensure_ascii=False,
                            )
                        )
                        replies += 1
                        break

                    # 其它事件（心跳等）忽略
            print(f"\n完成：收到 {replies} 次发送调用。")
            return 0 if replies else 1
    except OSError as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        print("请先启动机器人：python bot.py", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="用模拟 OneBot 客户端验证 FastChu 的 WS 链路")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--text",
        action="append",
        default=None,
        help="要发送的消息，可重复；默认发 /Rebellion 和 /帮助",
    )
    args = parser.parse_args()
    texts = args.text or ["/Rebellion", "/帮助"]
    return asyncio.run(run(args.url, texts))


if __name__ == "__main__":
    raise SystemExit(main())
