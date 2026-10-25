"""公開データの取得。取得したファイルは cache/ に保存して、同じ日に何度も取りに行かない。"""

import datetime as dt
import json
import subprocess
import tempfile
import time
from pathlib import Path

import requests

UA = "BoatLabAI/1.0 (+https://boat.aiaxislab.win)"
CACHE = Path("cache")

MBRACE = "https://www1.mbrace.or.jp/od2/{kind}/{ym}/{k}{ymd}.lzh"
JMA_FORECAST = "https://www.jma.go.jp/bosai/forecast/data/forecast/{office}.json"
JMA_TIDE = "https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/{year}/{station}.txt"


def _get(url: str) -> bytes | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return None


def _cached(name: str, url: str, max_age_h: float | None = None) -> bytes | None:
    path = CACHE / name
    if path.exists() and (max_age_h is None or time.time() - path.stat().st_mtime < max_age_h * 3600):
        return path.read_bytes()
    data = _get(url)
    time.sleep(1)  # 相手のサーバーに負担をかけない
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


def _extract_lzh(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "a.lzh"
        src.write_bytes(data)
        subprocess.run(["lha", "-xqw=" + d, str(src)], check=True)
        txt = [p for p in Path(d).iterdir() if p.suffix.lower() == ".txt"]
        return txt[0].read_bytes().decode("cp932", errors="replace").replace("\r\n", "\n")


def mbrace_text(kind: str, day: dt.date) -> str | None:
    """kind="B" で番組表、"K" で競走成績のテキストを返す。まだ公開前なら None。"""
    name = f"mbrace/{kind.lower()}{day:%y%m%d}.txt"
    path = CACHE / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    url = MBRACE.format(kind=kind, ym=f"{day:%Y%m}", k=kind.lower(), ymd=f"{day:%y%m%d}")
    data = _get(url)
    time.sleep(1)
    if not data or b"-lh" not in data[:8]:
        return None
    text = _extract_lzh(data)
    if "全レース終了後に登録されます" in text:
        return text  # ナイター場がまだ終わっていない。完成版を後で取り直すため保存しない
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def jma_forecast(office: str) -> list:
    data = _cached(f"jma/forecast_{office}.json", JMA_FORECAST.format(office=office), max_age_h=1)
    return json.loads(data)


def jma_tide(station: str, year: int) -> str | None:
    data = _cached(f"jma/tide_{year}_{station}.txt", JMA_TIDE.format(year=year, station=station))
    return data.decode("ascii", errors="replace") if data else None
