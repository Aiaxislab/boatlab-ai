"""全24場の基本情報。

area: 気象庁の天気予報（府県予報区の一次細分区域）のコード
tide: 気象庁 潮位表の地点記号（潮の影響がある場のみ）
"""

VENUES = {
    "01": dict(name="桐生", office="100000", area="100010", tide=None),
    "02": dict(name="戸田", office="110000", area="110010", tide=None),
    "03": dict(name="江戸川", office="130000", area="130010", tide=("TK", "東京")),
    "04": dict(name="平和島", office="130000", area="130010", tide=("TK", "東京")),
    "05": dict(name="多摩川", office="130000", area="130010", tide=None),
    "06": dict(name="浜名湖", office="220000", area="220040", tide=("MI", "舞阪")),
    "07": dict(name="蒲郡", office="230000", area="230020", tide=("G5", "形原")),
    "08": dict(name="常滑", office="230000", area="230010", tide=("ZD", "鬼崎")),
    "09": dict(name="津", office="240000", area="240010", tide=None),
    "10": dict(name="三国", office="180000", area="180010", tide=None),
    "11": dict(name="びわこ", office="250000", area="250010", tide=None),
    "12": dict(name="住之江", office="270000", area="270000", tide=None),
    "13": dict(name="尼崎", office="280000", area="280010", tide=None),
    "14": dict(name="鳴門", office="360000", area="360010", tide=("KM", "小松島")),
    "15": dict(name="丸亀", office="370000", area="370000", tide=("TX", "多度津")),
    "16": dict(name="児島", office="330000", area="330010", tide=("MM", "水島")),
    "17": dict(name="宮島", office="340000", area="340010", tide=("Q8", "広島")),
    "18": dict(name="徳山", office="350000", area="350030", tide=("QA", "徳山")),
    "19": dict(name="下関", office="350000", area="350010", tide=("CF", "長府")),
    "20": dict(name="若松", office="400000", area="400020", tide=("N0", "砂津")),
    "21": dict(name="芦屋", office="400000", area="400020", tide=None),
    "22": dict(name="福岡", office="400000", area="400010", tide=("QF", "博多")),
    "23": dict(name="唐津", office="410000", area="410020", tide=None),
    "24": dict(name="大村", office="420000", area="420010", tide=None),
}

# 全国平均のコース別1着率（場ごとの集計が少ないときの基準）
NATIONAL_LANE_WIN = [0.55, 0.14, 0.12, 0.11, 0.06, 0.02]
