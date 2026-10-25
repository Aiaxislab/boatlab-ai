"""機械学習モデル（AI学習モデル）。

model.py の簡易モデルは重みを手で決めている。ここでは同じ考え方（各艇の強さの点数 → プラケット＝ルースで
3連単の確率）のまま、点数の重みを過去のレース結果から学習する。手の重みに加えて、
場ごとのコースの有利さ、風・波・潮でコースの有利さがどう変わるか、も学習する。

学習データ（1レース1行）は train.py が作る。毎朝の予想と学習で同じ boat_features() を使うので、
学習した重みをそのまま毎朝の予想に使える。
"""

import gzip
import json
from pathlib import Path

import numpy as np

DIRS = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]
TIDE_TRENDS = ["上げ", "下げ"]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def boat_features(boats: list[dict], jcd: str, st_avg: list, env: dict) -> list[dict]:
    """6艇それぞれの特徴量 {名前: 値}。boats は枠番順。env は風・波・潮（無いものは None）。

    1号艇の項目を付けないもの（L2〜L6 など）は、1号艇を基準にした差として学習される。
    """
    nat = [b["nat_win"] or 0 for b in boats]
    nat2 = [b["nat_2r"] or 0 for b in boats]
    motor = [b["motor_2r"] or 0 for b in boats]
    boat2 = [b["boat_2r"] or 0 for b in boats]
    age = [b["age"] or 0 for b in boats]
    weight = [b["weight"] or 0 for b in boats]
    m = dict(nat=_mean(nat), nat2=_mean(nat2), motor=_mean(motor), boat=_mean(boat2),
             age=_mean(age), weight=_mean(weight), st=_mean(st_avg))
    wind = env.get("wind") or 0
    wdir = env.get("dir") if env.get("dir") in DIRS else None
    wave = env.get("wave") or 0
    tide = env.get("tide") if env.get("tide") in TIDE_TRENDS else None
    tide_dev = env.get("tide_dev")
    out = []
    for i, b in enumerate(boats):
        w = b["waku"]
        f = {}
        if w > 1:
            f[f"L{w}"] = 1.0                 # コースの有利さ（全国）
            f[f"V{jcd}L{w}"] = 1.0           # その場だけの差
            f[f"WindL{w}"] = wind / 5        # 風が強いとコースの有利さがどう変わるか
            f[f"WaveL{w}"] = wave / 10
        f["nat"] = nat[i] - m["nat"]
        f["nat2"] = (nat2[i] - m["nat2"]) / 10
        if b["loc_win"]:
            f["loc"] = b["loc_win"] - nat[i]
            f["loc2"] = ((b["loc_2r"] or 0) - nat2[i]) / 10
        else:
            f["loc_none"] = 1.0              # 当地初出走
        f["motor"] = (motor[i] - m["motor"]) / 10
        f["boat"] = (boat2[i] - m["boat"]) / 10
        f["age"] = (age[i] - m["age"]) / 10
        f["weight"] = (weight[i] - m["weight"]) / 10
        if b["cls"] in ("A1", "A2", "B2"):
            f[b["cls"]] = 1.0
        if st_avg[i] is not None:
            f["st"] = (st_avg[i] - m["st"]) * 10
        else:
            f["st_none"] = 1.0               # 今節初戦
        # 風向き：場ごとに、その向きの風がイン（1コース）と外（4〜6コース）にどう効くか
        if wdir and wind:
            if w == 1:
                f[f"V{jcd}D{wdir}In"] = wind / 5
            elif w >= 4:
                f[f"V{jcd}D{wdir}Out"] = wind / 5
        # 潮：場ごとに、上げ潮・下げ潮と潮位の高さがインにどう効くか
        if w == 1 and tide:
            f[f"V{jcd}T{tide}In"] = 1.0
            if tide_dev is not None:
                f[f"V{jcd}TdevIn"] = tide_dev / 100
        out.append(f)
    return out


def scores(model: dict, feats: list[dict]) -> list[float]:
    w = model["weights"]
    return [sum(w.get(k, 0.0) * v for k, v in f.items()) for f in feats]


# ---- 学習 ----

def _design(rows: list[dict], vocab: dict | None = None):
    """学習用の行列。X は (レース数×6, 特徴量数) の疎行列、order は各レースの1〜3着の枠番（0始まり、無ければ -1）。"""
    from scipy import sparse
    build = vocab is None
    vocab = {} if build else vocab
    data, idx, ptr = [], [], [0]
    orders = []
    for r in rows:
        for f in features_of(r):
            for k, v in f.items():
                if k not in vocab:
                    if not build:
                        continue
                    vocab[k] = len(vocab)
                if v:
                    data.append(v)
                    idx.append(vocab[k])
            ptr.append(len(data))
        o = [w - 1 for w in r["o"][:3]]
        orders.append(o + [-1] * (3 - len(o)))
    X = sparse.csr_matrix((np.array(data), np.array(idx), np.array(ptr)), shape=(len(rows) * 6, len(vocab)))
    return X, np.array(orders, dtype=int), vocab


def features_of(row: dict) -> list[dict]:
    keys = ["waku", "cls", "nat_win", "nat_2r", "loc_win", "loc_2r", "motor_2r", "boat_2r", "age", "weight"]
    boats = [dict(zip(keys, b)) for b in row["b"]]
    return boat_features(boats, row["j"], row["st"], row["env"])


def _pl(S: np.ndarray, order: np.ndarray, need_grad: bool = True):
    """プラケット＝ルースの対数尤度（1〜3着の順）と、点数についての勾配。"""
    n = len(S)
    alive = np.ones_like(S, dtype=bool)
    ll = np.zeros(n)
    G = np.zeros_like(S) if need_grad else None
    rows = np.arange(n)
    for s in range(3):
        o = order[:, s]
        ok = o >= 0
        z = np.where(alive, S, -np.inf)
        zmax = z.max(axis=1, keepdims=True)
        e = np.where(alive, np.exp(z - zmax), 0.0)
        tot = e.sum(axis=1, keepdims=True)
        p = e / tot
        lse = np.log(tot[:, 0]) + zmax[:, 0]
        ll[ok] += S[rows[ok], o[ok]] - lse[ok]
        if need_grad:
            G[ok] -= p[ok]
            G[rows[ok], o[ok]] += 1.0
        alive[rows[ok], o[ok]] = False
    return ll, G


def fit(rows: list[dict], lam: float = 5.0) -> dict:
    from scipy.optimize import minimize
    X, order, vocab = _design(rows)
    n = len(rows)

    def loss(w):
        S = (X @ w).reshape(n, 6)
        ll, G = _pl(S, order)
        g = X.T @ G.ravel()
        return -ll.sum() + lam * w @ w, -g + 2 * lam * w

    res = minimize(loss, np.zeros(len(vocab)), jac=True, method="L-BFGS-B", options=dict(maxiter=500))
    names = sorted(vocab, key=vocab.get)
    return dict(weights={k: round(float(res.x[vocab[k]]), 5) for k in names},
                fit=dict(races=n, iterations=int(res.nit), converged=bool(res.success)))


# ---- 比べるための指標 ----

def trifecta_probs(S: np.ndarray) -> tuple[np.ndarray, list]:
    """各レースの3連単120通りの確率 (レース数, 120)。"""
    import itertools
    perms = list(itertools.permutations(range(6), 3))
    W = np.exp(S - S.max(axis=1, keepdims=True))
    tot = W.sum(axis=1)
    P = np.empty((len(S), len(perms)))
    for c, (i, j, k) in enumerate(perms):
        P[:, c] = W[:, i] / tot * W[:, j] / (tot - W[:, i]) * W[:, k] / (tot - W[:, i] - W[:, j])
    return P, perms


def evaluate(S: np.ndarray, rows: list[dict], n_buy: int = 6) -> dict:
    """検証期間の成績。数字が大きいほど良い（対数尤度）・当たる。"""
    order = np.array([[w - 1 for w in r["o"][:3]] + [-1] * (3 - len(r["o"][:3])) for r in rows])
    full = (order >= 0).all(axis=1)
    P, perms = trifecta_probs(S)
    lookup = {c: i for i, c in enumerate(perms)}
    actual = np.array([lookup[tuple(o)] if ok else 0 for o, ok in zip(order, full)])
    p_act = P[np.arange(len(P)), actual]
    top = np.argsort(-P, axis=1)[:, :n_buy]
    hit = (top == actual[:, None]).any(axis=1) & full
    buy_prob = np.take_along_axis(P, top, axis=1).sum(axis=1)
    win_p = np.exp(S - S.max(axis=1, keepdims=True))
    win_p /= win_p.sum(axis=1, keepdims=True)
    winner = order[:, 0]
    ok1 = winner >= 0
    pay = np.array([r.get("pay") or 0 for r in rows])
    # 確率帯ごとに「言った確率」と「実際の的中率」を比べる（確率が当てになるか）
    bands = []
    for lo in range(0, 100, 10):
        sel = full & (buy_prob * 100 >= lo) & (buy_prob * 100 < lo + 10)
        if sel.sum():
            bands.append(dict(band=lo, races=int(sel.sum()), said=round(float(buy_prob[sel].mean()), 4),
                              hit=round(float(hit[sel].mean()), 4)))
    return dict(
        races=int(full.sum()),
        tri_logp=round(float(np.log(np.maximum(p_act[full], 1e-12)).mean()), 4),
        win_logp=round(float(np.log(win_p[ok1, winner[ok1]]).mean()), 4),
        honmei_win=round(float((win_p.argmax(axis=1)[ok1] == winner[ok1]).mean()), 4),
        buy_hit=round(float(hit[full].mean()), 4),
        buy_roi=round(float(np.where(hit, pay, 0)[full].sum() / (100 * n_buy * full.sum())), 4) if full.sum() else 0,
        bands=bands,
    )


# ---- 保存・読み込み ----

def load(path: Path) -> dict | None:
    """採用されたモデルだけを返す（無い・不採用なら None ＝ 簡易モデルを使う）。"""
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if m.get("adopted") and m.get("weights") else None


def read_rows(path: Path) -> tuple[dict, list[dict]]:
    """月ごとの学習データ（1行目はメタ情報）。"""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines = f.read().splitlines()
    meta = json.loads(lines[0]) if lines else {}
    return meta, [json.loads(l) for l in lines[1:]]


def write_rows(path: Path, meta: dict, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def wind_from_forecast(text: str) -> tuple[float | None, str | None]:
    """気象庁の風の文章（例「北西の風 やや強く 海上では 北西の風 強く」）から、陸上の風向と風速の目安(m)。"""
    if not text:
        return None, None
    land = text.split("海上")[0]
    d = next((x for x in sorted(DIRS, key=len, reverse=True) if land.startswith(x)), None)
    if "非常に強く" in land:
        speed = 12.0
    elif "やや強く" in land:
        speed = 6.0
    elif "強く" in land:
        speed = 9.0
    else:
        speed = 3.0
    return speed, d
