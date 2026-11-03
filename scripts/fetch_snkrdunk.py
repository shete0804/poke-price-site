"""スニダン（SNKRDUNK）の中古出品を、値段の安い順に上位5件ずつ取って、results/ に書き出す。

- results/snkrdunk_latest.csv : 今回の取得結果（毎回上書き）
- 対象は scripts/snkrdunk_targets.csv（カード・商品ID）。商品IDは、商品ページの URL（/apparels/商品ID）の番号。

出品の読み方:
- /v1/apparels/商品ID/used?order=price&perPage=50&page=N は、値段の昇順で返る（販売ページの「安い順」が使っている指定）。
  全ページを通して昇順になることを確認済み。
- 販売済み（status 4 など）も同じ並びに混ざるので、出品中（status 0）で未鑑定（A〜D）のものを集め、5件そろった時点で止める。
  昇順なので、そこまでで集めたものが最安の5件になる。
- 鑑定品（PSA・BGS・ARS）は外す。
"""
import csv
import datetime
import json
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ROOT / "scripts" / "snkrdunk_targets.csv"
LATEST = ROOT / "results" / "snkrdunk_latest.csv"
BASE = "https://snkrdunk.com/"
PRODUCT = BASE + "apparels/"
JST = datetime.timezone(datetime.timedelta(hours=9))
WAIT_SEC = 0.3
PER_PAGE = 50
TOP_N = 5
RAW_CONDITIONS = ("A", "B", "C", "D")  # 未鑑定の状態（A きれい〜D 大きなダメージ）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def fetch_json(path):
    req = urllib.request.Request(BASE + path, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "ja,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def raw_condition(item):
    """未鑑定の状態の記号（A〜D）。鑑定品（PSA など）なら None。"""
    text = item.get("displayWearCount") or ""
    head = text[:1]
    return head if head in RAW_CONDITIONS and text[1:2] in ("", "（", " ", "(") else None


def cheapest_listings(product_id):
    """値段の昇順に読み、出品中・未鑑定の出品が TOP_N 件そろった時点で止める。"""
    found = []
    page = 0
    while len(found) < TOP_N:
        page += 1
        query = f"order=price&perPage={PER_PAGE}&page={page}&withAllColors=false&isSaleOnly=false"
        items = fetch_json(f"v1/apparels/{product_id}/used?{query}").get("apparelUsedItems", [])
        if not items:  # 続きがない
            break
        for item in items:
            state = raw_condition(item) if item.get("status") == 0 else None
            if state:
                # 出品1件の個別ページは /apparels/商品ID/used/出品ID。写真は同じ応答の primaryPhoto から取る
                photo = (item.get("primaryPhoto") or {}).get("imageUrl", "")
                found.append((item["price"], state, f"{PRODUCT}{product_id}/used/{item['id']}", photo))
        time.sleep(WAIT_SEC)
    return found[:TOP_N]


def main():
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    with TARGETS.open(encoding="utf-8", newline="") as f:
        targets = list(csv.DictReader(f))

    results = []
    for t in targets:
        try:
            listings = cheapest_listings(t["商品ID"])
        except Exception as e:  # 取れなかったカードは空欄で残す
            listings = []
            print(f"取得失敗: {t['カード']}: {e}")
        for rank in range(1, TOP_N + 1):
            if rank <= len(listings):
                price, state, url, photo = listings[rank - 1]
                results.append({"カード": t["カード"], "順位": rank, "価格": price, "状態": state, "URL": url, "更新": now, "写真": photo})
            else:
                results.append({"カード": t["カード"], "順位": rank, "価格": "", "状態": "", "URL": "", "更新": now, "写真": ""})

    fields = ["カード", "順位", "価格", "状態", "URL", "更新", "写真"]
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    with LATEST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    missing = sorted({t["カード"] for t in targets} - {r["カード"] for r in results if r["価格"] != ""})
    print(f"{now} 対象 {len(targets)} 枚 / 行 {len(results)}")
    if missing:
        print("未鑑定の出品が1件も取れなかったカード: " + ", ".join(missing))


if __name__ == "__main__":
    main()
