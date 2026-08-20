# gyeol-concepts

iOS 앱 **결(Gyeol)** 이 읽어가는 컨셉별 유튜브 롱폼 목록을 만들어 GitHub Pages 로 서빙한다.

```
GitHub Actions (하루 2회) → search.list → videos.list 검증 → docs/concepts.json → Pages
                                                                                    ↓
                                                                         앱이 받아서 표시
```

## 왜 이렇게 하는가

YouTube Data API 의 `search.list` 는 **1회당 100 유닛**이고 하루 한도가 10,000 유닛이라
**하루 100회**뿐이다. 앱에서 사용자마다 검색을 돌리면 하루 100명에서 앱이 죽는다.

검색을 여기서만 하고 앱은 결과 JSON 만 받아 가면,

- **API 키가 앱에 들어가지 않는다** (Actions Secret 에만 있다)
- 사용자가 10만 명이어도 유튜브 호출은 하루 33회로 고정된다
- 선곡 품질을 사람이 통제할 수 있다
- 앱 업데이트 없이 목록이 갱신된다

## 구성

| 파일 | 역할 |
|---|---|
| `concepts_config.json` | 컨셉과 검색어 정의. **여기만 고치면 선곡이 바뀐다** |
| `scripts/fetch_concepts.py` | 검색 → 검증 → JSON 생성 (표준 라이브러리만) |
| `.github/workflows/refresh.yml` | 하루 2회(KST 04:00·16:00) + 수동 실행 |
| `docs/concepts.json` | 결과물. Pages 가 서빙한다 |

## ⚠️ 라이브 스트림은 반드시 걸러야 한다

앱 쪽 M1 검증에서 확인한 사실이다. 라이브 스트림과 그 **녹화본은 임베드가 막혀 있어**
IFrame 플레이어에서 `error 150` 과 함께 "실시간 스트림 녹화를 볼 수 없습니다" 가 뜬다.
작업용 음악 검색 결과에는 이런 게 대량으로 섞여 나온다.

`search.list` 의 `videoEmbeddable=true` 로는 **걸러지지 않는다.** 그래서 두 단계로 거른다.

1. `search.list` 결과에서 `snippet.liveBroadcastContent != "none"` 제거
2. `videos.list` 로 재검증 — `liveStreamingDetails` 필드가 있으면 과거 라이브라는 뜻이니 제거.
   함께 `status.embeddable`, `privacyStatus`, 실제 길이, `regionRestriction`(KR) 도 본다.

`videos.list` 는 **1회당 1 유닛**이라 아끼지 않고 후보를 전부 검증한다.

## 할당량

| 호출 | 사용 | 한도 |
|---|---|---|
| `search.list` | 33회/회차 × 2회차 = 66 | 100/일 |
| `videos.list` | 약 22회 | 10,000/일 |

## 안전장치

- 어떤 컨셉의 결과가 8개 미만이면 **이전 목록을 유지**한다.
  유튜브가 이상한 결과를 주는 날 좋은 목록을 덮어쓰지 않기 위해서다.
- `quotaExceeded` 는 재시도하지 않고 즉시 중단한다 (남은 호출을 태우지 않으려고).
- 내용이 같으면 `generatedAt` 만 바뀐 커밋을 남기지 않는다.

## 로컬 실행

```bash
YOUTUBE_API_KEY=... python3 scripts/fetch_concepts.py
```

⚠️ 로컬 실행도 실제 할당량을 쓴다. 하루 100회뿐이니 함부로 반복하지 말 것.
