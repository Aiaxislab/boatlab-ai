// 各ページの表示。データは毎朝の処理が /data/*.json に置く。
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "―" : (x * 100).toFixed(1) + "%");
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

function summaryRow(label, s) {
  if (!s.races) return `<tr><td>${label}</td><td class="n">0</td><td colspan="5" class="muted">まだ集計できるレースがありません</td></tr>`;
  return `<tr><td>${label}</td><td class="n">${s.races}</td><td class="n">${pct(s.honmei_win)}</td><td class="n">${pct(s.honmei_top3)}</td>
    <td class="n">${pct(s.buy_hit)}</td><td class="n">${pct(s.buy_roi)}</td><td class="n">${pct(s.trio_hit)}</td><td class="n">${pct(s.trio_roi)}</td></tr>`;
}
const HEAD = (first) => `<tr><th>${first}</th><th class="n">レース数</th><th class="n">◎の1着率</th><th class="n">◎の3着内率</th><th class="n">買い目の的中率</th><th class="n">回収率</th><th class="n">3連複の的中率</th><th class="n">3連複の回収率</th></tr>`;

async function stats() {
  try {
    const s = await load("stats");
    const t = s.total;
    $("stats-meta").textContent = t.races ? `${s.since} から ${t.races}レースを集計。${s.generated} 更新` : "まだ答え合わせできたレースがありません。予想を公開した翌朝から集計が始まります。";
    const k = (label, v, sub) => `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`;
    const l = s.last30;
    $("kpis").innerHTML = [
      k("◎の1着率", pct(t.honmei_win), `直近30日 ${pct(l.honmei_win)}`),
      k("買い目の的中率（3連単6点）", pct(t.buy_hit), `直近30日 ${pct(l.buy_hit)}`),
      k("買い目の回収率", pct(t.buy_roi), `直近30日 ${pct(l.buy_roi)}`),
      k("3連複の的中率（3点）", pct(t.trio_hit), `回収率 ${pct(t.trio_roi)}`),
    ].join("");
    $("venue-table").innerHTML = HEAD("会場") + s.venues.map((v) => summaryRow(esc(v.name), v)).join("");
    $("day-table").innerHTML = HEAD("日付") + s.days.map((d) => summaryRow(jdate(d.date), d)).join("");
    $("month-table").innerHTML = HEAD("月") + s.months.map((m) => summaryRow(m.month.replace("-", "年") + "月", m)).join("");
  } catch (e) {
    $("stats-meta").textContent = "成績データを読み込めませんでした。";
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

({ today, stats, archive })[window.PAGE]?.();
