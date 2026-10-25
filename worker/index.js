// aiaxislab.win（運営元のサイト）と boat.aiaxislab.win（BoatLab AI）を1つのWorkerで配信する。
// ページは site/ の静的ファイル、PDFと日々のデータは R2 バケットから返す。

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
  async fetch(request, env) {
    const url = new URL(request.url);
    // workers.dev のURLで確認するときは BoatLab AI 側を表示する
    const boat = url.hostname.startsWith("boat.") || url.hostname.endsWith(".workers.dev");

    if (boat && /^\/(pdf|data)\//.test(url.pathname)) {
      return fromBucket(env, url.pathname.slice(1));
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
