"""이치방쿠지 공식 사이트에서 작품별 쿠지와 상(賞) 목록을 수집."""
import html
import re

from .common import WORKS, db, get

BASE = "https://1kuji.com"
FIGURE_WORDS = ["フィギュア", "MASTERLISE", "SOFVIMATES", "ソフビ", "Revible Moment",
                "EXPIECE", "SMSP", "DXF", "MEGAIMPACT", "ちょこのっこ"]


def _text(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def list_kuji(kuji_id):
    out, page = [], 1
    while True:
        r = get(f"{BASE}/products/search?product_category_search=1"
                f"&index_master_search={kuji_id}&page={page}")
        if not r:
            break
        items = re.findall(
            r'<a href="/products/([^"]+)"><img[^>]*src="([^"]*)".*?'
            r'<p class="itemName">(.*?)</p>', r.text, re.S)
        new = [i for i in items if i[0] not in {o[0] for o in out}]
        if not new:
            break
        out += new
        page += 1
    return out


def parse_letter(name):
    if "ラストワン" in name:
        return "LAST"
    m = re.match(r"\s*([A-Z])賞", name)
    return m.group(1) if m else None


def parse_product(page):
    m = re.search(r"■発売日.*?(\d{4})年(\d{2})月(\d{2})日", page, re.S)
    date = "-".join(m.groups()) if m else None
    m = re.search(r"1回([\d,]+)円", page)
    price = int(m.group(1).replace(",", "")) if m else None
    prizes = []
    for block in page.split('<div class="itemColList">')[1:]:
        m = re.search(r'<h4 class="name[^"]*">(.*?)</h4>', block, re.S)
        if not m:
            continue
        name = _text(m.group(1))
        letter = parse_letter(name)
        if not letter:
            continue
        img = re.search(r'<img[^>]*src="([^"]+)"', block)
        size = re.search(r"■サイズ：([^<]*)", block)
        size = _text(size.group(1)) if size else ""
        cm = re.search(r"約?(\d+(?:\.\d+)?)\s*cm", size)
        is_fig = any(w in name for w in FIGURE_WORDS)
        # 이름에 표시가 없어도 10cm 이상 입체물로 보이면 피규어로 취급하지 않음(오탐 방지)
        prizes.append(dict(letter=letter, name=name, image=img.group(1) if img else "",
                           size=size, is_figure=int(is_fig),
                           cm=float(cm.group(1)) if cm else None))
    return date, price, prizes


def run():
    con = db()
    for work, w in WORKS.items():
        kujis = list_kuji(w["kuji_id"])
        print(f"[{w['ko']}] 쿠지 {len(kujis)}종")
        for slug, image, name in kujis:
            r = get(f"{BASE}/products/{slug}")
            if not r:
                continue
            date, price, prizes = parse_product(r.text)
            con.execute("INSERT OR REPLACE INTO kuji VALUES(?,?,?,?,?,?)",
                        (slug, work, _text(name), image, date, price))
            seen = set()
            for p in prizes:
                if p["letter"] in seen:
                    continue
                seen.add(p["letter"])
                con.execute("INSERT OR REPLACE INTO prize VALUES(?,?,?,?,?,?,?)",
                            (f"{slug}:{p['letter']}", slug, p["letter"], p["name"],
                             p["image"], p["size"], p["is_figure"]))
            figs = sum(p["is_figure"] for p in prizes)
            print(f"  {date or '미정':10} {_text(name)}  (상 {len(prizes)}, 피규어 {figs})")
            con.commit()
    con.close()
