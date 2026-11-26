"""매물 수집: 야후옥션 낙찰 내역(일본), 번개장터(한국)."""
import json
import re
from datetime import datetime, timezone

from .common import WORKS, db, get

YAHOO_PAGES = 5      # 검색어당 최대 페이지(100건/페이지)
BUNJANG_PAGES = 40   # 검색어당 최대 페이지(60건/페이지)
BUNJANG_QUICK_PAGES = 5  # 빠른 수집 때: 최근 수정된 매물 300건만
# 상 표기 검색어("주술회전 A상" 등)는 검색어 수가 많아 쪽수를 적게 본다
BUNJANG_LETTER_PAGES = 10
BUNJANG_LETTER_QUICK_PAGES = 2

UPSERT = """
INSERT INTO listing(source,item_id,work,title,price,currency,status,dated,url,image,first_seen,last_seen)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(source,item_id) DO UPDATE SET
  title=excluded.title, price=excluded.price, status=excluded.status,
  dated=excluded.dated, image=excluded.image, last_seen=excluded.last_seen
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def figure_letters(con, work):
    rows = con.execute(
        "SELECT DISTINCT letter FROM prize p JOIN kuji k USING(slug) "
        "WHERE k.work=? AND p.is_figure=1", (work,)).fetchall()
    return sorted(r[0] for r in rows)


def yahoo(con, work, quick=False):
    """낙찰 완료된 경매만 나오는 검색. 가격은 실제 낙찰가."""
    w = WORKS[work]
    n = 0
    for key in w.get("ja_keys", [w["ja"]]):
        for letter in figure_letters(con, work):
            word = "ラストワン" if letter == "LAST" else f"{letter}賞"
            for page in range(YAHOO_PAGES):
                r = get("https://auctions.yahoo.co.jp/closedsearch/closedsearch",
                        params={"p": f"一番くじ {key} {word}", "n": 100, "b": page * 100 + 1})
                if not r:
                    break
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
                if not m:
                    break
                try:
                    items = (json.loads(m.group(1))["props"]["pageProps"]["initialState"]
                             ["search"]["items"]["listing"]["items"])
                except (KeyError, ValueError):
                    break
                new = 0
                for it in items:
                    if not it.get("price") or it.get("bidCount", 0) < 1:
                        continue
                    if not con.execute("SELECT 1 FROM listing WHERE source='yahoo' AND item_id=?",
                                       (it["auctionId"],)).fetchone():
                        new += 1
                    con.execute(UPSERT, (
                        "yahoo", it["auctionId"], work, it["title"], int(it["price"]), "JPY",
                        "sold", it["endTime"],
                        f"https://auctions.yahoo.co.jp/jp/auction/{it['auctionId']}",
                        it.get("imageUrl", ""), _now(), _now()))
                n += len(items)
                con.commit()
                # 빠른 수집: 최신순이므로 새 낙찰이 없는 페이지를 만나면 그만둔다
                if len(items) < 100 or (quick and new == 0):
                    break
    print(f"  야후옥션 [{w['ko']}] {n}건 확인")


def bunjang(con, work, quick=False):
    """판매중 + 판매완료. 가격은 판매자가 올린 가격."""
    w = WORKS[work]
    n = 0
    name = w["ko_keys"][0]
    brand_pages = BUNJANG_QUICK_PAGES if quick else BUNJANG_PAGES
    letter_pages = BUNJANG_LETTER_QUICK_PAGES if quick else BUNJANG_LETTER_PAGES
    queries = [(f"{brand} {name}", brand_pages) for brand in ("제일복권", "이치방쿠지")]
    # 제목에 "제일복권"·"이치방쿠지"를 안 쓰고 "주술회전 고죠 A상"처럼만 올린 매물을 잡는다.
    # 번개장터 검색은 상 글자별로 결과가 갈려서, 피규어가 있는 상마다 따로 검색한다.
    for letter in figure_letters(con, work):
        word = "라스트원" if letter == "LAST" else f"{letter}상"
        queries.append((f"{name} {word}", letter_pages))
    for q, pages in queries:
        cursor, seen_cursors = None, set()
        for _ in range(pages):
            params = {"q": q, "status": "SOLD_OUT", "sort": "latest", "size": 60,
                      "policyKey": "pw.product.keyword"}
            if cursor:
                params["cursor"] = cursor
            r = get("https://api.bunjang.co.kr/api/search/v8/web/search", params=params)
            if not r:
                break
            try:
                res = r.json()["data"]["responses"]["mainGrid"]["searchResponse"]
            except (KeyError, ValueError, TypeError):
                break
            items = res.get("data") or []
            for it in items:
                if it.get("type") != "PRODUCT" or it.get("ad") or not it.get("price"):
                    continue
                status = "sold" if it.get("status") == "SOLD_OUT" else "selling"
                con.execute(UPSERT, (
                    "bunjang", str(it["pid"]), work, it["name"], int(it["price"]), "KRW",
                    status, it.get("updatedAt", ""),
                    f"https://m.bunjang.co.kr/products/{it['pid']}",
                    (it.get("productImage") or "").replace("{res}", "300"), _now(), _now()))
            n += len(items)
            con.commit()
            cursor = res.get("nextCursor")
            if not items or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
    print(f"  번개장터 [{w['ko']}] {n}건 확인")


def run(quick=False):
    con = db()
    for work in WORKS:
        yahoo(con, work, quick)
        bunjang(con, work, quick)
    total = con.execute("SELECT source,count(*) FROM listing GROUP BY source").fetchall()
    print("  저장된 매물:", {r[0]: r[1] for r in total})
    con.close()
