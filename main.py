# -*- coding: utf-8 -*-
import asyncio
import logging
import sys
import glob
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from pricing.preview import generate_pricing_preview
from pricing.ebay_csv_writer import (
    _prepare_single_output, _match_single_from_preview,
    _prepare_multi_output, _match_multi_from_preview,
)
from pricing.feishu_sender import send_output_to_group


async def main():
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    output_dir = Path(__file__).parent / "output" / today
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 生成定价预览表
    preview_path = await generate_pricing_preview(output_dir)

    # Step 2: 生成单属性改价表
    wb, out_path = _prepare_single_output("单属性.xlsx", output_dir)
    matched = _match_single_from_preview(preview_path, "单属性.xlsx", wb, out_path)
    print(f"单属性改价表已生成：{out_path}（匹配 {matched} 行）")

    # Step 3: 生成多属性改价表
    wb_multi, out_path_multi = _prepare_multi_output("多属性-1.xlsx", output_dir)
    matched_multi = _match_multi_from_preview(preview_path, "多属性-1.xlsx", wb_multi, out_path_multi)
    print(f"多属性改价表已生成：{out_path_multi}（匹配 {matched_multi} 个 listing 块）")

    # Step 4: 发送今日输出文件到飞书群聊
    await send_output_to_group(output_dir)


if __name__ == "__main__":
    asyncio.run(main())
