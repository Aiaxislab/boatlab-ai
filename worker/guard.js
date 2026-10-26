// サイトの守り（お金がかかる前に止める・嫌がらせ対策）。
// 1) PDFと公開データは Cloudflare の一時保存（キャッシュ）から返し、R2 の読み出し回数を増やさない
// 2) 同じIPから短い時間に大量に取りに来たら、しばらく断る（ふつうの人が困らない、ゆるめの上限）
// 3) 30分ごとに Cloudflare の使用量を見て、無料枠の上限に近づいたらサイトを「一時停止中」にする
//    再開は管理者ダッシュボードのボタンだけ（自動では戻さない）
// 使用量を見るには、GitHub Secrets の CF_ANALYTICS_TOKEN（Account Analytics の読み取り権限）が要る。
// 無いときは 3 だけ動かない（1・2 は動く）。

export const GUARD_KEY = "admin/guard.json";

// 上限（Cloudflare の無料枠に対する割合）。ふつうの使われ方の数百倍に置き、本当のお客様を止めないようにする
export const LIMITS = {
  // R2 の読み出し（Class B）：無料枠は月1,000万回。その70%で止める
  classB: { free: 10_000_000, stopAt: 7_000_000 },
  // R2 の書き込み・一覧（Class A）：無料枠は月100万回。その70%で止める
  classA: { free: 1_000_000, stopAt: 700_000 },
  // 30分で R2 の読み出しがこれを超えたら、月の合計を待たずに止める（明らかな嫌がらせ）
  burstB: 200_000,
};

// PDF・公開データを取りに来る回数の上限（1つのIPあたり60秒で120回）。
// 携帯回線などは大勢で1つのIPを共有するので、1人では届かない多めの値にしている
export const RATE = { limit: 120, period: 60 };

const PAUSE_PAGE = `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>一時停止中｜BoatLab AI</title><meta name="robots" content="noindex">
<style>body{margin:0;font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;background:#f5f7fa;color:#14181b;display:grid;place-items:center;min-height:100vh;padding:16px}
div{max-width:520px;background:#fff;border:1px solid #d5dce3;border-radius:10px;padding:24px;line-height:1.7}h1{font-size:20px;margin:0 0 8px;color:#0a2550}</style></head>
<body><div><h1>ただいま一時停止中です</h1><p>アクセスが集中したため、サイトを一時的に止めています。しばらくしてから、もう一度お越しください。</p><p style="color:#5d666c;font-size:13px">運営：AI Axis Lab</p></div></body></html>`;

// 一時停止の状態は、毎回 R2 を読まないように、同じ Worker の中で60秒だけ覚えておく
let memo = { at: 0, paused: false };

export async function readGuard(env) {
  const obj = await env.BUCKET.get(GUARD_KEY);
  return obj ? await obj.json() : { paused: false };
}

export async function writeGuard(env, state) {
  await env.BUCKET.put(GUARD_KEY, JSON.stringify(state), { httpMetadata: { contentType: "application/json" } });
  memo = { at: Date.now(), paused: !!state.paused };
}

export async function isPaused(env) {
  if (Date.now() - memo.at > 60_000) {
    try {
      memo = { at: Date.now(), paused: !!(await readGuard(env)).paused };
    } catch {
      memo = { at: Date.now(), paused: false }; // 読めないときは止めない（お客様を弾かない）
    }
  }
  return memo.paused;
}

export const pausedResponse = () =>
  new Response(PAUSE_PAGE, { status: 503, headers: { "content-type": "text/html; charset=utf-8", "retry-after": "3600", "cache-control": "no-store" } });

export async function rateLimited(env, request) {
  if (!env.RATE_LIMIT) return false;
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  try {
    const { success } = await env.RATE_LIMIT.limit({ key: ip });
    return !success;
  } catch {
    return false;
  }
}

export const tooMany = () =>
  new Response("短い時間にたくさんのアクセスがあったため、少し時間をおいてからお試しください。", {
    status: 429, headers: { "content-type": "text/plain; charset=utf-8", "retry-after": "60" },
  });

// キャッシュ（Cloudflare の一時保存）を通して返す。PDF は日付ごとに中身が変わらないので1日、データは5分
export async function cached(request, ctx, load) {
  const cache = caches.default;
  // ?以降を付けて毎回ちがうURLにしても、同じ一時保存を使う（R2 を読ませる嫌がらせを防ぐ）
  const u = new URL(request.url);
  u.search = "";
  const key = new Request(u.toString(), { method: "GET" });
  const hit = await cache.match(key);
  if (hit) return hit;
  const res = await load();
  if (res.ok) ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}

// ---- 使用量のチェック（30分ごと） ----

const GQL = `query($acc: String!, $start: Time!, $end: Time!, $since: Time!) {
  viewer { accounts(filter: { accountTag: $acc }) {
    month: r2OperationsAdaptiveGroups(limit: 100, filter: { datetime_geq: $start, datetime_leq: $end }) { sum { requests } dimensions { actionType } }
    recent: r2OperationsAdaptiveGroups(limit: 100, filter: { datetime_geq: $since, datetime_leq: $end }) { sum { requests } dimensions { actionType } }
    workers: workersInvocationsAdaptive(limit: 100, filter: { datetime_geq: $since, datetime_leq: $end }) { sum { requests } }
  } }
}`;

// R2 の操作の種類 → Class A（書き込み・一覧）か Class B（読み出し）か。無料の操作（削除など）は数えない
const CLASS_A = /^(ListBuckets|PutBucket|ListObjects|PutObject|CopyObject|CompleteMultipartUpload|CreateMultipartUpload|LifecycleStorageTierTransition|ListMultipartUploads|UploadPart|UploadPartCopy|ListParts|PutBucketEncryption|PutBucketCors|PutBucketLifecycleConfiguration)$/;
const CLASS_B = /^(HeadBucket|HeadObject|GetObject|UsageSummary|GetBucketEncryption|GetBucketLocation|GetBucketCors|GetBucketLifecycleConfiguration)$/;

function classify(groups) {
  let a = 0, b = 0;
  for (const g of groups || []) {
    const t = g.dimensions.actionType, n = g.sum.requests;
    if (CLASS_A.test(t)) a += n;
    else if (CLASS_B.test(t)) b += n;
  }
  return { a, b };
}

export async function fetchUsage(env, now = new Date()) {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)); // R2 は毎月1日（UTC）に数え直し
  const since = new Date(now.getTime() - 30 * 60_000);
  const res = await fetch("https://api.cloudflare.com/client/v4/graphql", {
    method: "POST",
    headers: { authorization: `Bearer ${env.CF_ANALYTICS_TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify({ query: GQL, variables: { acc: env.CF_ACCOUNT_ID, start: start.toISOString(), end: now.toISOString(), since: since.toISOString() } }),
  });
  const body = await res.json();
  if (body.errors && body.errors.length) throw new Error(body.errors.map((e) => e.message).join(" / "));
  const acc = body.data.viewer.accounts[0];
  const month = classify(acc.month), recent = classify(acc.recent);
  return {
    checked_at: now.toISOString(),
    month_classA: month.a, month_classB: month.b, recent_classB: recent.b,
    recent_requests: (acc.workers || []).reduce((s, g) => s + g.sum.requests, 0),
  };
}

// 上限を超えていたら止める理由を返す（超えていなければ null）
export function overLimit(u) {
  if (u.month_classB >= LIMITS.classB.stopAt) return `R2の読み出しが今月${u.month_classB.toLocaleString()}回（無料枠の${Math.round((u.month_classB / LIMITS.classB.free) * 100)}%）に達しました`;
  if (u.month_classA >= LIMITS.classA.stopAt) return `R2の書き込みが今月${u.month_classA.toLocaleString()}回（無料枠の${Math.round((u.month_classA / LIMITS.classA.free) * 100)}%）に達しました`;
  if (u.recent_classB >= LIMITS.burstB) return `直近30分でR2の読み出しが${u.recent_classB.toLocaleString()}回あり、明らかに多すぎます`;
  return null;
}

export async function checkUsage(env) {
  if (!env.CF_ANALYTICS_TOKEN || !env.CF_ACCOUNT_ID) return { ok: false, reason: "CF_ANALYTICS_TOKEN が未設定です" };
  const state = await readGuard(env);
  let usage;
  try {
    usage = await fetchUsage(env);
  } catch (e) {
    // 使用量が読めないときは止めない（お客様を弾かない）。理由だけ残す
    await writeGuard(env, { ...state, error: String(e.message || e).slice(0, 300), error_at: new Date().toISOString() });
    return { ok: false, reason: e.message };
  }
  const next = { ...state, usage, error: null };
  const why = overLimit(usage);
  if (why && !state.paused) Object.assign(next, { paused: true, paused_at: usage.checked_at, reason: why, by: "auto" });
  await writeGuard(env, next);
  return { ok: true, paused: next.paused, reason: why };
}
