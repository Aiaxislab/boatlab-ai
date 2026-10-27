// 毎朝、的中確率が高いレースだけを自分宛てにメールする。
// 毎朝の処理が R2 に置く admin/picks.json を、Worker の Cron（6:00〜8:30 に30分おき）で見に行き、
// 今日の分ができていて、まだ送っていなければ1回だけ送る。
// 送り先 MAIL_TO は GitHub Secrets から渡す（カンマ区切りで複数可。Cloudflare Email Routing で確認済みのアドレスだけに届く）。

const SENT_KEY = "admin/mail-sent.json";
export const LOG_KEY = "admin/mail-log.json"; // 送ったレースの記録（ダッシュボードで結果と突き合わせる）
const WEEK = ["日", "月", "火", "水", "木", "金", "土"];
const combo = (c, sep = "-") => c.join(sep);
const pct = (p) => (p * 100).toFixed(1) + "%";
const jstDate = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

// 的中確率の範囲の言い方（max が 1 なら上限なし）
export const rangeText = (min, max = 1) => `${Math.round(min * 100)}%超え${max < 1 ? `${Math.round(max * 100)}%未満` : ""}`;

export function buildMail(picks, min, max = 1) {
  // Worker は UTC で動くので、日付の文字列から直接つくる
  const [y, m, dd] = picks.date.split("-").map(Number);
  const day = `${m}/${dd}（${WEEK[new Date(Date.UTC(y, m - 1, dd)).getUTCDay()]}）`;
  const races = picks.races.filter((r) => r.buy_prob > min && (max >= 1 || r.buy_prob < max)).sort((a, b) => (a.deadline < b.deadline ? -1 : 1));
  const subject = races.length
    ? `【BoatLab AI】${day} 的中確率${rangeText(min, max)} ${races.length}レース`
    : `【BoatLab AI】${day} 的中確率${rangeText(min, max)}のレースはありません`;
  const lines = [
    `${day}の予想で、買い目（3連単6点）の的中確率が${Math.round(min * 100)}%を超え${max < 1 ? `、${Math.round(max * 100)}%未満の` : "た"}レースです。`,
    races.length ? `全${picks.races.length}レース中 ${races.length}レース。締切が早い順に並べています。` : `今日は該当するレースがありませんでした（全${picks.races.length}レース）。`,
    "",
  ];
  for (const r of races) {
    lines.push(
      `■ ${r.venue} ${r.no}R　${r.deadline}締切（${r.kind}）`,
      `本命：${r.honmei}号艇 ${r.honmei_name}`,
      `的中確率：約${Math.round(r.buy_prob * 100)}%`,
      `買い方：3連単 6点を各100円（計600円）`,
      ...r.buy.map((b, i) => `　${i + 1}. ${combo(b.combo)}（${pct(b.prob)}）`),
      `押さえ：3連複 ${r.trio.map((c) => combo(c, "=")).join("・")}（3点で約${Math.round(r.trio_prob * 100)}%）`,
      `PDF：https://boat.aiaxislab.win/${r.pdf}`,
      "",
    );
  }
  lines.push(
    "※的中確率はAIによる計算値で、的中を保証するものではありません。",
    "※朝の時点の情報で計算しています（展示・オッズ・直前の天候は入っていません）。",
    `予想作成：${picks.generated}`,
  );
  return { subject, text: lines.join("\n"), count: races.length, keys: races.map((r) => `${r.jcd}-${r.no}`) };
}

function b64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

const recipients = (env) => String(env.MAIL_TO || "").split(/[,\s]+/).filter(Boolean);

// 1通に1人ずつ送る（お互いのアドレスが見えないように）
export async function sendMail(env, subject, text) {
  for (const to of recipients(env)) await sendOne(env, to, subject, text);
}

async function sendOne(env, to, subject, text) {
  const { EmailMessage } = await import("cloudflare:email");
  const from = env.MAIL_FROM || "noreply@aiaxislab.win";
  const raw = [
    `From: BoatLab AI <${from}>`,
    `To: <${to}>`,
    `Subject: =?UTF-8?B?${b64(subject)}?=`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${crypto.randomUUID()}@${from.split("@")[1]}>`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    b64(text).replace(/.{76}/g, "$&\r\n"),
  ].join("\r\n");
  await env.MAIL.send(new EmailMessage(from, to, raw));
}

export const SETTINGS_KEY = "admin/settings.json"; // ダッシュボードで変える設定（メールの基準など）
export const MIN_CHOICES = [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80];
export const MAX_CHOICES = [25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100]; // 上限（未満）。100 は上限なし

// メールの基準（%）。ダッシュボードで選んだ値 → なければ MAIL_MIN → なければ50%
export async function mailMinPct(env) {
  const obj = await env.BUCKET.get(SETTINGS_KEY);
  const saved = obj ? Number((await obj.json()).mailMin) : NaN;
  return MIN_CHOICES.includes(saved) ? saved : Number(env.MAIL_MIN) || 50;
}

// メールの上限（%、未満）。選んでいなければ100（上限なし）
export async function mailMaxPct(env) {
  const obj = await env.BUCKET.get(SETTINGS_KEY);
  const saved = obj ? Number((await obj.json()).mailMax) : NaN;
  return MAX_CHOICES.includes(saved) ? saved : 100;
}

// force: ダッシュボードの「テスト送信」。送ったかどうかの記録を無視して今すぐ送る
export async function dailyMail(env, { force = false } = {}) {
  if (!recipients(env).length || !env.MAIL) return { ok: false, reason: "MAIL_TO が未設定です" };
  const obj = await env.BUCKET.get("admin/picks.json");
  if (!obj) return { ok: false, reason: "今日の予想データがまだありません" };
  const picks = await obj.json();
  const today = jstDate();
  if (picks.date !== today && !force) return { ok: false, reason: "今日の予想データがまだありません" };
  const sent = await env.BUCKET.get(SENT_KEY);
  if (!force && sent && (await sent.json()).date === today) return { ok: false, reason: "今日はもう送りました" };
  const min = (await mailMinPct(env)) / 100, max = (await mailMaxPct(env)) / 100;
  const { subject, text, count, keys } = buildMail(picks, min, max);
  await sendMail(env, subject, text);
  if (!force) {
    await env.BUCKET.put(SENT_KEY, JSON.stringify({ date: today, count }));
    const old = await env.BUCKET.get(LOG_KEY);
    const log = (old ? await old.json() : []).filter((d) => d.date !== picks.date);
    log.push({ date: picks.date, min, max, races: keys });
    await env.BUCKET.put(LOG_KEY, JSON.stringify(log));
  }
  return { ok: true, count, date: picks.date };
}
