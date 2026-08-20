#!/usr/bin/env python3
"""컨셉별 유튜브 롱폼 목록을 만들어 docs/concepts.json 으로 저장한다.

이 스크립트는 GitHub Actions 안에서만 돈다. 앱은 결과 JSON 만 받아 간다.
그래서 **API 키가 앱에 들어가지 않고**, 사용자가 몇 명이든 유튜브 할당량과 무관하다.

의존성 없음(표준 라이브러리만). pip install 단계가 없어 Actions 가 빠르고 덜 깨진다.
"""

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://www.googleapis.com/youtube/v3"
ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "concepts_config.json"
OUT_PATH = ROOT / "docs" / "concepts.json"

# 컨셉당 최대 보관 수. 넘기기를 한참 눌러도 안 바닥나면서 파일이 비대해지지 않는 선.
MAX_PER_CONCEPT = 60
# 이보다 적게 남으면 이번 회차 결과를 버리고 **이전 목록을 유지**한다.
# 유튜브가 일시적으로 이상한 결과를 주는 날 좋은 목록을 덮어쓰지 않기 위한 안전장치.
MIN_PER_CONCEPT = 8
# 롱폼 기준. search 의 videoDuration=long 은 20분 이상이지만 실제 길이로 한 번 더 본다.
MIN_SECONDS = 20 * 60

_DUR = re.compile(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def die(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def api_get(path: str, params: dict) -> dict:
    """유튜브 API 호출. 일시적 오류만 재시도하고, 할당량 소진은 즉시 중단한다.

    할당량이 떨어진 상태에서 재시도하면 남은 호출까지 태우면서 결국 실패한다.
    그래서 quotaExceeded 는 재시도하지 않고 바로 죽인다.
    """
    q = dict(params)
    q["key"] = os.environ["YOUTUBE_API_KEY"]
    url = f"{API}/{path}?" + urllib.parse.urlencode(q)

    last = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last = f"HTTP {e.code}: {body[:400]}"
            if "quotaExceeded" in body or "dailyLimitExceeded" in body:
                die(f"할당량 소진 — {last}")
            if e.code in (500, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
        except urllib.error.URLError as e:
            last = f"네트워크 오류: {e}"
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
    die(f"{path} 호출 실패 — {last}")
    return {}


def parse_duration(iso: str) -> int:
    """ISO-8601 기간(PT1H2M3S)을 초로. 형식이 낯설면 0 을 돌려 걸러지게 한다."""
    m = _DUR.match(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def search_ids(query: str, opts: dict) -> list:
    """search.list — **1회당 100 유닛.** 이 앱에서 가장 비싼 호출이라 컨셉당 3회로 묶어 쓴다."""
    data = api_get("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoEmbeddable": "true",   # 임베드 가능한 것만
        "videoSyndicated": "true",   # 유튜브 바깥에서 재생 가능한 것만
        "videoDuration": "long",     # 20분 이상
        "order": opts["order"],
        "regionCode": opts["regionCode"],
        "relevanceLanguage": opts["relevanceLanguage"],
        "maxResults": 25,
    })
    ids = []
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        if not vid:
            continue
        # ⚠️ 라이브/예정 방송을 여기서 1차로 쳐낸다. videoEmbeddable=true 는 이걸 못 거른다.
        if item.get("snippet", {}).get("liveBroadcastContent", "none") != "none":
            continue
        ids.append(vid)
    return ids


def fetch_details(ids: list) -> dict:
    """videos.list — **1회당 1 유닛**, 한 번에 50개. search 에 비하면 사실상 공짜라
    후보를 전부 검증한다. M1 에서 라이브 녹화가 임베드 불가(error 150)인 걸 확인했기 때문에
    이 검증이 없으면 사용자가 '넘기기'만 누르는 앱이 된다."""
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = api_get("videos", {
            "part": "snippet,contentDetails,status,liveStreamingDetails",
            "id": ",".join(chunk),
        })
        for v in data.get("items", []):
            out[v["id"]] = v
    return out


# ─────────────────────────────────────────────────────────────────────
# 선곡에서 빼는 것들.
#
# ⚠️ **좁게 유지할 것.** 561개를 직접 훑어 본 결과 진짜로 걸러야 할 건 2개뿐이었고,
# 처음에 짐작으로 만든 규칙은 대부분 오탐이었다. 실제로 겪은 오탐:
#   · "노동요" 가 `동요` 에 걸렸다 — 작업용 음악을 가리키는 말이라 정상이다.
#     그래서 `동요` 앞에 `노` 가 오면 빼는 lookbehind 를 넣었다.
#   · 비 오는 날의 ASMR, 잠들기 전의 명상·수면음악은 **그 컨셉에 맞는 것**이다.
#   · "메이플스토리 작업용 BGM" 도 정상이다. 게임 OST 로 작업하는 건 자리 잡은 장르다.
# 규칙을 늘리고 싶으면 먼저 현재 목록에 돌려 보고 무엇이 빠지는지 눈으로 확인할 것.
#
# 걸러야 하는 건 "취향에 안 맞는 음악" 이 아니라 **작업용 BGM 이 아닌 것** 두 가지다.
BLOCK_RULES = [
    # ① 유아·아동용 — 랜덤에서 튀어나오면 가장 어색하다.
    ("유아·아동", r"뽀로로|핑크퐁|베베핀|아기상어|상어가족|콩순이|티니핑|캐리와|"
                 r"코코멜론|cocomelon|baby shark|super simple songs|little baby bum|"
                 # `아기자기` 는 아기와 무관한 말이라 뺀다(카페·인테리어 계열 제목에 흔하다).
                 r"아기(?!자기)|유아|어린이|키즈|유치원|어린이집|율동|(?<!노)동요|"
                 r"nursery rhyme|kids song"),
    # ② 음악이 아니라 말(음성) — 배경으로 깔면 집중을 깨뜨린다.
    ("말 콘텐츠", r"설교|법문|기도문|오디오북|낭독|노래방|karaoke"),
]


def blocked_by_keyword(v: dict):
    """제목·채널명에 걸리는 금칙어가 있으면 (사유) 를 돌려준다."""
    sn = v.get("snippet", {})
    text = f"{sn.get('title', '')} {sn.get('channelTitle', '')}"
    for label, pattern in BLOCK_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def judge(v: dict):
    """(통과 여부, 탈락 사유). 사유를 남기는 이유는 Actions 로그로 선곡 품질을 볼 수 있어서다."""
    # liveStreamingDetails 가 있으면 과거에 라이브였다는 뜻이다.
    # 이런 '실시간 스트림 녹화' 는 임베드가 막혀 있다 (M1 에서 실측).
    if "liveStreamingDetails" in v:
        return False, "라이브(녹화 포함)"

    st = v.get("status", {})
    if not st.get("embeddable"):
        return False, "임베드 불가"
    if st.get("privacyStatus") != "public":
        return False, "비공개"

    cd = v.get("contentDetails", {})
    secs = parse_duration(cd.get("duration", ""))
    if secs < MIN_SECONDS:
        return False, f"짧음({secs}초)"

    # ⚠️ 금칙어는 **마지막에** 본다. 앞의 검사들이 기술적 재생 가능 여부라
    # 그쪽부터 걸러야 로그의 탈락 사유가 원인을 정확히 가리킨다.
    if (label := blocked_by_keyword(v)):
        return False, label

    rr = cd.get("regionRestriction", {})
    if "KR" in rr.get("blocked", []):
        return False, "KR 차단"
    allowed = rr.get("allowed")
    if allowed is not None and "KR" not in allowed:
        return False, "KR 미허용"

    return True, ""


def load_previous() -> dict:
    if not OUT_PATH.exists():
        return {}
    try:
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return {c["id"]: c for c in prev.get("concepts", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        print("::warning::이전 concepts.json 을 읽지 못했다. 폴백 없이 진행한다.")
        return {}


def main() -> None:
    if not os.environ.get("YOUTUBE_API_KEY"):
        die("YOUTUBE_API_KEY 가 없다. 레포 Settings → Secrets → Actions 에 등록할 것.")

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    previous = load_previous()

    # 검색 옵션. 실험할 때만 환경변수로 덮어쓴다(기본값은 설정 파일에 버전 관리된다).
    opts = {k: v for k, v in config.get("search", {}).items() if not k.startswith("_")}
    opts.setdefault("order", "viewCount")
    opts.setdefault("regionCode", "KR")
    opts.setdefault("relevanceLanguage", "ko")
    if os.environ.get("GYEOL_ORDER"):
        opts["order"] = os.environ["GYEOL_ORDER"]

    # 컨셉 일부만 돌리는 실험 모드. 하루 100회뿐이라 전체(33회)를 매번 태울 수 없다.
    only = {x.strip() for x in os.environ.get("GYEOL_ONLY", "").split(",") if x.strip()}
    # 실험 결과는 로그로만 보고 파일을 건드리지 않는다.
    dry_run = os.environ.get("GYEOL_DRY_RUN", "").lower() in ("1", "true", "yes")

    targets = [c for c in config["concepts"] if not only or c["id"] in only]
    if only:
        missing = only - {c["id"] for c in config["concepts"]}
        if missing:
            die(f"없는 컨셉 id: {', '.join(sorted(missing))}")
    print(f"검색 옵션 {opts} · 대상 컨셉 {len(targets)}개"
          f"{' · DRY RUN(파일 미변경)' if dry_run else ''}\n")

    used = set()          # 컨셉 간 중복 제거용. 앞 컨셉이 가져간 영상은 뒤에서 안 쓴다.
    concepts_out = []
    searches = 0

    for conf in targets:
        cid, name = conf["id"], conf["name"]

        candidates = []
        for q in conf["queries"]:
            candidates.extend(search_ids(q, opts))
            searches += 1

        # 컨셉 안 중복 제거 + 이미 다른 컨셉이 가져간 것 제외 (순서 유지)
        seen, ordered = set(), []
        for vid in candidates:
            if vid in seen or vid in used:
                continue
            seen.add(vid)
            ordered.append(vid)

        details = fetch_details(ordered)

        videos, rejects = [], {}
        for vid in ordered:
            v = details.get(vid)
            if v is None:
                rejects["조회 실패"] = rejects.get("조회 실패", 0) + 1
                continue
            ok, why = judge(v)
            if not ok:
                rejects[why] = rejects.get(why, 0) + 1
                continue
            sn = v["snippet"]
            videos.append({
                "id": vid,
                "title": sn.get("title", ""),
                "channel": sn.get("channelTitle", ""),
                "seconds": parse_duration(v["contentDetails"]["duration"]),
            })
            if len(videos) >= MAX_PER_CONCEPT:
                break

        reject_str = ", ".join(f"{k} {n}" for k, n in sorted(rejects.items())) or "없음"
        print(f"[{name}] 후보 {len(ordered)} → 통과 {len(videos)}  (탈락: {reject_str})")

        if len(videos) < MIN_PER_CONCEPT and cid in previous:
            kept = previous[cid].get("videos", [])
            print(f"::warning::[{name}] {len(videos)}개뿐이라 이전 목록 {len(kept)}개를 유지한다.")
            videos = kept

        used.update(v["id"] for v in videos)
        concepts_out.append({
            "id": cid,
            "name": name,
            "order": conf["order"],
            "videos": videos,
        })

    if dry_run or only:
        print("\n실험 모드 — 파일을 쓰지 않는다. 위 로그로 선곡을 판단할 것.")
        for c in concepts_out:
            print(f"\n[{c['name']}] 상위 10곡")
            for v in c["videos"][:10]:
                print(f"  {v['seconds']//60:4d}분  {v['title'][:58]}  | {v['channel'][:20]}")
        return

    total = sum(len(c["videos"]) for c in concepts_out)
    empty = [c["name"] for c in concepts_out if not c["videos"]]
    print(f"\n합계 영상 {total}개 · search.list {searches}회 (하루 한도 100)")
    if empty:
        die(f"비어 있는 컨셉이 있다: {', '.join(empty)}")

    payload = {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "concepts": concepts_out,
    }

    # 내용이 같으면 generatedAt 만 바뀐 커밋을 남기지 않는다. 히스토리를 읽을 수 있게 유지.
    if OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            if old.get("concepts") == concepts_out:
                print("내용 변경 없음 — 파일을 그대로 둔다.")
                return
        except json.JSONDecodeError:
            pass

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{OUT_PATH} 저장 ({OUT_PATH.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
