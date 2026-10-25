"""簡易予想モデル。

各艇の「強さ」を、コースの有利さ・勝率・当地勝率・モーター・級別・今節のスタートから点数化し、
プラケット＝ルースモデル（強い艇ほど先に抜け出す確率が高い、という順位の確率モデル）で
3連単120通りの確率を出す。重みは手で決めた初期値で、結果がたまったら見直す。
"""

import itertools
import math
from statistics import mean

from .venues import NATIONAL_LANE_WIN

W_NAT = 0.45      # 全国勝率 1点あたり
W_LOC = 0.15      # 当地勝率と全国勝率の差 1点あたり
W_MOTOR = 0.015   # モーター2連率 1%あたり
W_ST = -6.0       # 今節平均ST 1秒あたり（速いほど強い）
CLASS = {"A1": 0.25, "A2": 0.10, "B1": 0.0, "B2": -0.25}


def lane_rates(history: dict, jcd: str, prior: float = 300) -> tuple[list[float], int]:
    """場ごとのコース別1着率。レース数が少ないうちは全国平均に寄せる。"""
    wins, n = [0] * 6, 0
    for day in history.values():
        for r in day.get(jcd, {}).get("races", []):
            first = next((b for b in r["boats"] if b["place"] == "01" and b["course"]), None)
            if first:
                wins[first["course"] - 1] += 1
                n += 1
    return [(wins[i] + prior * NATIONAL_LANE_WIN[i]) / (n + prior) for i in range(6)], n


def series_runs(history: dict, jcd: str, today, series_day: int) -> dict:
    """今節（この開催）の各選手の成績。{登番: [{place, st, wind, wind_dir, wave}]}"""
    import datetime as dt
    runs = {}
    for back in range(series_day - 1, 0, -1):
        day = history.get((today - dt.timedelta(days=back)).isoformat(), {})
        for r in day.get(jcd, {}).get("races", []):
            for b in r["boats"]:
                runs.setdefault(b["toban"], []).append(dict(
                    place=b["place"], st=b["st"], wind=r["wind"], wind_dir=r["wind_dir"], wave=r["wave"]))
    return runs


def score_boats(boats: list[dict], lanes: list[float], runs: dict) -> list[float]:
    nat_mean = mean(b["nat_win"] or 0 for b in boats)
    motor_mean = mean(b["motor_2r"] or 0 for b in boats)
    sts = {b["toban"]: [r["st"] for r in runs.get(b["toban"], []) if r["st"] is not None] for b in boats}
    st_avg = {k: mean(v) for k, v in sts.items() if v}
    st_mean = mean(st_avg.values()) if st_avg else None
    scores = []
    for b in boats:
        nat = b["nat_win"] or 0
        loc = b["loc_win"] if b["loc_win"] else nat
        s = math.log(lanes[b["waku"] - 1])
        s += W_NAT * (nat - nat_mean)
        s += W_LOC * (loc - nat)
        s += W_MOTOR * ((b["motor_2r"] or 0) - motor_mean)
        s += CLASS.get(b["cls"], 0)
        if st_mean is not None and b["toban"] in st_avg:
            s += W_ST * (st_avg[b["toban"]] - st_mean)
        scores.append(s)
    return scores


def trifecta(scores: list[float]) -> dict[tuple, float]:
    w = [math.exp(s) for s in scores]
    total = sum(w)
    probs = {}
    for i, j, k in itertools.permutations(range(6), 3):
        p = w[i] / total * w[j] / (total - w[i]) * w[k] / (total - w[i] - w[j])
        probs[(i + 1, j + 1, k + 1)] = p
    return probs


def place_probs(tri: dict) -> list[tuple[float, float, float]]:
    """各艇の 1着・2着以内・3着以内 の確率。"""
    out = []
    for n in range(1, 7):
        p1 = sum(p for c, p in tri.items() if c[0] == n)
        p2 = sum(p for c, p in tri.items() if n in c[:2])
        p3 = sum(p for c, p in tri.items() if n in c)
        out.append((p1, p2, p3))
    return out


def predict(race: dict, lanes: list[float], runs: dict, n_buy: int = 6, scores: list[float] | None = None) -> dict:
    """scores を渡すと（AI学習モデルの点数）、手の重みの点数の代わりにそれを使う。"""
    if scores is None:
        scores = score_boats(race["boats"], lanes, runs)
    tri = trifecta(scores)
    ranked = sorted(tri.items(), key=lambda kv: -kv[1])
    pp = place_probs(tri)
    order = sorted(range(6), key=lambda i: -pp[i][0])
    marks = [""] * 6
    for sym, i in zip(["◎", "○", "▲", "△", "△"], order):
        marks[i] = sym
    trio = {}
    for c, p in tri.items():
        key = tuple(sorted(c))
        trio[key] = trio.get(key, 0) + p
    trio_ranked = sorted(trio.items(), key=lambda kv: -kv[1])
    buy = ranked[:n_buy]
    return dict(
        scores=scores, place=pp, marks=marks, order=[i + 1 for i in order],
        top5=[(c, p) for c, p in ranked[:5]],
        buy=[c for c, _ in buy], buy_each=[p for _, p in buy], buy_prob=sum(p for _, p in buy),
        trio=[c for c, _ in trio_ranked[:3]], trio_prob=sum(p for _, p in trio_ranked[:3]),
    )
