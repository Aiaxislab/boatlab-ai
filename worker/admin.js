// 管理者ダッシュボード（https://aiaxislab.win/<ADMIN_PATH>/）。
// 守りは2重: 1) Cloudflare Access のログイン（許可したメールアドレスだけ）
//            2) Worker 側でも Access の証明書（JWT）を検証し、無ければ普通の404を返す
// ADMIN_PATH・ACCESS_TEAM（・ACCESS_AUD）は公開リポジトリに書かず、GitHub Secrets から公開時に渡す。
// ADMIN_PATH か ACCESS_TEAM が未設定なら、ダッシュボードはどこにも存在しないのと同じ扱いになる。
// ACCESS_AUD は任意。無いときは「自分のチームが発行した証明書か」までを確かめる
// （チームの Access アプリがこれ1つなら守りは同じ。アプリを増やすなら AUD も登録すること）。

import PAGE from "./admin.html";
import { dailyMail, LOG_KEY } from "./mail.js";

const CSV_KEY = "admin/races.csv";
const META_KEY = "admin/meta.json";
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
  const sum = { pdf: 0, csv: 0, data: 0, other: 0, pdfFiles: 0, oldestPdf: null };
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
      else sum.other += o.size;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  const site = Number(env.SITE_BYTES) || 0;
  return { ...sum, site, total: sum.pdf + sum.csv + sum.data + sum.other + site, limit: 10 * 1024 ** 3, mail: !!env.MAIL_TO, mailMin: Number(env.MAIL_MIN) || 50 };
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
  if (sub === "/mail-log" && request.method === "GET") {
    const obj = await env.BUCKET.get(LOG_KEY);
    return new Response(obj ? obj.body : "[]", { headers: { ...PRIVATE, "content-type": "application/json; charset=utf-8" } });
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
