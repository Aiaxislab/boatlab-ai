// 今日買う舟券の一覧（購入はテレボートで本人が行う。ここでは「どれを買うか」を決めるだけ）。
// 毎朝の予想 admin/picks.json から、買い目の的中確率が基準以上のレースを、確率の高い順に
// 1日の上限金額まで選ぶ。選んだレースは締切の早い順に並べ直して返す。
// 設定（基準・上限）はダッシュボードで変え、admin/settings.json の buyMin・buyLimit に入る。

import { SETTINGS_KEY } from "./mail.js";

export const BUY_MIN_CHOICES = [40, 45, 50, 55, 60, 65, 70, 75, 80];
export const BUY_LIMIT_CHOICES = [0, 600, 1200, 1800, 2400, 3000, 3600, 4200, 4800, 5400, 6000];
export const BET_YEN = 100; // 1点の金額（3連単6点 × 100円 = 1レース600円。メールの買い方と同じ）
const DEFAULTS = { buyMin: 60, buyLimit: 3000 };

export async function buySettings(env) {
  const obj = await env.BUCKET.get(SETTINGS_KEY);
  const s = obj ? await obj.json() : {};
  return {
    buyMin: BUY_MIN_CHOICES.includes(Number(s.buyMin)) ? Number(s.buyMin) : DEFAULTS.buyMin,
    buyLimit: BUY_LIMIT_CHOICES.includes(Number(s.buyLimit)) ? Number(s.buyLimit) : DEFAULTS.buyLimit,
  };
}

export function buildBuyList(picks, { buyMin, buyLimit }) {
  const min = buyMin / 100;
  const over = picks.races.filter((r) => r.buy_prob >= min).sort((a, b) => b.buy_prob - a.buy_prob);
  const chosen = [], skipped = [];
  let total = 0;
  for (const r of over) {
    const cost = r.buy.length * BET_YEN;
    if (total + cost > buyLimit) { skipped.push({ ...brief(r), cost, why: "1日の上限を超えるため" }); continue; }
    total += cost;
    chosen.push({ ...brief(r), cost, bets: r.buy.map((b) => ({ type: "3連単", combo: b.combo, yen: BET_YEN, prob: b.prob })) });
  }
  chosen.sort((a, b) => (a.deadline < b.deadline ? -1 : 1));
  return { date: picks.date, generated: picks.generated, buyMin, buyLimit, total, count: chosen.length, races: chosen, skipped, all: picks.races.length };
}

const brief = (r) => ({ jcd: r.jcd, venue: r.venue, no: r.no, deadline: r.deadline, prob: r.buy_prob, honmei: r.honmei, honmei_name: r.honmei_name });
