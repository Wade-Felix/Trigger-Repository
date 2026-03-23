# -*- coding: utf-8 -*-
"""
飞书数据读取层（Feishu Reader Layer）

职责：从飞书多维表格中读取产品行数据，推算每条产品的
``msku`` 与 ``base_price``（中间售价，后续由策略层叠加店铺规则）。

每行飞书记录产生 3 条 FeishuProductRecord：
    {upc}_M{内存}_H{硬盘}PC        base_price = BD成本定价售价
    {upc}_M{内存}_H{硬盘}PC_G      base_price = BD成本定价售价 × 0.8
    {upc}_M{内存}_H{硬盘}PC_LN     base_price = BD成本定价售价 - 20

对外输出规范（不可变更）：
    - 返回 ``List[FeishuProductRecord]``
    - 每条记录包含 ``msku: str`` 和 ``base_price: float``

环境变量（必须在运行前注入）：
    - FEISHU_APP_ID      飞书应用 App ID
    - FEISHU_APP_SECRET  飞书应用 App Secret
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 多维表格配置（非密钥，固定指向目标表格）
# ---------------------------------------------------------------------------

_BITABLE_APP_TOKEN = "VGZ4bG9mPaHhEKsHITlchhYxnPd"
_TABLE_ID = "tblLo9cck9tiJ23Z"
_FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"

# 字段名（与飞书多维表格中的列名完全一致）
_FIELD_UPC = "upc"
_FIELD_RAM = "内存"
_FIELD_STORAGE = "硬盘"
_FIELD_BD_PRICE = "BD成本定价售价"

# 分页每次拉取条数（飞书上限 500）
_PAGE_SIZE = 500


# ---------------------------------------------------------------------------
# 对外 DTO（接口契约，不可修改）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeishuProductRecord:
    """飞书产品行的标准输出 DTO。

    :param msku: 亚马逊卖家 SKU（Merchant SKU），不可为空字符串。
    :param base_price: 由飞书数据推算出的基础参考价（float，> 0）。
    """

    msku: str
    base_price: float

    def __post_init__(self) -> None:
        if not self.msku or not isinstance(self.msku, str):
            raise ValueError(f"msku 必须是非空字符串，收到: {self.msku!r}")
        if not isinstance(self.base_price, (int, float)) or self.base_price <= 0:
            raise ValueError(
                f"base_price 必须是正数（float），收到: {self.base_price!r}"
            )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

async def _get_tenant_access_token(
    session: aiohttp.ClientSession, app_id: str, app_secret: str
) -> str:
    """向飞书请求 tenant_access_token。"""
    url = f"{_FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal"
    async with session.post(url, json={"app_id": app_id, "app_secret": app_secret}) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"飞书鉴权失败：{data.get('msg')} (code={data.get('code')})"
        )
    logger.info("飞书 tenant_access_token 获取成功。")
    return data["tenant_access_token"]


async def _fetch_all_records(
    session: aiohttp.ClientSession, token: str
) -> list[dict]:
    """分页拉取多维表格全部原始记录。"""
    url = (
        f"{_FEISHU_BASE_URL}/bitable/v1/apps"
        f"/{_BITABLE_APP_TOKEN}/tables/{_TABLE_ID}/records"
    )
    headers = {"Authorization": f"Bearer {token}"}
    all_items: list[dict] = []
    page_token: str | None = None

    while True:
        params: dict = {"page_size": _PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token

        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"拉取飞书记录失败：{data.get('msg')} (code={data.get('code')})"
            )

        items = data.get("data", {}).get("items", [])
        all_items.extend(items)

        has_more = data.get("data", {}).get("has_more", False)
        if not has_more:
            break
        page_token = data.get("data", {}).get("page_token")

    logger.info("飞书多维表格原始记录拉取完成，共 %d 行。", len(all_items))
    return all_items


def _build_msku(upc: str, ram: str, storage: str, suffix: str = "") -> str:
    """按规则拼接 MSKU 字符串。storage 已预处理为显示字符串（如 '256'、'1T'、'1TB'）。"""
    base = f"{upc}_M{ram}_H{storage}PC"
    return f"{base}_{suffix}" if suffix else base


def _parse_records(raw_items: list[dict]) -> list[FeishuProductRecord]:
    """将原始飞书记录解析为 FeishuProductRecord 列表。

    每行产生最多 3 条记录（base / _G / _LN），计算后价格 ≤ 0 的变体跳过。
    """
    results: list[FeishuProductRecord] = []
    skipped = 0

    for item in raw_items:
        fields = item.get("fields", {})
        try:
            upc = str(fields[_FIELD_UPC]).strip()
            ram = str(fields[_FIELD_RAM]).strip()
            storage = str(fields[_FIELD_STORAGE]).strip()
            bd_price = float(fields[_FIELD_BD_PRICE])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "跳过记录（字段缺失或格式异常）：record_id=%s | 错误：%s",
                item.get("record_id", "?"),
                exc,
            )
            skipped += 1
            continue

        if not upc or bd_price <= 0:
            logger.warning(
                "跳过记录（upc 为空或价格非正数）：upc=%r, bd_price=%r",
                upc,
                bd_price,
            )
            skipped += 1
            continue

        # 内存：个位数时同时生成补零和不补零（M8 / M08）
        ram_variants = [ram]
        if int(ram) < 10 and not ram.startswith("0"):
            ram_variants.append(ram.zfill(2))

        # 存储：TB 时同时生成 nT 和 nTB；GB 时直接使用原值
        storage_int = int(storage)
        if storage_int <= 10:
            storage_variants = [f"{storage}T", f"{storage}TB"]
        else:
            storage_variants = [storage]

        # 每种内存写法 × 每种存储写法 × 三种后缀 × 有无 W11h
        for r in ram_variants:
            for s in storage_variants:
                base = _build_msku(upc, r, s)
                for suffix, price in [
                    ("",   round(bd_price, 2)),
                    ("G",  round(bd_price * 0.8, 2)),
                    ("LN", round(bd_price - 20, 2)),
                ]:
                    if price <= 0:
                        logger.warning("跳过变体（计算后价格非正数）：base=%s suffix=%s price=%.2f", base, suffix, price)
                        continue
                    msku = f"{base}_{suffix}" if suffix else base
                    w11h_msku = f"{base}_W11h_{suffix}" if suffix else f"{base}_W11h"
                    results.append(FeishuProductRecord(msku=msku, base_price=price))
                    results.append(FeishuProductRecord(msku=w11h_msku, base_price=price))

    if skipped:
        logger.warning("共跳过 %d 条原始记录（字段异常或价格无效）。", skipped)

    return results


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

async def read_feishu_products() -> list[FeishuProductRecord]:
    """从飞书多维表格读取产品数据并推算基础价格。

    每行飞书记录产生 3 条 FeishuProductRecord（base / _G / _LN 变体），
    base_price 为中间售价，后续由 runner 叠加各店铺的调价策略。

    :returns: 飞书产品记录列表 ``List[FeishuProductRecord]``
    :raises EnvironmentError: 若 FEISHU_APP_ID 或 FEISHU_APP_SECRET 未设置
    :raises RuntimeError: 若飞书鉴权或数据拉取返回错误码
    :raises aiohttp.ClientError: 网络层异常
    """
    logger.info("开始从飞书多维表格读取产品数据……")

    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()

    if not app_id:
        raise EnvironmentError(
            "环境变量 FEISHU_APP_ID 未设置或为空，"
            "请在 .env 文件或系统环境变量中配置后重试。"
        )
    if not app_secret:
        raise EnvironmentError(
            "环境变量 FEISHU_APP_SECRET 未设置或为空，"
            "请在 .env 文件或系统环境变量中配置后重试。"
        )

    async with aiohttp.ClientSession() as session:
        token = await _get_tenant_access_token(session, app_id, app_secret)
        raw_items = await _fetch_all_records(session, token)

    records = _parse_records(raw_items)
    logger.info(
        "飞书产品数据解析完成，共生成 %d 条 FeishuProductRecord（原始 %d 行 × 最多 3 变体）。",
        len(records),
        len(raw_items),
    )
    return records
