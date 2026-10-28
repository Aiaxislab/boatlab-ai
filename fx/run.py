"""FXの判定の入口（python -m fx.run --mode morning|intraday）。GitHub Actions の「FXの判定」から動く。

morning : 毎朝8:30（日本時間）の定時判定。記録して翌朝以降に採点し、メール（Worker が送る）の文面も作る
intraday: 日中の途中経過（2時間おき）。ダッシュボード用で、採点はしない

前回までの記録（pending.json・scores.csv・crowd.json）は、ワークフローが R2 から --state に取ってくる。
できたファイルも --state に書き、ワークフローが R2 に戻す。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np

from . import sources
from .config import SWING_K, SYMBOLS
from .context import JST, _day_start, global_features, jst, market_open
from .engine import judge, message
from .grade import grade, read_csv, stats, write_csv
from .indicators import resample, resample_ny, resample_utc_hours, tf_features
from .sources import Bars

DISCLAIMER = "※ 確信度とMRの％は点数で、勝率の保証ではありません。投資の判断はご自身で行ってください。"


def complete(b: Bars, sec: int, t: float) -> Bars:
    """t の時点で終わっている足だけ"""
    return b.until(t - sec + 1)


def fetch(sym: dict) -> dict | None:
    if sym["kind"] == "crypto":
        p = sym["cb"]
        d = {"1m": sources.coinbase(p, 60), "5m": sources.coinbase(p, 300, pages=2), "15m": sources.coinbase(p, 900),
             "1h": sources.coinbase(p, 3600, pages=4), "D": sources.coinbase(p, 86400)}
        if any(v is None for v in d.values()):
            return None
        d["base"] = d["5m"]  # 採点・値幅には5分足を使う（1分足は300本しか取れないため）
        return d
    m1 = sources.twelvedata(sym["td"], "1min")
    h1 = sources.twelvedata(sym["td"], "1h")
    if m1 is None or h1 is None:
        return None
    return {"1m": m1, "1h": h1, "base": m1}


def timeframes(sym: dict, raw: dict, t: float) -> tuple[dict, dict]:
    """足ごとの確定足（t の時点）と、銘柄全体の計算に使う足"""
    tf: dict[str, Bars] = {}
    if sym["kind"] == "crypto":
        tf["1m"] = complete(raw["1m"], 60, t)
        tf["5m"] = complete(raw["5m"], 300, t)
        tf["10m"] = complete(resample(raw["5m"].until(t), 10), 600, t)
        tf["15m"] = complete(raw["15m"], 900, t)
        tf["1h"] = complete(raw["1h"], 3600, t)
        tf["4h"] = complete(resample_utc_hours(raw["1h"].until(t), 4), 4 * 3600, t)
        tf["D"] = complete(raw["D"], 86400, t)
        daily = raw["D"].until(t)
    else:
        m1 = raw["1m"].until(t)
        h1 = raw["1h"].until(t)
        tf["1m"] = complete(m1, 60, t)
        for n in (5, 10, 15):
            tf[f"{n}m"] = complete(resample(m1, n), n * 60, t)
        tf["1h"] = complete(h1, 3600, t)
        h4 = resample_ny(h1, 4)
        tf["4h"] = h4.until(t - 4 * 3600 + 1)
        daily = resample_ny(h1, 24)
        tf["D"] = daily.until(_day_start(sym["kind"], t))
    ctx = {"base": raw["base"].until(t), "1h": raw["1h"].until(t), "D": daily}
    return tf, ctx


def crowd_at(history: list[dict], t: float, name: str) -> dict | None:
    """24時間前（±3時間）の大衆比率"""
    best = None
    for h in history:
        if abs(h["t"] - (t - 86400)) <= 3 * 3600 and name in h["data"]:
            if best is None or abs(h["t"] - (t - 86400)) < abs(best["t"] - (t - 86400)):
                best = h
    return best["data"][name] if best else None


def mail_text(kind: str, when: str, results: list[dict], skipped: list[dict]) -> tuple[str, str]:
    title = "毎朝8:30の見解" if kind == "morning" else "いまの見解"
    top = results[0] if results else None
    subject = f"【FX】{when} {title}" + (f"｜注目 {top['symbol']} {top['dir_symbol']}{top['dir_label']}" if top else "")
    lines = [f"{when} 時点の{title}です（地合いの良い順）。", ""]
    for r in results:
        mr = r["mr"]
        lines.append(f"{r['symbol']:<7} {r['dir_symbol']:<2} {r['dir_label']:<4} 確信度{r['confidence']}%  転換{r['rc_level']}  MR {mr['symbol']}{(' ' + str(mr['score']) + '%') if mr.get('score') else ''}")
    for s in skipped:
        lines.append(f"{s['symbol']:<7} －  {s['reason']}")
    lines += ["", "─" * 20, ""]
    for r in results:
        lines += [r["message"], ""]
    lines.append(DISCLAIMER)
    return subject, "\n".join(lines)


def share_text(latest: dict, st: dict | None) -> str:
    """Claude に読ませるための要約（ログイン不要の非公開URLで出す）。取得した足データそのものは載せない"""
    out = ["# FX判断ログ（AI Axis Lab）", "",
           "このページは、毎朝8:30（日本時間）のFX判定と、その答え合わせの要約です。判定は事前に用意したルール表だけで作っています（AIは使っていません）。",
           f"最終更新：{latest['time']}（{'8:30の定時判定' if latest['kind'] == 'morning' else '途中経過'}）", ""]
    if st:
        a = st["last30"]
        o = a["overall"]
        out += ["## 的中率（直近30日）", "",
                f"- 方向の的中率：{o['rate'] if o['rate'] is not None else '－'}%（的中{o['hit']}・外れ{o['miss']}・引き分け{o['draw']}）",
                f"- 比較：前日と同じ方向 {a['baseline_prev']['rate'] or '－'}% ／ 日足20EMAの向き {a['baseline_ema']['rate'] or '－'}% ／ コイン投げ 50%",
                "- 確信度別：" + " ／ ".join(f"{k} {v['rate'] if v['rate'] is not None else '－'}%（{v['n']}件）" for k, v in a["by_stars"].items()),
                "- 銘柄別：" + " ／ ".join(f"{k} {v['rate'] if v['rate'] is not None else '－'}%（{v['n']}件）" for k, v in a["by_symbol"].items()),
                "- マジョリティリバース：" + " ／ ".join(f"{k} {v['n']}件中 到達{v['reached']}・成功{v['success']}・失敗{v['fail']}" for k, v in a["mr"].items()),
                f"- 実戦（0.5ADRの先着）：勝ち{a['real']['勝ち']}・負け{a['real']['負け']}・どちらも届かず{a['real']['どちらも届かず']}",
                f"- 記録した日数：{st['days']}日、{st['rows']}件", ""]
        out += ["## 直近の採点（新しい順）", "", "日付 | 銘柄 | 方向 | 確信度 | 結果 | 値動き(ADR比) | 実戦 | MR | MR結果 | 前日と同じ | 20EMA", "---|---|---|---|---|---|---|---|---|---|---"]
        for r in st["latest"]:
            out.append(f"{r['日付']} | {r['銘柄']} | {r['方向']} | {r['星']}{r['確信度']}% | {r['結果']} | {r['値動き（ADR比）']} | {r['実戦']} | {r['MR記号']}{'' if r['MR記号'] == '－' else r['MR点数']} | {r['MR結果']} | {r['前日と同じ']} | {r['20EMAの向き']}")
        weak = [x for x in st["rules"] if x["n"] >= 20 and x["rate"] is not None and x["rate"] < 45]
        if weak:
            out += ["", "## 効いていない可能性があるルール（20件以上で一致率45%未満）", ""] + [f"- {x['id']}：{x['rate']}%（{x['n']}件）" for x in weak[:10]]
        out.append("")
    out += ["## 最新の判定", ""]
    for r in latest["symbols"]:
        out += ["```", r["message"], "```", ""]
    for s in latest.get("skipped", []):
        out.append(f"- {s['symbol']}：{s['reason']}")
    out += ["", DISCLAIMER]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "intraday"], default="intraday")
    ap.add_argument("--state", default="out/fx")
    args = ap.parse_args()
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)

    now = time.time()
    today = jst(now).date()
    if args.mode == "morning":
        t = dt.datetime(today.year, today.month, today.day, 8, 30, tzinfo=JST).timestamp()
        if now < t:
            wait = min(t - now + 30, 30 * 60)
            print(f"8:30 になるまで {wait / 60:.0f} 分待ちます")
            time.sleep(wait)
            now = time.time()
    else:
        t = now - now % 60

    pending = json.loads((state / "pending.json").read_text(encoding="utf-8")) if (state / "pending.json").exists() else []
    history = json.loads((state / "crowd.json").read_text(encoding="utf-8")) if (state / "crowd.json").exists() else []
    rows = read_csv(state / "scores.csv")
    judge_file = state / "judge" / f"{today.isoformat()}.json"
    already = args.mode == "morning" and judge_file.exists()

    events = sources.calendar()
    if events is None:
        print("経済指標カレンダーが取れませんでした（指標のルールは使わずに判定します）")
    outlook = sources.myfxbook_outlook() or {}
    if "BTCUSD" not in outlook:
        btc = sources.btc_crowd()
        if btc:
            outlook["BTCUSD"] = btc
    print(f"大衆の比率：{len(outlook)}銘柄")

    results, skipped, new_pending, new_rows = [], [], [], []
    for sym in SYMBOLS:
        name = sym["name"]
        is_open = market_open(sym["kind"], t)
        due = [p for p in pending if p["symbol"] == name and p["end"] <= now]
        if not is_open and not due:
            skipped.append({"symbol": name, "reason": "市場が休み（土日）"})
            continue
        raw = fetch(sym)
        if raw is None:
            skipped.append({"symbol": name, "reason": "足データが取れませんでした"})
            print(f"{name}: 足データが取れませんでした")
            continue

        # 答え合わせ
        for p in due:
            g = grade(p, raw["base"].since(p["t"]).until(p["end"]))
            if g:
                new_rows.append(g)
                pending.remove(p)
                print(f"{name} {p['date']} を採点しました：{g['結果']}")
            elif now - p["end"] > 5 * 86400:
                pending.remove(p)
                print(f"{name} {p['date']} はデータが足りないので採点をあきらめました")

        if not is_open:
            skipped.append({"symbol": name, "reason": "市場が休み（土日）"})
            continue
        tfb, ctx = timeframes(sym, raw, t)
        if not len(ctx["base"]):
            skipped.append({"symbol": name, "reason": "判定時刻の足がありません"})
            continue
        tfs = {tf: tf_features(b, SWING_K[tf], sym["round"]) for tf, b in tfb.items()}
        g = global_features(sym, tfs, ctx, t, events, outlook.get(name), crowd_at(history, t, name))
        res = judge(sym, tfs, g)
        res["message"] = message(res)
        ev4h = 0 < g.get("mins_to_next_high_event", 9999) <= 240
        res["rank"] = round(abs(res["ds"]) * res["confidence"] / 100 * (1 - 0.3 * ev4h) * (1 - 0.2 * (g.get("session") == "rollover")), 1)
        results.append(res)
        print(f"{name}: {res['dir_symbol']} {res['dir_label']} 確信度{res['confidence']}% MR {res['mr']['symbol']}")

        if args.mode == "morning" and not already and res.get("adr20"):
            daily = tfb["D"]
            base_prev = int(np.sign(daily.c[-1] - daily.o[-1])) if len(daily) else 0
            slope = tfs.get("D", {}).get("ema20_slope_atr")
            new_pending.append({
                "date": today.isoformat(), "symbol": name, "t": t, "time": jst(t).strftime("%H:%M"),
                "end": _day_start(sym["kind"], t) + 86400 if sym["kind"] != "crypto" else t + 86400,
                "price": res["price"], "price_text": res["price_text"], "dir": res["dir"], "ds": res["ds"],
                "confidence": res["confidence"], "stars": res["stars"], "adr20": res["adr20"], "rules": [r for r in res["rules"] if r[1]],
                "mr": {k: res["mr"].get(k) for k in ("symbol", "score", "dir", "zone_from", "zone_to")},
                "base_prev": base_prev, "base_ema": 0 if slope is None else (1 if slope > 0 else -1 if slope < 0 else 0),
            })

    results.sort(key=lambda r: -r["rank"])
    when = jst(t).strftime("%-m/%-d %H:%M")
    subject, text = mail_text(args.mode, when, results, skipped)
    latest = {"kind": args.mode, "date": today.isoformat(), "time": jst(t).strftime("%Y-%m-%d %H:%M"),
              "generated": jst(time.time()).strftime("%Y-%m-%d %H:%M"), "delay_min": round((time.time() - t) / 60),
              "symbols": results, "skipped": skipped, "mail": {"subject": subject, "text": text}}

    rows += new_rows
    pending += new_pending
    if new_rows or not (state / "scores.csv").exists():
        write_csv(state / "scores.csv", rows)
    st = stats(rows, today) if rows else None

    history = [h for h in history if h["t"] > now - 3 * 86400] + ([{"t": t, "data": outlook}] if outlook else [])
    w = lambda name, obj: (state / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    w("latest.json", latest)
    w("pending.json", pending)
    w("crowd.json", history)
    if st:
        w("stats.json", st)
    if args.mode == "morning" and not already:
        judge_file.parent.mkdir(parents=True, exist_ok=True)
        judge_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
    elif already:
        print("今日の8:30の判定はもう記録してあるので、記録は書き換えません（latest.json だけ更新）")
    (state / "share.md").write_text(share_text(latest, st), encoding="utf-8")
    print(f"判定 {len(results)}銘柄、休み・取得失敗 {len(skipped)}銘柄、採点 {len(new_rows)}件")


if __name__ == "__main__":
    main()
