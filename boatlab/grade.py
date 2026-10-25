"""予想の答え合わせと、勝率ページ用の集計。"""

from collections import defaultdict

from .venues import VENUES


def grade_day(preds: dict, results: dict) -> list[dict]:
    """1日分の予想と結果を照らし合わせる。中止・不成立のレースは除く。"""
    rows = []
    for jcd, races in preds["venues"].items():
        res = {r["no"]: r for r in results.get(jcd, {}).get("races", [])}
        for p in races:
            r = res.get(p["no"])
            if not r or "tri" not in r["payout"]:
                continue
            tri = tuple(int(x) for x in r["payout"]["tri"]["combo"].split("-"))
            trio = tuple(sorted(int(x) for x in r["payout"]["trio"]["combo"].split("-"))) if "trio" in r["payout"] else None
            honmei = p["order"][0]
            buy = [tuple(c) for c in p["buy"]]
            trio_buy = [tuple(c) for c in p["trio"]]
            rows.append(dict(
                date=preds["date"], model=preds.get("model", "簡易"), jcd=jcd, no=p["no"], deadline=p["deadline"], result=list(tri),
                honmei=honmei, top1=list(p["top5"][0]), buy_prob=p.get("buy_prob"), buy=[list(c) for c in buy], trio=[list(c) for c in trio_buy],
                trio_result=list(trio) if trio else None,
                honmei_win=tri[0] == honmei, honmei_top3=honmei in tri,
                top1_hit=tuple(p["top5"][0]) == tri,
                buy_hit=tri in buy, buy_cost=100 * len(buy),
                buy_return=r["payout"]["tri"]["yen"] if tri in buy else 0,
                trio_hit=trio in trio_buy if trio else False, trio_cost=100 * len(trio_buy),
                trio_return=r["payout"]["trio"]["yen"] if trio and trio in trio_buy else 0,
            ))
    return rows


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return dict(races=0)
    s = lambda k: sum(r[k] for r in rows)
    return dict(
        races=n,
        honmei_win=s("honmei_win") / n, honmei_top3=s("honmei_top3") / n,
        top1_hit=s("top1_hit") / n,
        buy_hit=s("buy_hit") / n, buy_roi=s("buy_return") / s("buy_cost"),
        trio_hit=s("trio_hit") / n, trio_roi=s("trio_return") / s("trio_cost"),
        buy_cost=s("buy_cost"), buy_return=s("buy_return"),
    )


def build_stats(graded: list[dict], generated: str) -> dict:
    by_venue, by_month, by_day = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in graded:
        by_venue[r["jcd"]].append(r)
        by_month[r["date"][:7]].append(r)
        by_day[r["date"]].append(r)
    days = sorted(by_day)
    recent = [r for d in days[-30:] for r in by_day[d]]
    return dict(
        generated=generated,
        since=days[0] if days else None,
        total=_summary(graded),
        last30=_summary(recent),
        venues=[dict(jcd=j, name=VENUES[j]["name"], **_summary(rs)) for j, rs in sorted(by_venue.items())],
        months=[dict(month=m, **_summary(rs)) for m, rs in sorted(by_month.items(), reverse=True)],
        days=[dict(date=d, **_summary(by_day[d])) for d in reversed(days[-60:])],
    )
