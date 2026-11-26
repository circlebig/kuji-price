"""매물 제목 -> (쿠지, 상) 매칭과 걸러내기."""
import re
import unicodedata

from .names import CHARS, KUJI, VARIANTS

_STRIP = re.compile(r"[\s~〜・･『』「」【】()\[\]!\-‐—–_/:,、。.★☆◆■◎●♪*#|]+")


def norm(s):
    return _STRIP.sub("", unicodedata.normalize("NFKC", s or "").lower())


# 묶음 판매, 구매글, 피규어가 아닌 상품을 뜻하는 단어
EXCLUDE = [norm(x) for x in """
まとめ 一括 全種 コンプ 複数 大量 色々 引換 半券 アクリル タオル ラバー ステッカー クリアファイル
色紙 缶バッジ キーホルダー マスコット バッジ カード マグネット タンブラー クロス ぬいぐるみ ちょこのっこ ポスター 空箱 箱のみ 台座のみ パーツ セット売 点セット 体セット
일괄 세트 전종 풀셋 삽니다 구합니다 구해요 구함 매입 아크릴 타올 타월 러버 스티커 클리어파일
색지 캔뱃지 키링 봉제 북커버 뱃지 카드 고무 마스코트 머그 인형 누이 포스터 박스만 공박스 쵸코놋코 초코놋코 판넬 보드
세가 타이토 후류 반프레스토 중국쿠지 해피쿠지 럭키쿠지 가챠 프라이즈
""".split()]
RE_LOT = re.compile(r"(他|など|等)\s*$|他\s|含む|含\s")
# 개봉·하자품 (시세 계산에서 제외)
RE_OPENED_JA = re.compile(r"(?<!未)開封|箱なし|箱無|ジャンク|難あり|破損|欠品|訳あり")
RE_OPENED_KO = re.compile(r"(?<!미)개봉|박스\s?없|하자|파손|부러|전시")

RE_LETTER_JA = re.compile(r"([a-z])賞")
RE_LAST_JA = re.compile(r"ラストワン|ラスワン|lastone")
RE_LETTER_KO = re.compile(r"(?<![a-z0-9])([a-o])\s?상(?![품태자점세황])")
RE_LAST_KO = re.compile(r"라스트\s?원|라원|라스트상|last\s?one")


def letters_in(title, lang):
    t = unicodedata.normalize("NFKC", title).lower()
    if lang == "ja":
        found = {m.upper() for m in RE_LETTER_JA.findall(t)}
        if RE_LAST_JA.search(t):
            found.add("LAST")
    else:
        found = {m.upper() for m in RE_LETTER_KO.findall(t)}
        if RE_LAST_KO.search(t):
            found.add("LAST")
    return found


def _has(alias, t):
    """제목(정규화됨)에 표기가 있는지. "나루토×2"처럼 쓰면 두 번 이상 나와야 한다."""
    if alias.endswith("×2"):
        return t.count(norm(alias[:-2])) >= 2
    return norm(alias) in t


def _any_group(groups, text):
    return any(all(norm(tok) in text for tok in g) for g in groups)


def kuji_entry(kuji_name):
    """공식 쿠지명 -> (그룹 번호, 설정). 재판매분은 같은 그룹으로 묶인다."""
    n = norm(kuji_name)
    for i, (needle, cfg) in enumerate(KUJI):
        if norm(needle) in n:
            cfg = dict(cfg)
            cfg.setdefault("ja", [[needle]])
            cfg.setdefault("ko", [])
            return i, cfg
    return None, None


def prize_chars(prize_name):
    keys = [k for k in CHARS if k.strip() and k in prize_name + " "]
    # 긴 이름에 포함된 짧은 키(예: リカ ⊂ 祈本里香 와 별개)는 그대로 둔다
    return keys


def is_target(prize):
    """시세를 추적할 피규어 상인지 (미니 피규어·인형 제외)."""
    n = prize["name"]
    skip = ("ちょこのっこ", "ぬいぐるみ", "マグネット", "ミニフィギュア", "デスクトップ")
    return bool(prize["is_figure"]) and not any(w in n for w in skip)


class Matcher:
    FRESH_DAYS = 150  # 부제가 없는 매물을 "최근 발매 쿠지"로 볼 수 있는 기간

    def __init__(self, prizes, all_cfgs):
        """prizes: 추적 대상 상 목록. all_cfgs: {work: [(group, cfg)]} 추적하지 않는 쿠지 포함."""
        self.by_key = {}
        self.all_cfgs = all_cfgs
        for p in prizes:
            p["chars"] = prize_chars(p["name"])
            self.by_key.setdefault((p["work"], p["letter"]), []).append(p)

    @staticmethod
    def _keyed(cfg, lang, t):
        return (_any_group(cfg[lang], t)
                and not any(norm(x) in t for x in cfg.get("not", [])))

    def match(self, work, title, lang, dated):
        """(상 dict 또는 None, 사유)"""
        t = norm(title)
        if any(x in t for x in EXCLUDE) or RE_LOT.search(title):
            return None, "묶음·비피규어"
        letters = letters_in(title, lang)
        if len(letters) != 1:
            return None, "상 표기 없음/여러 개"
        cands = self.by_key.get((work, next(iter(letters))), [])
        idx = 0 if lang == "ja" else 1
        day = (dated or "")[:10]
        hit = []
        for p in cands:
            if not p["release_date"] or (day and day < p["release_date"]):
                continue  # 발매일 미정이거나 발매 전 날짜의 매물은 다른(옛) 쿠지
            if any(v not in p["chars"] and any(_has(a, t) for a in CHARS[v][idx])
                   for v in VARIANTS):
                continue  # 제목이 이 상에 없는 다른 형태(초사이어인4 등)를 가리킴
            found = sum(any(_has(a, t) for a in CHARS[c][idx]) for c in p["chars"])
            if p["chars"] and found < min(2, len(p["chars"])):
                continue  # 2명 이상인 상은 2명 이상 언급돼야 함
            hit.append(p)
        if not hit:
            return None, "캐릭터 불일치"
        # 제목에 적힌 부제가 가리키는 쿠지들
        named = {g for g, cfg in self.all_cfgs.get(work, []) if self._keyed(cfg, lang, t)}
        if named:
            pool = [p for p in hit if p["group"] in named]
            if not pool:
                return None, "다른 쿠지"
        else:
            # 부제가 없는 매물은 최근 발매된 쿠지로만 인정한다.
            # (목록에 없는 옛 쿠지의 같은 상·같은 캐릭터와 섞이는 것을 막기 위함)
            pool = [p for p in hit if p["chars"] and p["release_date"] and day
                    and (p["cfg"].get("any_age")
                         or _days_between(p["release_date"], day) <= self.FRESH_DAYS)
                    and not any(norm(x) in t for x in p["cfg"].get("not", []))]
            if not pool:
                return None, "쿠지 특정 불가"
        groups = {p["group"] for p in pool}
        if len(groups) > 1:
            num = [p for p in pool
                   if any(norm(x) in t for x in p["cfg"].get("num_" + lang, []))]
            if len({p["group"] for p in num}) == 1:
                pool, groups = num, {num[0]["group"]}
        if len(groups) == 1:
            return pool[0], "ok"
        return None, "쿠지 특정 불가"


def _days_between(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def is_opened(title, lang):
    t = unicodedata.normalize("NFKC", title)
    return bool((RE_OPENED_JA if lang == "ja" else RE_OPENED_KO).search(t))
