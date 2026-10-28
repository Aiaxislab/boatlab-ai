"""毎朝8:30の判定の答え合わせと、的中率の集計（fx/knowledge/evaluation_rules.json）。

判定は R2 の admin/fx/pending.json に置いておき、判定の期間（為替・金は翌朝のNYクローズ、BTCは24時間）が
終わってから、その間の値動きで採点して admin/fx/scores.csv に1行ずつ足す。採点した行は書き換えない。
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from .sources import Bars

COLS = ["日付", "銘柄", "判定時刻", "価格", "方向", "総合スコア", "確信度", "星", "結果", "値動き（ADR比）", "実戦",
        "MR記号", "MR点数", "MR方向", "MRゾーン到達", "MR結果", "前日と同じ", "20EMAの向き", "ルール"]
DIR_NAME = {1: "上", -1: "下", 0: "レンジ"}


def _dir_result(d: int, move: float) -> str:
    if move * d >= 0.10:
        return "的中"
    if move * d <= -0.10:
        return "外れ"
    return "引き分け"


def grade(p: dict, path: Bars) -> dict | None:
    """p は判定の記録、path は判定時刻から期間の終わりまでの足。足りなければ None（まだ採点しない）"""
    if not len(path) or path.t[-1] < p["end"] - 2 * 3600:
        return None
    adr, price = p["adr20"], p["price"]
    move = (float(path.c[-1]) - price) / adr
    d = p["dir"]
    if d:
        result = _dir_result(d, move)
    else:
        result = "的中" if (path.h.max() - path.l.min()) < 0.8 * adr and abs(move) < 0.25 else "外れ"

    # 実戦向け：予想した方向に0.5ADR進むのが先か、逆に0.5ADR進むのが先か
    real = "－"
    if d:
        real = "どちらも届かず"
        for h, l in zip(path.h, path.l):
            fav = (h - price) if d == 1 else (price - l)
            adv = (price - l) if d == 1 else (h - price)
            if fav >= 0.5 * adr and adv >= 0.5 * adr:
                real = "同じ足で両方（判定不能）"
                break
            if fav >= 0.5 * adr:
                real = "勝ち"
                break
            if adv >= 0.5 * adr:
                real = "負け"
                break

    mr = p.get("mr") or {}
    reached, mr_result = "－", "－"
    if mr.get("dir") and mr.get("zone_from") is not None:
        z, md = mr["zone_from"], mr["dir"]
        hit = [i for i in range(len(path)) if (path.l[i] <= z if md == -1 else path.h[i] >= z)]
        if not hit:
            reached = "未到達"
        else:
            reached, mr_result = "到達", "未決着"
            for i in range(hit[0], len(path)):
                go = (z - path.l[i]) if md == -1 else (path.h[i] - z)
                back = (path.h[i] - z) if md == -1 else (z - path.l[i])
                if i > hit[0] and back >= 0.3 * adr:
                    mr_result = "失敗"
                    break
                if go >= 0.5 * adr:
                    mr_result = "成功"
                    break

    base = lambda b: _dir_result(b, move) if b else "－"
    return {
        "日付": p["date"], "銘柄": p["symbol"], "判定時刻": p["time"], "価格": p["price_text"],
        "方向": DIR_NAME[d], "総合スコア": p["ds"], "確信度": p["confidence"], "星": p["stars"],
        "結果": result, "値動き（ADR比）": f"{move:+.2f}", "実戦": real,
        "MR記号": mr.get("symbol", "－"), "MR点数": mr.get("score", 0), "MR方向": DIR_NAME.get(mr.get("dir", 0), "－") if mr.get("dir") else "－",
        "MRゾーン到達": reached, "MR結果": mr_result,
        "前日と同じ": base(p.get("base_prev", 0)), "20EMAの向き": base(p.get("base_ema", 0)),
        "ルール": " ".join(f"{rid}:{b:+g}" for rid, b in p.get("rules", [])),
    }


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _rate(hit: int, miss: int) -> float | None:
    return round(hit / (hit + miss) * 100, 1) if hit + miss else None


def _tally(rows: list[dict], key: str = "結果") -> dict:
    c = defaultdict(int)
    for r in rows:
        c[r[key]] += 1
    return {"n": c["的中"] + c["外れ"], "hit": c["的中"], "miss": c["外れ"], "draw": c["引き分け"], "rate": _rate(c["的中"], c["外れ"])}


def stats(rows: list[dict], today: dt.date) -> dict:
    def block(rs: list[dict]) -> dict:
        out = {
            "overall": _tally(rs),
            "baseline_prev": _tally([r for r in rs if r["前日と同じ"] != "－"], "前日と同じ"),
            "baseline_ema": _tally([r for r in rs if r["20EMAの向き"] != "－"], "20EMAの向き"),
            "by_stars": {s: _tally([r for r in rs if r["星"] == s]) for s in ("★★★", "★★", "★")},
            "by_dir": {d: _tally([r for r in rs if r["方向"] == d]) for d in ("上", "下", "レンジ")},
            "by_symbol": {},
            "real": {k: sum(1 for r in rs if r["実戦"] == k) for k in ("勝ち", "負け", "どちらも届かず")},
            "mr": {},
        }
        for sym in sorted({r["銘柄"] for r in rs}):
            out["by_symbol"][sym] = _tally([r for r in rs if r["銘柄"] == sym])
        for s in ("◯", "△", "×"):
            m = [r for r in rs if r["MR記号"] == s]
            out["mr"][s] = {"n": len(m), "reached": sum(1 for r in m if r["MRゾーン到達"] == "到達"),
                            "success": sum(1 for r in m if r["MR結果"] == "成功"), "fail": sum(1 for r in m if r["MR結果"] == "失敗")}
        return out

    since = (today - dt.timedelta(days=30)).isoformat()
    recent = [r for r in rows if r["日付"] >= since]

    # ルールごと：方向点の向きと、実際の値動き（±0.1ADR以上）の向きが合った割合
    rule = defaultdict(lambda: [0, 0])
    for r in rows:
        mv = float(r["値動き（ADR比）"])
        if abs(mv) < 0.10:
            continue
        for item in r["ルール"].split():
            rid, b = item.split(":")
            b = float(b)
            if b:
                rule[rid][0] += 1
                rule[rid][1] += int((b > 0) == (mv > 0))
    rules = sorted(({"id": k, "n": n, "agree": a, "rate": _rate(a, n - a)} for k, (n, a) in rule.items()), key=lambda x: -x["n"])

    cal = defaultdict(lambda: {"hit": 0, "miss": 0, "draw": 0})
    for r in recent:
        cal[r["日付"]][{"的中": "hit", "外れ": "miss"}.get(r["結果"], "draw")] += 1

    return {"updated": today.isoformat(), "days": len({r["日付"] for r in rows}), "rows": len(rows),
            "last30": block(recent), "all": block(rows), "rules": rules, "calendar": dict(sorted(cal.items())),
            "latest": rows[-40:][::-1]}
