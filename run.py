"""이치방쿠지 한일 시세 프로토타입 실행기.

  python run.py catalog   공식 사이트에서 쿠지·상 목록 수집
  python run.py collect   야후옥션(일본)·번개장터(한국) 매물 전체 수집 (약 20분)
  python run.py build     매칭·집계 후 site/index.html 생성
  python run.py all       위 세 단계를 순서대로
  python run.py quick     새 매물만 빠르게 수집하고 집계 (몇 분)
  python run.py watch [분]  계속 켜 두는 자동 갱신. 기본 60분마다 quick,
                            하루에 한 번은 카탈로그와 전체 수집까지 수행. Ctrl+C로 종료.
"""
import sys
import time
import traceback
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def step(cmd):
    if cmd in ("catalog", "all"):
        from kujiprice import catalog
        catalog.run()
    if cmd in ("collect", "all", "quick"):
        from kujiprice import sources
        sources.run(quick=(cmd == "quick"))
    if cmd in ("build", "all", "quick"):
        from kujiprice import build
        build.run()


def watch(minutes):
    last_full = None
    print(f"자동 갱신 시작: {minutes}분 간격 (Ctrl+C로 종료)")
    while True:
        today = datetime.now().strftime("%Y-%m-%d")
        mode = "quick" if last_full == today else "all"
        print(f"\n[{datetime.now():%Y-%m-%d %H:%M}] {'전체' if mode == 'all' else '빠른'} 수집")
        try:
            step(mode)
            if mode == "all":
                last_full = today
        except Exception:  # 한 번 실패해도 다음 주기에 다시 시도
            traceback.print_exc()
        time.sleep(minutes * 60)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "watch":
        watch(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    elif cmd in ("catalog", "collect", "build", "all", "quick"):
        step(cmd)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
