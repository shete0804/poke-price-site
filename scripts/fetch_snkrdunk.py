"""スニダン（SNKRDUNK）の出品の最安値を、状態（A〜D）ごとに取って、results/ に書き出す。

- results/snkrdunk_latest.csv : 今回の取得結果（毎回上書き）
- 対象は scripts/snkrdunk_targets.csv（カード・商品ID）。商品IDは、商品ページの URL（/apparels/商品ID）の番号。
- 販売履歴は取らない。商品ページの「A〜D の出品の最安値」だけを読む。PSA は取らない。
"""
import csv
import datetime
import pathlib
import re
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ROOT / "scripts" / "snkrdunk_targets.csv"
LATEST = ROOT / "results" / "snkrdunk_latest.csv"
BASE = "https://snkrdunk.com/apparels/"
JST = datetime.timezone(datetime.timedelta(hours=9))
WAIT_SEC = 1.5
RETRY = 3

# 状態ごとの価格の並び（A の中に「A ¥15,000〜」の形で入っている）
CHIP = re.compile(
    r'YOqw-G_variant">([A-D])</p><p class="YOqw-G_price"><span class="YOqw-G_priceUnit">¥</span>([\d,]+)'
)


def fetch_html(product_id):
    req = urllib.request.Request(BASE + product_id, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8")


def parse_conditions(html):
    """状態 A〜D の最安値（円）を返す。出品がない状態は含めない。"""
    return {state: int(price.replace(",", "")) for state, price in CHIP.findall(html)}


def main():
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    with TARGETS.open(encoding="utf-8", newline="") as f:
        targets = list(csv.DictReader(f))

    results = []
    for t in targets:
        url = BASE + t["商品ID"]
        prices = None
        # 価格が読めないことがあるので、読めるまで数回試す（それでもだめなら空欄）
        for attempt in range(RETRY):
            try:
                prices = parse_conditions(fetch_html(t["商品ID"]))
            except Exception as e:
                print(f"取得失敗: {t['カード']}（{attempt + 1}回目）: {e}")
                prices = None
            if prices:
                break
            time.sleep(WAIT_SEC * 3)
        time.sleep(WAIT_SEC)
        for state in "ABCD":
            price = "" if prices is None or state not in prices else str(prices[state])
            results.append({
                "カード": t["カード"],
                "状態": state,
                "最安値": price,
                "URL": url,
                "更新": now,
            })

    fields = ["カード", "状態", "最安値", "URL", "更新"]
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    with LATEST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    # 状態のどれか1つでも価格があればよい。どの状態も価格がないカードだけを報告する
    priced = {r["カード"] for r in results if r["最安値"] != ""}
    missing = sorted({t["カード"] for t in targets} - priced)
    print(f"{now} 対象 {len(targets)} 枚 / 行 {len(results)}")
    if missing:
        print("どの状態も価格が取れなかったカード（出品待ち、または取得失敗）: " + ", ".join(missing))


if __name__ == "__main__":
    main()
