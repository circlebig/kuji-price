"""공통 설정, HTTP, DB."""
import json
import sqlite3
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DB_PATH = DATA / "kuji.sqlite"

# 수집 대상 작품. kuji_id 는 이치방쿠지 공식 사이트(1kuji.com)의 캐릭터 검색 번호.
WORKS = {
    "aot": {"ko": "진격의 거인", "ja": "進撃の巨人", "kuji_id": 174,
            "ko_keys": ["진격의 거인", "진격의거인", "진격거", "진격"]},
    "mha": {"ko": "나의 히어로 아카데미아", "ja": "僕のヒーローアカデミア", "kuji_id": 359,
            "ja_keys": ["僕のヒーローアカデミア", "ヒロアカ"],
            "ko_keys": ["나의 히어로 아카데미아", "나의히어로아카데미아", "나히아", "히로아카"]},
    "jjk": {"ko": "주술회전", "ja": "呪術廻戦", "kuji_id": 646,
            "ko_keys": ["주술회전", "주술"]},
    "csm": {"ko": "체인소 맨", "ja": "チェンソーマン", "kuji_id": 1070,
            "ko_keys": ["체인소맨", "체인소 맨", "체인쏘맨"]},
    "op": {"ko": "원피스", "ja": "ワンピース", "kuji_id": 54,
           "ko_keys": ["원피스"]},
    "db": {"ko": "드래곤볼", "ja": "ドラゴンボール", "kuji_id": 35,
           "ko_keys": ["드래곤볼"]},
    "kny": {"ko": "귀멸의 칼날", "ja": "鬼滅の刃", "kuji_id": 584,
            "ko_keys": ["귀멸의 칼날", "귀멸의칼날", "귀멸", "귀칼"]},
    "nrt": {"ko": "나루토", "ja": "NARUTO", "kuji_id": 6, "ja_keys": ["NARUTO", "ナルト"],
            "ko_keys": ["나루토"]},
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
DELAY = 1.0  # 요청 간격(초). 상대 서버에 부담을 주지 않도록 줄이지 말 것.

_session = requests.Session()
_session.headers["User-Agent"] = UA
_last = 0.0


def get(url, **kw):
    """간격을 두고 GET. 실패 시 2회 재시도."""
    global _last
    for attempt in range(3):
        wait = DELAY - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            r = _session.get(url, timeout=30, **kw)
            if r.status_code == 200:
                return r
            print(f"  ! {r.status_code} {url[:100]}")
        except requests.RequestException as e:
            print(f"  ! {e} {url[:100]}")
        time.sleep(3 * (attempt + 1))
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS kuji(
  slug TEXT PRIMARY KEY, work TEXT, name TEXT, image TEXT,
  release_date TEXT, price_per_draw INTEGER);
CREATE TABLE IF NOT EXISTS prize(
  id TEXT PRIMARY KEY,            -- slug:letter
  slug TEXT, letter TEXT,         -- letter: A,B,... 또는 LAST
  name TEXT, image TEXT, size TEXT, is_figure INTEGER);
CREATE TABLE IF NOT EXISTS listing(
  source TEXT, item_id TEXT, work TEXT, title TEXT,
  price INTEGER, currency TEXT,
  status TEXT,                    -- sold / selling
  dated TEXT,                     -- 낙찰일 또는 최종 수정일 (ISO)
  url TEXT, image TEXT,
  first_seen TEXT, last_seen TEXT,
  PRIMARY KEY(source, item_id));
CREATE TABLE IF NOT EXISTS snapshot(   -- 하루 한 줄씩 쌓이는 시세 기록
  day TEXT, prize_id TEXT, jp_median INTEGER, jp_n INTEGER,
  kr_median INTEGER, kr_n INTEGER, fx REAL,
  PRIMARY KEY(day, prize_id));
"""


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_json(path, default=None):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default
