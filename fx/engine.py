"""判定エンジン。特徴量を解釈テーブル（fx/knowledge/interpretation_table.json）に当てて説明文を集め、
総合判断ルール（composite_rules.json）で方向・確信度・転換リスク・マジョリティリバース（MR）を決める。AIは使わない。
"""

from __future__ import annotations

import ast
import json
import math
import operator
from pathlib import Path

from .config import TF_LABEL, TF_ORDER

KNOW = Path(__file__).parent / "knowledge"
TABLE = json.loads((KNOW / "interpretation_table.json").read_text(encoding="utf-8"))
COMP = json.loads((KNOW / "composite_rules.json").read_text(encoding="utf-8"))
EW = TABLE["evidence_weight"]
SCORED = {"direction", "mr", "timing", "crowd"}
WEIGHTS = COMP["step2_daily_direction"]["tf_weights"]

PRICE_KEYS = {"close", "ema20", "ema50", "sma200", "bb_upper", "bb_lower", "bb_mid", "last_swing_high", "last_swing_low",
              "nearest_res", "nearest_sup", "pdh", "pdl", "round_level", "asia_high", "asia_low",
              "crowd_avg_long", "crowd_avg_short", "stop_zone_from", "stop_zone_to", "price"}

# ---- 安全な式の評価（比較・and/or/not・in・abs・四則演算だけ） ----
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_PARSED: dict[str, ast.Expression] = {}


class _Missing(Exception):
    pass


def evaluate(expr: str, env: dict) -> bool:
    def ev(n):
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(ev(v) for v in n.values)
            return any(ev(v) for v in n.values)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return (not v) if isinstance(n.op, ast.Not) else -v
        if isinstance(n, ast.BinOp):
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.Name):
            v = env.get(n.id)
            if v is None:
                raise _Missing(n.id)
            return v
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.List):
            return [ev(e) for e in n.elts]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "abs":
            return abs(ev(n.args[0]))
        raise ValueError(f"許可されていない式です: {expr}")

    tree = _PARSED.get(expr) or _PARSED.setdefault(expr, ast.parse(expr, mode="eval"))
    try:
        return bool(ev(tree.body))
    except _Missing:
        return False  # データが無いルールは「一致しない」


def fmt(v, key: str, pip: float) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if key in PRICE_KEYS:
        return f"{v:,.{max(0, round(-math.log10(pip)))}f}"
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}".rstrip("0").rstrip(".")


def render(text: str, env: dict, pip: float) -> str:
    class D(dict):
        def __missing__(self, k):
            return "?"
    return text.format_map(D({k: fmt(v, k, pip) for k, v in env.items()}))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _sign(x: float) -> int:
    return 1 if x >= 15 else -1 if x <= -15 else 0


def judge(sym: dict, tfs: dict, g: dict) -> dict:
    """1銘柄の判定。tfs は足ごとの特徴量、g は銘柄全体の特徴量（context.global_features）"""
    pip = sym["pip"]
    matched: list[tuple[dict, dict, str | None]] = []
    scores: dict[str, float] = {}

    for tf in TF_ORDER:
        f = tfs.get(tf) or {}
        if not f:
            continue
        env = dict(f, tf_label=TF_LABEL[tf])
        s = 0.0
        for r in TABLE["rules"]:
            if r["scope"] == "tf" and tf in r["tfs"] and evaluate(r["when"], env):
                matched.append((r, env, tf))
                if r["kind"] in SCORED:
                    s += r["bias"] * EW[r["evidence"]]
        scores[tf] = clamp(10 * s, -100, 100)
    dirs = {tf: _sign(s) for tf, s in scores.items()}

    g = dict(g)
    g["align_up"] = sum(1 for d in dirs.values() if d == 1)
    g["align_down"] = sum(1 for d in dirs.values() if d == -1)
    if "D" in scores and "4h" in scores:
        g["htf_dir"] = _sign((scores["D"] + scores["4h"]) / 2)
    ltf = [scores[k] for k in ("1h", "15m", "5m") if k in scores]
    if ltf:
        g["ltf_dir"] = _sign(sum(ltf) / len(ltf))
    g["dir_D"], g["dir_4h"] = dirs.get("D"), dirs.get("4h")

    gs = 0.0
    for r in TABLE["rules"]:
        if r["scope"] == "global" and evaluate(r["when"], g):
            matched.append((r, g, None))
            if r["kind"] in SCORED:
                gs += r["bias"] * EW[r["evidence"]]

    # 1日の方向
    ds = clamp(sum(WEIGHTS[tf] * scores.get(tf, 0) for tf in WEIGHTS) + 6 * gs, -100, 100)
    dsym = next(s for s in COMP["step2_daily_direction"]["symbols"] if s["min"] <= ds <= s["max"])
    dsign = _sign(ds)

    # 確信度
    conf = 50
    agree = sum(1 for tf in ("D", "4h", "1h") if dsign and dirs.get(tf) == dsign)
    conf += 20 if agree == 3 else 5 if agree == 2 else -10
    adx4h = (tfs.get("4h") or {}).get("adx14")
    if adx4h is not None:
        conf += 10 if adx4h >= 25 else -10 if adx4h < 20 else 0
    if g.get("events_known"):
        if g["events_today_high"] >= 2 or g.get("cb_event_today"):
            conf -= 15
        elif g["events_today_high"] == 1:
            conf -= 8
    if ((tfs.get("1h") or {}).get("atr_pctile") or 0) >= 90:
        conf -= 5
    cl = g.get("crowd_long_pct")
    crowd_side = 0 if cl is None else 1 if cl >= 65 else -1 if cl <= 35 else 0
    if dsign and crowd_side:
        conf += 5 if crowd_side == -dsign else -5
    conf = clamp(conf, 20, 90)
    stars = next(s for s in COMP["step3_confidence"]["stars"] if conf >= s["min"])

    # 時間による転換リスク
    rc, rcw = [], 0
    if g.get("events_known") and 0 < g.get("mins_to_next_high_event", 9999) < 9999:
        rc.append(f"⚡{g['next_event_time']} {g['next_event_name']}：発表を境に方向が変わる可能性。")
        rcw += 3
    if g.get("asia_range_ratio") is not None and g["asia_range_ratio"] < 0.6 and g.get("session") in ("tokyo", "pre_london"):
        rc.append(f"⏰{g['london_open_time']} ロンドンオープン：東京レンジ（{fmt(g['asia_low'], 'asia_low', pip)}〜{fmt(g['asia_high'], 'asia_high', pip)}）のブレイクで1日の方向が決まりやすい。")
        rcw += 2
    h1 = tfs.get("1h") or {}
    if g.get("htf_dir") and (h1.get("rsi_div") == -g["htf_dir"] or h1.get("macd_div") == -g["htf_dir"]):
        rc.append("↺ 1時間足でダイバージェンス。上位足の流れが一服し、反転・調整に移る可能性。")
        rcw += 2
    if (g.get("adr_used") or 0) >= 100:
        rc.append("🔚 すでに1日分の値幅を消化。ここから先は反転か停滞が起きやすい。")
        rcw += 1
    if g.get("is_friday"):
        rc.append("⏰ 金曜NY後半：週末前の手仕舞いで流れが巻き戻されやすい。")
        rcw += 1
    rcl = next(lv for lv in COMP["step4_regime_change"]["levels"] if rcw >= lv["min"])

    mr = _majority_reverse(tfs, g, dirs, adx4h, pip)

    # 説明文：同じルールが複数の足で一致したら1行にまとめる（値は一番上位の足のもの）
    groups: dict[str, tuple[dict, list]] = {}
    for r, env, tf in matched:
        groups.setdefault(r["id"], (r, []))[1].append((tf, env))
    lines = []
    for r, hits in groups.values():
        hits.sort(key=lambda h: TF_ORDER.index(h[0]) if h[0] else -1)
        env = hits[0][1]
        if hits[0][0]:
            env = dict(env, tf_label="・".join(TF_LABEL[tf] for tf, _ in hits))
        lines.append((r, render(r["text"], env, pip)))
    lines.sort(key=lambda x: -x[0]["priority"])

    return {
        "symbol": sym["name"], "kind": sym["kind"], "price": g["price"], "price_text": fmt(g["price"], "price", pip),
        "ds": round(ds), "dir": dsign, "dir_symbol": dsym["symbol"], "dir_label": dsym["label"], "dir_advice": dsym["advice"],
        "confidence": conf, "stars": stars["symbol"],
        "rc_level": rcl["label"], "rc_symbol": rcl["symbol"], "rc_lines": rc,
        "mr": mr,
        "reasons": [t for r, t in lines if r["kind"] != "risk"][:10],
        "risks": [t for r, t in lines if r["kind"] == "risk"],
        "tf_scores": {tf: round(scores[tf]) for tf in TF_ORDER if tf in scores},
        "rules": sorted({r["id"]: r["bias"] for r, _, _ in matched}.items()),
        "events": g.get("events_list", []),
        "crowd": {k: g.get(k) for k in ("crowd_long_pct", "crowd_source", "crowd_avg_long", "crowd_avg_short")} if cl is not None else None,
        "atr1h": g.get("atr1h"), "adr20": g.get("adr20"),
    }


def _majority_reverse(tfs: dict, g: dict, dirs: dict, adx4h: float | None, pip: float) -> dict:
    cl = g.get("crowd_long_pct")
    if cl is None:
        return {"symbol": "－", "score": 0, "dir": 0, "line": "－ 大衆のポジションのデータが取れませんでした"}
    skew = max(cl, 100 - cl)
    if skew < 60:
        return {"symbol": "－", "score": 0, "dir": 0, "line": f"－ 大衆の偏りなし（買い{cl:g}%）"}
    major = 1 if cl >= 60 else -1
    mr = -major
    pts = 15 if skew < 65 else 25 if skew < 70 else 32 if skew < 75 else 38 if skew < 80 else 42
    uw = g.get("crowd_underwater_atr")
    if uw is not None:
        pts += 15 if uw >= 1.5 else 8 if uw >= 0.5 else -5 if uw < 0 else 0
    zd = g.get("stop_zone_dist_atr")
    if zd is not None:
        pts += 15 if zd <= 1 else 8 if zd <= 2 else 0
    trend = g.get("htf_dir", 0)
    kind = "T型：トレンド方向への刈り取り" if trend == mr else "R型：反転の刈り取り"
    if trend == mr:
        pts += 15
    elif trend == 0:
        pts += 5
    elif adx4h is not None:
        pts += -35 if adx4h >= 35 else -20 if adx4h >= 25 else 0
    for tf in ("15m", "1h"):
        f = tfs.get(tf) or {}
        if f.get("bos") == mr:
            pts += 10
        if f.get("fake_break") == mr:
            pts += 10
        if f.get("rsi_div") == mr or f.get("macd_div") == mr:
            pts += 5
    chg, pchg = g.get("crowd_long_pct_chg24"), g.get("price_chg24_atr")
    if chg is not None and pchg is not None and ((major == 1 and chg >= 8 and pchg <= -1) or (major == -1 and chg <= -8 and pchg >= 1)):
        pts += 5
    if 0 < g.get("mins_to_next_high_event", 9999) <= 240:
        pts += 5
    score = clamp(pts, 0, 95)
    sym = next(r for r in COMP["step5_majority_reverse"]["rating"] if score >= r["min"])["symbol"]
    note = ""
    if dirs.get("D") == -mr and dirs.get("4h") == -mr and (adx4h or 0) >= 30:
        sym, note = "×", "（強いトレンドに逆らうため見送り）"
    if (0 < g.get("mins_to_next_high_event", 9999) <= 30 or 0 <= g.get("mins_since_last_high_event", 9999) <= 15) and sym == "◯":
        sym, note = "△", "（指標の前後なので形を確認してから）"
    d = "下" if mr == -1 else "上"
    side = "買い" if major == 1 else "売り"
    out = {"symbol": sym, "score": score, "dir": mr, "dir_label": d, "type": kind, "note": note}
    line = f"{sym} {score}% {d}方向（{kind}）{note}"
    if g.get("stop_zone_from") is not None:
        out["zone_from"], out["zone_to"] = g["stop_zone_from"], g["stop_zone_to"]
        line += f"｜発生しやすい水準：{fmt(g['stop_zone_from'], 'price', pip)}〜{fmt(g['stop_zone_to'], 'price', pip)}（多数派の{side}の損切りが集まりやすい所）"
    out["line"] = line
    return out


def message(j: dict) -> str:
    """1銘柄分の文面（メール・共有ページ・ダッシュボード共通）"""
    mr = j["mr"]
    head = f"【{j['symbol']}】{j['price_text']}　{j['dir_symbol']} {j['dir_label']}（確信度 {j['stars']} {j['confidence']}%）｜転換リスク {j['rc_symbol']}{j['rc_level']}｜MR {mr['symbol']}"
    if mr.get("score"):
        head += f" {mr['score']}% {mr.get('dir_label', '')}"
    lines = [head, f"■ 今日の見立て：{j['dir_advice']}（総合スコア {j['ds']:+d}）",
             "■ 時間で変わる可能性：" + (" / ".join(j["rc_lines"]) if j["rc_lines"] else "特になし"),
             f"■ マジョリティリバース：{mr['line']}", "■ 根拠（重要な順）："]
    lines += [f"  ・{t}" for t in j["reasons"]]
    if j["risks"]:
        lines += ["■ 注意："] + [f"  ・{t}" for t in j["risks"]]
    lines.append("■ 足ごとのスコア：" + "  ".join(f"{TF_LABEL[tf]} {s:+d}" for tf, s in j["tf_scores"].items()))
    return "\n".join(lines)
