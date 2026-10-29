"""BoatLab AI 2（お買い得舟券）用のデータ。

毎朝: 全レースの 3連単120通り・3連複20通りのAI確率を out/admin/ai2/ に書き出す。
      締切5分前に Worker（worker/ai2/ai2.js）がオッズと比べて、期待値の高い舟券を選ぶ。
      schedule.json … 今日のレース一覧（締切順）。Worker が1分ごとに読む小さいファイル
      probs/{場コード}.json … その場の全レースの確率（並びは TRI_ORDER・TRIO_ORDER。Worker と同じ）
翌朝: Worker がレース直後に結果を取れなかった行（log.csv の「結果」が空欄）を、公式の成績ファイルで埋める。
"""

import csv
import io
import itertools
import json
from pathlib import Path

TRI_ORDER = list(itertools.permutations(range(1, 7), 3))  # 1-2-3, 1-2-4, … 6-5-4
TRIO_ORDER = list(itertools.combinations(range(1, 7), 3))  # 1=2=3, 1=2=4, … 4=5=6
LOG_COLUMNS = ["日付", "場コード", "会場", "レース", "締切", "式別", "組番", "AI確率", "オッズ（5分前）",
               "期待値", "しきい値", "判定時刻", "結果", "的中", "払戻"]


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export(out: Path, date: str, generated: str, model: str, venues: dict[str, tuple[str, list[dict]]]) -> int:
    """venues: {場コード: (会場名, render.venue_context の races)}。締切前のレースだけ渡すこと。"""
    schedule = []
    for jcd, (name, races) in sorted(venues.items()):
        if not races:
            continue
        _save(out / "probs" / f"{jcd}.json", dict(date=date, model=model, races=[dict(
            no=r["no"], deadline=r["deadline"],
            tri=[round(r["pred"]["tri_all"][c], 5) for c in TRI_ORDER],
            trio=[round(r["pred"]["trio_all"][c], 5) for c in TRIO_ORDER],
        ) for r in races]))
        schedule += [dict(jcd=jcd, venue=name, no=r["no"], deadline=r["deadline"]) for r in races]
    schedule.sort(key=lambda r: (r["deadline"], r["jcd"]))
    _save(out / "schedule.json", dict(date=date, generated=generated, model=model, races=schedule))
    return len(schedule)


def fill_results(text: str, history: dict) -> tuple[str, int]:
    """log.csv の「結果」が空欄の行を、公式の成績ファイル（history）で埋める。埋めた行数も返す。"""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or rows[0] != LOG_COLUMNS:
        return text, 0
    col = {c: i for i, c in enumerate(LOG_COLUMNS)}
    filled = 0
    for r in rows[1:]:
        if len(r) != len(LOG_COLUMNS) or r[col["結果"]]:
            continue
        day = history.get(r[col["日付"]], {})
        race = next((x for x in day.get(r[col["場コード"]], {}).get("races", []) if str(x["no"]) == r[col["レース"]]), None)
        if not race:
            continue
        trio = r[col["式別"]] == "3連複"
        pay = race["payout"].get("trio" if trio else "tri")
        if not pay:
            # 中止・不成立。舟券は返ってくるので、払戻＝購入額（100円）として扱う
            r[col["結果"]], r[col["的中"]], r[col["払戻"]] = "返還", "0", "100"
        else:
            nums = [int(x) for x in pay["combo"].split("-")]
            result = "=".join(map(str, sorted(nums))) if trio else "-".join(map(str, nums))
            hit = result == r[col["組番"]]
            r[col["結果"]], r[col["的中"]], r[col["払戻"]] = result, str(int(hit)), str(pay["yen"] if hit else 0)
        filled += 1
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(rows)
    return "﻿" + out.getvalue(), filled
