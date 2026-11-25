"""규칙으로 분류하지 못한 매물 제목을 LLM(OpenAI 호환 API)으로 분류.

.env 에 KUJI_LLM_BASE_URL / KUJI_LLM_MODEL / KUJI_LLM_API_KEY 가 있어야 동작한다.
결과는 llm_match 테이블에 저장해 같은 제목을 다시 묻지 않는다.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from .common import WORKS, db

BATCH = 15       # 한 번에 묻는 제목 수
WORKERS = 4      # 동시 요청 수
# LLM에게 다시 물어볼 가치가 있는 규칙 탈락 사유.
# 묶음·비피규어, 다른 쿠지는 규칙 판단을 믿고, 상 표기가 없거나 여러 개인 제목은 어차피 인정하지 않는다.
RETRY_REASONS = {"쿠지 특정 불가", "캐릭터 불일치"}

SYSTEM = """You classify second-hand marketplace listing titles (Japanese or Korean) for Ichiban Kuji (一番くじ / 제일복권 / 이치방쿠지) prize figures.

You get a CATALOG of figure prizes for one anime, then numbered listing TITLES with their dates.
For each title decide which ONE catalog prize is being sold. Answer "none" unless you are confident.

Answer "none" when:
- the listing sells several items (lot, set, bundle, multiple prize letters, 일괄, 세트, まとめ, 全種), or is a wanted-to-buy post (삽니다, 구해요)
- the item is not that figure: acrylic stand, towel, rubber charm, sticker, poster, plush, keychain, mini figures (ちょこのっこ / 초코노코), empty box, parts
- the title names a kuji subtitle/series that is NOT in the catalog (an older or different kuji). Do not map it to a similar catalog prize.
- the prize letter in the title (A賞, B상, ラストワン/라스트원/라원 = LAST) does not match the catalog prize, or the character does not match
- the listing date is earlier than the kuji release date
- several catalog prizes fit equally well and the title does not say which kuji it is

"named" is the exact words copied from the title that name the kuji subtitle/series (Japanese, a Korean translation, or an obvious abbreviation such as 5주년, 1224, 2탄). Use "" when the title has only the anime name, prize letter and character names. A character name is never a subtitle.

Reply with ONLY a JSON array, one object per title, in order:
[{"i": 1, "id": "<catalog id or none>", "named": "<words from the title or empty>"}, ...]"""


def config():
    base, model = os.environ.get("KUJI_LLM_BASE_URL"), os.environ.get("KUJI_LLM_MODEL")
    if not base or not model:
        return None
    return dict(base=base.rstrip("/"), model=model, key=os.environ.get("KUJI_LLM_API_KEY", ""))


def catalog_text(work, prizes):
    lines = [f"Anime: {WORKS[work]['ja']} / {WORKS[work]['ko']}",
             "CATALOG (id | kuji name | Korean kuji name | release date | prize):"]
    for p in prizes:
        if p["work"] == work:
            lines.append(f"{p['id']} | {p['kuji_ja']} | {p['kuji_ko']} | {p['release_date'] or '?'} | "
                         f"{p['name']} ({p['label_ko']})")
    return "\n".join(lines)


def ask(cfg, catalog, items, think=False):
    """items: [(title, date)] -> [(prize_id 또는 None, named)] (실패 시 None)"""
    titles = "\n".join(f"{i + 1}. [{d or '?'}] {t}" for i, (t, d) in enumerate(items))
    body = {"model": cfg["model"], "temperature": 0, "max_tokens": 6000 if think else 1200,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"{catalog}\n\nTITLES:\n{titles}"}]}
    if not think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    err = None
    for _ in range(2):
        try:
            r = requests.post(cfg["base"] + "/chat/completions", json=body, timeout=180,
                              headers={"Authorization": "Bearer " + cfg["key"]})
            text = r.json()["choices"][0]["message"]["content"] or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
            arr = json.loads(text[text.index("["): text.rindex("]") + 1])
            out = [(None, "")] * len(items)
            for o in arr:
                i = int(o.get("i", 0)) - 1
                if 0 <= i < len(items):
                    pid = o.get("id")
                    named = o.get("named")
                    out[i] = (pid if pid and pid != "none" else None,
                              named if isinstance(named, str) else "")
            return out
        except Exception as e:  # 형식이 깨졌거나 서버 오류: 한 번 더 시도
            err = e
    print("  ! LLM 응답 실패:", str(err)[:120])
    return None


def classify(cfg, prizes, todo, think=False, progress=True):
    """todo: [(work, key, title, date)] -> {key: (prize_id|None, named)}"""
    valid = {p["id"] for p in prizes}
    jobs = []
    for work in WORKS:
        rows = [t for t in todo if t[0] == work]
        cat = catalog_text(work, prizes)
        for i in range(0, len(rows), BATCH):
            jobs.append((cat, rows[i:i + BATCH]))
    result, done = {}, 0

    def run_job(job):
        cat, rows = job
        return rows, ask(cfg, cat, [(r[2], r[3]) for r in rows], think)

    with ThreadPoolExecutor(WORKERS) as ex:
        for rows, ans in ex.map(run_job, jobs):
            done += 1
            if ans:
                for r, (pid, named) in zip(rows, ans):
                    result[r[1]] = (pid if pid in valid else None, named)
            if progress and done % 20 == 0:
                print(f"  LLM 분류 {done}/{len(jobs)} 묶음")
    return result


def translate_new(cfg, con, prizes):
    """사전에 없는 쿠지 이름과 캐릭터를 모르는 상 이름을 한국어로 옮겨 auto_name 에 저장."""
    have = {r["ja"] for r in con.execute("SELECT ja FROM auto_name")}
    need = []
    for p in prizes:
        if p["auto"] and p["kuji_ja"] not in have and p["kuji_ja"] not in need:
            need.append(p["kuji_ja"])
        if not p["chars"] and p["name"] not in have and p["name"] not in need:
            need.append(p["name"])
    if not need:
        return 0
    prompt = ("Translate these Ichiban Kuji (一番くじ) product names into natural Korean as used by Korean "
              "anime fans (official Korean character names, e.g. 五条悟 -> 고죠 사토루). For kuji names give only "
              "the subtitle (drop 一番くじ and the anime title). For prize names drop the prize letter (A賞 etc.) "
              "and keep it short.\nReply with ONLY a JSON array of strings, same order.\n\n"
              + "\n".join(f"{i + 1}. {n}" for i, n in enumerate(need)))
    body = {"model": cfg["model"], "temperature": 0, "max_tokens": 2000,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt}]}
    try:
        r = requests.post(cfg["base"] + "/chat/completions", json=body, timeout=180,
                          headers={"Authorization": "Bearer " + cfg["key"]})
        text = r.json()["choices"][0]["message"]["content"]
        arr = json.loads(text[text.index("["): text.rindex("]") + 1])
    except Exception as e:
        print("  ! 이름 번역 실패:", str(e)[:120])
        return 0
    if len(arr) != len(need):
        print("  ! 이름 번역 개수가 맞지 않아 건너뜀")
        return 0
    for ja, ko in zip(need, arr):
        if isinstance(ko, str) and ko.strip():
            con.execute("INSERT OR REPLACE INTO auto_name VALUES(?,?)", (ja, ko.strip()))
    con.commit()
    print(f"  새 쿠지·상 이름 {len(need)}건 한국어 번역")
    return len(need)


def run():
    """규칙 탈락 매물 중 아직 묻지 않은 것(또는 제목이 바뀐 것)을 분류해 저장."""
    from .build import load_matcher
    cfg = config()
    if not cfg:
        print("  LLM 설정이 없어 건너뜀 (.env.example 참고)")
        return
    con = db()
    prizes, matcher = load_matcher(con)
    if translate_new(cfg, con, prizes):
        prizes, matcher = load_matcher(con)  # 번역된 이름을 카탈로그 설명에 반영
    cached = {(r["source"], r["item_id"]): r["title"] for r in con.execute("SELECT * FROM llm_match")}
    todo = []
    for row in con.execute("SELECT * FROM listing"):
        lang = "ja" if row["source"] == "yahoo" else "ko"
        prize, why = matcher.match(row["work"], row["title"], lang, row["dated"])
        key = (row["source"], row["item_id"])
        if not prize and why in RETRY_REASONS and cached.get(key) != row["title"]:
            todo.append((row["work"], key, row["title"], (row["dated"] or "")[:10]))
    print(f"  LLM으로 분류할 제목 {len(todo)}건")
    if not todo:
        con.close()
        return
    titles = {t[1]: t[2] for t in todo}
    for key, (pid, named) in classify(cfg, prizes, todo).items():
        con.execute("INSERT OR REPLACE INTO llm_match VALUES(?,?,?,?,?,?)",
                    (key[0], key[1], titles[key], pid, named, cfg["model"]))
    con.commit()
    n = con.execute("SELECT count(*), count(prize_id) FROM llm_match").fetchone()
    print(f"  LLM 분류 누적 {n[0]}건 중 상품으로 인정 {n[1]}건")
    con.close()
