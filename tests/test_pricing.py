# -*- coding: utf-8 -*-
"""
飞书多维表格模块 自动化测试套件
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


class TestFeishuProductRecord:
    """FeishuProductRecord 的约束验证"""

    def test_normal_creation(self):
        from pricing.feishu_reader import FeishuProductRecord
        record = FeishuProductRecord(msku="MY-SKU", base_price=29.99)
        assert record.msku == "MY-SKU"
        assert record.base_price == 29.99

    def test_empty_msku_raises(self):
        from pricing.feishu_reader import FeishuProductRecord
        with pytest.raises(ValueError, match="msku"):
            FeishuProductRecord(msku="", base_price=10.0)

    def test_zero_base_price_raises(self):
        from pricing.feishu_reader import FeishuProductRecord
        with pytest.raises(ValueError, match="base_price"):
            FeishuProductRecord(msku="SKU-1", base_price=0)

    @pytest.mark.asyncio
    async def test_read_feishu_products_raises_when_env_missing(self, monkeypatch):
        """FEISHU_APP_ID 未设置时应抛出 EnvironmentError"""
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        from pricing.feishu_reader import read_feishu_products
        with pytest.raises(EnvironmentError, match="FEISHU_APP_ID"):
            await read_feishu_products()
