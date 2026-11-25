"""매칭·집계 후 site/index.html 생성."""
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median

import requests

from .common import DATA, ROOT, WORKS, db
from .llm import RETRY_REASONS
from .match import Matcher, is_opened, is_target, kuji_entry, letters_in, norm
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


def trend(rows, country):
    """주(월요일 시작)별 중간값(원화). 미개봉 기준, 동떨어진 값 제외.
    반환: ([[주 시작일, 중간값, 건수], ...], 최근 4주가 그 전 4주보다 몇 % 변했는지 또는 None)"""
    pts = [r for r in rows if r["c"] == country and r["d"] and not r["o"]]
    if not pts:
        return [], None
    med = median(r["krw"] for r in pts)
    pts = [r for r in pts if med * 0.4 <= r["krw"] <= med * 2.5]
    weeks = {}
    for r in pts:
        d = date.fromisoformat(r["d"])
        weeks.setdefault((d - timedelta(days=d.weekday())).isoformat(), []).append(r["krw"])
    series = [[w, round(median(v)), len(v)] for w, v in sorted(weeks.items())]
    today = date.today()
    last = [r["krw"] for r in pts if (today - date.fromisoformat(r["d"])).days < 28]
    prev = [r["krw"] for r in pts if 28 <= (today - date.fromisoformat(r["d"])).days < 56]
    change = None
    if len(last) >= 3 and len(prev) >= 3:
        change = round((median(last) - median(prev)) / median(prev) * 100)
    return series, change


def work_names(work):
    w = WORKS[work]
    return [w["ja"], *w.get("ja_keys", [])]


def ko_label(prize, auto=None):
    letter = "라스트원상" if prize["letter"] == "LAST" else f"{prize['letter']}상"
    if not prize["chars"]:  # 캐릭터 사전에 없는 상: LLM 번역이 있으면 쓰고, 없으면 일본어 이름 그대로
        name = (auto or {}).get(prize["name"]) or re.sub(r"^\s*(ラストワン|[A-Z])賞\s*", "", prize["name"])
        return f"{letter} {name}".strip()
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
    auto = {r["ja"]: r["ko"] for r in con.execute("SELECT * FROM auto_name")}
    for k in kujis.values():
        group, cfg = kuji_entry(k["name"], work_names(k["work"]))
        if group is not None and (group, cfg) not in all_cfgs.setdefault(k["work"], []):
            all_cfgs[k["work"]].append((group, cfg))
    for r in con.execute("SELECT * FROM prize"):
        p = dict(r)
        if not is_target(p):
            continue
        k = kujis[p["slug"]]
        if k["release_date"] and k["release_date"] < cutoff:
            continue
        group, cfg = kuji_entry(k["name"], work_names(k["work"]))
        if group is None:
            print("  ! 부제를 알 수 없는 쿠지(건너뜀):", k["name"])
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
                           kuji_ja=k["name"], kuji_ko=cfg.get("ko_name") or auto.get(k["name"]) or k["name"],
                           auto=bool(cfg.get("auto")),
                           release_date=date)
    return list(groups.values()), all_cfgs


def load_matcher(con):
    prizes, all_cfgs = load_prizes(con)
    matcher = Matcher(prizes, all_cfgs)   # 각 상에 chars 를 채운다
    auto = {r["ja"]: r["ko"] for r in con.execute("SELECT * FROM auto_name")}
    for p in prizes:
        p["label_ko"] = ko_label(p, auto)
    return prizes, matcher


def named_ok(text, t, work):
    """LLM이 부제라고 짚은 문구가 실제로 제목에 있고, 캐릭터·작품 이름이 아닌지 확인."""
    n = norm(text if isinstance(text, str) else "")
    if len(n) < 2 or n not in t:
        return False
    w = WORKS[work]
    generic = [w["ja"], w["ko"], *w.get("ja_keys", []), *w.get("ko_keys", []),
               "一番くじ", "제일복권", "이치방쿠지", "쿠지", "피규어", "フィギュア", "미개봉", "未開封"]
    names = [a for ja, ko in CHARS.values() for a in ja + ko] + generic
    return not any(n in norm(a) or norm(a) == n for a in names)


def llm_pick(row, why, llm, by_id, matcher):
    """규칙이 놓친 매물에 대해 저장된 LLM 분류를 적용. 날짜 조건은 규칙과 똑같이 검사한다."""
    hit = llm.get((row["source"], row["item_id"]))
    if not hit or why not in RETRY_REASONS or hit["title"] != row["title"] or not hit["prize_id"]:
        return None
    p = by_id.get(hit["prize_id"])
    day = (row["dated"] or "")[:10]
    if not p:
        return None
    # LLM 답을 그대로 믿지 않고 검산한다: 상 표기가 일치하고, 캐릭터가 제목에 있거나 부제가 적혀 있어야 함
    lang = "ja" if row["source"] == "yahoo" else "ko"
    t = norm(row["title"])
    has_char = any(norm(a) in t for c in p["chars"] for a in CHARS[c][0 if lang == "ja" else 1])
    named = named_ok(hit["named"], t, row["work"])
    keyed = {g for g, cfg in matcher.all_cfgs.get(row["work"], []) if matcher._keyed(cfg, lang, t)}
    if len(keyed) > 1:
        return None
    if letters_in(row["title"], lang) != {p["letter"]} or not (has_char or named):
        return None
    if not p or not p["release_date"] or not day or day < p["release_date"]:
        return None
    # 부제가 없는 제목은 최근 발매 쿠지로만 인정 (옛 쿠지와 섞이는 것 방지)
    age = (date.fromisoformat(day) - date.fromisoformat(p["release_date"])).days
    if not named and age > Matcher.FRESH_DAYS:
        return None
    return p


def run():
    con = db()
    prizes, matcher = load_matcher(con)
    by_id = {p["id"]: p for p in prizes}
    llm = {(r["source"], r["item_id"]): r for r in con.execute("SELECT * FROM llm_match")}
    rate, rate_time = fx_rate()
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()

    matched, reasons = {}, Counter()
    for row in con.execute("SELECT * FROM listing"):
        lang = "ja" if row["source"] == "yahoo" else "ko"
        prize, why = matcher.match(row["work"], row["title"], lang, row["dated"])
        by = "rule"
        if not prize:
            prize = llm_pick(row, why, llm, by_id, matcher)
            if prize:
                why, by = "ok (LLM)", "llm"
        reasons[(row["source"], why)] += 1
        if prize:
            matched.setdefault(prize["id"], []).append((row, lang, by))
    for (src, why), n in sorted(reasons.items()):
        print(f"  {src:8} {why}: {n}")

    out = []
    for p in prizes:
        rows = []
        for row, lang, by in matched.get(p["id"], []):
            krw = round(row["price"] * rate) if row["currency"] == "JPY" else row["price"]
            rows.append(dict(c="jp" if lang == "ja" else "kr", t=row["title"], p=row["price"],
                             krw=krw, s=row["status"], d=(row["dated"] or "")[:10],
                             u=row["url"], o=is_opened(row["title"], lang), m=by))
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
        jp_trend, jp_chg = trend(rows, "jp")
        kr_trend, kr_chg = trend(rows, "kr")
        con.execute("INSERT OR REPLACE INTO snapshot VALUES(?,?,?,?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d"), p["id"],
                     jp and jp["median"], jp and jp["n"], kr and kr["median"], kr and kr["n"], rate))
        out.append(dict(id=p["id"], work=p["work"], kuji=p["kuji_ko"], kuji_ja=p["kuji_ja"],
                        date=p["release_date"], label=p["label_ko"], name_ja=p["name"],
                        image=p["image"], size=p["size"], jp=jp, kr=kr, gap=gap,
                        trend=dict(jp=jp_trend, kr=kr_trend), chg=dict(jp=jp_chg, kr=kr_chg),
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
