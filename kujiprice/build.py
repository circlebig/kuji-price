"""매칭·집계 후 site/index.html 생성."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

import requests

from .common import DATA, ROOT, WORKS, db
from .match import Matcher, is_opened, is_target, kuji_entry
from .names import CHARS

WINDOW_DAYS = 120     # 시세 계산에 쓰는 기간
MAX_AGE_YEARS = 3     # 이보다 오래된 쿠지는 추적하지 않음(옛 상품과 헷갈리는 것 방지)
FX_FALLBACK = 9.0     # 환율 조회 실패 시 1엔당 원화


def fx_rate():
    try:
        r = requests.get("https://open.er-api.com/v6/latest/JPY", timeout=20).json()
        return float(r["rates"]["KRW"]), r.get("time_last_update_utc", "")
    except Exception as e:  # 환율 API 장애 시에도 페이지는 만들어지게
        print("  ! 환율 조회 실패, 임시값 사용:", e)
        return FX_FALLBACK, "조회 실패(임시값)"


def stats(prices):
    """중간값 기준으로 동떨어진 값(0.4배 미만, 2.5배 초과)을 빼고 요약."""
    if not prices:
        return None
    med = median(prices)
    kept = [p for p in prices if med * 0.4 <= p <= med * 2.5]
    return {"n": len(kept), "median": round(median(kept)), "min": min(kept), "max": max(kept)}


def ko_label(prize):
    letter = "라스트원상" if prize["letter"] == "LAST" else f"{prize['letter']}상"
    names = []
    for c in prize["chars"]:
        ko = CHARS[c][1][0]
        if ko not in names:
            names.append(ko)
    return f"{letter} {' & '.join(names)}".strip()


def load_prizes(con):
    kujis = {r["slug"]: dict(r) for r in con.execute("SELECT * FROM kuji")}
    groups, all_cfgs = {}, {}
    cutoff = (datetime.now() - timedelta(days=365 * MAX_AGE_YEARS)).strftime("%Y-%m-%d")
    for k in kujis.values():
        group, cfg = kuji_entry(k["name"])
        if group is not None and (group, cfg) not in all_cfgs.setdefault(k["work"], []):
            all_cfgs[k["work"]].append((group, cfg))
    for r in con.execute("SELECT * FROM prize"):
        p = dict(r)
        if not is_target(p):
            continue
        k = kujis[p["slug"]]
        if k["release_date"] and k["release_date"] < cutoff:
            continue
        group, cfg = kuji_entry(k["name"])
        if group is None:
            print("  ! 사전에 없는 쿠지(건너뜀):", k["name"])
            continue
        key = (group, p["letter"])
        date = k["release_date"]
        if key in groups:  # 재판매분: 가장 이른 발매일을 사용
            g = groups[key]
            if date and (not g["release_date"] or date < g["release_date"]):
                g["release_date"] = date
            continue
        groups[key] = dict(id=f"{group}-{p['letter']}", work=k["work"], group=group, cfg=cfg,
                           letter=p["letter"], name=p["name"], image=p["image"], size=p["size"],
                           kuji_ja=k["name"], kuji_ko=cfg.get("ko_name", k["name"]),
                           release_date=date)
    return list(groups.values()), all_cfgs


def run():
    con = db()
    prizes, all_cfgs = load_prizes(con)
    matcher = Matcher(prizes, all_cfgs)
    rate, rate_time = fx_rate()
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()

    matched, reasons = {}, Counter()
    for row in con.execute("SELECT * FROM listing"):
        lang = "ja" if row["source"] == "yahoo" else "ko"
        prize, why = matcher.match(row["work"], row["title"], lang, row["dated"])
        reasons[(row["source"], why)] += 1
        if prize:
            matched.setdefault(prize["id"], []).append((row, lang))
    for (src, why), n in sorted(reasons.items()):
        print(f"  {src:8} {why}: {n}")

    out = []
    for p in prizes:
        rows = []
        for row, lang in matched.get(p["id"], []):
            krw = round(row["price"] * rate) if row["currency"] == "JPY" else row["price"]
            rows.append(dict(c="jp" if lang == "ja" else "kr", t=row["title"], p=row["price"],
                             krw=krw, s=row["status"], d=(row["dated"] or "")[:10],
                             u=row["url"], o=is_opened(row["title"], lang)))
        recent = [r for r in rows if r["d"] >= since[:10] and not r["o"]]
        jp = stats([r["p"] for r in recent if r["c"] == "jp"])
        kr_sold = [r["p"] for r in recent if r["c"] == "kr" and r["s"] == "sold"]
        kr_all = [r["p"] for r in recent if r["c"] == "kr"]
        kr = stats(kr_sold if len(kr_sold) >= 3 else kr_all)
        if kr:
            kr["basis"] = "판매완료가" if len(kr_sold) >= 3 else "등록가(판매중 포함)"
        gap = None
        if jp and kr:
            jp_krw = jp["median"] * rate
            gap = round((kr["median"] - jp_krw) / jp_krw * 100)
        rows.sort(key=lambda r: r["d"], reverse=True)
        con.execute("INSERT OR REPLACE INTO snapshot VALUES(?,?,?,?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d"), p["id"],
                     jp and jp["median"], jp and jp["n"], kr and kr["median"], kr and kr["n"], rate))
        out.append(dict(id=p["id"], work=p["work"], kuji=p["kuji_ko"], kuji_ja=p["kuji_ja"],
                        date=p["release_date"], label=ko_label(p), name_ja=p["name"],
                        image=p["image"], size=p["size"], jp=jp, kr=kr, gap=gap,
                        rows=[r for c in ("jp", "kr")
                              for r in [x for x in rows if x["c"] == c][:60]]))
    out.sort(key=lambda x: (x["date"] or "", x["id"]), reverse=True)

    data = dict(generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fx=round(rate, 3),
                fx_time=rate_time, window=WINDOW_DAYS,
                works={k: v["ko"] for k, v in WORKS.items()}, prizes=out)
    (DATA / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tpl = (ROOT / "kujiprice" / "template.html").read_text(encoding="utf-8")
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (site / "index.html").write_text(tpl.replace("/*DATA*/null", payload), encoding="utf-8")
    (site / "version.txt").write_text(data["generated"], encoding="utf-8")  # 페이지 자동 새로고침용
    con.commit()
    both = sum(1 for x in out if x["jp"] and x["kr"])
    print(f"  피규어 상 {len(out)}종 중 한일 시세가 모두 잡힌 것 {both}종 -> site/index.html")
    con.close()
