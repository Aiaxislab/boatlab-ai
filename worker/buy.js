// 今日買う舟券の一覧（購入はテレボートで本人が行う。ここでは「どれを買うか」を決めるだけ）。
// 毎朝の予想 admin/picks.json から、買い目の的中確率が範囲内（〜%以上〜%未満）のレースを、締切の早い順に
// 1日の上限金額に届くまで選ぶ（朝から順に買えるだけ買う）。
// 設定（確率の範囲・金額の上限）はダッシュボードで変え、admin/settings.json の buyMin・buyMax・buyLimit に入る。

import { inRange, MAX_CHOICES, MIN_CHOICES, SETTINGS_KEY } from "./mail.js";

export const BUY_MIN_CHOICES = MIN_CHOICES;
export const BUY_MAX_CHOICES = MAX_CHOICES;
export const BUY_LIMIT_MAX = 999999; // 1日の上限金額は手入力（6桁まで）
export const okLimit = (v) => Number.isInteger(v) && v >= 0 && v <= BUY_LIMIT_MAX;
export const BET_YEN = 100; // 1点の金額（3連単6点 × 100円 = 1レース600円。メールの買い方と同じ）
const DEFAULTS = { buyMin: 60, buyMax: 100, buyLimit: 3000 };

export async function buySettings(env) {
  const obj = await env.BUCKET.get(SETTINGS_KEY);
  const s = obj ? await obj.json() : {};
  const buyMin = BUY_MIN_CHOICES.includes(Number(s.buyMin)) ? Number(s.buyMin) : DEFAULTS.buyMin;
  return {
    buyMin,
    buyMax: BUY_MAX_CHOICES.includes(Number(s.buyMax)) && Number(s.buyMax) > buyMin ? Number(s.buyMax) : DEFAULTS.buyMax,
    buyLimit: okLimit(Number(s.buyLimit)) ? Number(s.buyLimit) : DEFAULTS.buyLimit,
  };
}

export function buildBuyList(picks, { buyMin, buyMax = 100, buyLimit }) {
  const min = buyMin / 100, max = buyMax / 100;
  const hit = picks.races.filter((r) => inRange(r.buy_prob, min, max)).sort((a, b) => (a.deadline < b.deadline ? -1 : 1));
  const chosen = [], skipped = [];
  let total = 0;
  for (const r of hit) {
    const cost = r.buy.length * BET_YEN;
    if (total + cost > buyLimit) { skipped.push({ ...brief(r), cost, why: "1日の上限金額に届いたため" }); continue; }
    total += cost;
    chosen.push({ ...brief(r), cost, bets: r.buy.map((b) => ({ type: "3連単", combo: b.combo, yen: BET_YEN, prob: b.prob })) });
  }
  return { date: picks.date, generated: picks.generated, buyMin, buyMax, buyLimit, total, count: chosen.length, races: chosen, skipped, all: picks.races.length };
}

const brief = (r) => ({ jcd: r.jcd, venue: r.venue, no: r.no, deadline: r.deadline, prob: r.buy_prob, honmei: r.honmei, honmei_name: r.honmei_name });
