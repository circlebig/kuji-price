# kuji-price

이치방쿠지(一番くじ) 피규어의 한국·일본 중고 시세를 비교하는 프로토타입입니다.

- **일본**: 야후옥션 낙찰 내역 (실제 낙찰가)
- **한국**: 번개장터 (판매완료가, 부족하면 판매중 포함 등록가)
- **카탈로그**: 이치방쿠지 공식 사이트의 쿠지·상 목록
- **대상 작품**: 진격의 거인, 나의 히어로 아카데미아, 주술회전, 체인소 맨

## 실행

Python 3.11 이상이 필요합니다.

```
pip install -r requirements.txt
python run.py all          # 카탈로그 + 전체 수집 + 집계 (약 20분)
python -m http.server 8765 --directory site
```

브라우저에서 http://localhost:8765 를 엽니다.

| 명령 | 하는 일 |
|---|---|
| `python run.py catalog` | 공식 사이트에서 쿠지·상 목록 수집 |
| `python run.py collect` | 야후옥션·번개장터 매물 전체 수집 |
| `python run.py build` | 매칭·집계 후 `site/index.html` 생성 |
| `python run.py quick` | 새 매물만 빠르게 수집하고 집계 |
| `python run.py watch [분]` | 자동 갱신. 기본 60분마다 quick, 하루 한 번 전체 수집 |

## 구조

| 파일 | 역할 |
|---|---|
| `kujiprice/common.py` | 대상 작품 설정, HTTP 요청(1초 간격), SQLite 스키마 |
| `kujiprice/catalog.py` | 공식 사이트 카탈로그 수집 |
| `kujiprice/sources.py` | 야후옥션·번개장터 매물 수집 |
| `kujiprice/names.py` | 쿠지 부제·캐릭터의 일본어/한국어 키워드 사전 (수작업) |
| `kujiprice/match.py` | 매물 제목 → (쿠지, 상) 매칭, 묶음·비피규어 제외 |
| `kujiprice/build.py` | 최근 120일 중간값 집계, 환율 적용, 페이지 생성 |
| `kujiprice/template.html` | 시세 비교 페이지 템플릿 |

수집 데이터(`data/`)와 생성된 페이지(`site/`)는 저장소에 포함하지 않습니다.

## 알려진 한계

- 제목만으로 자동 분류하므로 잘못 분류된 매물이 섞일 수 있습니다. 특히 같은 부제의 옛 시리즈가 있는 오래된 쿠지는 부정확합니다.
- 공식 사이트 검색이 작품당 최신 10종만 보여 줘서 그보다 오래된 쿠지는 다루지 않습니다.
- 새 쿠지가 나오면 `names.py`에 한국어 부제와 캐릭터를 추가해야 시세가 잡힙니다.
- 야후옥션과 번개장터는 공식 API가 아니라 웹 페이지가 쓰는 데이터를 읽습니다. 사이트 구조가 바뀌면 수집이 깨질 수 있고, 공개 서비스로 운영하려면 각 사이트 약관을 확인해야 합니다.
