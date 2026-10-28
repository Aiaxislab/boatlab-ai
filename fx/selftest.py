"""作り物のデータでの動作確認（python -m fx.selftest）。外部には繋がない。PRのときに GitHub Actions が動かす。

1日目の8:30に判定 → 日中の途中経過 → 2日目の8:30に判定と1日目の採点、までを通しで動かし、
できたファイルの中身を確かめる。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import run, sources
from .indicators import resample
from .sources import Bars

JST = dt.timezone(dt.timedelta(hours=9))
END = dt.datetime(2026, 11, 3, 9, 0, tzinfo=JST).timestamp()
START = END - 260 * 86400
NOW = [0.0]
_series: dict[str, Bars] = {}
rng = np.random.default_rng(7)


def _walk(p0: float) -> Bars:
    n = int((END - START) // 60)
    t = START + np.arange(n) * 60.0
    r = rng.normal(np.sin(np.arange(n) / (n / 7)) * 1e-5, 2.5e-4, n)
    c = p0 * np.exp(np.cumsum(r))
    o = np.concatenate([[p0], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 1e-4, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 1e-4, n)))
    return Bars(t, o, h, l, c)


def _clip(name: str, sec: int, size: int) -> Bars:
    if name not in _series:
        _series[name] = _walk({"USD/JPY": 151.0, "XAU/USD": 2700.0, "BTC-USD": 70000.0}.get(name, 1.1))
    b = _series[name].until(NOW[0])
    if sec > 60:
        b = resample(b, sec // 60)
    k = max(0, len(b) - size)
    return Bars(b.t[k:], b.o[k:], b.h[k:], b.l[k:], b.c[k:])


def main() -> None:
    sources.twelvedata = lambda symbol, interval, size=5000: _clip(symbol, 60 if interval == "1min" else 3600, size)
    sources.coinbase = lambda product, gran, pages=1, end=None: _clip(product, gran, 300 * pages)
    sources.myfxbook_outlook = lambda: {"USDJPY": {"long_pct": 74.0, "avg_long": 152.5, "avg_short": 150.0, "source": "test"}}
    sources.btc_crowd = lambda: {"long_pct": 66.0, "avg_long": None, "avg_short": None, "source": "test"}
    t0 = dt.datetime(2026, 10, 26, tzinfo=JST).timestamp()
    sources.calendar = lambda: [{"t": t0 + d * 86400 + 21.5 * 3600, "title": "CPI m/m", "country": "USD", "forecast": "", "previous": ""} for d in range(10)]
    run.time.time = lambda: NOW[0]
    run.time.sleep = lambda s: NOW.__setitem__(0, NOW[0] + s)

    with tempfile.TemporaryDirectory() as d:
        steps = [("2026-10-29T08:20", "morning"), ("2026-10-29T14:00", "intraday"), ("2026-10-30T08:30", "morning")]
        for when, mode in steps:
            NOW[0] = dt.datetime.fromisoformat(when).replace(tzinfo=JST).timestamp()
            sys.argv = ["fx.run", "--mode", mode, "--state", d]
            run.main()
        st = Path(d)
        latest = json.loads((st / "latest.json").read_text(encoding="utf-8"))
        stats = json.loads((st / "stats.json").read_text(encoding="utf-8"))
        assert len(latest["symbols"]) == 12, "12銘柄すべてを判定できていません"
        assert latest["mail"]["text"], "メールの文面がありません"
        assert (st / "judge" / "2026-10-30.json").exists(), "8:30の判定の記録がありません"
        assert stats["rows"] == 12, f"1日目の採点が12件になっていません（{stats['rows']}件）"
        assert "FX判断ログ" in (st / "share.md").read_text(encoding="utf-8")
        print(latest["symbols"][0]["message"])
    print("動作確認OK")


if __name__ == "__main__":
    main()
