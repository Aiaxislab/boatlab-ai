// BoatLab AI 2（お買い得舟券）。管理者ダッシュボードの「BoatLab AI 2」タブ（/<ADMIN_PATH>/ai2/）と、その裏の定時処理。
// 1) 毎朝の処理（boatlab/ai2.py）が、全レースの 3連単120通り・3連複20通りのAI確率を R2 の admin/ai2/ に置く
// 2) Cron（AI2_CRON、日本時間 8:00〜23:59 の1分おき）が、締切5分前になったレースの公式オッズを取りに行き、
//    期待値（AI確率×オッズ）がしきい値以上の舟券を選ぶ（live.json）。自動メールがオンならその場で送る
// 3) 締切10分後からレース結果を取りに行き、答え合わせした行を log.csv に足して、live.json からは消す
//    （終わったレースのオッズや候補は残さない。残すのは log.csv の記録だけ）
//    2時間たっても結果が取れなければ「結果」を空欄で記録し、翌朝の処理が公式の成績ファイルで埋める
// 記録は判定した時点のしきい値・オッズで残すので、あとでしきい値を変えても過去の予実は変わらない。

import PAGE from "./ai2.html";
import { sendMail } from "../mail.js";

export const AI2_CRON = "* 23,0-14 * * *"; // 日本時間 8:00〜23:59 の1分おき（wrangler.toml と同じ文字列にする）
const KEY = (name) => `admin/ai2/${name}`;
const LIVE = KEY("live.json"), LOG = KEY("log.csv"), SETTINGS = KEY("settings.json");
const LOG_COLUMNS = ["日付", "場コード", "会場", "レース", "締切", "式別", "組番", "AI確率", "オッズ（5分前）",
  "期待値", "しきい値", "判定時刻", "結果", "的中", "払戻"]; // boatlab/ai2.py の LOG_COLUMNS と同じ
const UA = "BoatLabAI/1.0 (+https://boat.aiaxislab.win)";
const BASE = "https://www.boatrace.jp/owpc/pc/race";
const MIN = 60e3;

// 選び方の決まり（しきい値だけダッシュボードで変える）
export const RULES = {
  "3連単": { minProb: 0.05, minOdds: 5, maxOdds: 150, max: 3 },
  "3連複": { minProb: 0.15, minOdds: 3, maxOdds: 50, max: 2 },
};
export const EV_CHOICES = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0];
const DEFAULTS = { ev: 1.2, auto: false };
export const okEv = (v) => Number.isFinite(v) && v >= 1 && v <= 10;
const JUDGE_PER_RUN = 3, RESULT_PER_RUN = 3; // 1回の処理で取りに行くレースの上限（無料枠のCPU時間に収める）

// 3連単・3連複の並び（boatlab/ai2.py の TRI_ORDER・TRIO_ORDER と同じ）
const TRI_ORDER = [], TRIO_ORDER = [];
for (let a = 1; a <= 6; a++) for (let b = 1; b <= 6; b++) for (let c = 1; c <= 6; c++) {
  if (a !== b && b !== c && a !== c) TRI_ORDER.push(`${a}-${b}-${c}`);
  if (a < b && b < c) TRIO_ORDER.push(`${a}=${b}=${c}`);
}

const jstDate = (t = Date.now()) => new Date(t + 9 * 3600e3).toISOString().slice(0, 10);
const jstHm = (t = Date.now()) => new Date(t + 9 * 3600e3).toISOString().slice(11, 16);
const deadlineAt = (date, hm) => { const [y, m, d] = date.split("-").map(Number); return Date.UTC(y, m - 1, d, Number(hm.slice(0, 2)) - 9, Number(hm.slice(3, 5))); };
const keyOf = (r) => `${r.jcd}-${r.no}`;
const recipients = (env) => String(env.MAIL_TO || "").split(/[,\s]+/).filter(Boolean);

async function readJson(env, key) {
  const obj = await env.BUCKET.get(key);
  return obj ? await obj.json() : null;
}
const putJson = (env, key, data) => env.BUCKET.put(key, JSON.stringify(data), { httpMetadata: { contentType: "application/json" } });

export async function ai2Settings(env) {
  const s = (await readJson(env, SETTINGS)) || {};
  return { ev: okEv(Number(s.ev)) ? Number(s.ev) : DEFAULTS.ev, auto: typeof s.auto === "boolean" ? s.auto : DEFAULTS.auto, updated_at: s.updated_at || null };
}

// ---- 公式サイトの読み取り ----
async function fetchText(url) {
  const res = await fetch(url, { headers: { "user-agent": UA, "accept-language": "ja" } });
  if (!res.ok) throw new Error(`公式サイト ${res.status}`);
  return res.text();
}

const cellText = (s) => s.replace(/<[^>]*>/g, "").replace(/&nbsp;/g, " ").trim();

// オッズ表（odds3t・odds3f の最後の table1）を {組番: オッズ} にする。
// 行のはじめのセルが 2着（3連複は2番目）の艇番のとき、その行は [2着, 3着, オッズ] × 1着の6列、ほかの行は [3着, オッズ] × 6列。
// 3連単の2着の艇番は1列目（1号艇）のものしか見ず、1着の列ごとに読み替える（1着と同じ番号は飛ばすため）
export function parseOdds(html, trio) {
  if (html.includes("該当レースは中止")) return { cancelled: true, odds: {} };
  const start = html.lastIndexOf('class="table1');
  const tb = html.indexOf("<tbody", start), te = html.indexOf("</tbody>", tb);
  if (start < 0 || tb < 0 || te < 0) return { odds: {} };
  const odds = {};
  let b2 = null;
  for (const tr of html.slice(tb, te).split("</tr>")) {
    const tds = [...tr.matchAll(/<td([^>]*)>([\s\S]*?)<\/td>/g)].map((m) => ({ cls: m[1], text: cellText(m[2]) }));
    if (!tds.length) continue;
    let pairs;
    if (tds[0].cls.includes("is-fs14")) {
      b2 = Number(tds[0].text);
      pairs = [];
      for (let i = 1; i + 1 < tds.length; i += 3) pairs.push([tds[i], tds[i + 1]]);
    } else {
      pairs = [];
      for (let i = 0; i + 1 < tds.length; i += 2) pairs.push([tds[i], tds[i + 1]]);
    }
    if (!b2) continue;
    for (let i = 0; i < pairs.length && i < 6; i++) {
      const b1 = i + 1, b3 = Number(pairs[i][0].text), o = Number(pairs[i][1].text.replace(/,/g, ""));
      if (trio) {
        if (!(b1 < b2 && b2 < b3)) break;
        if (o > 0) odds[`${b1}=${b2}=${b3}`] = o;
      } else {
        const second = b2 - (b1 >= b2 ? 1 : 0);
        if (o > 0 && b3 >= 1 && b3 <= 6) odds[`${b1}-${second}-${b3}`] = o;
      }
    }
  }
  return { odds };
}

// レース結果のページから 3連単・3連複の結果と払戻（100円あたり）を読む。まだ出ていなければ null
export function parseResult(html) {
  if (html.includes("レース中止")) return { refund: true };
  const out = {};
  for (const m of html.matchAll(/<td[^>]*>\s*(3連単|3連複)\s*<\/td>([\s\S]*?)<\/tr>/g)) {
    const kind = m[1];
    if (out[kind]) continue;
    const nums = [...m[2].matchAll(/numberSet1_number[^>]*>\s*(\d)\s*</g)].map((x) => Number(x[1]));
    const yen = (m[2].match(/is-payout1[^>]*>([^<]*)</) || [])[1];
    if (m[2].includes("不成立")) { out[kind] = { refund: true }; continue; }
    if (nums.length !== 3 || !yen || !/\d/.test(yen)) continue;
    out[kind] = { combo: kind === "3連複" ? [...nums].sort().join("=") : nums.join("-"), yen: Number(yen.replace(/[^\d]/g, "")) };
  }
  return out["3連単"] && out["3連複"] ? out : null;
}

// ---- 締切5分前の判定 ----
export function choose(probs, tri, trio, ev) {
  const picks = [];
  for (const [type, order, p, odds] of [["3連単", TRI_ORDER, probs.tri, tri], ["3連複", TRIO_ORDER, probs.trio, trio]]) {
    const rule = RULES[type];
    const cands = order.map((combo, i) => ({ type, combo, prob: p[i], odds: odds[combo] }))
      .filter((c) => c.odds && c.prob >= rule.minProb && c.odds >= rule.minOdds && c.odds <= rule.maxOdds)
      .map((c) => ({ ...c, ev: Math.round(c.prob * c.odds * 100) / 100 }))
      .filter((c) => c.ev >= ev)
      .sort((a, b) => b.ev - a.ev)
      .slice(0, rule.max);
    picks.push(...cands);
  }
  return picks;
}

// 選んだ舟券をまとめて買ったときの数字（式別ごとに「どれかが当たる確率」と期待回収率）
export function summary(picks) {
  const out = {};
  for (const type of Object.keys(RULES)) {
    const xs = picks.filter((p) => p.type === type);
    if (xs.length) out[type] = { n: xs.length, hit: xs.reduce((s, p) => s + p.prob, 0), ret: xs.reduce((s, p) => s + p.ev, 0) / xs.length };
  }
  return out;
}

async function judge(env, race, date, ev, probsCache, now) {
  const base = { date, jcd: race.jcd, venue: race.venue, no: race.no, deadline: race.deadline, judged_at: jstHm(now), ev, picks: [] };
  try {
    if (!probsCache[race.jcd]) probsCache[race.jcd] = await readJson(env, KEY(`probs/${race.jcd}.json`));
    const pv = probsCache[race.jcd];
    const probs = pv && pv.date === date && pv.races.find((r) => r.no === race.no);
    if (!probs) return { ...base, note: "AIの確率がありません" };
    const q = `?rno=${race.no}&jcd=${race.jcd}&hd=${date.replace(/-/g, "")}`;
    const [t, f] = await Promise.all([fetchText(`${BASE}/odds3t${q}`), fetchText(`${BASE}/odds3f${q}`)]);
    const tri = parseOdds(t, false), trio = parseOdds(f, true);
    if (tri.cancelled || trio.cancelled) return { ...base, note: "レース中止" };
    const n = Object.keys(tri.odds).length;
    if (n < 60) return { ...base, note: `オッズを読み取れませんでした（3連単 ${n}通り）` };
    return { ...base, picks: choose(probs, tri.odds, trio.odds, ev) };
  } catch (e) {
    return { ...base, note: `オッズを取れませんでした（${e.message}）` };
  }
}

function logRows(race, result) {
  return race.picks.map((p) => {
    let res = "", hit = "", pay = "";
    if (result && result.refund) [res, hit, pay] = ["返還", 0, 100];
    else if (result && result[p.type]) {
      const r = result[p.type];
      if (r.refund) [res, hit, pay] = ["返還", 0, 100];
      else [res, hit, pay] = [r.combo, Number(r.combo === p.combo), r.combo === p.combo ? r.yen : 0];
    }
    return [race.date, race.jcd, race.venue, race.no, race.deadline, p.type, p.combo, (p.prob * 100).toFixed(1), p.odds, p.ev.toFixed(2), race.ev, race.judged_at, res, hit, pay];
  });
}

async function appendLog(env, rows) {
  if (!rows.length) return;
  const obj = await env.BUCKET.get(LOG);
  const text = obj ? await obj.text() : "﻿" + LOG_COLUMNS.join(",") + "\r\n";
  const add = rows.map((r) => r.map((v) => (/[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v)).join(",") + "\r\n").join("");
  await env.BUCKET.put(LOG, text + add, { httpMetadata: { contentType: "text/csv; charset=utf-8" } });
}

// ---- メール ----
const pct = (p) => `${(p * 100).toFixed(1)}%`;
export function buildMail(races) {
  const withPicks = races.filter((r) => r.picks.length);
  const head = withPicks[0];
  const subject = withPicks.length === 1
    ? `【BoatLab AI 2】${head.venue} ${head.no}R ${head.deadline}締切 お買い得 ${head.picks.length}点`
    : `【BoatLab AI 2】お買い得 ${withPicks.length}レース`;
  const lines = [];
  for (const r of withPicks) {
    lines.push(`■ ${r.venue} ${r.no}R　${r.deadline}締切`, `締切5分前（${r.judged_at}）のオッズで、期待値${r.ev}以上の舟券です。`);
    for (const p of r.picks) lines.push(`　${p.type} ${p.combo}　AI確率 ${pct(p.prob)}　オッズ ${p.odds}倍　期待値 ${p.ev.toFixed(2)}`);
    for (const [type, s] of Object.entries(summary(r.picks))) {
      lines.push(`　${type} ${s.n}点（各100円）：どれかが当たる確率 約${pct(s.hit)}／期待回収率 約${Math.round(s.ret * 100)}%`);
    }
    lines.push("");
  }
  lines.push(
    "※期待値＝AI確率×オッズ。1を超えると、長い目で見て得になる計算です。",
    "※オッズは締切まで動きます（人気の目は下がることが多い）。AI確率は計算値で、的中を保証しません。",
  );
  return { subject, text: lines.join("\n") };
}

async function mailRaces(env, races) {
  if (!recipients(env).length || !env.MAIL) return { ok: false, reason: "MAIL_TO が未設定です" };
  if (!races.some((r) => r.picks.length)) return { ok: false, reason: "メールで送る舟券がありません" };
  const { subject, text } = buildMail(races);
  await sendMail(env, subject, text, { name: "BoatLab AI 2" });
  return { ok: true, count: races.filter((r) => r.picks.length).length };
}

// ダッシュボードのボタン：締切前の判定済みレースを送る。無ければ、いちばん新しい判定を送る
async function mailNow(env) {
  const live = (await readJson(env, LIVE)) || { races: {} };
  const all = Object.values(live.races).filter((r) => r.picks.length).sort((a, b) => (a.deadline < b.deadline ? -1 : 1));
  const open = all.filter((r) => deadlineAt(r.date, r.deadline) > Date.now());
  const races = open.length ? open : all.slice(-1);
  if (!races.length) return { ok: false, reason: "いま送れる判定はありません（締切5分前に判定します）" };
  return mailRaces(env, races);
}

// ---- Cron ----
export async function ai2Scheduled(event, env) {
  const now = event.scheduledTime;
  const today = jstDate(now);
  const live = (await readJson(env, LIVE)) || { races: {} };
  const out = { judged: [], graded: [], mailed: 0 };
  let changed = false;
  const rows = [];

  // 1) 締切5分前になったレースを判定する
  const sched = await readJson(env, KEY("schedule.json"));
  if (sched && sched.date === today) {
    const due = sched.races.filter((r) => {
      const left = deadlineAt(today, r.deadline) - now;
      return !live.races[keyOf(r)] && left > 1 * MIN && left <= 5.5 * MIN;
    }).slice(0, JUDGE_PER_RUN);
    if (due.length) {
      const { ev, auto } = await ai2Settings(env);
      const cache = {};
      for (const r of due) {
        const j = await judge(env, r, today, ev, cache, now);
        live.races[keyOf(r)] = j;
        out.judged.push(`${keyOf(r)}:${j.picks.length}${j.note ? `(${j.note})` : ""}`);
        changed = true;
      }
      const fresh = due.map((r) => live.races[keyOf(r)]).filter((r) => r.picks.length);
      if (auto && fresh.length) {
        try {
          const res = await mailRaces(env, fresh);
          if (res.ok) { out.mailed = res.count; for (const r of fresh) r.mailed = true; }
        } catch (e) {
          out.mailError = e.message;
        }
      }
    }
  }

  // 2) 終わったレースの答え合わせ。候補が無いレースは締切が過ぎたら消す
  let fetched = 0;
  for (const [k, r] of Object.entries(live.races)) {
    const since = now - deadlineAt(r.date, r.deadline);
    if (!r.picks.length) {
      if (since > 0) { delete live.races[k]; changed = true; }
      continue;
    }
    if (since < 10 * MIN) continue;
    let result = null;
    if (r.date === today && since <= 120 * MIN && fetched < RESULT_PER_RUN && !(r.tried && now - r.tried < 3 * MIN)) {
      fetched++;
      r.tried = now;
      changed = true;
      try {
        result = parseResult(await fetchText(`${BASE}/raceresult?rno=${r.no}&jcd=${r.jcd}&hd=${r.date.replace(/-/g, "")}`));
      } catch (e) {
        r.error = e.message;
      }
    }
    if (result || since > 120 * MIN || r.date !== today) {
      rows.push(...logRows(r, result)); // 結果が取れなかったときは空欄で残し、翌朝の処理が埋める
      delete live.races[k];
      out.graded.push(`${k}:${result ? "ok" : "later"}`);
      changed = true;
    }
  }
  await appendLog(env, rows);
  if (changed) await putJson(env, LIVE, live);
  return out;
}

// ---- 管理者ダッシュボードの BoatLab AI 2 タブ（admin.js の handleAdmin から呼ぶ。Access の確認は済んでいる） ----
export async function handleAi2Admin(request, env, url, sub, PRIVATE) {
  const asJson = (body, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  const sameSite = (tag) => request.headers.get("origin") === url.origin && request.headers.get("x-boatlab-admin") === tag;
  const NOT_FOUND = () => new Response("ページが見つかりません", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });

  if (sub === "/ai2" && request.method === "GET") return Response.redirect(`${url.origin}${url.pathname}/`, 302);
  if (sub === "/ai2/" && request.method === "GET") {
    return new Response(PAGE, { headers: { ...PRIVATE, "content-type": "text/html; charset=utf-8", "content-security-policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'" } });
  }
  if (sub === "/ai2/live" && request.method === "GET") {
    const live = (await readJson(env, LIVE)) || { races: {} };
    const sched = await readJson(env, KEY("schedule.json"));
    const today = jstDate(), now = Date.now();
    // 次に判定するレース（締切5分前より前のもの）を少しだけ
    const next = sched && sched.date === today
      ? sched.races.filter((r) => !live.races[keyOf(r)] && deadlineAt(today, r.deadline) - now > 1 * MIN).slice(0, 5)
      : [];
    const races = Object.values(live.races).map((r) => ({ ...r, summary: summary(r.picks) })).sort((a, b) => (a.deadline < b.deadline ? -1 : 1));
    return asJson({
      today, races, next, ready: !!(sched && sched.date === today), model: sched && sched.model, generated: sched && sched.generated,
      settings: await ai2Settings(env), choices: EV_CHOICES, rules: RULES, mail: recipients(env).length > 0,
    });
  }
  if (sub === "/ai2/log.csv" && request.method === "GET") {
    const obj = await env.BUCKET.get(LOG);
    const headers = { ...PRIVATE, "content-type": "text/csv; charset=utf-8" };
    if (url.searchParams.has("dl")) headers["content-disposition"] = `attachment; filename="BoatLabAI2_${jstDate().replace(/-/g, "")}.csv"`;
    return new Response(obj ? obj.body : "", { headers });
  }
  if (sub === "/ai2/settings" && request.method === "POST") {
    if (!sameSite("ai2-settings")) return NOT_FOUND();
    const body = await request.json().catch(() => ({}));
    const cur = await ai2Settings(env);
    const next = { ev: cur.ev, auto: cur.auto };
    if ("ev" in body) {
      const v = Math.round(Number(body.ev) * 100) / 100;
      if (!okEv(v)) return asJson({ ok: false, reason: "しきい値は1.00〜10.00で入れてください" }, 400);
      next.ev = v;
    }
    if ("auto" in body) next.auto = body.auto === true;
    next.updated_at = new Date(Date.now() + 9 * 3600e3).toISOString().replace("Z", "+09:00");
    await putJson(env, SETTINGS, next);
    return asJson({ ok: true, ...next });
  }
  if (sub === "/ai2/mail" && request.method === "POST") {
    if (!sameSite("ai2-mail")) return NOT_FOUND();
    try {
      return asJson(await mailNow(env));
    } catch (e) {
      return asJson({ ok: false, reason: `送信に失敗しました（${e.message}）` });
    }
  }
  return NOT_FOUND();
}
