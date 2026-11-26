"""쿠지별 상 개수(로트 구성)를 블로그 글에서 읽어 초안 JSON 으로 만든다.

출처 블로그는 글마다 공식 상품 페이지 주소(1kuji.com/products/<slug>)를 적어 두므로,
그 slug 로 우리 쿠지와 정확히 연결한다. 결과는 사람이 훑어본 뒤 저장소에 넣는다.
"""
import html
import json
import re
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"}
BLOG = "https://meli-melo.blog.jp"
OUT = sys.argv[1]


def get(url, **kw):
    time.sleep(1.0)
    return requests.get(url, headers=UA, timeout=30, **kw)


# 공개 사이트의 데이터에서 쿠지 목록을 얻는다
page = get("https://circlebig.github.io/kuji-price/").text
data = json.loads(re.search(r"const D = (\{.*?\});\n", page, re.S).group(1).replace("<\\/", "</"))
today = data["generated"][:10]
kujis = {}
for p in data["prizes"]:
    slug = p["sid"].rsplit("-", 1)[0]
    if p["date"] and p["date"] <= today:
        kujis.setdefault(slug, dict(slug=slug, name=p["kuji_ja"], work=p["work"], date=p["date"]))
print("발매된 쿠지", len(kujis))


def flat(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return " / ".join(x.strip() for x in t.split("\n") if x.strip())


result, miss = {}, []
for k in kujis.values():
    q = k["name"].replace("一番くじ", "").strip()
    r = get(BLOG + "/search", params={"q": "一番くじ " + q})
    cands = [u for u, t in re.findall(r'<a href="(' + re.escape(BLOG) + r'/archives/\d+\.html)"[^>]*>([^<]+)</a>', r.text)
             if "一番くじ" in t]
    found = None
    for u in list(dict.fromkeys(cands))[:3]:
        h = get(u).text
        slugs = set(re.findall(r"1kuji\.com/products/([A-Za-z0-9_\-]+)", h))
        if not any(s == k["slug"] or s.startswith(k["slug"] + "-") or k["slug"].startswith(s + "-") for s in slugs):
            continue
        t = flat(h)
        counts = {}
        for m in re.finditer(r"([A-Z])賞 / ([^/]{1,60}?) / (\d{1,3}) / ", t):
            counts.setdefault(m.group(1), int(m.group(3)))
        letters = sorted(set(re.findall(r"([A-Z])賞 / ", t)))
        found = dict(url=u, counts=counts, letters=letters, last="ラストワン賞" in t)
        break
    if found and found["counts"]:
        total = sum(found["counts"].values())
        complete = set(found["counts"]) == set(found["letters"])
        result[k["slug"]] = dict(name=k["name"], work=k["work"], total=total, counts=found["counts"],
                                 complete=complete, source=found["url"])
        print(f"OK  {k['slug']:16} 총 {total:3} {'완전' if complete else '일부 누락 ' + str(sorted(set(found['letters']) - set(found['counts'])))} {found['counts']}")
    else:
        miss.append(k["slug"])
        print(f"--  {k['slug']:16} {'글은 찾았으나 개수 없음' if found else '글 못 찾음'}  {k['name'][:40]}")

json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n개수 확보 {len(result)} / {len(kujis)}  | 못 찾음 {len(miss)}")
