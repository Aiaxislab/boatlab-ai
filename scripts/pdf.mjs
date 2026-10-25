// HTMLをPDFにする。使い方: node scripts/pdf.mjs pairs.json
// pairs.json は [[html, pdf], ...]。ブラウザは1回だけ起動する。
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const pairs = JSON.parse(readFileSync(process.argv[2], "utf8"));
const browser = await chromium.launch();
const page = await browser.newPage();
for (const [html, pdf] of pairs) {
  await page.goto(pathToFileURL(html).href, { waitUntil: "networkidle", timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({ path: pdf, format: "A4", scale: 0.88, printBackground: true,
                   margin: { top: "0", bottom: "0", left: "0", right: "0" } });
  console.log("pdf", pdf);
}
await browser.close();
