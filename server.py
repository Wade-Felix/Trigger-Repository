# -*- coding: utf-8 -*-
"""
飞书 Webhook 服务

接收飞书事件推送，当群内有人 @机器人 发送「ebay调价」时触发定价 pipeline。

启动方式：
    uvicorn server:app --host 0.0.0.0 --port 8000

飞书后台配置：
    开放平台 → 事件订阅 → 请求地址：http://<服务器IP>:8000/webhook
    订阅事件：im.message.receive_v1
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "src"))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Response
from pricing.preview import generate_pricing_preview
from pricing.ebay_csv_writer import (
    _prepare_single_output, _match_single_from_preview,
    _prepare_multi_output, _match_multi_from_preview,
)
from pricing.feishu_sender import send_output_to_group

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI()

# pipeline 运行锁，防止并发重复触发
_pipeline_lock = asyncio.Lock()

_TRIGGER_KEYWORD = "ebay调价"
_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")


def _verify_token(token: str) -> bool:
    """校验飞书推送请求的 token。token 为空时跳过校验（开发调试用）。"""
    if not _VERIFICATION_TOKEN:
        return True
    return token == _VERIFICATION_TOKEN


async def _reply_text(chat_id: str, text: str) -> None:
    """向群聊发送一条文字消息。"""
    import aiohttp
    from pricing.feishu_sender import _get_tenant_access_token

    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

    async with aiohttp.ClientSession() as session:
        token = await _get_tenant_access_token(session, app_id, app_secret)
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        async with session.post(
            url, headers={"Authorization": f"Bearer {token}"}, json=payload
        ) as resp:
            data = await resp.json()
    if data.get("code") != 0:
        logger.warning("回复消息失败：%s (code=%s)", data.get("msg"), data.get("code"))


async def _run_pipeline(chat_id: str) -> None:
    """执行完整 pipeline，开始和结束都向群里发通知。"""
    if _pipeline_lock.locked():
        await _reply_text(chat_id, "⏳ 定价任务正在运行中，请稍后再试。")
        return

    async with _pipeline_lock:
        await _reply_text(chat_id, "🚀 收到指令，开始执行 eBay 定价，完成后将发送文件...")
        logger.info("pipeline 开始执行，触发群聊：%s", chat_id)
        try:
            today = datetime.now().strftime("%Y%m%d")
            output_dir = BASE_DIR / "output" / today
            output_dir.mkdir(parents=True, exist_ok=True)

            preview_path = await generate_pricing_preview(output_dir)

            wb, out_path = _prepare_single_output("单属性.xlsx", output_dir)
            _match_single_from_preview(preview_path, "单属性.xlsx", wb, out_path)

            wb_multi, out_path_multi = _prepare_multi_output("多属性-1.xlsx", output_dir)
            _match_multi_from_preview(preview_path, "多属性-1.xlsx", wb_multi, out_path_multi)

            await send_output_to_group(output_dir)
            logger.info("pipeline 执行完成。")

        except Exception as exc:
            logger.exception("pipeline 执行失败：%s", exc)
            await _reply_text(chat_id, f"❌ 定价任务执行失败：{exc}")


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    # 飞书 URL 验证握手
    if body.get("type") == "url_verification":
        token = body.get("token", "")
        if not _verify_token(token):
            return Response(status_code=403)
        return {"challenge": body["challenge"]}

    # 校验 token
    header = body.get("header", {})
    if not _verify_token(header.get("token", "")):
        return Response(status_code=403)

    # 只处理消息接收事件
    if header.get("event_type") != "im.message.receive_v1":
        return {"ok": True}

    event = body.get("event", {})
    message = event.get("message", {})

    # 只处理群文字消息
    if message.get("chat_type") != "group" or message.get("message_type") != "text":
        return {"ok": True}

    content = json.loads(message.get("content", "{}"))
    text = content.get("text", "")
    mentions = event.get("message", {}).get("mentions", [])

    # 必须有 @mention 且文本含触发关键词
    if not mentions or _TRIGGER_KEYWORD not in text:
        return {"ok": True}

    chat_id = message.get("chat_id", "")
    logger.info("收到触发指令，chat_id=%s，text=%r", chat_id, text)

    # 异步执行，立即返回 200 给飞书
    asyncio.create_task(_run_pipeline(chat_id))
    return {"ok": True}
