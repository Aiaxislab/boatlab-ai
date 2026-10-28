// FX（AI Axis Lab の自分用FXアドバイザー）。判定そのものは GitHub Actions の「FXの判定」（fx/ の Python）が作り、
// R2 の admin/fx/ に置く。この Worker は、それを見せる・送るだけ。
// 1) 管理者ダッシュボードの FX タブ（/<ADMIN_PATH>/fx/）。守りは admin.js と同じ（Access ＋ JWT の検証）
// 2) Claude に読ませる要約の非公開URL（/<FX_SHARE_PATH>）。ログイン不要。URLの文字列は GitHub Secrets に置く
// 3) 毎朝8:30の判定のメール（Cron が 8:00〜10:55 に5分おきに見て、今日の分ができていれば1回だけ送る）
// 4) GH_DISPATCH_TOKEN があれば、8:20 に判定のワークフローを起動する（GitHub の時刻指定の遅れ対策）。ダッシュボードの「最新に更新」も同じ

import PAGE from "./fx.html";
import { sendMail } from "../mail.js";

export const FX_CRON = "*/5 23,0,1 * * *"; // 日本時間 8:00〜10:55 の5分おき（wrangler.toml と同じ文字列にする）
const KEY = (name) => `admin/fx/${name}`;
const SENT_KEY = KEY("mail-sent.json");
const REPO = "Aiaxislab/boatlab-ai";
const WORKFLOW = "fx.yml";
const NOT_FOUND = () => new Response("ページが見つかりません", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
const jstNow = () => new Date(Date.now() + 9 * 3600e3);
const jstDate = () => jstNow().toISOString().slice(0, 10);
const recipients = (env) => String(env.FX_MAIL_TO || env.MAIL_TO || "").split(/[,\s]+/).filter(Boolean);

async function readJson(env, name) {
  const obj = await env.BUCKET.get(KEY(name));
  return obj ? await obj.json() : null;
}

// ---- 管理者ダッシュボードの FX タブ（admin.js の handleAdmin から呼ぶ。Access の確認は済んでいる） ----
export async function handleFxAdmin(request, env, url, sub, PRIVATE) {
  const asJson = (body, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  const sameSite = (tag) => request.headers.get("origin") === url.origin && request.headers.get("x-boatlab-admin") === tag;

  if (sub === "/fx" && request.method === "GET") return Response.redirect(`${url.origin}${url.pathname}/`, 302);
  if (sub === "/fx/" && request.method === "GET") {
    return new Response(PAGE, { headers: { ...PRIVATE, "content-type": "text/html; charset=utf-8", "content-security-policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'" } });
  }
  if ((sub === "/fx/latest" || sub === "/fx/stats") && request.method === "GET") {
    const obj = await env.BUCKET.get(KEY(sub === "/fx/latest" ? "latest.json" : "stats.json"));
    return asJson(obj ? obj.body : "null");
  }
  if (sub === "/fx/scores.csv" && request.method === "GET") {
    const obj = await env.BUCKET.get(KEY("scores.csv"));
    const headers = { ...PRIVATE, "content-type": "text/csv; charset=utf-8" };
    if (url.searchParams.has("dl")) headers["content-disposition"] = `attachment; filename="FX_scores_${jstDate().replace(/-/g, "")}.csv"`;
    return new Response(obj ? obj.body : "", { headers });
  }
  if (sub === "/fx/info" && request.method === "GET") {
    const share = sharePath(env);
    return asJson({ share: share ? `${url.origin}/${share}` : null, mail: recipients(env).length > 0, dispatch: !!env.GH_DISPATCH_TOKEN, sent: await readJson(env, "mail-sent.json") });
  }
  if (sub === "/fx/mail" && request.method === "POST") {
    if (!sameSite("fx-mail")) return NOT_FOUND();
    try {
      return asJson(await sendLatest(env));
    } catch (e) {
      return asJson({ ok: false, reason: `送信に失敗しました（${e.message}）` });
    }
  }
  if (sub === "/fx/refresh" && request.method === "POST") {
    if (!sameSite("fx-refresh")) return NOT_FOUND();
    return asJson(await dispatch(env, "intraday"));
  }
  return NOT_FOUND();
}

// ---- Claude に読ませる要約（ログイン不要の非公開URL） ----
function sharePath(env) {
  const p = env.FX_SHARE_PATH && String(env.FX_SHARE_PATH).replace(/^\/+|\/+$/g, "");
  return p && p.length >= 16 ? p : null;
}

export async function handleFxShare(request, env, url) {
  const p = sharePath(env);
  if (!p || url.hostname.startsWith("boat.") || request.method !== "GET") return null;
  const root = `/${p}`;
  if (url.pathname !== root && !url.pathname.startsWith(root + "/")) return null;
  const sub = url.pathname.slice(root.length) || "/";
  // 検索に載せない・どこにも埋め込ませない。robots.txt では拒否しない（拒否すると Claude も読めなくなる）
  const headers = { "cache-control": "no-store", "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer", "x-frame-options": "DENY" };
  const files = { "/": ["share.md", "text/plain; charset=utf-8"], "/scores.csv": ["scores.csv", "text/csv; charset=utf-8"], "/stats.json": ["stats.json", "application/json; charset=utf-8"] };
  if (!files[sub]) return NOT_FOUND();
  const obj = await env.BUCKET.get(KEY(files[sub][0]));
  if (!obj) return new Response("まだデータがありません（毎朝8:30の判定のあとにできます）", { status: 404, headers: { ...headers, "content-type": "text/plain; charset=utf-8" } });
  return new Response(obj.body, { headers: { ...headers, "content-type": files[sub][1] } });
}

// ---- メール ----
async function send(env, subject, text) {
  const to = recipients(env);
  if (!to.length || !env.MAIL) return { ok: false, reason: "FX_MAIL_TO（または MAIL_TO）が未設定です" };
  await sendMail(env, subject, text, { to, name: "AI Axis Lab FX" });
  return { ok: true };
}

// ダッシュボードの「いまの見解をメールで送る」。最新の判定（8:30 か途中経過）をすぐ送る
async function sendLatest(env) {
  const latest = await readJson(env, "latest.json");
  if (!latest) return { ok: false, reason: "まだ判定がありません" };
  const r = await send(env, latest.mail.subject, latest.mail.text);
  return { ...r, time: latest.time };
}

// 毎朝8:30の判定。今日の分ができていて、まだ送っていなければ1回だけ送る
export async function fxMorningMail(env) {
  const today = jstDate();
  const obj = await env.BUCKET.get(KEY(`judge/${today}.json`));
  if (!obj) return { ok: false, reason: "今日の8:30の判定はまだです" };
  const sent = await readJson(env, "mail-sent.json");
  if (sent && sent.date === today) return { ok: false, reason: "今日はもう送りました" };
  const judge = await obj.json();
  const r = await send(env, judge.mail.subject, judge.mail.text);
  if (r.ok) await env.BUCKET.put(SENT_KEY, JSON.stringify({ date: today, at: jstNow().toISOString().replace("Z", "+09:00") }), { httpMetadata: { contentType: "application/json" } });
  return r;
}

// ---- 判定のワークフローを起動（GH_DISPATCH_TOKEN が無ければ何もしない。そのときは GitHub の時刻指定だけで動く） ----
async function dispatch(env, mode) {
  if (!env.GH_DISPATCH_TOKEN) return { ok: false, reason: "GH_DISPATCH_TOKEN が未登録なので、ここからは起動できません（判定は2時間おきに自動で更新されます）" };
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "aiaxislab-worker", "content-type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { mode } }),
  });
  return res.status === 204 ? { ok: true, reason: "取得を始めました。5分ほどで反映されます" } : { ok: false, reason: `起動できませんでした（GitHub ${res.status}）` };
}

// Cron（FX_CRON）から呼ぶ
export async function fxScheduled(event, env) {
  const t = new Date(event.scheduledTime);
  const out = {};
  if (t.getUTCHours() === 23 && t.getUTCMinutes() === 20) out.dispatch = await dispatch(env, "morning");
  out.mail = await fxMorningMail(env);
  return out;
}
