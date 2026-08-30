import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("dashboard is product-specific and API-connected", async () => {
  const [page, html, css] = await Promise.all([
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("index.html", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(html, /松坐 · 久坐监测/);
  assert.match(page, /\/api\/overview/);
  assert.match(page, /\/api\/settings/);
  assert.match(page, /\/api\/camera\/settings/);
  assert.match(page, /测试并保存摄像头/);
  assert.match(page, /摄像头离线提醒/);
  assert.match(page, /\/api\/notifications\/settings/);
  assert.match(page, /连续会话合并窗口/);
  assert.match(page, /人脸置信度/);
  assert.match(page, /今日时间轴/);
  assert.match(page, /timeline-list-row/);
  assert.doesNotMatch(page, /timeline-axis/);
  assert.match(page, /周与月汇总/);
  assert.match(page, /近 7 日同期平均/);
  assert.match(css, /prefers-reduced-motion/);
  assert.doesNotMatch(page + html, /SkeletonPreview|codex-preview|Your site is taking shape/);
});
