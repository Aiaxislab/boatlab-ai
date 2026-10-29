// aiaxislab.win（運営元のサイト）と boat.aiaxislab.win（BoatLab AI）を1つのWorkerで配信する。
// ページは site/ の静的ファイル、PDFと日々のデータは R2 バケットから返す。
// 管理者ダッシュボードは admin.js（公開ページからはリンクしない・Cloudflare Access で守る）。

import { handleAdmin } from "./admin.js";
import { dailyMail } from "./mail.js";
import { FX_CRON, fxScheduled, handleFxShare } from "./fx/fx.js";
import { AI2_CRON, ai2Scheduled } from "./ai2/ai2.js";
import { cached, checkUsage, isPaused, pausedResponse, rateLimited, tooMany } from "./guard.js";

const VENUE_NAMES = { "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川", "06": "浜名湖", "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国", "11": "びわこ", "12": "住之江", "13": "尼崎", "14": "鳴門", "15": "丸亀", "16": "児島", "17": "宮島", "18": "徳山", "19": "下関", "20": "若松", "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村" };

async function fromBucket(env, key) {
  const obj = await env.BUCKET.get(key);
  if (!obj) return new Response("見つかりません（PDFは30日で削除されます）", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  if (key.endsWith(".pdf")) {
    const m = key.match(/pdf\/(\d{4}-\d{2}-\d{2})\/(\d{2})\.pdf$/);
    const name = m ? `BoatLabAI_${VENUE_NAMES[m[2]] || m[2]}_${m[1]}.pdf` : "BoatLabAI.pdf";
    headers.set("content-type", "application/pdf");
    headers.set("content-disposition", `inline; filename*=UTF-8''${encodeURIComponent(name)}`);
    headers.set("cache-control", "public, max-age=86400");
  } else {
    headers.set("content-type", "application/json; charset=utf-8");
    headers.set("cache-control", "public, max-age=300");
  }
  return new Response(obj.body, { headers });
}

export default {
  // 30分おき。使用量を見て、無料枠に近づいていたらサイトを一時停止する（guard.js）。
  // 朝 6:00〜8:30（日本時間）は、今日の予想ができていれば高確率のレースをメールする（1日1回）
  // FX_CRON（8:00〜10:55 の5分おき）は FX の毎朝のメールだけ（fx/fx.js）
  // AI2_CRON（8:00〜23:59 の1分おき）は BoatLab AI 2 の締切5分前の判定と答え合わせだけ（ai2/ai2.js）
  async scheduled(event, env, ctx) {
    if (event.cron === AI2_CRON) {
      ctx.waitUntil(ai2Scheduled(event, env).then((r) => console.log("ai2", JSON.stringify(r))));
      return;
    }
    if (event.cron === FX_CRON) {
      ctx.waitUntil(fxScheduled(event, env).then((r) => console.log("fx", JSON.stringify(r))));
      return;
    }
    ctx.waitUntil(checkUsage(env).then((r) => console.log("guard", JSON.stringify(r))));
    const h = new Date(event.scheduledTime).getUTCHours();
    if (h >= 21 && h <= 23) ctx.waitUntil(dailyMail(env).then((r) => console.log("mail", JSON.stringify(r))));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // workers.dev のURLで確認するときは BoatLab AI 側を表示する
    const boat = url.hostname.startsWith("boat.") || url.hostname.endsWith(".workers.dev");

    // 管理者ダッシュボードは一時停止中でも開ける（再開ボタンを押すため）
    const admin = await handleAdmin(request, env, url);
    if (admin) return admin;

    // FXの要約（Claude に読ませる非公開URL）。自分用なので、サイトの一時停止中でも開ける
    const fxShare = await handleFxShare(request, env, url);
    if (fxShare) return fxShare;

    if (await isPaused(env)) return pausedResponse();

    // 公開するのは本日分と過去30日の一覧だけ（成績の集計は管理者ダッシュボードへ移した）
    if (boat && /^\/(pdf\/\d{4}-\d{2}-\d{2}\/\d{2}\.pdf|data\/(today|archive)\.json)$/.test(url.pathname)) {
      if (await rateLimited(env, request)) return tooMany();
      return cached(request, ctx, () => fromBucket(env, url.pathname.slice(1)));
    }

    let path = url.pathname;
    if (path.endsWith("/")) path += "index.html";
    else if (!/\.[a-z0-9]+$/i.test(path)) path += ".html";
    const assetUrl = new URL((boat ? "/boat" : "/main") + path, url.origin);
    const res = await env.ASSETS.fetch(new Request(assetUrl, request));
    if (res.status === 404) {
      return new Response("ページが見つかりません", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
    }
    return res;
  },
};
