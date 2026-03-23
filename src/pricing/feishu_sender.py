# -*- coding: utf-8 -*-
"""
飞书群消息发送器

将指定目录下的所有 xlsx 文件上传并发送到飞书群聊。

环境变量：
    FEISHU_APP_ID       飞书应用 App ID（与 feishu_reader 共用）
    FEISHU_APP_SECRET   飞书应用 App Secret（与 feishu_reader 共用）
    FEISHU_NOTIFY_CHAT_ID  目标群聊 chat_id
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

_FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"


async def _get_tenant_access_token(
    session: aiohttp.ClientSession, app_id: str, app_secret: str
) -> str:
    url = f"{_FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal"
    async with session.post(url, json={"app_id": app_id, "app_secret": app_secret}) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书鉴权失败：{data.get('msg')} (code={data.get('code')})")
    return data["tenant_access_token"]


async def _upload_file(
    session: aiohttp.ClientSession, token: str, file_path: Path
) -> str:
    """上传文件，返回 file_key。"""
    url = f"{_FEISHU_BASE_URL}/im/v1/files"
    headers = {"Authorization": f"Bearer {token}"}
    with open(file_path, "rb") as f:
        form = aiohttp.FormData()
        form.add_field("file_type", "xls")
        form.add_field("file_name", file_path.name)
        form.add_field(
            "file", f,
            filename=file_path.name,
            content_type="application/octet-stream",
        )
        async with session.post(url, headers=headers, data=form) as resp:
            resp.raise_for_status()
            data = await resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"文件上传失败 [{file_path.name}]：{data.get('msg')} (code={data.get('code')})"
        )
    return data["data"]["file_key"]


async def _send_text(
    session: aiohttp.ClientSession, token: str, chat_id: str, text: str
) -> None:
    url = f"{_FEISHU_BASE_URL}/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    async with session.post(url, headers=headers, json=payload) as resp:
        data = await resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"发送文本消息失败：{data.get('msg')} (code={data.get('code')})")


async def _send_file(
    session: aiohttp.ClientSession, token: str, chat_id: str, file_key: str
) -> None:
    url = f"{_FEISHU_BASE_URL}/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }
    async with session.post(url, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"发送文件消息失败：{data.get('msg')} (code={data.get('code')})"
        )


async def send_output_to_group(output_dir: str | Path) -> None:
    """将 output_dir 下所有 xlsx 文件依次发送到飞书群聊。

    群聊 ID 从环境变量 FEISHU_NOTIFY_CHAT_ID 读取。
    """
    output_dir = Path(output_dir)
    files = sorted(output_dir.glob("*.xlsx"))
    if not files:
        logger.warning("目录中没有找到 xlsx 文件，跳过发送：%s", output_dir)
        return

    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    chat_id = os.environ.get("FEISHU_NOTIFY_CHAT_ID", "").strip()

    if not app_id or not app_secret:
        raise EnvironmentError("FEISHU_APP_ID 或 FEISHU_APP_SECRET 未设置")
    if not chat_id:
        raise EnvironmentError("FEISHU_NOTIFY_CHAT_ID 未设置")

    date_str = output_dir.name  # e.g. "20260323"

    async with aiohttp.ClientSession() as session:
        token = await _get_tenant_access_token(session, app_id, app_secret)

        await _send_text(
            session, token, chat_id,
            f"【eBay 定价】{date_str} 改价文件已生成，共 {len(files)} 个，请查收👇",
        )

        for file_path in files:
            logger.info("上传并发送：%s", file_path.name)
            file_key = await _upload_file(session, token, file_path)
            await _send_file(session, token, chat_id, file_key)

    logger.info("全部 %d 个文件已发送至群聊 %s", len(files), chat_id)
