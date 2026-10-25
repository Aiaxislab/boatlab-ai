"""管理者ダッシュボード用の「レースごとの成績」CSV。

答え合わせできたレースを1行ずつ足していく。消すのはダッシュボードのクリアボタンだけ。
クリアした日（meta.json の from）より前のレースは、もう足さない。
"""

import csv
import datetime as dt
import io

from .venues import VENUES

WEEK = "月火水木金土日"
COLUMNS = ["日付", "曜日", "場コード", "会場", "レース", "締切", "結果", "本命", "予想1位",
           "本命1着", "本命3着内", "予想1位的中", "買い目", "買い目的中", "購入額", "払戻",
           "3連複買い目", "3連複結果", "3連複的中", "3連複購入額", "3連複払戻", "予想的中確率", "モデル"]

combo = lambda c: "-".join(map(str, c))


def to_row(g: dict) -> list:
    day = dt.date.fromisoformat(g["date"])
    return [
        g["date"], WEEK[day.weekday()], g["jcd"], VENUES[g["jcd"]]["name"], g["no"], g["deadline"],
        combo(g["result"]), g["honmei"], combo(g["top1"]),
        int(g["honmei_win"]), int(g["honmei_top3"]), int(g["top1_hit"]),
        " ".join(map(combo, g["buy"])), int(g["buy_hit"]), g["buy_cost"], g["buy_return"],
        " ".join(map(combo, g["trio"])), combo(g["trio_result"]) if g["trio_result"] else "",
        int(g["trio_hit"]), g["trio_cost"], g["trio_return"],
        # PDFに載せた「買い目の的中確率」（%）。確率帯ごとの実際の的中率を見るのに使う
        round(g["buy_prob"] * 100, 1) if g.get("buy_prob") is not None else "",
        g.get("model", "簡易"),  # 簡易モデル か AI学習モデル（学習日）か
    ]


def merge(existing: str | None, graded: list[dict], cleared_from: str | None) -> tuple[str, int]:
    """今のCSVに、まだ載っていないレースを足す。今ある行は消さない。足した行数も返す。"""
    rows = list(csv.reader(io.StringIO(existing.lstrip("﻿")))) if existing else []
    head, body = (rows[0], rows[1:]) if rows and rows[0][:1] == COLUMNS[:1] else (COLUMNS, rows)
    # 列を足したあとも、前からある行は見出しの名前で並べ直す（無い列は空欄）
    body = [[dict(zip(head, r)).get(c, "") for c in COLUMNS] for r in body]
    have = {(r[0], r[2], r[4]) for r in body if len(r) > 4}
    added = []
    for g in sorted(graded, key=lambda g: (g["date"], g["jcd"], g["no"])):
        if cleared_from and g["date"] < cleared_from:
            continue
        if (g["date"], g["jcd"], str(g["no"])) not in have:
            added.append(to_row(g))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(COLUMNS)
    w.writerows(body + [[str(x) for x in r] for r in added])
    # Excelでそのまま開けるように BOM つきの UTF-8 にする
    return "﻿" + out.getvalue(), len(added)
