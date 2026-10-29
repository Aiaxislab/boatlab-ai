"""銘柄全体の特徴量（fx/knowledge/features.json の global_features）。時間帯・五十日・指標・大衆・値幅など。

時刻は内部ではUTCの秒で持ち、表示だけ日本時間にする。夏時間はロンドンとNYで別々に判定する（zoneinfo）。
"""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

import holidays
import numpy as np

from .config import CB_WORDS
from .sources import Bars

JST = dt.timezone(dt.timedelta(hours=9))
LDN = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")
JP_HOLIDAYS = holidays.Japan(years=range(2025, 2031))


def jst(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, JST)


def hm(t: float) -> str:
    d = jst(t)
    return f"{d.hour}:{d.minute:02d}"


def _at(day: dt.date, hour: int, minute: int, tz) -> float:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).timestamp()


def market_open(kind: str, t: float) -> bool:
    """為替と金は、金曜17:00（NY）〜日曜17:00（NY）が休み"""
    if kind == "crypto":
        return True
    ny = dt.datetime.fromtimestamp(t, NY)
    wd, h = ny.weekday(), ny.hour
    return not (wd == 5 or (wd == 4 and h >= 17) or (wd == 6 and h < 17))


def session_code(t: float) -> str:
    """時間帯コード（composite_rules.json の sessions）。ロンドン・NYの現地時刻から決めるので夏時間に自動で合う"""
    ny = dt.datetime.fromtimestamp(t, NY)
    ldn = dt.datetime.fromtimestamp(t, LDN)
    j = jst(t)
    nyh, lh = ny.hour + ny.minute / 60, ldn.hour + ldn.minute / 60
    if 16 <= nyh < 18:
        return "rollover"
    if 8 <= nyh < 10:
        return "ny_open"
    if 10 <= nyh < 12:
        return "overlap"
    if 12 <= nyh < 16:
        return "ny_late"
    if 8 <= lh < 10:
        return "london_open"
    if lh >= 10 and nyh < 8:
        return "london"
    if j.hour >= 15 and lh < 8:
        return "pre_london"
    return "tokyo"


def _business(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in JP_HOLIDAYS


def is_gotobi(d: dt.date) -> bool:
    """五十日：5・10・15・20・25日と月末。休みに当たるときは前の営業日"""
    if not _business(d):
        return False
    nxt = d + dt.timedelta(days=1)
    while not _business(nxt):
        nxt += dt.timedelta(days=1)
    x = d
    while x < nxt:
        last = (x + dt.timedelta(days=1)).month != x.month
        if x.day in (5, 10, 15, 20, 25) or last:
            return True
        x += dt.timedelta(days=1)
    return False


def is_month_end(d: dt.date) -> bool:
    if not _business(d):
        return False
    x = d + dt.timedelta(days=1)
    while not _business(x):
        x += dt.timedelta(days=1)
    return x.month != d.month


def _today_window(t: float) -> tuple[float, float]:
    """『今日』＝日本時間7:00〜翌7:00"""
    j = jst(t)
    day = j.date() if j.hour >= 7 else j.date() - dt.timedelta(days=1)
    start = _at(day, 7, 0, JST)
    return start, start + 86400


def event_features(t: float, events: list[dict] | None, ccy: list[str]) -> dict:
    if events is None:
        return {"events_known": 0}
    mine = [e for e in events if e["country"] in ccy]
    start, end = _today_window(t)
    nxt = next((e for e in mine if e["t"] > t), None)
    last = next((e for e in reversed(mine) if e["t"] <= t), None)
    today = [e for e in mine if start <= e["t"] < end]
    f = {
        "events_known": 1,
        "events_today_high": len(today),
        "cb_event_today": int(any(any(w.lower() in e["title"].lower() for w in CB_WORDS) for e in today)),
        "mins_to_next_high_event": (nxt["t"] - t) / 60 if nxt else 9999,
        "next_event_name": f"{nxt['country']} {nxt['title']}" if nxt else "",
        "next_event_time": hm(nxt["t"]) if nxt else "",
        "mins_since_last_high_event": (t - last["t"]) / 60 if last else 9999,
        "last_event_name": f"{last['country']} {last['title']}" if last else "",
        "events_list": [{"time": hm(e["t"]), "name": f"{e['country']} {e['title']}", "forecast": e["forecast"], "previous": e["previous"]} for e in today],
    }
    # 今日の分がもう過ぎていて、次が明日以降なら『今日の予定』としては数えない
    if nxt and nxt["t"] >= end:
        f["mins_to_next_high_event"] = 9999
    return f


def _range(b: Bars) -> float | None:
    return float(b.h.max() - b.l.min()) if len(b) else None


def global_features(sym: dict, tfs: dict, bars: dict, t: float, events: list[dict] | None, crowd: dict | None, crowd_prev: dict | None) -> dict:
    """bars: {"base": 1分足（BTCは5分足）, "1h": 1時間足, "D": 日足}。どれも t より前の足だけ"""
    name, kind, pip = sym["name"], sym["kind"], sym["pip"]
    base, h1, d1 = bars["base"], bars["1h"], bars["D"]
    price = float(base.c[-1])
    j = jst(t)
    atr1h = (tfs.get("1h") or {}).get("atr14")
    g: dict = {"pair": name, "kind": kind, "has_jpy": int("JPY" in name), "price": price, "pip": pip, "atr1h": atr1h,
               "adx4h": (tfs.get("4h") or {}).get("adx14")}

    # 前日の高値・安値、ADR（日足は確定した足だけ。最後の足が今日の途中なら除く）
    done = d1.until(_day_start(kind, t))
    if len(done) >= 2:
        g["pdh"], g["pdl"] = float(done.h[-1]), float(done.l[-1])
        if atr1h:
            g["near_pdh"] = int(abs(price - g["pdh"]) <= 0.3 * atr1h)
            g["near_pdl"] = int(abs(price - g["pdl"]) <= 0.3 * atr1h)
    if len(done) >= 20:
        g["adr20"] = float((done.h[-20:] - done.l[-20:]).mean())
        today = base.since(_day_start(kind, t))
        if len(today):
            g["adr_used"] = round(_range(today) / g["adr20"] * 100)

    step = sym["round"]
    lvl = round(price / step) * step
    g["round_level"], g["round_dist_pips"] = lvl, round(abs(price - lvl) / pip, 1)

    # 東京時間（8:00〜15:00）の値幅と、過去20日の平均との比
    tokyo_start = _at(j.date(), 8, 0, JST)
    if kind != "crypto" and len(h1):
        today_tokyo = base.since(tokyo_start).until(min(t, tokyo_start + 7 * 3600))
        past = []
        for k in range(1, 30):
            s = tokyo_start - k * 86400
            r = _range(h1.since(s).until(s + 7 * 3600))
            if r:
                past.append(r)
            if len(past) == 20:
                break
        if len(today_tokyo) and len(past) >= 10:
            g["asia_high"], g["asia_low"] = float(today_tokyo.h.max()), float(today_tokyo.l.min())
            g["asia_range_ratio"] = _range(today_tokyo) / float(np.mean(past))
            g["asia_range_ratio_pct"] = round(g["asia_range_ratio"] * 100)

    # 時間帯（BTCには使わない）
    if kind != "crypto":
        g["session"] = session_code(t)
        london_open = _at(dt.datetime.fromtimestamp(t, LDN).date(), 8, 0, LDN)
        g["london_open_time"] = hm(london_open)
        fix = _at(dt.datetime.fromtimestamp(t, LDN).date(), 16, 0, LDN)
        if fix <= t:
            fix += 86400
        g["mins_to_london_fix"], g["london_fix_time"] = (fix - t) / 60, hm(fix)
        g["mins_to_tokyo_fix"] = (_at(j.date(), 9, 55, JST) - t) / 60
        g["is_gotobi"] = int(is_gotobi(j.date()))
        g["is_month_end"] = int(is_month_end(j.date()))
        # その取引日（NY 17:00 で切り替わる）が金曜か。日本時間の金曜0時台はまだNYの木曜
        g["is_friday"] = int((dt.datetime.fromtimestamp(t, NY) + dt.timedelta(hours=7)).weekday() == 4)
    else:
        g["session"] = "crypto"
        g["is_friday"] = 0

    g.update(event_features(t, events, sym["ccy"]))

    # 大衆のポジション
    if crowd:
        cl = crowd["long_pct"]
        g["crowd_long_pct"], g["crowd_short_pct"] = cl, round(100 - cl, 1)
        g["crowd_source"] = crowd.get("source", "")
        g["crowd_major_side"] = 1 if cl >= 60 else -1 if cl <= 40 else 0
        g["crowd_avg_long"], g["crowd_avg_short"] = crowd.get("avg_long"), crowd.get("avg_short")
        if crowd_prev:
            g["crowd_long_pct_chg24"] = round(cl - crowd_prev["long_pct"], 1)
        if atr1h:
            ago = base.until(t - 86400)
            if len(ago):
                g["price_chg24_atr"] = (price - float(ago.c[-1])) / atr1h
            avg = crowd.get("avg_long") if g["crowd_major_side"] == 1 else crowd.get("avg_short") if g["crowd_major_side"] == -1 else None
            if avg:
                # 多数派の含み損（ATR単位。含み益ならマイナス）
                g["crowd_underwater_atr"] = round((avg - price) / atr1h if g["crowd_major_side"] == 1 else (price - avg) / atr1h, 2)
            g.update(_stop_zone(g["crowd_major_side"], price, tfs, atr1h, step))
    return {k: v for k, v in g.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _day_start(kind: str, t: float) -> float:
    """今の取引日の始まり（為替・金はNYクローズ、BTCはUTC 0時）"""
    if kind == "crypto":
        return t - t % 86400
    ny = dt.datetime.fromtimestamp(t, NY)
    start = ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if ny.hour < 17:
        start -= dt.timedelta(days=1)
    return start.timestamp()


def _stop_zone(side: int, price: float, tfs: dict, atr1h: float, step: float) -> dict:
    """多数派の損切りが集まりそうな水準。買いが多数なら直近の1時間足・4時間足の安値の少し下（と、その下のキリ番）"""
    if side == 0:
        return {}
    if side == 1:
        lows = [tfs[k]["last_swing_low"] for k in ("1h", "4h") if tfs.get(k, {}).get("last_swing_low") and tfs[k]["last_swing_low"] < price]
        if not lows:
            return {}
        z_from = max(lows)
        z_to = z_from - 0.3 * atr1h
        rnd = math.floor(z_from / step) * step
        if z_to - 0.5 * atr1h <= rnd < z_to:
            z_to = rnd
        dist = (price - z_from) / atr1h
    else:
        highs = [tfs[k]["last_swing_high"] for k in ("1h", "4h") if tfs.get(k, {}).get("last_swing_high") and tfs[k]["last_swing_high"] > price]
        if not highs:
            return {}
        z_from = min(highs)
        z_to = z_from + 0.3 * atr1h
        rnd = math.ceil(z_from / step) * step
        if z_to < rnd <= z_to + 0.5 * atr1h:
            z_to = rnd
        dist = (z_from - price) / atr1h
    return {"stop_zone_from": z_from, "stop_zone_to": z_to, "stop_zone_dist_atr": max(0.0, dist)}
