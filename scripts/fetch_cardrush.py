"""カードラッシュの買取価格を取って、results/ に書き出す。

- results/cardrush_latest.csv   : 今回の取得結果（毎回上書き）
- results/cardrush_history.csv  : 取得のたびに追記（時刻つき）

対象は scripts/targets.csv（検索名・表示名・型番）。
カードラッシュの買取表は、name パラメータで絞り込める（?name=）。
"""
import csv
import datetime
import pathlib
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ROOT / "scripts" / "targets.csv"
LATEST = ROOT / "results" / "cardrush_latest.csv"
HISTORY = ROOT / "results" / "cardrush_history.csv"
BASE = "https://cardrush.media/pokemon/buying_prices?name="
JST = datetime.timezone(datetime.timedelta(hours=9))
PRICE = re.compile(r"¥([\d,]+)")
WAIT_SEC = 1.5


class RowParser(HTMLParser):
    """表の行（tr）ごとに、セルの文字列を集める。"""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def fetch_rows(query):
    url = BASE + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        html = res.read().decode("utf-8")
    parser = RowParser()
    parser.feed(html)
    return parser.rows


def find_price(rows, name, number):
    """型番が一致する行から、名前が完全一致するものを優先して買取価格を返す。"""
    hits = [r for r in rows if len(r) >= 5 and r[3] == number and name in r[0]]
    if not hits:
        return None
    exact = [r for r in hits if r[0] == name]
    row = (exact or hits)[0]
    m = PRICE.search(row[4])
    return int(m.group(1).replace(",", "")) if m else None


def main():
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    with TARGETS.open(encoding="utf-8", newline="") as f:
        targets = list(csv.DictReader(f))

    cache = {}
    results = []
    for t in targets:
        query = t["検索名"]
        if query not in cache:
            try:
                cache[query] = fetch_rows(query)
            except Exception as e:  # 取れなかったものは空欄で残す
                cache[query] = None
                print(f"取得失敗: {query}: {e}")
            time.sleep(WAIT_SEC)
        rows = cache[query]
        price = find_price(rows, t["名前"], t["型番"]) if rows is not None else None
        results.append({
            "取得時刻": now,
            "カード": t["表示名"],
            "型番": t["型番"],
            "買取価格": "" if price is None else str(price),
        })

    fields = ["取得時刻", "カード", "型番", "買取価格"]
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    with LATEST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    new_file = not HISTORY.exists()
    with HISTORY.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerows(results)

    missing = [r["カード"] for r in results if r["買取価格"] == ""]
    print(f"{now} 取得 {len(results) - len(missing)}/{len(results)} 件")
    if missing:
        print("価格が取れなかったもの: " + ", ".join(missing))


if __name__ == "__main__":
    main()
