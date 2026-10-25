import { test, expect, type Page } from "@playwright/test";
import { buildSync } from "esbuild";
import { readFileSync } from "node:fs";
import path from "node:path";

const bundle = buildSync({ entryPoints: [path.resolve("e2e/fixtures/profile-priorities.tsx")], bundle: true,
  write: false, format: "iife", platform: "browser", jsx: "automatic" }).outputFiles[0].text;
const css = readFileSync(path.resolve("app/globals.css"), "utf8");
const fonts = ["manrope", "ibm-plex-mono"].map(font => {
  const bytes = readFileSync(path.resolve(`node_modules/@fontsource/${font}/files/${font}-latin-400-normal.woff2`)).toString("base64");
  return `@font-face{font-family:'${font === "manrope" ? "Manrope" : "IBM Plex Mono"}';src:url(data:font/woff2;base64,${bytes})}`;
}).join("");
async function open(page: Page, initialRanking?: string[]) {
  await page.setContent(`<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${fonts}${css}</style></head><body><div id="root"></div><script>window.profileInitialRanking = ${JSON.stringify(initialRanking) ?? "undefined"};${bundle.replaceAll("</script", "<\\/script")}</script></body></html>`);
  await expect(page.getByText(`QUESTION ${initialRanking ? 4 : 3} OF 9`)).toBeVisible();
}
const order = (page: Page) => page.locator(".priority-list li").evaluateAll(rows => rows.map(row => (row as HTMLElement).dataset.priority));

test("an older partial ranking retains its order and requires confirmation of the completed list", async ({ page }) => {
  await open(page, ["comfort", "affordability"]);
  await page.getByRole("button", { name: "Back", exact: true }).click();
  expect(await order(page)).toEqual(["comfort", "affordability", "togetherness", "time_efficiency", "experience_quality", "discovery"]);
  await expect(page.getByLabel("This order is right")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Save and continue" })).toBeDisabled();
  await page.getByLabel("This order is right").check();
  await expect(page.getByRole("button", { name: "Save and continue" })).toBeEnabled();
});

test("all six priorities appear immediately, support drag/keyboard ordering, and save the displayed order", async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 1200 });
  await open(page);
  await expect(page.locator(".priority-list li")).toHaveCount(6);
  await expect(page.getByRole("button", { name: /^(Add|Remove|Replace) / })).toHaveCount(0);
  expect(await order(page)).toEqual(["affordability", "togetherness", "time_efficiency", "comfort", "experience_quality", "discovery"]);
  await page.getByLabel("This order is right").check();
  const handle = page.getByRole("button", { name: "Reorder Discovery", exact: true });
  const from = (await handle.boundingBox())!;
  const to = (await page.locator(".priority-list li").first().boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2, to.y + to.height / 2, { steps: 12 });
  await page.mouse.up();
  expect(await order(page)).toEqual(["discovery", "affordability", "togetherness", "time_efficiency", "comfort", "experience_quality"]);
  await expect(page.getByLabel("This order is right")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Save and continue" })).toBeDisabled();
  await handle.focus();
  await page.keyboard.press("ArrowDown");
  const expected = ["affordability", "discovery", "togetherness", "time_efficiency", "comfort", "experience_quality"];
  expect(await order(page)).toEqual(expected);
  await page.getByLabel("This order is right").check();
  await page.screenshot({ path: info.outputPath("priorities-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Save and continue" }).click();
  expect(await page.evaluate(() => window.profileSaves.map(save => ({ questionId: save.questionId, value: JSON.parse(save.valueJson) }))))
    .toEqual([{ questionId: "C03", value: { ranked: expected, confirmed: true } }]);
});

test("touch dragging and cancellation preserve a usable mobile ranking", async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await open(page);
  await page.getByLabel("This order is right").check();
  const handle = page.getByRole("button", { name: "Reorder Group togetherness", exact: true });
  await handle.scrollIntoViewIfNeeded();
  const from = (await handle.boundingBox())!;
  const to = (await page.locator(".priority-list li").first().boundingBox())!;
  const x = from.x + from.width / 2, y = from.y + from.height / 2, endY = to.y + to.height / 2;
  const cdp = await context.newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: endY }] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  expect(await order(page)).toEqual(["togetherness", "affordability", "time_efficiency", "comfort", "experience_quality", "discovery"]);
  await expect(page.getByLabel("This order is right")).not.toBeChecked();
  await handle.dispatchEvent("pointerdown", { pointerId: 1, button: 0, clientY: endY });
  await handle.dispatchEvent("pointermove", { pointerId: 1, clientY: y });
  await handle.dispatchEvent("pointercancel", { pointerId: 1 });
  expect(await order(page)).toEqual(["togetherness", "affordability", "time_efficiency", "comfort", "experience_quality", "discovery"]);
  await expect(page.locator(".priority-list li")).toHaveCount(6);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("priorities-mobile.png"), fullPage: true });
  await context.close();
});
