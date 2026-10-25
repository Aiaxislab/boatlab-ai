"""毎朝の処理: 前日までの結果の取り込み → 答え合わせ → 本日の全場PDF作成。

    python -m boatlab.daily [--date 2026-10-26]

state/ … 結果・予想・成績のデータ（ずっと残す。GitHubの data ブランチに保存）
out/   … サイトに載せるもの（PDFはR2で30日たつと自動で消える）
out/admin/ … 管理者ダッシュボード用のCSV。R2から取ってきたものに今回の分を足して戻す
"""

import argparse
import datetime as dt
import json
from pathlib import Path

from . import grade, ledger, render, sources
from .parse import parse_program, parse_results
from .venues import VENUES

JST = dt.timezone(dt.timedelta(hours=9))
STATE, OUT = Path("state"), Path("out")
HISTORY_DAYS = 90
ARCHIVE_DAYS = 30


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _deadline(day: dt.date, hhmm: str) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(int(hhmm[:2]), int(hhmm[3:5])), JST)


def update_history(today: dt.date, backfill: int) -> dict:
    """直近90日の競走成績を state/results に集める。足りない日だけ取りに行く。"""
    history, fetched = {}, 0
    for back in range(1, HISTORY_DAYS + 1):  # 新しい日から順に
        day = today - dt.timedelta(days=back)
        path = STATE / "results" / f"{day.isoformat()}.json"
        data = _load(path, None)
        incomplete = data is not None and data.get("_incomplete")
        if (data is None and fetched < backfill) or incomplete or back == 1 and data is None:
            text = sources.mbrace_text("K", day)
            fetched += 1
            if text:
                data = parse_results(text)
                if "全レース終了後に登録されます" in text:
                    data["_incomplete"] = True
                _save(path, data)
        if data:
            history[day.isoformat()] = {k: v for k, v in data.items() if not k.startswith("_")}
    return dict(sorted(history.items()))


def regrade(history: dict, generated: str) -> tuple[dict, list[dict]]:
    graded = []
    for path in sorted((STATE / "preds").glob("*.json")):
        preds = _load(path, None)
        if preds and preds["date"] in history:
            graded += grade.grade_day(preds, history[preds["date"]])
    stats = grade.build_stats(graded, generated)
    _save(STATE / "stats.json", stats)
    return stats, graded


def update_ledger(graded: list[dict]) -> None:
    """ダッシュボードのCSVに、答え合わせできたレースを足す。"""
    path = OUT / "admin" / "races.csv"
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    meta = _load(OUT / "admin" / "meta.json", {})
    text, added = ledger.merge(existing, graded, meta.get("from"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"ダッシュボードのCSVに {added}レース追加しました")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="対象日（YYYY-MM-DD）。省略すると今日（日本時間）")
    ap.add_argument("--backfill", type=int, default=HISTORY_DAYS, help="1回で取りに行く過去日数の上限")
    ap.add_argument("--venues", help="場コードをカンマ区切りで（試作用）")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now(JST)
    today = dt.date.fromisoformat(args.date) if args.date else now.date()
    generated = now.strftime("%m/%d %H:%M")

    history = update_history(today, args.backfill)
    stats, graded = regrade(history, generated)
    print(f"成績データ {len(history)}日分、答え合わせ {stats['total'].get('races', 0)}レース")
    update_ledger(graded)

    text = sources.mbrace_text("B", today)
    if not text:
        raise SystemExit(f"{today} の番組表がまだ公開されていません")
    program = parse_program(text)
    if args.venues:
        program = {k: v for k, v in program.items() if k in args.venues.split(",")}

    html_dir = OUT / "html"
    pdf_dir = OUT / "pdf" / today.isoformat()
    html_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    preds = dict(date=today.isoformat(), generated=generated, venues={})
    listing, pairs, picks = [], [], []
    for jcd in sorted(program):
        prog = program[jcd]
        if not prog["races"] or jcd not in VENUES:
            continue
        ctx = render.venue_context(jcd, prog, today, history, generated)
        html = html_dir / f"{jcd}.html"
        html.write_text(render.render_html(ctx), encoding="utf-8")
        pairs.append((html, pdf_dir / f"{jcd}.pdf"))
        # 締切を過ぎてから作った予想は、答え合わせに入れない（後出しにならないように）
        preds["venues"][jcd] = [dict(
            no=r["no"], deadline=r["deadline"], order=r["pred"]["order"],
            top5=[list(c) for c, _ in r["pred"]["top5"]], buy=[list(c) for c in r["pred"]["buy"]],
            trio=[list(c) for c in r["pred"]["trio"]], buy_prob=round(r["pred"]["buy_prob"], 4),
        ) for r in ctx["races"] if now < _deadline(today, r["deadline"])]
        # 毎朝のメール用（締切前のレースだけ。どこまでの確率を送るかは Worker 側で決める）
        picks += [dict(
            jcd=jcd, venue=VENUES[jcd]["name"], no=r["no"], deadline=r["deadline"], kind=r["kind"],
            honmei=r["pred"]["order"][0], honmei_name=r["boats"][r["pred"]["order"][0] - 1]["name"],
            buy=[dict(combo=list(c), prob=round(p, 4)) for c, p in zip(r["pred"]["buy"], r["pred"]["buy_each"])],
            buy_prob=round(r["pred"]["buy_prob"], 4), trio=[list(c) for c in r["pred"]["trio"]],
            trio_prob=round(r["pred"]["trio_prob"], 4), pdf=f"pdf/{today.isoformat()}/{jcd}.pdf",
        ) for r in ctx["races"] if now < _deadline(today, r["deadline"])]
        listing.append(dict(
            jcd=jcd, name=VENUES[jcd]["name"], title=prog["title"], day=prog["day"],
            pdf=f"pdf/{today.isoformat()}/{jcd}.pdf",
            first=ctx["races"][0]["deadline"], last=ctx["races"][-1]["deadline"],
            races=[dict(no=r["no"], deadline=r["deadline"], kind=r["kind"],
                        honmei=r["pred"]["order"][0], honmei_name=r["boats"][r["pred"]["order"][0] - 1]["name"],
                        buy_prob=round(r["pred"]["buy_prob"] * 100)) for r in ctx["races"]],
        ))
    if not listing:
        raise SystemExit(f"{today} の番組表を読み取れませんでした（開催場が0）")
    if not args.no_pdf:
        render.html_to_pdf(pairs)
    # 予想は結果が出る前に保存する（あとで答え合わせに使う）
    pred_path = STATE / "preds" / f"{today.isoformat()}.json"
    if not pred_path.exists():
        _save(pred_path, preds)

    today_json = dict(date=today.isoformat(), generated=generated, venues=listing)
    archive = [a for a in _load(STATE / "archive.json", []) if a["date"] != today.isoformat()]
    archive = sorted(archive + [dict(date=today.isoformat(), venues=[dict(jcd=v["jcd"], name=v["name"], pdf=v["pdf"]) for v in listing])],
                     key=lambda a: a["date"], reverse=True)[:ARCHIVE_DAYS]
    _save(STATE / "archive.json", archive)
    _save(OUT / "data" / "today.json", today_json)
    _save(OUT / "data" / "archive.json", archive)
    _save(OUT / "admin" / "picks.json", dict(date=today.isoformat(), generated=generated, races=picks))
    print(f"{today}: {len(listing)}場のPDFを作成しました")


if __name__ == "__main__":
    main()
