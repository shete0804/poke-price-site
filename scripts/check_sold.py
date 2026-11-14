"""チェックした出品が売り切れたら ntfy で知らせる。

- 読むもの: 共有状態 results/state.json（state ブランチ）の「チェック」（checked）の URL。
- 判定:
  - メルカリ: 商品詳細の status が sold_out（取引中 trading は売り切れ扱いにしない）。
  - スニダン: 出品一覧で status 4（販売済み）。一覧から消えて個別ページも 404 なら「出品が消えた」として知らせる。
  - PayPay: 出品ページの出品1件の status が SOLD なら売り切れ（OPEN は出品中）。
- 通知の中身: タイトルは「【理由】サイトの表示名（カード名）」、本文は「価格」と「元の出品の URL」。
  表示名と価格は、サイトの CSV（カード・価格）から探す。CSV から外れていれば、判定の時に取った値を使う。
- 一度知らせた URL は results/sold_notified.json に残し、二度知らせない。
- 必要な環境変数: NTFY_TOPIC（なければ送らず画面に出すだけ）

判定できなかった（通信エラーなど）出品は、知らせも記録もしない。次の実行で再び調べる。
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTIFIED_PATH = ROOT / "results" / "sold_notified.json"
STATE_URL = "https://raw.githubusercontent.com/shete0804/poke-price-site/state/results/state.json"
# サイトの表示名と価格を探す CSV（カード・価格・URL の列がある）
CATALOG_URLS = [
    "https://raw.githubusercontent.com/shete0804/poke-price-site/main/results/snkrdunk_latest.csv",
    "https://raw.githubusercontent.com/shete0804/mercari-scraper/main/results/mercari.csv",
    "https://raw.githubusercontent.com/shete0804/paypayfleamarket-price-tracker/main/results/paypay.csv",
]
NTFY_URL = "https://ntfy.sh/"
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SNKR_MAX_PAGES = 40
SNKR_PER_PAGE = 50
SNKR_WAIT_SEC = 0.3
MERCARI_WAIT_SEC = 0.5
PAYPAY_WAIT_SEC = 6

SNKR_RE = re.compile(r"snkrdunk\.com/apparels/(\d+)/used/(\d+)")
MERCARI_RE = re.compile(r"jp\.mercari\.com/(?:item|shops/product)/([A-Za-z0-9]+)")
PAYPAY_RE = re.compile(r"paypayfleamarket\.yahoo\.co\.jp/item/([A-Za-z0-9]+)")


def load_catalog() -> dict[str, tuple[str, int | None]]:
    """URL → (サイトの表示名, 価格)。3つの CSV を読む。読めなかった CSV は飛ばす。"""
    catalog: dict[str, tuple[str, int | None]] = {}
    for url in CATALOG_URLS:
        try:
            res = requests.get(url, timeout=30)
            res.raise_for_status()
        except Exception as err:  # noqa: BLE001
            print(f"CSV を読めず（表示名は別の方法で探す）: {url}: {err}", file=sys.stderr)
            continue
        for row in csv.DictReader(io.StringIO(res.text)):
            link = (row.get("URL") or "").strip()
            if not link:
                continue
            price = int(row["価格"]) if (row.get("価格") or "").isdigit() else None
            catalog[link] = ((row.get("カード") or "").strip(), price)
    return catalog


def load_snkr_targets() -> dict[str, str]:
    """スニダンの商品ID → サイトの表示名（scripts/snkrdunk_targets.csv）。出品が無くなっても表示名が分かる。"""
    path = ROOT / "scripts" / "snkrdunk_targets.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {(row.get("商品ID") or "").strip(): (row.get("カード") or "").strip() for row in csv.DictReader(f)}


def paypay_status(item_id: str) -> tuple[str, int | None, str]:
    """PayPay の出品ページ（__NEXT_DATA__ の出品1件）。
    戻り値は (状態, 価格, 題名)。状態は OPEN → 'on_sale'、SOLD → 'sold'、それ以外・取れない（429 など）は 'unknown'。"""
    req = urllib.request.Request(f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}",
                                 headers={"User-Agent": UA, "Accept-Language": "ja"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            html = res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return "unknown", None, ""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return "unknown", None, ""
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("id") == item_id and "status" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    if not found:
        return "unknown", None, ""
    item = found[0]
    price = int(item["price"]) if str(item.get("price", "")).isdigit() else None
    title = str(item.get("title") or "")
    status = item.get("status")
    if status == "OPEN":
        return "on_sale", price, title
    if status == "SOLD":
        return "sold", price, title
    return "unknown", price, title


def snkr_get(path: str):
    req = urllib.request.Request("https://snkrdunk.com/" + path, headers={
        "User-Agent": UA, "Accept": "application/json", "Accept-Language": "ja,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def snkr_listing_status(product_id: str, listing_id: str, cache: dict) -> tuple[str, int | None]:
    """(状態, 価格)。状態は 'sold' / 'on_sale' / 'missing' / 'other'。商品ごとの出品一覧を一度だけ全ページ読む。"""
    if product_id not in cache:
        found: dict[str, tuple[int | None, int | None]] = {}
        for page in range(1, SNKR_MAX_PAGES + 1):
            query = f"order=price&perPage={SNKR_PER_PAGE}&page={page}&withAllColors=false&isSaleOnly=false"
            items = snkr_get(f"v1/apparels/{product_id}/used?{query}").get("apparelUsedItems", [])
            if not items:
                break
            for item in items:
                found[str(item["id"])] = (item.get("status"), item.get("price"))
            time.sleep(SNKR_WAIT_SEC)
        cache[product_id] = found
    hit = cache[product_id].get(listing_id)
    if hit is None:
        return "missing", None
    status, price = hit
    if status == 4:
        return "sold", price
    if status == 0:
        return "on_sale", price
    return "other", price


def snkr_page_gone(url: str) -> bool:
    """個別ページが 404 なら True（出品そのものが無くなっている）。通信の失敗は False（知らせない）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return False
    except urllib.error.HTTPError as err:
        return err.code == 404


async def mercari_status(item_id: str) -> tuple[str, int | None, str]:
    """(状態, 価格, 題名)。状態は 'sold' / 'on_sale' / 'other'。"""
    from mercapi import Mercapi  # 依存は workflow で入れる

    item = await Mercapi().item(item_id)
    status = str(getattr(item, "status", ""))
    price = getattr(item, "price", None)
    name = str(getattr(item, "name", "") or "")
    if status == "sold_out":
        return "sold", price, name
    if status in ("on_sale", "ITEM_STATUS_ON_SALE"):
        return "on_sale", price, name
    return "other", price, name


def load_state() -> dict:
    res = requests.get(STATE_URL, timeout=30)
    res.raise_for_status()
    return res.json()


def notify(title: str, body: str, click: str) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print(f"[ntfy 未設定のため送らない] {title} / {body} / {click}")
        return
    res = requests.post(NTFY_URL, json={
        "topic": topic, "title": title, "message": body, "click": click, "priority": 4, "tags": ["moneybag"],
    }, timeout=15)
    res.raise_for_status()


def build_message(reason: str, card: str, price: int | None, url: str) -> tuple[str, str]:
    """通知のタイトルと本文。タイトルは理由とサイトの表示名、本文は価格と元の出品の URL。"""
    name = card or "（カード名不明）"
    title = f"【{reason}】{name}"
    price_text = f"¥{price:,}" if price else "価格不明"
    return title, f"{price_text}\n{url}"


async def main() -> int:
    state = load_state()
    checked: dict = state.get("checked", {}) or {}
    notified: dict[str, str] = {}
    if NOTIFIED_PATH.exists():
        notified = json.loads(NOTIFIED_PATH.read_text(encoding="utf-8"))
    catalog = load_catalog()
    snkr_names = load_snkr_targets()
    today = datetime.now(JST).strftime("%Y-%m-%d")
    snkr_cache: dict = {}
    sent = 0
    errors: list[str] = []

    for url in checked:
        if url in notified:
            continue
        reason = None
        fetched_price: int | None = None
        fetched_name = ""
        try:
            m = SNKR_RE.search(url)
            if m:
                st, fetched_price = snkr_listing_status(m.group(1), m.group(2), snkr_cache)
                if st == "sold":
                    reason = "売り切れ（スニダン）"
                elif st == "missing" and snkr_page_gone(url):
                    # 一覧にも無く、個別ページも 404（売れた、または取り下げ）
                    reason = "出品が消えた（売れた・取り下げ）"
                time.sleep(SNKR_WAIT_SEC)
            elif PAYPAY_RE.search(url):
                st, fetched_price, fetched_name = paypay_status(PAYPAY_RE.search(url).group(1))
                if st == "sold":
                    reason = "売り切れ（PayPay）"
                time.sleep(PAYPAY_WAIT_SEC)
            else:
                m = MERCARI_RE.search(url)
                if not m:
                    errors.append(f"対象外の URL: {url}")
                    continue
                st, fetched_price, fetched_name = await mercari_status(m.group(1))
                if st == "sold":
                    reason = "売り切れ（メルカリ）"
                time.sleep(MERCARI_WAIT_SEC)
        except Exception as err:  # noqa: BLE001
            errors.append(f"{url}: {type(err).__name__}: {err}")
            continue

        if reason:
            card, catalog_price = catalog.get(url, ("", None))
            # 表示名・価格は、サイトの CSV を優先。CSV に無ければ判定の時に取った値
            m_snkr = SNKR_RE.search(url)
            if not card and m_snkr:
                card = snkr_names.get(m_snkr.group(1), "")
            card = card or fetched_name
            price = catalog_price or fetched_price
            title, body = build_message(reason, card, price, url)
            notify(title, body, url)
            notified[url] = today
            sent += 1
            print(f"通知: {title} / {body.replace(chr(10), ' ')}")

    NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED_PATH.write_text(json.dumps(notified, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"チェック {len(checked)} 件 / 新しく知らせた {sent} 件 / 判定できなかった {len(errors)} 件")
    for e in errors[:10]:
        print("  判定できず:", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
