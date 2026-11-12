"""サイトの「PayPayフリマ」「メルカリ」「SNKRDUNK」の文字に付けるリンクを、カードごとに作って results/links.json に書く。

- PayPay：設定（target.yaml）の1個目の検索語 ＋ 下限の値段 で、安い順の検索ページ
- メルカリ：mercari_scraper.py の CardRule の1個目の検索語 ＋ 下限 で、安い順の検索ページ
- SNKRDUNK：商品ID（snkrdunk_targets.csv）のカードのトップページ（状態を問わず全部入る）

出品の有る無しに関係なく、常にリンクにする。下限や検索語は変わるので、毎時の取得のついでに作り直す。
キーはサイト側と同じ「全角＆を半角に・空白を除く・小文字」にしたカード名。
"""
import csv
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "links.json"
TARGETS = ROOT / "scripts" / "snkrdunk_targets.csv"
PAYPAY_YAML = "https://raw.githubusercontent.com/shete0804/paypayfleamarket-price-tracker/main/target.yaml"
MERCARI_PY = "https://raw.githubusercontent.com/shete0804/mercari-scraper/main/mercari_scraper.py"
MIN_CARDS = 40  # これより少なく読めたら、壊れたとみなして書き換えない


def key_of(name: str) -> str:
    return re.sub(r"[\s　]", "", name.replace("＆", "&")).lower()


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as res:
        return res.read().decode("utf-8")


def paypay_links(text: str) -> dict:
    out = {}
    name = None
    keyword = None
    for line in text.splitlines():
        m = re.match(r"\s*- name: (?!PayPay)(.+?)\s*$", line)
        if m:
            name, keyword = m.group(1), None
            continue
        m = re.match(r"\s*search_keyword:\s*(.+?)\s*$", line)
        if m and name:
            keyword = m.group(1).split("|")[0].strip()
            continue
        m = re.match(r"\s*exclude_keyword:\s*(.+?)\s*$", line)
        if m and name and keyword:
            terms = m.group(1).split(",")
            first = [int(t[len("下限A:"):]) for t in terms if t.startswith("下限A:")]
            second = [int(t[len("下限B:"):]) for t in terms if t.startswith("下限B:")]
            floor = max(first) if first else max(second, default=0)
            url = "https://paypayfleamarket.yahoo.co.jp/search/" + urllib.parse.quote(keyword, safe="")
            url += f"?minPrice={floor}&sort=price&order=asc&open=1" if floor else "?sort=price&order=asc&open=1"
            out[key_of(name)] = url
    return out


def mercari_links(text: str) -> dict:
    out = {}
    # 下限は floor=、なければ floor_second=（ミュウ RGB は floor_second だけで書かれている）
    for m in re.finditer(r'CardRule\(\s*"([^"]+)",\s*\[\s*"([^"]+)"[^\]]*\],\s*(floor|floor_second)\s*=\s*([\d_]+)', text):
        name, keyword, floor = m.group(1), m.group(2), int(m.group(4).replace("_", ""))
        url = "https://jp.mercari.com/search?keyword=" + urllib.parse.quote(keyword, safe="")
        url += f"&status=on_sale&sort=price&order=asc&price_min={floor}"
        out[key_of(name)] = url
    return out


def snkrdunk_links() -> dict:
    out = {}
    with TARGETS.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[key_of(row["カード"])] = f"https://snkrdunk.com/apparels/{row['商品ID']}"
    return out


def main() -> int:
    pp = paypay_links(fetch(PAYPAY_YAML))
    mc = mercari_links(fetch(MERCARI_PY))
    sn = snkrdunk_links()
    print(f"PayPay {len(pp)} / メルカリ {len(mc)} / SNKRDUNK {len(sn)}")
    if min(len(pp), len(mc), len(sn)) < MIN_CARDS:
        print("読めた数が少ないので、links.json は書き換えません", file=sys.stderr)
        return 1
    keys = sorted(set(pp) | set(mc) | set(sn))
    links = {k: {"paypay": pp.get(k, ""), "mercari": mc.get(k, ""), "snkrdunk": sn.get(k, "")} for k in keys}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{OUT} に {len(links)} 枚分を書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
