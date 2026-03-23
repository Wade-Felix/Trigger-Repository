# -*- coding: utf-8 -*-
"""
飞书 Webhook 服务

接收飞书事件推送：
  - 群内 @机器人 发送「ebay调价」→ 触发定价 pipeline
  - 群内发送文件（文件名含「单属性」或「多属性」）→ 自动更新对应模板

启动方式：
    uvicorn server:app --host 0.0.0.0 --port 8000

飞书后台配置：
    开放平台 → 事件订阅 → 请求地址：http://<服务器IP>:8000/webhook
    订阅事件：im.message.receive_v1
    权限：im:message、im:message.file:download
"""

from __future__ import annotations

import asyncio
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

_pipeline_lock = asyncio.Lock()
_TRIGGER_KEYWORD = "ebay调价"
_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")

# 文件名关键词 → 本地模板路径
_TEMPLATE_MAP = {
    "单属性": BASE_DIR / "单属性.xlsx",
    "多属性": BASE_DIR / "多属性-1.xlsx",
}


def _verify_token(token: str) -> bool:
    if not _VERIFICATION_TOKEN:
        return True
    return token == _VERIFICATION_TOKEN


async def _get_token() -> str:
    import aiohttp
    from pricing.feishu_sender import _get_tenant_access_token
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    async with aiohttp.ClientSession() as session:
        return await _get_tenant_access_token(session, app_id, app_secret)


async def _reply_text(chat_id: str, text: str) -> None:
    import aiohttp
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    token = await _get_token()
    async with aiohttp.ClientSession() as session:
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


async def _download_template(message_id: str, file_key: str, save_path: Path) -> None:
    """从飞书下载文件并保存到 save_path。"""
    import aiohttp
    token = await _get_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"下载文件失败，HTTP {resp.status}")
            content = await resp.read()
    save_path.write_bytes(content)
    logger.info("模板已更新：%s（%d bytes）", save_path.name, len(content))


async def _handle_file(message: dict, chat_id: str) -> None:
    """处理文件消息，匹配模板关键词后下载保存。"""
    content = json.loads(message.get("content", "{}"))
    file_key = content.get("file_key", "")
    file_name = content.get("file_name", "")
    message_id = message.get("message_id", "")

    matched_key = next((k for k in _TEMPLATE_MAP if k in file_name), None)
    if not matched_key:
        return  # 文件名无关，忽略

    save_path = _TEMPLATE_MAP[matched_key]
    try:
        await _download_template(message_id, file_key, save_path)
        await _reply_text(chat_id, f"✅ {matched_key}模板已更新：{file_name}")
    except Exception as exc:
        logger.exception("模板更新失败：%s", exc)
        await _reply_text(chat_id, f"❌ 模板更新失败：{exc}")


async def _run_pipeline(chat_id: str) -> None:
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

            wb, out_path = _prepare_single_output(BASE_DIR / "单属性.xlsx", output_dir)
            _match_single_from_preview(preview_path, BASE_DIR / "单属性.xlsx", wb, out_path)

            wb_multi, out_path_multi = _prepare_multi_output(BASE_DIR / "多属性-1.xlsx", output_dir)
            _match_multi_from_preview(preview_path, BASE_DIR / "多属性-1.xlsx", wb_multi, out_path_multi)

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
        if not _verify_token(body.get("token", "")):
            return Response(status_code=403)
        return {"challenge": body["challenge"]}

    # 校验 token
    header = body.get("header", {})
    if not _verify_token(header.get("token", "")):
        return Response(status_code=403)

    if header.get("event_type") != "im.message.receive_v1":
        return {"ok": True}

    event = body.get("event", {})
    message = event.get("message", {})

    if message.get("chat_type") != "group":
        return {"ok": True}

    msg_type = message.get("message_type")
    chat_id = message.get("chat_id", "")

    # 文件消息 → 尝试更新模板
    if msg_type == "file":
        asyncio.create_task(_handle_file(message, chat_id))
        return {"ok": True}

    # 文字消息 → 检查触发关键词
    if msg_type == "text":
        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "")
        mentions = message.get("mentions", [])
        if mentions and _TRIGGER_KEYWORD in text:
            logger.info("收到触发指令，chat_id=%s", chat_id)
            asyncio.create_task(_run_pipeline(chat_id))

    return {"ok": True}
