"""AI学習モデルの学習（週1回、GitHub Actions の「AIモデルの学習」で動く）。

    python -m boatlab.train [--months 36] [--dir out/ml]

1. 過去の番組表と競走成績から、月ごとの学習データ（1レース1行）を作る。作った月は R2 に置いて次回から使い回す
2. 最後の60日を「検証用」に取っておき、それより前で学習して、今の簡易モデルと当たり具合を比べる
3. 簡易モデルより良ければ「採用」にして、全期間で学び直したモデルを model.json に書く
   （毎朝の予想は採用されたモデルだけを使う。不採用なら今までどおり簡易モデル）
"""

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

from . import conditions, ml, model, sources
from .parse import parse_program, parse_results
from .venues import NATIONAL_LANE_WIN, VENUES

JST = dt.timezone(dt.timedelta(hours=9))
HOLDOUT_DAYS = 60
SERIES_LOOKBACK = 7  # 今節のスタートを出すのに、何日前までの成績を見るか


def _month_range(ym: str) -> tuple[dt.date, dt.date]:
    y, m = map(int, ym.split("-"))
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def _results(day: dt.date, cache: dict) -> dict:
    if day not in cache:
        text = sources.mbrace_text("K", day)
        cache[day] = parse_results(text) if text and "全レース終了後に登録されます" not in text else {}
    return cache[day]


def _tide_env(jcd: str, day: dt.date, hhmm: str, tides: dict) -> dict:
    v = VENUES[jcd]
    if not v["tide"]:
        return {}
    key = (v["tide"][0], day)
    if key not in tides:
        tides[key] = conditions.tide_day(v["tide"][0], day)
    td = tides[key]
    t = conditions.tide_at(td, hhmm)
    if not t or t["trend"] not in ml.TIDE_TRENDS:
        return {}
    return dict(tide=t["trend"], tide_dev=round(t["level"] - sum(td["hourly"]) / 24))


def build_day(day: dt.date, cache: dict, tides: dict) -> list[dict]:
    """1日分の学習データ。朝の時点で分かる情報（番組表・前日までの今節成績・潮）と、
    そのレースの実際の風・波・着順を1行にまとめる。"""
    text = sources.mbrace_text("B", day)
    res = _results(day, cache)
    if not text or not res:
        return []
    program = parse_program(text)
    history = {(day - dt.timedelta(days=b)).isoformat(): _results(day - dt.timedelta(days=b), cache)
               for b in range(1, SERIES_LOOKBACK + 1)}
    rows = []
    for jcd, prog in program.items():
        if jcd not in VENUES or jcd not in res:
            continue
        runs = model.series_runs(history, jcd, day, min(prog["day"], SERIES_LOOKBACK + 1))
        by_no = {r["no"]: r for r in res[jcd]["races"]}
        for race in prog["races"]:
            r = by_no.get(race["no"])
            boats = sorted(race["boats"], key=lambda b: b["waku"])
            if not r or [b["waku"] for b in boats] != [1, 2, 3, 4, 5, 6]:
                continue
            order = [b["waku"] for b in sorted((b for b in r["boats"] if b["place"].isdigit()), key=lambda b: b["place"])][:3]
            if not order:
                continue
            st = []
            for b in boats:
                sts = [x["st"] for x in runs.get(b["toban"], []) if x["st"] is not None]
                st.append(round(sum(sts) / len(sts), 3) if sts else None)
            env = dict(wind=r["wind"], dir=r["wind_dir"], wave=r["wave"], **_tide_env(jcd, day, race["deadline"], tides))
            rows.append(dict(
                d=day.isoformat(), j=jcd, n=race["no"], env=env, st=st, o=order,
                b=[[b["waku"], b["cls"], b["nat_win"], b["nat_2r"], b["loc_win"], b["loc_2r"],
                    b["motor_2r"], b["boat_2r"], b["age"], b["weight"]] for b in boats],
                pay=r["payout"].get("tri", {}).get("yen"),
            ))
    return rows


def build_month(ym: str, upto: dt.date, cache: dict, tides: dict) -> tuple[dict, list[dict]]:
    first, last = _month_range(ym)
    last = min(last, upto)
    rows, days = [], 0
    day = first
    while day <= last:
        got = build_day(day, cache, tides)
        rows += got
        days += bool(got)
        day += dt.timedelta(days=1)
        # 古い成績はもう使わないので捨てる（メモリ節約）
        for k in [k for k in cache if k < day - dt.timedelta(days=SERIES_LOOKBACK + 1)]:
            del cache[k]
    print(f"{ym}: {days}日・{len(rows)}レース")
    return dict(month=ym, upto=last.isoformat(), races=len(rows), version=1), rows


def month_keys(today: dt.date, months: int) -> list[str]:
    upto = today - dt.timedelta(days=1)
    yms, y, m = [], upto.year, upto.month
    for _ in range(months):
        yms.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return yms[::-1]


def ensure_data(base: Path, months: int, today: dt.date, budget_min: float) -> tuple[list[dict], list[str]]:
    """必要な月の学習データをそろえる。足りない月・途中までの月だけ作り直す。

    1日分を作るのに20〜30秒かかる（3年分だと7時間を超える）ので、新しい月から順に作り、
    budget_min 分を過ぎたら残りの月は次回に回す（作った月は R2 に置かれ、次回は取ってくるだけ）。
    """
    import time
    start = time.time()
    upto = today - dt.timedelta(days=1)  # 前日まで（成績が出そろっている日）
    rows, changed, skipped, cache, tides = [], [], [], {}, {}
    for key in reversed(month_keys(today, months)):
        path = base / "races" / f"{key}.jsonl.gz"
        meta, got = ml.read_rows(path) if path.exists() else ({}, [])
        _, month_last = _month_range(key)
        want = min(month_last, upto).isoformat()
        if meta.get("version") != 1 or meta.get("upto", "") < want:
            if time.time() - start > budget_min * 60:
                skipped.append(key)
                rows += got  # 途中までの月はあるだけ使う
                continue
            cache.clear()  # メモリ節約（成績はファイルにも控えてある）
            meta, got = build_month(key, upto, cache, tides)
            ml.write_rows(path, meta, got)
            changed.append(key)
        rows += got
    if skipped:
        print(f"時間の都合で次回に回した月: {', '.join(sorted(skipped))}（もう一度動かすと続きを作ります）")
    return rows, changed


def baseline_scores(train: list[dict], test: list[dict]) -> np.ndarray:
    """今の簡易モデル（手で決めた重み）の点数。コース別1着率は学習期間から場ごとに数える。"""
    wins, n = {}, {}
    for r in train:
        wins.setdefault(r["j"], [0] * 6)[r["o"][0] - 1] += 1
        n[r["j"]] = n.get(r["j"], 0) + 1
    out = []
    keys = ["waku", "cls", "nat_win", "nat_2r", "loc_win", "loc_2r", "motor_2r", "boat_2r", "age", "weight"]
    for r in test:
        w, k = wins.get(r["j"], [0] * 6), n.get(r["j"], 0)
        lanes = [(w[i] + 300 * NATIONAL_LANE_WIN[i]) / (k + 300) for i in range(6)]
        boats = [dict(zip(keys, b), toban=str(i)) for i, b in enumerate(r["b"])]
        runs = {str(i): [dict(st=s)] for i, s in enumerate(r["st"]) if s is not None}
        out.append(model.score_boats(boats, lanes, runs))
    return np.array(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=36, help="何か月分で学習するか")
    ap.add_argument("--dir", default="out/ml", help="学習データとモデルの置き場")
    ap.add_argument("--date", help="今日（YYYY-MM-DD）。省略すると今日（日本時間）")
    ap.add_argument("--budget", type=float, default=180, help="学習データ作りに使う時間の上限（分）")
    ap.add_argument("--print-months", action="store_true", help="使う月（YYYY-MM）を並べて表示するだけ")
    args = ap.parse_args()
    base = Path(args.dir)
    now = dt.datetime.now(JST)
    today = dt.date.fromisoformat(args.date) if args.date else now.date()
    if args.print_months:
        print(" ".join(month_keys(today, args.months)))
        return

    rows, changed = ensure_data(base, args.months, today, args.budget)
    (base / "changed.txt").write_text("".join(f"{k}\n" for k in changed), encoding="utf-8")
    print(f"学習データ {len(rows)}レース（作り直した月: {', '.join(changed) or 'なし'}）")
    if len(rows) < 500:
        raise SystemExit("学習データが少なすぎます")
    rows.sort(key=lambda r: (r["d"], r["j"], r["n"]))
    cut = (today - dt.timedelta(days=HOLDOUT_DAYS)).isoformat()
    train = [r for r in rows if r["d"] < cut]
    test = [r for r in rows if r["d"] >= cut]
    if len(train) < 500 or len(test) < 100:  # 試し（1〜2か月分）のときは半分で分ける
        half = len(rows) * 2 // 3
        train, test = rows[:half], rows[half:]

    trial = ml.fit(train)
    X, _, _ = ml._design(test, vocab={k: i for i, k in enumerate(trial["weights"])})
    w = np.array(list(trial["weights"].values()))
    ml_eval = ml.evaluate((X @ w).reshape(len(test), 6), test)
    base_eval = ml.evaluate(baseline_scores(train, test), test)
    adopted = ml_eval["tri_logp"] > base_eval["tri_logp"]
    print("検証（AI学習モデル）", json.dumps(ml_eval, ensure_ascii=False))
    print("検証（簡易モデル）  ", json.dumps(base_eval, ensure_ascii=False))
    print("採用" if adopted else "不採用（簡易モデルのまま）")

    final = ml.fit(rows) if adopted else trial
    out = dict(
        version=1, trained_at=now.strftime("%Y-%m-%d %H:%M"), adopted=adopted,
        data=dict(since=rows[0]["d"], until=rows[-1]["d"], races=len(rows)),
        check=dict(since=test[0]["d"], until=test[-1]["d"], races=len(test), ml=ml_eval, base=base_eval),
        fit=final["fit"], weights=final["weights"],
    )
    (base / "model.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    top = sorted(((k, v) for k, v in final["weights"].items() if not k.startswith("V")), key=lambda kv: -abs(kv[1]))[:15]
    print("主な重み:", ", ".join(f"{k}={v:+.3f}" for k, v in top))
    if not all(math.isfinite(v) for v in final["weights"].values()):
        raise SystemExit("学習に失敗しました（重みが数値になりませんでした）")


if __name__ == "__main__":
    main()
