"""足の合成（1分足→5分・10分・15分、1時間足→4時間・日足）と、足ごとの特徴量（fx/knowledge/features.json の tf_features）の計算。

指標の式は原典どおり（RSI・ADX・ATR は Wilder の平滑化、MACD は 12/26/9、ボリンジャーは 20本・2σ）。
計算できない値（本数が足りないなど）は None にして、ルール側で「一致しない」として扱う。
"""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

import numpy as np

from .sources import Bars

NY = ZoneInfo("America/New_York")


# ---- 足の合成 ----

def _group(b: Bars, keys: np.ndarray, starts: np.ndarray | None = None) -> Bars:
    if not len(b):
        return b
    cut = np.flatnonzero(np.diff(keys)) + 1
    idx = np.concatenate([[0], cut])
    end = np.concatenate([cut, [len(keys)]])
    t = b.t[idx] if starts is None else starts[idx]
    return Bars(t, b.o[idx], np.maximum.reduceat(b.h, idx), np.minimum.reduceat(b.l, idx), b.c[end - 1])


def resample(b: Bars, minutes: int) -> Bars:
    """UTCの時計に合わせて n 分足にまとめる（5分・10分・15分・1時間など）"""
    step = minutes * 60
    keys = (b.t // step).astype(np.int64)
    return _group(b, keys, keys * float(step))


def _ny_session(t: float) -> tuple[int, int]:
    """NYクローズ（17:00 NY）で区切った取引日の番号と、その日の何時間目か"""
    ny = dt.datetime.fromtimestamp(t, NY)
    shifted = ny + dt.timedelta(hours=7)  # 17:00 NY を 0:00 とみなす
    return shifted.toordinal(), shifted.hour


def resample_ny(b: Bars, hours: int) -> Bars:
    """1時間足から、NYクローズ基準の4時間足（hours=4）・日足（hours=24）を作る"""
    if not len(b):
        return b
    keys, starts = np.empty(len(b), dtype=np.int64), np.empty(len(b))
    for i, t in enumerate(b.t):
        day, hour = _ny_session(t)
        keys[i] = day * 24 + (hour // hours) * hours
        starts[i] = t - (hour % hours) * 3600
    return _group(b, keys, starts)


def resample_utc_hours(b: Bars, hours: int) -> Bars:
    step = hours * 3600
    keys = (b.t // step).astype(np.int64)
    return _group(b, keys, keys * float(step))


# ---- 指標 ----

def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    v = x[~np.isnan(x)]
    first = len(x) - len(v)
    if len(v) < n:
        return out
    out[first + n - 1] = v[:n].mean()
    for i in range(first + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=np.nan)
    gain, loss = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    gain[0] = loss[0] = np.nan
    up, dn = wilder(gain, n), wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(dn == 0, 100.0, 100 - 100 / (1 + up / dn))


def atr(b: Bars, n: int = 14) -> np.ndarray:
    pc = np.concatenate([[np.nan], b.c[:-1]])
    tr = np.nanmax(np.vstack([b.h - b.l, np.abs(b.h - pc), np.abs(b.l - pc)]), axis=0)
    return wilder(tr, n)


def adx(b: Bars, n: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = np.diff(b.h, prepend=np.nan)
    dn = -np.diff(b.l, prepend=np.nan)
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdm[0] = mdm[0] = np.nan
    a = atr(b, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * wilder(pdm, n) / a
        mdi = 100 * wilder(mdm, n) / a
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return wilder(dx, n), pdi, mdi


def pctile(x: np.ndarray, n: int = 120) -> float | None:
    """最後の値が、直近 n 本の中で何パーセンタイルか（0〜100）"""
    w = x[-n:]
    w = w[~np.isnan(w)]
    if len(w) < 30:
        return None
    return float((w < w[-1]).sum() / (len(w) - 1) * 100)


# ---- 値動きの構造 ----

def swings(b: Bars, k: int) -> tuple[list[int], list[int]]:
    """フラクタル（左右 k 本より高い／安い足）の位置。右側 k 本が確定したものだけ"""
    hi, lo = [], []
    for i in range(k, len(b) - k):
        if b.h[i] == b.h[i - k:i + k + 1].max() and b.h[i] > b.h[i - k:i].max():
            hi.append(i)
        if b.l[i] == b.l[i - k:i + k + 1].min() and b.l[i] < b.l[i - k:i].min():
            lo.append(i)
    return hi, lo


def _div(price_idx: list[int], series: np.ndarray, price: np.ndarray, low: bool, n: int) -> int:
    """直近2つのスイングで、価格と指標の向きが逆か（ダイバージェンス）。新しい方のスイングが直近 20 本以内のときだけ"""
    if len(price_idx) < 2 or price_idx[-1] < n - 20:
        return 0
    a, b = price_idx[-2], price_idx[-1]
    if np.isnan(series[a]) or np.isnan(series[b]):
        return 0
    if low and price[b] < price[a] and series[b] > series[a]:
        return 1
    if not low and price[b] > price[a] and series[b] < series[a]:
        return -1
    return 0


def _num(v) -> float | None:
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) or math.isinf(v) else v


def tf_features(b: Bars, k: int, round_step: float) -> dict:
    """1つの時間足の特徴量。b は確定した足だけ（古い順）"""
    n = len(b)
    if n < 60:
        return {}
    c = b.c
    e20, e50, s200 = ema(c, 20), ema(c, 50), sma(c, 200)
    m = ema(c, 12) - ema(c, 26)
    ms = ema(np.where(np.isnan(m), 0, m), 9)
    ms[np.isnan(m)] = np.nan
    hist = m - ms
    r = rsi(c)
    a = atr(b)
    ad, pdi, mdi = adx(b)
    mid, sd = sma(c, 20), np.array([c[max(0, i - 19):i + 1].std() if i >= 19 else np.nan for i in range(n)])
    up, lo = mid + 2 * sd, mid - 2 * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (up - lo) / mid
    hi_idx, lo_idx = swings(b, k)
    last_hi = b.h[hi_idx[-1]] if hi_idx else None
    last_lo = b.l[lo_idx[-1]] if lo_idx else None

    dow = 0
    if len(hi_idx) >= 2 and len(lo_idx) >= 2:
        hh, hl = b.h[hi_idx[-1]] > b.h[hi_idx[-2]], b.l[lo_idx[-1]] > b.l[lo_idx[-2]]
        lh, ll = b.h[hi_idx[-1]] < b.h[hi_idx[-2]], b.l[lo_idx[-1]] < b.l[lo_idx[-2]]
        dow = 1 if hh and hl else -1 if lh and ll else 0

    # 構造ブレイクとダマシ：直近3本の中で、その足より前に確定していたスイングを基準にする
    bos = fake = 0
    for i in range(max(1, n - 3), n):
        his = [j for j in hi_idx if j + k < i]
        los = [j for j in lo_idx if j + k < i]
        if his and c[i] > b.h[his[-1]] >= c[i - 1]:
            bos = 1
        if los and c[i] < b.l[los[-1]] <= c[i - 1]:
            bos = -1
        if los and b.l[i] < b.l[los[-1]] and c[i] > b.l[los[-1]]:
            fake = 1
        if his and b.h[i] > b.h[his[-1]] and c[i] < b.h[his[-1]]:
            fake = -1

    def cross(x, y, within):
        for i in range(max(1, n - within), n):
            if x[i - 1] <= y[i - 1] and x[i] > y[i]:
                return 1
            if x[i - 1] >= y[i - 1] and x[i] < y[i]:
                return -1
        return 0

    price = c[-1]
    levels_up = [b.h[j] for j in hi_idx[-6:] if b.h[j] > price] + [math.ceil(price / round_step) * round_step]
    levels_dn = [b.l[j] for j in lo_idx[-6:] if b.l[j] < price] + [math.floor(price / round_step) * round_step]
    res, sup = min(levels_up), max(levels_dn)
    at = a[-1]
    w = width[-4:]
    f = {
        "open": b.o[-1], "high": b.h[-1], "low": b.l[-1], "close": price,
        "ema20": e20[-1], "ema50": e50[-1], "sma200": s200[-1],
        "ema20_slope_atr": (e20[-1] - e20[-6]) / 5 / at if n > 6 else None,
        "dist_ema20_atr": (price - e20[-1]) / at,
        "ma_cross": cross(e20, e50, 3),
        "macd": m[-1], "macd_signal": ms[-1], "macd_hist": hist[-1], "macd_hist_prev": hist[-2],
        "macd_cross": cross(m, ms, 2),
        "macd_div": _div(lo_idx, hist, b.l, True, n) or _div(hi_idx, hist, b.h, False, n),
        "rsi14": r[-1], "rsi14_prev": r[-2],
        "rsi_div": _div(lo_idx, r, b.l, True, n) or _div(hi_idx, r, b.h, False, n),
        "adx14": ad[-1], "adx_prev": ad[-6] if n > 6 else None, "plus_di": pdi[-1], "minus_di": mdi[-1],
        "bb_upper": up[-1], "bb_lower": lo[-1], "bb_mid": mid[-1],
        "bb_pctb": (price - lo[-1]) / (up[-1] - lo[-1]) if up[-1] != lo[-1] else None,
        "bb_width_pctile": pctile(width),
        "bb_width_expanding": int(len(w) == 4 and bool(np.all(np.diff(w) > 0))),
        "atr14": at, "atr_pctile": pctile(a),
        "dow_trend": dow, "last_swing_high": last_hi, "last_swing_low": last_lo,
        "bos": bos, "fake_break": fake,
        "nearest_res": res, "nearest_sup": sup,
        "dist_res_atr": (res - price) / at, "dist_sup_atr": (price - sup) / at,
    }
    return {key: (_num(v) if isinstance(v, (float, np.floating)) or v is None else int(v) if isinstance(v, (bool, np.bool_, np.integer)) else v) for key, v in f.items()}
