"""FXの外部データの取得。足データ（Twelve Data・Coinbase）、大衆のポジション比率（Myfxbook ほか）、経済指標カレンダー（Forex Factory）。

どれも無料の範囲で使う。取れなかったものは None を返し、判定はそのデータ抜きで続ける（1つの失敗で全体を止めない）。
足データは「時刻（UTCの秒）・始値・高値・安値・終値」の numpy 配列の組（Bars）で返す。
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass

import numpy as np
import requests

UA = "BoatLabAI-FX/1.0 (+https://aiaxislab.win)"

TWELVEDATA = "https://api.twelvedata.com/time_series"
COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
MYFXBOOK = "https://www.myfxbook.com/api/{name}.json"
FF_CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Twelve Data の無料枠は1分8回まで。余裕をみて8秒に1回にする
TD_INTERVAL_S = 8.0
_last_td = 0.0


@dataclass
class Bars:
    t: np.ndarray  # 足の始まりの時刻（UTCの秒）
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def until(self, ts: float) -> "Bars":
        """ts より前に始まった足だけ（判定時刻より後のデータを使わないため）"""
        k = int(np.searchsorted(self.t, ts, side="left"))
        return Bars(self.t[:k], self.o[:k], self.h[:k], self.l[:k], self.c[:k])

    def since(self, ts: float) -> "Bars":
        k = int(np.searchsorted(self.t, ts, side="left"))
        return Bars(self.t[k:], self.o[k:], self.h[k:], self.l[k:], self.c[k:])


def _bars(rows: list[tuple[float, float, float, float, float]]) -> Bars:
    rows = sorted({r[0]: r for r in rows}.values())  # 時刻の重複を除いて古い順に
    a = np.array(rows, dtype=float).reshape(-1, 5)
    return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4])


def _get_json(url: str, params: dict | None = None, timeout: int = 30, tries: int = 3):
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code in (403, 404, 451):
                return None  # 地域制限・存在しない。再試行しても同じ
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == tries - 1:
                print(f"取得に失敗しました: {url}（{type(e).__name__}）")
                return None
            time.sleep(5 * (attempt + 1))
    return None


# ---- 足データ ----

def twelvedata(symbol: str, interval: str, size: int = 5000) -> Bars | None:
    """Twelve Data の足。symbol は "EUR/USD" の形。interval は "1min" "1h" など。"""
    global _last_td
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return None
    wait = TD_INTERVAL_S - (time.time() - _last_td)
    if wait > 0:
        time.sleep(wait)
    _last_td = time.time()
    data = _get_json(TWELVEDATA, {"symbol": symbol, "interval": interval, "outputsize": size, "timezone": "UTC", "order": "ASC", "apikey": key})
    if not data or data.get("status") != "ok" or not data.get("values"):
        msg = (data or {}).get("message", "応答なし")
        print(f"Twelve Data {symbol} {interval}: 取れませんでした（{msg[:120]}）")
        return None
    rows = []
    for v in data["values"]:
        ts = dt.datetime.strptime(v["datetime"][:19], "%Y-%m-%d %H:%M:%S" if len(v["datetime"]) > 10 else "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
        rows.append((ts, float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
    return _bars(rows)


def coinbase(product: str, granularity: int, pages: int = 1, end: float | None = None) -> Bars | None:
    """Coinbase Exchange の公開API（キー不要）。1回300本までなので pages 回さかのぼって取る。"""
    end = end or time.time()
    rows = []
    for _ in range(pages):
        start = end - granularity * 300
        data = _get_json(COINBASE.format(product=product), {
            "granularity": granularity,
            "start": dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(),
            "end": dt.datetime.fromtimestamp(end, dt.timezone.utc).isoformat(),
        })
        time.sleep(0.5)
        if not isinstance(data, list) or not data:
            break
        rows += [(float(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4])) for r in data]  # [時刻, 安値, 高値, 始値, 終値, 出来高]
        end = start
    return _bars(rows) if rows else None


# ---- 大衆のポジション比率 ----

def myfxbook_outlook() -> dict[str, dict] | None:
    """Myfxbook のコミュニティアウトルック。{"EURUSD": {"long_pct", "avg_long", "avg_short"}} を返す。"""
    email, password = os.environ.get("MYFXBOOK_EMAIL"), os.environ.get("MYFXBOOK_PASSWORD")
    if not email or not password:
        print("MYFXBOOK_EMAIL・MYFXBOOK_PASSWORD が未登録なので、大衆の比率は Myfxbook から取りません")
        return None
    login = _get_json(MYFXBOOK.format(name="login"), {"email": email, "password": password}, tries=2)
    if not login or login.get("error"):
        print(f"Myfxbook にログインできませんでした（{(login or {}).get('message', '応答なし')}）")
        return None
    session = login["session"]
    try:
        data = _get_json(MYFXBOOK.format(name="get-community-outlook"), {"session": session}, tries=2)
    finally:
        _get_json(MYFXBOOK.format(name="logout"), {"session": session}, tries=1)
    if not data or data.get("error"):
        return None
    out = {}
    for s in data.get("symbols", []):
        try:
            out[s["name"].upper()] = {"long_pct": float(s["longPercentage"]), "avg_long": float(s["avgLongPrice"]), "avg_short": float(s["avgShortPrice"]), "source": "Myfxbook"}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def btc_crowd() -> dict | None:
    """BTCの大衆比率。Myfxbook に無いときの予備。取引所の公開データ（口座数ベースのロング比率）を順に試す。平均取得価格は無い。"""
    tries = [
        ("OKX", "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "1H"},
         lambda d: (lambda r: r / (1 + r) * 100)(float(d["data"][0][1]))),
        ("Bybit", "https://api.bybit.com/v5/market/account-ratio", {"category": "linear", "symbol": "BTCUSDT", "period": "1h", "limit": 1},
         lambda d: float(d["result"]["list"][0]["buyRatio"]) * 100),
        ("Binance", "https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": "BTCUSDT", "period": "1h", "limit": 1},
         lambda d: float(d[-1]["longAccount"]) * 100),
    ]
    for name, url, params, pick in tries:
        data = _get_json(url, params, timeout=15, tries=1)
        try:
            pct = pick(data)
            if 0 < pct < 100:
                return {"long_pct": round(pct, 1), "avg_long": None, "avg_short": None, "source": name}
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        print(f"BTCの大衆比率: {name} から取れませんでした")
    return None


# ---- 経済指標カレンダー ----

def calendar() -> list[dict] | None:
    """今週の経済指標（Forex Factory の公開フィード）。重要度 High のものだけ、時刻をUTCの秒にして返す。"""
    data = _get_json(FF_CALENDAR, tries=2)
    if not isinstance(data, list):
        return None
    out = []
    for e in data:
        try:
            if e.get("impact") != "High":
                continue
            ts = dt.datetime.fromisoformat(e["date"]).timestamp()
            out.append({"t": ts, "title": e.get("title", ""), "country": e.get("country", ""), "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda e: e["t"])
