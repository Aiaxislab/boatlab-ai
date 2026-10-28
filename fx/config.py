"""見る銘柄の設定（fx/knowledge/symbols.json の内容をコードで使う形にしたもの）。

kind: fx=為替（土日は休み）/ gold=金（為替と同じ時間に取引）/ crypto=BTC（24時間365日）
td: Twelve Data での銘柄名。cb: Coinbase での銘柄名
pip: 1pipの大きさ。round: キリ番の刻み。ccy: 関係する経済指標の通貨
"""

SYMBOLS = [
    {"name": "USDJPY", "kind": "fx", "td": "USD/JPY", "pip": 0.01, "round": 0.5, "ccy": ["USD", "JPY"]},
    {"name": "EURUSD", "kind": "fx", "td": "EUR/USD", "pip": 0.0001, "round": 0.005, "ccy": ["EUR", "USD"]},
    {"name": "GBPUSD", "kind": "fx", "td": "GBP/USD", "pip": 0.0001, "round": 0.005, "ccy": ["GBP", "USD"]},
    {"name": "AUDUSD", "kind": "fx", "td": "AUD/USD", "pip": 0.0001, "round": 0.005, "ccy": ["AUD", "USD"]},
    {"name": "NZDUSD", "kind": "fx", "td": "NZD/USD", "pip": 0.0001, "round": 0.005, "ccy": ["NZD", "USD"]},
    {"name": "USDCHF", "kind": "fx", "td": "USD/CHF", "pip": 0.0001, "round": 0.005, "ccy": ["USD", "CHF"]},
    {"name": "USDCAD", "kind": "fx", "td": "USD/CAD", "pip": 0.0001, "round": 0.005, "ccy": ["USD", "CAD"]},
    {"name": "EURJPY", "kind": "fx", "td": "EUR/JPY", "pip": 0.01, "round": 0.5, "ccy": ["EUR", "JPY"]},
    {"name": "GBPJPY", "kind": "fx", "td": "GBP/JPY", "pip": 0.01, "round": 0.5, "ccy": ["GBP", "JPY"]},
    {"name": "AUDJPY", "kind": "fx", "td": "AUD/JPY", "pip": 0.01, "round": 0.5, "ccy": ["AUD", "JPY"]},
    {"name": "XAUUSD", "kind": "gold", "td": "XAU/USD", "pip": 0.1, "round": 10.0, "ccy": ["USD"]},
    {"name": "BTCUSD", "kind": "crypto", "cb": "BTC-USD", "pip": 1.0, "round": 1000.0, "ccy": ["USD"]},
]

TF_ORDER = ["D", "4h", "1h", "15m", "10m", "5m", "1m"]
TF_LABEL = {"1m": "1分足", "5m": "5分足", "10m": "10分足", "15m": "15分足", "1h": "1時間足", "4h": "4時間足", "D": "日足"}
# スイング（山と谷）を決める左右の本数。日足・4時間足は2本、それより下は3本
SWING_K = {"D": 2, "4h": 2, "1h": 3, "15m": 3, "10m": 3, "5m": 3, "1m": 3}

# 中央銀行のイベントとみなす指標名の一部
CB_WORDS = ["Rate Statement", "Policy Rate", "FOMC", "Monetary Policy", "Press Conference", "Cash Rate", "Official Bank Rate", "Main Refinancing", "Overnight Rate", "Interest Rate Decision"]
