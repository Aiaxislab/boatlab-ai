"""1場12R分の冊子（HTML→PDF）を作る。"""

import datetime as dt
from pathlib import Path
from statistics import mean

from jinja2 import Environment, FileSystemLoader

from . import conditions, ml, model
from .venues import VENUES

ROOT = Path(__file__).resolve().parent.parent
ENV = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True)
WEEK = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
STRONG_WIND = 4


def _pct(p: float) -> str:
    return "<1%" if p < 0.005 else f"{p * 100:.0f}%"


def gloss(venue_name: str, lane1: float) -> str:
    items = [
        ("印", "◎本命 ○対抗 ▲穴 △押さえ。本誌の評価順。"),
        ("級別", "選手のランク。上からA1・A2・B1・B2の4段階。"),
        ("勝率", "1走の平均点（1着10点〜6着1点）。6点台は上位、4点台は下位。"),
        ("2連（2連対率）", "2着以内に入った割合。高いほど安定して上位に来る。"),
        ("全国／当地", f"全国すべての場での成績／このレース場（{venue_name}）だけの成績。"),
        ("モーター", "抽選で配られるエンジン。2連はその使用者の2着以内率で、性能の目安。"),
        ("ST", "スタートの速さ（秒）。0に近いほど速い。0より前はフライング（F）。"),
        ("コース", f"スタート時の並び。1コース（最も内側）が有利で、{venue_name}は1着率{lane1 * 100:.0f}%。"),
        ("今節", "今回の開催での成績。四角の数字が各レースの着順。"),
        ("3連単／3連複", "1〜3着を着順どおり当てる／順番は問わず当てる舟券。"),
        ("必要オッズ", "その倍率以上なら長い目で見て得になる目安（＝1÷確率）。"),
        ("確率", "本誌の計算による目安。展示・オッズ・直前の気象は入っていない。"),
    ]
    body = "".join(f"<div><b>{k}</b>　{v}</div>" for k, v in items)
    return f'<div class="gloss"><div class="t">初めての方へ：表の見方</div>{body}</div>'


def _runs(runs: list[dict], fallback: str) -> list[dict]:
    if not runs:
        return [dict(mark=c, cls="e", title="") for c in fallback]
    out = []
    for r in runs:
        mark = r["place"].lstrip("0") or r["place"]
        cls = "w" if r["wind"] >= STRONG_WIND else "e"
        out.append(dict(mark=mark[:1] if mark.isdigit() else mark[:1], cls=cls,
                        title=f"風 {r['wind_dir']} {r['wind']}m 波 {r['wave']}cm"))
    return out


def _why(race: dict, pred: dict, lanes: list[float], venue_name: str) -> list[dict]:
    boats = race["boats"]
    nat_rank = sorted(range(6), key=lambda i: -(boats[i]["nat_win"] or 0))
    motor_rank = sorted(range(6), key=lambda i: -(boats[i]["motor_2r"] or 0))
    sts = [b["st_avg"] for b in boats if b["st_avg"] is not None]
    out = []
    for i in [n - 1 for n in pred["order"]]:
        b = boats[i]
        facts = []  # (重要度, 見出し, 説明)
        nat, loc = b["nat_win"] or 0, b["loc_win"]
        r = nat_rank.index(i)
        if r == 0:
            facts.append((3, f"全国勝率{nat:.2f}はメンバー最上位。", f"{b['cls']}で地力は一番。"))
        elif r >= 4:
            facts.append((2, f"全国勝率{nat:.2f}はメンバー下位。", ""))
        if loc and loc - nat >= 0.8:
            facts.append((2, "当地は得意。", f"当地勝率{loc:.2f}は全国（{nat:.2f}）より高い。"))
        elif loc and nat - loc >= 0.8:
            facts.append((2, "当地は苦手。", f"当地勝率{loc:.2f}は全国（{nat:.2f}）より低い。"))
        if motor_rank.index(i) == 0 and (b["motor_2r"] or 0) >= 35:
            facts.append((2, f"モーターは今回一番（2連{b['motor_2r']:.1f}%）。", ""))
        runs = [x for x in b["runs_raw"] if x["place"].isdigit()]
        if len(runs) >= 3:
            top2 = sum(1 for x in runs if int(x["place"]) <= 2)
            if top2 >= len(runs) / 2:
                facts.append((3, f"今節は{len(runs)}走で2着以内が{top2}回と好調。", ""))
            elif top2 == 0:
                facts.append((2, f"今節は{len(runs)}走して2着以内なし。", ""))
        if b["st_avg"] is not None and len(sts) >= 3:
            if b["st_avg"] == min(sts):
                facts.append((2, f"今節のスタートはメンバー最速（平均{b['st_avg']:.2f}秒）。", ""))
            elif b["st_avg"] == max(sts):
                facts.append((1, "", f"今節のスタートは遅め（平均{b['st_avg']:.2f}秒）。"))
        lane = lanes[b["waku"] - 1]
        if b["waku"] == 1:
            facts.append((1, "", f"一番有利な1コース（{venue_name}の1着率{lane * 100:.0f}%）に入る予定。"))
        elif b["waku"] >= 5:
            facts.append((1, "", f"外の{b['waku']}コースは1着率{lane * 100:.0f}%と不利。"))
        facts.sort(key=lambda f: -f[0])
        main = next((f for f in facts if f[1]), (0, f"全国勝率{nat:.2f}。", ""))
        head = main[1]
        body = main[2] + "".join([f[1] + f[2] for f in facts if f is not main][:2])
        p1 = pred["place"][i][0]
        body += f"1着確率は約{_pct(p1)}、3着以内は約{_pct(pred['place'][i][2])}と見る。"
        out.append(dict(waku=b["waku"], head=f"{b['name']}：{head}", body=body))
    return out


def ai_env(fc: dict | None, td: dict | None, yest: list[dict], hhmm: str) -> dict:
    """AI学習モデルに渡す、そのレースの風・波・潮。朝の時点の情報なので、風は気象庁予報の文章からの目安、
    波は前日のその場の平均（学習では実際の値を使っている）。"""
    wind, wdir = ml.wind_from_forecast(fc.get("wind", "") if fc else "")
    env = dict(wind=wind, dir=wdir, wave=round(mean(r["wave"] for r in yest)) if yest else None)
    t = conditions.tide_at(td, hhmm)
    if t and t["trend"] in ml.TIDE_TRENDS:
        env.update(tide=t["trend"], tide_dev=round(t["level"] - sum(td["hourly"]) / 24))
    return env


def venue_context(jcd: str, prog: dict, day: dt.date, history: dict, generated: str, n_buy: int = 6,
                  ai: dict | None = None) -> dict:
    v = VENUES[jcd]
    lanes, n_lane = model.lane_rates(history, jcd)
    runs = model.series_runs(history, jcd, day, prog["day"])
    fc = conditions.forecast(v["office"], v["area"], day)
    td = conditions.tide_day(v["tide"][0], day) if v["tide"] else None
    yest = history.get((day - dt.timedelta(days=1)).isoformat(), {}).get(jcd, {}).get("races", [])
    cond = dict(area=fc.get("area_name", "") if fc else "", weather=conditions.compact(fc["weather"]) if fc else "",
                weather_short=conditions.short_weather(fc["weather"]) if fc else "",
                wind=conditions.compact(fc.get("wind", "")) if fc else "",
                wind_short=(fc.get("wind", "").split(" 海上")[0].split(" ")[0] if fc else ""),
                area_full=f"{fc['office'].replace('地方気象台', '').replace('管区気象台', '').replace('気象台', '')}・{fc.get('area_name', '')}" if fc else "",
                tide_name=v["tide"][1] if td else None)
    if yest:
        winds = [r["wind"] for r in yest]
        last = yest[-1]
        cond["yesterday"] = f"風 {last['wind_dir']}{last['wind']}m・波{last['wave']}cm"
        cond["yesterday_sub"] = f"最終レース時点。前日の風は{min(winds)}〜{max(winds)}m"
    races = []
    for race in prog["races"]:
        for b in race["boats"]:
            b["runs_raw"] = runs.get(b["toban"], [])
            st = [x["st"] for x in b["runs_raw"] if x["st"] is not None]
            b["st_avg"] = mean(st) if st else None
            b["runs"] = _runs(b["runs_raw"], b["series"])
        ai_scores = None
        if ai:
            boats = sorted(race["boats"], key=lambda b: b["waku"])
            feats = ml.boat_features(boats, jcd, [b["st_avg"] for b in boats], ai_env(fc, td, yest, race["deadline"]))
            by_waku = dict(zip([b["waku"] for b in boats], ml.scores(ai, feats)))
            ai_scores = [by_waku[b["waku"]] for b in race["boats"]]
        pred = model.predict(race, lanes, runs, n_buy=n_buy, scores=ai_scores)
        for b, (p1, p2, p3) in zip(race["boats"], pred["place"]):
            b.update(p1=_pct(p1), p2=_pct(p2), p3=_pct(p3))
        race = dict(race, pred=pred, pop_pct=conditions.pop_at(fc, race["deadline"]),
                    tide=conditions.tide_at(td, race["deadline"]))
        race["why"] = _why(race, pred, lanes, v["name"])
        races.append(race)
    lane_note = f"直近{n_lane}レース＋全国平均で補正" if n_lane else "全国平均（今後は場ごとの集計）"
    if ai:
        model_note = (f"予測はAI学習モデル（過去{ai['data']['races']:,}レースの結果から重みを学習。{ai['trained_at'][:10]}更新）で、"
                      "勝率・当地勝率・モーター・ボート・級別・年齢・体重・今節のスタート・場ごとのコースの有利さに加え、"
                      "気象庁予報の風と潮位表の潮を使って計算しています。")
    else:
        model_note = "予測は勝率・当地勝率・モーター・コース別1着率・今節のスタートから計算し、風・潮は使っていません。"
    return dict(date=day, weekday=WEEK[day.weekday()], generated=generated, n_buy=n_buy, model_note=model_note,
                venue=dict(name=v["name"], title=prog["title"], day=prog["day"]),
                races=races, cond=cond, lanes=lanes, lane_note=lane_note,
                gloss=gloss(v["name"], lanes[0]),
                css=(ROOT / "templates" / "style.css").read_text(encoding="utf-8"))


def render_html(ctx: dict) -> str:
    from markupsafe import Markup
    ctx = dict(ctx, gloss=Markup(ctx["gloss"]), css=Markup(ctx["css"]))
    return ENV.get_template("venue.html.j2").render(**ctx)


def html_to_pdf(pairs: list[tuple[Path, Path]]) -> None:
    """(HTML, PDF) の組をまとめてPDFにする（Node.js の Playwright を使う）。"""
    import json
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([[str(h.resolve()), str(p.resolve())] for h, p in pairs], f)
    subprocess.run(["node", str(ROOT / "scripts" / "pdf.mjs"), f.name], check=True)
