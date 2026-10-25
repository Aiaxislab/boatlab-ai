// 各ページの表示。データは毎朝の処理が /data/*.json に置く。
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const bt = (n) => `<span class="bt n${n}">${n}</span>`;
const WEEK = ["日", "月", "火", "水", "木", "金", "土"];
const jdate = (iso) => { const d = new Date(iso + "T00:00:00+09:00"); return `${d.getMonth() + 1}月${d.getDate()}日（${WEEK[d.getDay()]}）`; };

async function load(name) {
  const r = await fetch(`/data/${name}.json`, { cache: "no-cache" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function today() {
  try {
    const t = await load("today");
    $("today-title").textContent = `${jdate(t.date)}の予想PDF`;
    $("today-meta").textContent = `${t.venues.length}場で開催。${t.generated} 作成`;
    $("venues").innerHTML = t.venues.map((v) => `
      <div class="card">
        <h3>${esc(v.name)}<small>${v.day}日目・${v.first}〜${v.last}</small></h3>
        <div class="title">${esc(v.title)}</div>
        <a class="btn" href="/${v.pdf}" target="_blank" rel="noopener">12R分のPDFを開く</a>
        <details><summary>レースごとの本命を見る</summary>
          <table><tr><th>R</th><th>締切</th><th>◎本命</th><th class="n">買い目の的中確率</th></tr>
          ${v.races.map((r) => `<tr><td>${r.no}R</td><td>${r.deadline}</td><td>${bt(r.honmei)}${esc(r.honmei_name)}</td><td class="n">約${r.buy_prob}%</td></tr>`).join("")}
          </table></details>
      </div>`).join("");
  } catch (e) {
    $("today-meta").textContent = "";
    $("venues").innerHTML = `<div class="empty">本日のPDFはまだありません。毎朝5時半ごろに公開します。</div>`;
  }
}

async function archive() {
  try {
    const a = await load("archive");
    $("archive").innerHTML = a.map((d) => `
      <h2>${jdate(d.date)}</h2>
      <div class="grid">${d.venues.map((v) => `<a class="btn" href="/${v.pdf}" target="_blank" rel="noopener">${esc(v.name)}</a>`).join("")}</div>`).join("");
  } catch (e) {
    $("archive").innerHTML = `<div class="empty">まだありません。</div>`;
  }
}

({ today, archive })[window.PAGE]?.();
