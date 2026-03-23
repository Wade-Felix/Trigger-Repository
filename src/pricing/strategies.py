# -*- coding: utf-8 -*-
"""
eBay 店铺定价策略层

每个店铺对应一个 PricingStrategy 子类，compute(base_price) 返回该店铺的最终售价。
STRATEGY_REGISTRY 供外部模块查询。
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class PricingStrategy(ABC):
    @abstractmethod
    def compute(self, base_price: float) -> float: ...


class _PassThroughStrategy(PricingStrategy):
    def compute(self, base_price: float) -> float:
        if base_price <= 0:
            raise ValueError(f"base_price 必须为正数，收到: {base_price}")
        return round(base_price, 2)


class _NimoDirectStrategy(PricingStrategy):
    def compute(self, base_price: float) -> float:
        if base_price <= 0:
            raise ValueError(f"base_price 必须为正数，收到: {base_price}")
        return round(base_price / 0.9, 2)


STRATEGY_REGISTRY: dict[str, PricingStrategy] = {
    "nimo-official": _PassThroughStrategy(),
    "BESTPTV":       _PassThroughStrategy(),
    "nimooutlet":    _PassThroughStrategy(),
    "nimodeals":     _PassThroughStrategy(),
    "nimo-direct":   _NimoDirectStrategy(),
}
