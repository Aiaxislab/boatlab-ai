"""気象庁の天気予報と潮位表から、レース時刻の天気・風・波・潮をまとめる。"""

import datetime as dt
import re

from . import sources


def _clean(s: str) -> str:
    return re.sub(r"[　\s]+", " ", s).strip()


def forecast(office: str, area: str, day: dt.date) -> dict | None:
    """その日の天気・風・波（文章）と、6時間ごとの降水確率。"""
    try:
        data = sources.jma_forecast(office)
    except Exception:
        return None
    short = data[0]
    out = dict(office=short.get("publishingOffice", ""), report=short.get("reportDatetime", ""), pops={})
    for ts in short["timeSeries"]:
        times = [dt.datetime.fromisoformat(t) for t in ts["timeDefines"]]
        a = next((x for x in ts["areas"] if x["area"]["code"] == area), None)
        if a is None:
            continue
        out["area_name"] = a["area"]["name"]
        for i, t in enumerate(times):
            if "weathers" in a and t.date() == day:
                out["weather"] = _clean(a["weathers"][i])
                out["wind"] = _clean(a.get("winds", [""] * len(times))[i])
                if a.get("waves"):
                    out["wave"] = _clean(a["waves"][i])
            if "pops" in a and t.date() == day and a["pops"][i] != "":
                out["pops"][t.hour] = int(a["pops"][i])
    return out if "weather" in out else None


def pop_at(fc: dict | None, hhmm: str) -> int | None:
    if not fc or not fc["pops"]:
        return None
    h = int(hhmm[:2])
    slots = sorted(k for k in fc["pops"] if k <= h)
    return fc["pops"][slots[-1]] if slots else None


def short_weather(text: str) -> str:
    """「雨 夜遅く くもり …」→「雨」のように最初の天気だけ取り出す。"""
    return text.split(" ")[0] if text else ""


def tide_day(station: str, day: dt.date) -> dict | None:
    """潮位表のその日の行。毎時潮位(cm)と満潮・干潮の時刻。"""
    try:
        txt = sources.jma_tide(station, day.year)
    except Exception:
        return None
    if not txt:
        return None
    key = f"{day.year % 100:2d}{day.month:2d}{day.day:2d}{station}"
    for line in txt.splitlines():
        if line[72:80] == key:
            hourly = [int(line[i:i + 3]) for i in range(0, 72, 3)]

            def events(seg):
                ev = []
                for i in range(0, 28, 7):
                    s = seg[i:i + 7]
                    if s.strip() and s[:4] != "9999":
                        ev.append((f"{int(s[:2]):02d}:{s[2:4]}", int(s[4:7])))
                return ev
            return dict(hourly=hourly, high=events(line[80:108]), low=events(line[108:136]))
    return None


def tide_at(td: dict | None, hhmm: str) -> dict | None:
    if not td:
        return None
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    v = td["hourly"]
    a, b = v[min(h, 23)], v[min(h + 1, 23)]
    level = round(a + (b - a) * m / 60)
    trend = "上げ" if b > a else "下げ" if b < a else "止まり"
    t = h * 60 + m
    nxt = sorted(
        [(int(x[:2]) * 60 + int(x[3:]) - t, "満潮", x, lv) for x, lv in td["high"]]
        + [(int(x[:2]) * 60 + int(x[3:]) - t, "干潮", x, lv) for x, lv in td["low"]]
    )
    near = min(nxt, key=lambda e: abs(e[0])) if nxt else None
    return dict(level=level, trend=trend, near=near, high=td["high"], low=td["low"])


def compact(s: str) -> str:
    """気象庁の文章は語の間にスペースが入っているので詰める（「雨 所により」→「雨所により」）。"""
    return s.replace(" ", "")
