"""매일 아침 GitHub Actions가 실행: 나이스 급식·학사일정 + 컴시간 시간표를 받아 data.json 갱신."""
import datetime as dt
import json
import os
import re
import time

import requests
from pycomcigan import TimeTable

OFFICE, SCHOOL = "J10", "7530851"          # 경기북과학고 (나이스 코드)
COMCI_NAME = "경기북과학고"
NEIS_KEY = os.environ.get("NEIS_KEY", "")   # 없으면 한 번에 5줄씩만 옴
PSIZE = 1000 if NEIS_KEY else 5
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

BASIC = re.compile(r"(혼합잡곡밥|발아현미밥|흑미밥|차조밥|찰보리밥|서리태콩밥|통밀밥|보조밥.*|쌀밥|백미밥)$|김치$|깍두기|석박지|겉절이|단무지|^우유$|시리얼|식빵|모닝빵|장아찌|잼$")
PROTEIN = re.compile(r"스테이크|까스|카츠|커틀렛|치킨|닭|돼지|돈육|돈등뼈|소고기|쇠고기|한우|오리|갈비|불고기|보쌈|족발|제육|탕수육|강정|깐쇼|깐풍|새우|연어|가자미|고등어|삼치|임연수|코다리|꽁치|낙지|오징어|주꾸미|꽃게|전복|스팸|미트볼|함박|만두|라멘|국수|짬뽕|우동|파스타|스파게티|덮밥|볶음밥|비빔밥|자장|짜장|카레|나시고랭|떡볶이|소떡|부대찌개|감자탕|순대|파닭|핫도그|도그$|버거|피자|오므라이스")
SIDE = re.compile(r"무침|나물|샐러드|샐럿|생채|김구이|계란찜|콘치즈|알감자|감자채|애호박|어묵|멸치|진미채|파래|묵|요거트|주스|음료|파이|츄러스|빵|휘낭시에|붕어빵|과일")
SOUP = re.compile(r"국$|탕$|찌개|스프$|전골")


def date(d):
    return d.strftime("%Y%m%d")


def neis(endpoint, **params):
    params.update({"Type": "json", "pIndex": 1, "pSize": PSIZE,
                   "ATPT_OFCDC_SC_CODE": OFFICE, "SD_SCHUL_CODE": SCHOOL})
    if NEIS_KEY:
        params["KEY"] = NEIS_KEY
    for i in range(3):
        try:
            r = requests.get(f"https://open.neis.go.kr/hub/{endpoint}", params=params, timeout=20)
            data = r.json()
            if endpoint not in data:
                return []
            return [row for sec in data[endpoint] if "row" in sec for row in sec["row"]]
        except Exception:
            time.sleep(2 + i * 2)
    return []


def split_dishes(raw):
    out = []
    for d in re.split(r"<br\s*/?>", raw):
        d = re.sub(r"\(\s*[\d.\s]+\)", "", d)        # 알레르기 번호
        d = re.sub(r"[*#★☆]", "", d)
        d = re.sub(r"\s+", " ", d).strip().rstrip(".")
        if d:
            out.append(d)
    return out


def pick_main(dishes):
    """주메뉴 고르기: 고기·생선·면·밥요리 같은 메인 반찬을 먼저, 부족하면 건더기 있는 국·탕."""
    cand = [d for d in dishes if not BASIC.search(d) and (not SIDE.search(d) or re.search(r"밥|덮밥|국수|면", d))]
    mains = [d for d in cand if PROTEIN.search(d) and not SOUP.search(d)]
    soups = [d for d in cand if PROTEIN.search(d) and SOUP.search(d)]
    picked = mains[:2]
    # 찌개·국이 사실상 메인인 날(부대찌개, 감자탕 등)이나 메인이 하나뿐인 날은 국물 요리를 하나 더
    if len(picked) < 2 and soups:
        picked.append(soups[0])
    if not picked:
        picked = cand[:1] or [d for d in dishes if not BASIC.search(d)][:1]
    return picked


def fetch_meals():
    """이번 주 월요일부터 3주치. 키가 없으면 '한 주 × 한 끼' 단위로 나눠 요청(한 번에 최대 5일)."""
    monday = TODAY - dt.timedelta(days=TODAY.weekday())
    if TODAY.weekday() >= 5:
        monday += dt.timedelta(days=7)
    meals = {}
    for w in range(3):
        start = monday + dt.timedelta(days=7 * w)
        end = start + dt.timedelta(days=4)
        for code in ("1", "2", "3"):
            rows = neis("mealServiceDietInfo", MMEAL_SC_CODE=code,
                        MLSV_FROM_YMD=date(start), MLSV_TO_YMD=date(end))
            for r in rows:
                key = {"1": "breakfast", "2": "lunch", "3": "dinner"}[r["MMEAL_SC_CODE"]]
                dishes = split_dishes(r["DDISH_NM"])
                kcal = float(re.sub(r"[^\d.]", "", r.get("CAL_INFO", "") or "0") or 0)
                meals.setdefault(r["MLSV_YMD"], {"date": r["MLSV_YMD"]})[key] = {
                    "dishes": dishes, "main": pick_main(dishes), "kcal": kcal}
            time.sleep(0.3)
    return meals


def fetch_events():
    """오늘부터 약 5달치 학사일정. 키가 없으면 일주일씩 나눠 요청."""
    events, step = [], (150 if NEIS_KEY else 7)
    d = TODAY
    while d < TODAY + dt.timedelta(days=150):
        e = d + dt.timedelta(days=step - 1)
        for r in neis("SchoolSchedule", AA_FROM_YMD=date(d), AA_TO_YMD=date(e)):
            name = r.get("EVENT_NM", "").strip()
            if not name or "토요휴업" in name:
                continue
            events.append({"date": r["AA_YMD"], "title": name, "source": "NEIS"})
        d = e + dt.timedelta(days=1)
        time.sleep(0.3)
    return events


def comcigan_week(week):
    for _ in range(12):                      # 컴시간 서버는 가끔 빈 응답을 줘서 여러 번 시도
        try:
            return TimeTable(COMCI_NAME, week_num=week)
        except Exception:
            time.sleep(2)
    return None


def fetch_timetable():
    def grab(t):
        res = {}
        for g in range(1, len(t.timetable)):
            for c in range(1, len(t.timetable[g])):
                days, teach, chg = {}, {}, {}
                for d in range(1, 6):
                    ps = t.timetable[g][c][d] if d < len(t.timetable[g][c]) else []
                    days[str(d)] = [p.subject.strip() for p in ps]
                    teach[str(d)] = [p.teacher.strip() for p in ps]
                    chg[str(d)] = [bool(p.replaced) for p in ps]
                res[f"{g}-{c}"] = {"days": days, "teachers": teach, "changed": chg}
        return res

    t0 = comcigan_week(0)
    if t0 is None:
        return None
    t1 = comcigan_week(1)
    cur, nxt = grab(t0), (grab(t1) if t1 else {})
    times = [x.split("(")[1].rstrip(")") if "(" in x else "" for x in t0.day_time]
    out = {}
    for key, v in cur.items():
        g, c = map(int, key.split("-"))
        out[key] = {"grade": g, "cls": c, "source": "컴시간", "updated": t0.update_date, "times": times,
                    "weekStart": t0.start_date.replace("-", ""), **v,
                    "next": {"weekStart": t1.start_date.replace("-", "") if t1 else "", **nxt.get(key, {})}}
    return out


def main():
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {}

    # 급식: 2주 전까지는 남기고 새로 받은 것으로 덮어쓰기
    keep_from = date(TODAY - dt.timedelta(days=14))
    meals = {m["date"]: m for m in old.get("meals", []) if m["date"] >= keep_from}
    meals.update(fetch_meals())

    events = fetch_events()
    if not events:                           # 나이스가 응답하지 않으면 기존 일정 유지
        events = old.get("events", [])
    else:
        past = [e for e in old.get("events", []) if e["date"] < date(TODAY)]
        events = past + events

    tt = fetch_timetable() or old.get("timetable", {})

    data = {"updated": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "meals": sorted(meals.values(), key=lambda m: m["date"]),
            "events": sorted(events, key=lambda e: e["date"]),
            "timetable": tt}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"meals {len(data['meals'])}, events {len(data['events'])}, classes {len(tt)}")


if __name__ == "__main__":
    main()
