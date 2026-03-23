# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

eBay Auto-Pricing System: reads product data from Feishu (飞书) multi-dimensional tables, computes per-store prices, generates eBay File Exchange Excel files, and sends them to a Feishu group chat.

## Setup & Commands

```bash
# Install dependencies (use venv on Linux servers)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure credentials
# Fill in all four variables in .env (see Environment Variables below)

# Run full pipeline
python main.py

# Run preview only (Feishu → Excel pricing table, no eBay files)
python preview.py

# Run tests
pytest tests/test_pricing.py -v
pytest tests/test_pricing.py::test_name -v
```

## Architecture

**Pipeline (main.py):**
```
飞书多维表格
    ↓ feishu_reader.py  → List[FeishuProductRecord]
    ↓ strategies.py     → per-store prices
    ↓ preview.py        → output/YYYYMMDD/定价预览_{date}.xlsx
    ↓ ebay_csv_writer.py
    │   ├── output/YYYYMMDD/单属性改价_{date}.xlsx
    │   ├── output/YYYYMMDD/单属性未匹配_{date}.xlsx
    │   ├── output/YYYYMMDD/多属性改价_{date}.xlsx
    │   └── output/YYYYMMDD/多属性未匹配_{date}.xlsx
    ↓ feishu_sender.py  → 发送全部文件到飞书群聊
```

**Modules:**
- `src/pricing/feishu_reader.py` — Feishu Bitable API (OAuth2, pagination), MSKU expansion
- `src/pricing/strategies.py` — per-store pricing strategy registry
- `src/pricing/preview.py` — Excel preview generator
- `src/pricing/ebay_csv_writer.py` — template-based Excel writer for eBay upload
- `src/pricing/feishu_sender.py` — uploads output xlsx files and sends to Feishu group chat

## Environment Variables

All three credentials files share the same Feishu app:

| 变量 | 用途 |
|------|------|
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret |
| `FEISHU_NOTIFY_CHAT_ID` | 接收文件的群聊 chat_id（`oc_` 开头） |

## Feishu Bitable Schema

App token: `VGZ4bG9mPaHhEKsHITlchhYxnPd` / Table ID: `tblLo9cck9tiJ23Z`

Each Feishu row expands into multiple `FeishuProductRecord` entries:

| MSKU suffix | base_price |
|---|---|
| `{upc}_M{内存}_H{硬盘}PC` | `BD成本定价售价` |
| `{upc}_M{内存}_H{硬盘}PC_G` | `BD成本定价售价 × 0.8` |
| `{upc}_M{内存}_H{硬盘}PC_LN` | `BD成本定价售价 − 20` |

Additional expansion: storage ≤10 → both `1T` and `1TB` variants; single-digit RAM → both `8` and `08` variants. Each MSKU also gets a `_W11h` duplicate.

## Pricing Strategies

Registry in `strategies.py` maps store name → `PricingStrategy.compute(base_price)`:
- `nimo-official`, `BESTPTV`, `nimooutlet`, `nimodeals` → pass-through
- `nimo-direct` → `base_price / 0.9` (≈11.1% markup)

## eBay Output Files

Template files (`单属性.xlsx`, `多属性-1.xlsx`) stay in the repo root. Each run creates `output/YYYYMMDD/` and writes all 5 output files there.

**单属性 matching logic** (`ebay_csv_writer.py`):
- Match key: `SKU` column first; if empty, fall back to `PlatformSKU`
- Matched rows → 改价文件 with updated `StartPrice`
- Unmatched rows → 未匹配文件 with yellow highlight
- All non-blank template rows must appear in exactly one of the two output files

**多属性 matching logic**:
- Groups rows into listing blocks by `eBayItemID` (parent row has `eBayUserID`)
- Any `V_SKU` hit in a block → entire block written with updated `V_Price`
- De-duplicates by `eBayItemID`
- Store mapping: `eBayUserID "NzuTUH3XQv-"` → `"BESTPTV"`

## Key Conventions

- `FeishuProductRecord` is a frozen dataclass: `msku` non-empty, `base_price` positive.
- All entry points are `async`; driven by `asyncio.run()` in `main.py` / `preview.py`.
- `run.sh` (Linux) / `run.bat` (Windows) are the scheduled-task entry points; logs append to `logs/run.log`.
