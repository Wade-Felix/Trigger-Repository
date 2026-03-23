# -*- coding: utf-8 -*-
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from pricing.preview import generate_pricing_preview

if __name__ == "__main__":
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    output_dir = Path(__file__).parent / "output" / today
    output_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(generate_pricing_preview(output_dir))
