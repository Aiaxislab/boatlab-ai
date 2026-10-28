// 管理者ダッシュボード（https://aiaxislab.win/<ADMIN_PATH>/）。
// 守りは2重: 1) Cloudflare Access のログイン（許可したメールアドレスだけ）
//            2) Worker 側でも Access の証明書（JWT）を検証し、無ければ普通の404を返す
// ADMIN_PATH・ACCESS_TEAM（・ACCESS_AUD）は公開リポジトリに書かず、GitHub Secrets から公開時に渡す。
// ADMIN_PATH か ACCESS_TEAM が未設定なら、ダッシュボードはどこにも存在しないのと同じ扱いになる。
// ACCESS_AUD は任意。無いときは「自分のチームが発行した証明書か」までを確かめる
// （チームの Access アプリがこれ1つなら守りは同じ。アプリを増やすなら AUD も登録すること）。

import PAGE from "./admin.html";
import { BUY_LIMIT_MAX, BUY_MAX_CHOICES, BUY_MIN_CHOICES, buildBuyList, buySettings, okLimit } from "./buy.js";
import { checkUsage, LIMITS, RATE, readGuard, writeGuard } from "./guard.js";
import { handleFxAdmin } from "./fx/fx.js";
import { dailyMail, LOG_KEY, SETTINGS_KEY, MIN_CHOICES, MAX_CHOICES, mailMinPct, mailMaxPct } from "./mail.js";

const CSV_KEY = "admin/races.csv";
const META_KEY = "admin/meta.json";
const MODEL_KEY = "admin/ml/model.json"; // 週1回の「AIモデルの学習」が置く
const NOT_FOUND = () => new Response("ページが見つかりません", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
const PRIVATE = { "cache-control": "no-store", "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer", "x-frame-options": "DENY" };

const b64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const json = (s) => JSON.parse(new TextDecoder().decode(b64url(s)));

function teamHost(team) {
  const h = team.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  return h.includes(".") ? h : `${h}.cloudflareaccess.com`;
}

async function accessOk(request, env) {
  const cookie = (request.headers.get("cookie") || "").match(/(?:^|;\s*)CF_Authorization=([^;]+)/);
  const token = request.headers.get("cf-access-jwt-assertion") || (cookie && cookie[1]);
  if (!token) return false;
  try {
    const [h, p, sig] = token.split(".");
    const header = json(h), payload = json(p);
    const host = teamHost(env.ACCESS_TEAM);
    const now = Date.now() / 1000;
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (header.alg !== "RS256" || payload.iss !== `https://${host}` || (env.ACCESS_AUD && !aud.includes(env.ACCESS_AUD)) || !(payload.exp > now)) return false;
    const res = await fetch(`https://${host}/cdn-cgi/access/certs`, { cf: { cacheTtl: 3600, cacheEverything: true } });
    const jwk = (await res.json()).keys.find((k) => k.kid === header.kid);
    if (!jwk) return false;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    return await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64url(sig), new TextEncoder().encode(`${h}.${p}`));
  } catch {
    return false;
  }
}

async function usage(env) {
  const sum = { pdf: 0, csv: 0, data: 0, ml: 0, other: 0, pdfFiles: 0, oldestPdf: null };
  let cursor;
  do {
    const page = await env.BUCKET.list({ cursor, limit: 1000 });
    for (const o of page.objects) {
      if (o.key.startsWith("pdf/")) {
        sum.pdf += o.size;
        sum.pdfFiles++;
        const d = o.key.slice(4, 14);
        if (!sum.oldestPdf || d < sum.oldestPdf) sum.oldestPdf = d;
      } else if (o.key === CSV_KEY) sum.csv += o.size;
      else if (o.key.startsWith("data/")) sum.data += o.size;
      else if (o.key.startsWith("admin/ml/")) sum.ml += o.size;
      else sum.other += o.size;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  const site = Number(env.SITE_BYTES) || 0;
  return { ...sum, site, total: sum.pdf + sum.csv + sum.data + sum.ml + sum.other + site, limit: 10 * 1024 ** 3, mail: !!env.MAIL_TO, mailMin: await mailMinPct(env), mailMax: await mailMaxPct(env), mailChoices: MIN_CHOICES, mailMaxChoices: MAX_CHOICES };
}

const jstDate = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

export async function handleAdmin(request, env, url) {
  const base = env.ADMIN_PATH && String(env.ADMIN_PATH).replace(/^\/+|\/+$/g, "");
  if (!base || base.length < 16 || !env.ACCESS_TEAM) return null;
  if (url.hostname.startsWith("boat.")) return null;
  const root = `/${base}`;
  if (url.pathname !== root && !url.pathname.startsWith(root + "/")) return null;
  if (!(await accessOk(request, env))) return NOT_FOUND();

  const sub = url.pathname.slice(root.length) || "/";
  if (sub === "/fx" || sub.startsWith("/fx/")) return handleFxAdmin(request, env, url, sub, PRIVATE);
  if (sub === "/" && request.method === "GET") {
    if (!url.pathname.endsWith("/")) return Response.redirect(`${url.origin}${root}/`, 302);
    return new Response(PAGE, { headers: { ...PRIVATE, "content-type": "text/html; charset=utf-8", "content-security-policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'" } });
  }
  if (sub === "/races.csv" && request.method === "GET") {
    const obj = await env.BUCKET.get(CSV_KEY);
    const meta = await env.BUCKET.get(META_KEY);
    const headers = { ...PRIVATE, "content-type": "text/csv; charset=utf-8", "x-cleared-from": meta ? (await meta.json()).from || "" : "" };
    if (url.searchParams.has("dl")) headers["content-disposition"] = `attachment; filename="BoatLabAI_races_${jstDate().replace(/-/g, "")}.csv"`;
    return new Response(obj ? obj.body : "", { headers });
  }
  if (sub === "/usage" && request.method === "GET") {
    return new Response(JSON.stringify(await usage(env)), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/model" && request.method === "GET") {
    // AI学習モデルの状態（重みそのものは大きいので、主なものだけ返す）
    const obj = await env.BUCKET.get(MODEL_KEY);
    let body = "null";
    if (obj) {
      const { weights = {}, ...m } = await obj.json();
      m.n_weights = Object.keys(weights).length;
      m.main_weights = Object.fromEntries(Object.entries(weights).filter(([k]) => !k.startsWith("V")));
      body = JSON.stringify(m);
    }
    return new Response(body, { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/guard" && request.method === "GET") {
    const state = await readGuard(env);
    return new Response(JSON.stringify({ ...state, limits: LIMITS, rate: RATE, analytics: !!(env.CF_ANALYTICS_TOKEN && env.CF_ACCOUNT_ID) }), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/guard" && request.method === "POST") {
    if (request.headers.get("origin") !== url.origin || request.headers.get("x-boatlab-admin") !== "guard") return NOT_FOUND();
    const { action } = await request.json().catch(() => ({}));
    const now = new Date(Date.now() + 9 * 3600e3).toISOString().replace("Z", "+09:00");
    const state = await readGuard(env);
    if (action === "pause") await writeGuard(env, { ...state, paused: true, paused_at: now, reason: "管理者ダッシュボードから止めました", by: "admin" });
    else if (action === "resume") await writeGuard(env, { ...state, paused: false, resumed_at: now, reason: null, by: null });
    else if (action === "check") await checkUsage(env);
    else return new Response(JSON.stringify({ ok: false }), { status: 400, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
    return new Response(JSON.stringify({ ok: true, ...(await readGuard(env)) }), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/buy" && request.method === "GET") {
    // 今日買う舟券の一覧（ダッシュボードの「今日の買い目」と、パソコンの拡張機能が読む）
    const settings = await buySettings(env);
    const obj = await env.BUCKET.get("admin/picks.json");
    const picks = obj ? await obj.json() : null;
    const body = picks && picks.date === jstDate()
      ? { ready: true, ...buildBuyList(picks, settings) }
      : { ready: false, date: jstDate(), ...settings, reason: "今日の予想がまだできていません（毎朝5:30ごろから作ります）" };
    return new Response(JSON.stringify({ ...body, minChoices: BUY_MIN_CHOICES, maxChoices: BUY_MAX_CHOICES, limitMax: BUY_LIMIT_MAX }), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/mail-log" && request.method === "GET") {
    const obj = await env.BUCKET.get(LOG_KEY);
    return new Response(obj ? obj.body : "[]", { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/settings" && request.method === "POST") {
    if (request.headers.get("origin") !== url.origin || request.headers.get("x-boatlab-admin") !== "settings") return NOT_FOUND();
    // 送られてきた項目だけ変える（メールの範囲・購入の範囲・1日の上限金額）
    const body = await request.json().catch(() => ({}));
    const allowed = { mailMin: MIN_CHOICES, mailMax: MAX_CHOICES, buyMin: BUY_MIN_CHOICES, buyMax: BUY_MAX_CHOICES, buyLimit: okLimit };
    const change = {};
    for (const [k, ok] of Object.entries(allowed)) {
      if (!(k in body)) continue;
      const v = Number(body[k]);
      if (!(typeof ok === "function" ? ok(v) : ok.includes(v))) return new Response(JSON.stringify({ ok: false }), { status: 400, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
      change[k] = v;
    }
    if (!Object.keys(change).length) return new Response(JSON.stringify({ ok: false }), { status: 400, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
    const old = await env.BUCKET.get(SETTINGS_KEY);
    const prev = old ? await old.json() : {};
    // 上限（未満）は下限（以上）より大きくないといけない（100 は上限なし）
    const next = { mailMin: await mailMinPct(env), mailMax: await mailMaxPct(env), ...(await buySettings(env)), ...change };
    if (next.mailMax <= next.mailMin || next.buyMax <= next.buyMin) return new Response(JSON.stringify({ ok: false, reason: "上限は基準より大きくしてください" }), { status: 400, headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
    const settings = { ...prev, ...change, updated_at: new Date(Date.now() + 9 * 3600e3).toISOString().replace("Z", "+09:00") };
    await env.BUCKET.put(SETTINGS_KEY, JSON.stringify(settings), { httpMetadata: { contentType: "application/json" } });
    return new Response(JSON.stringify({ ok: true, ...change }), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/mail-test" && request.method === "POST") {
    if (request.headers.get("origin") !== url.origin || request.headers.get("x-boatlab-admin") !== "mail") return NOT_FOUND();
    let result;
    try {
      result = await dailyMail(env, { force: true });
    } catch (e) {
      result = { ok: false, reason: `送信に失敗しました（${e.message}）` };
    }
    return new Response(JSON.stringify(result), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  if (sub === "/clear" && request.method === "POST") {
    // 別のサイトから勝手に送らせる攻撃（CSRF）を防ぐため、同じページからの送信だけ受け付ける
    if (request.headers.get("origin") !== url.origin || request.headers.get("x-boatlab-admin") !== "clear") return NOT_FOUND();
    const now = new Date(Date.now() + 9 * 3600e3).toISOString().replace("Z", "+09:00");
    await env.BUCKET.delete(CSV_KEY);
    // この日より前のレースは、次の朝の処理でもCSVに戻さない
    await env.BUCKET.put(META_KEY, JSON.stringify({ from: jstDate(), cleared_at: now }), { httpMetadata: { contentType: "application/json" } });
    return new Response(JSON.stringify({ ok: true, from: jstDate() }), { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
  }
  return NOT_FOUND();
}
