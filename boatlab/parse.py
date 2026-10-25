"""番組表（B）と競走成績（K）のテキストを読み取る。"""

import re
import unicodedata


def _z2h(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _num(s: str) -> float | None:
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _blocks(text: str, kind: str):
    """場ごとのブロックに分ける。(場コード, 行のリスト) を返す。"""
    for m in re.finditer(rf"^(\d\d){kind}BGN\n(.*?)^\1{kind}END", text, re.S | re.M):
        yield m.group(1), m.group(2).splitlines()


def parse_program(text: str) -> dict:
    """番組表。{場コード: {"title", "day", "races": [...]}}"""
    venues = {}
    for jcd, lines in _blocks(text, "B"):
        head = _z2h(lines[0]) if lines else ""
        m = re.search(r"第\s*(\d+)日", head)
        day = int(m.group(1)) if m else 0
        # 見出しの下にある大会名の行（1行目の大会名は途中で切れていることがある）
        mark = next((i for i, l in enumerate(lines) if "番組表" in l), 0)
        title = next((_z2h(l).strip() for l in lines[mark + 1:mark + 4] if l.strip()), "")
        races, cur = [], None
        for raw in lines:
            z = _z2h(raw)
            rm = re.match(r"^\s*(\d+)R\s+(\S+)\s+.*?H(\d+)m\s+電話投票締切予定(\d+):(\d+)", z)
            if rm:
                cur = dict(no=int(rm.group(1)), kind=rm.group(2), distance=int(rm.group(3)),
                           deadline=f"{int(rm.group(4)):02d}:{rm.group(5)}", boats=[])
                races.append(cur)
                continue
            if cur is not None and re.match(r"^[1-6] \d{4}", raw):
                l = raw.ljust(73)
                cur["boats"].append(dict(
                    waku=int(l[0]), toban=l[2:6], name=l[6:10].replace("　", " ").strip(),
                    age=int(l[10:12]), branch=l[12:14], weight=int(l[14:16]), cls=l[16:18],
                    nat_win=_num(l[18:23]), nat_2r=_num(l[23:29]),
                    loc_win=_num(l[29:34]), loc_2r=_num(l[34:40]),
                    motor=l[40:43].strip(), motor_2r=_num(l[43:49]),
                    boat=l[49:52].strip(), boat_2r=_num(l[52:58]),
                    series=l[58:70].replace(" ", ""),
                ))
        venues[jcd] = dict(title=title, day=day, races=[r for r in races if len(r["boats"]) == 6])
    return venues


def parse_results(text: str) -> dict:
    """競走成績。{場コード: {"races": [...]}}。各艇の着順・進入・ST・天候・風・波・払戻を持つ。"""
    venues = {}
    for jcd, lines in _blocks(text, "K"):
        races, cur = [], None
        for raw in lines:
            z = _z2h(raw)
            rm = re.match(r"^\s*(\d+)R\s+(\S+)\s+.*?H\d+m\s+(\S+)\s+風\s+(\S+)\s+(\d+)m\s+波\s+(\d+)cm", z)
            if rm:
                cur = dict(no=int(rm.group(1)), kind=rm.group(2), weather=rm.group(3),
                           wind_dir=rm.group(4), wind=int(rm.group(5)), wave=int(rm.group(6)),
                           boats=[], payout={})
                races.append(cur)
                continue
            if cur is None:
                continue
            bm = re.match(r"^  (\S\S)\s+([1-6]) (\d{4}) ", raw)
            if bm:
                rest = raw[21:].split()
                st = rest[4] if len(rest) > 4 else ""
                cur["boats"].append(dict(
                    place=bm.group(1).strip(), waku=int(bm.group(2)), toban=bm.group(3),
                    course=int(rest[3]) if len(rest) > 3 and rest[3].isdigit() else None,
                    st=_num(st.lstrip("FL")) if st and st[0] not in "FL" else None,
                    flying=st.startswith("F"),
                ))
                continue
            pm = re.match(r"^\s+(３連単|３連複)\s+([\d-]+)\s+(\d+)", raw)
            if pm:
                cur["payout"]["tri" if pm.group(1) == "３連単" else "trio"] = dict(combo=pm.group(2), yen=int(pm.group(3)))
        venues[jcd] = dict(races=races)
    return venues
