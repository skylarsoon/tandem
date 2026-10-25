import { test, expect, type Page } from "@playwright/test";
import { buildSync } from "esbuild";
import { readFileSync } from "node:fs";
import path from "node:path";

const bundle = buildSync({ entryPoints: [path.resolve("e2e/fixtures/planning.tsx")], bundle: true, write: false, format: "iife",
  platform: "browser", jsx: "automatic", define: { "process.env.NODE_ENV": '"development"' } }).outputFiles[0].text;
const css = readFileSync(path.resolve("app/globals.css"), "utf8");
const fonts = ["manrope", "ibm-plex-mono"].map((font) => {
  const bytes = readFileSync(path.resolve(`node_modules/@fontsource/${font}/files/${font}-latin-400-normal.woff2`)).toString("base64");
  return `@font-face{font-family:'${font === "manrope" ? "Manrope" : "IBM Plex Mono"}';src:url(data:font/woff2;base64,${bytes}) format('woff2');font-weight:100 900;}`;
}).join("");
async function open(page: Page) {
  await page.setContent(`<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${fonts}${css}</style></head><body><div id="root"></div><script>${bundle.replaceAll("</script", "<\\/script")}</script></body></html>`);
  await expect(page.getByRole("heading", { name: "Your trip, in conversation" })).toBeVisible();
}

test("two panels support ongoing private chat without flight inputs", async ({ page }, info) => {
  await open(page);
  await expect(page.getByRole("complementary", { name: "Trip details" })).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Trip conversation" })).toHaveCount(1);
  await expect(page.getByText("Only you", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Shared with trip", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("Flight budget (USD)", { exact: true })).toHaveCount(0);
  await page.getByLabel("Message your agent").fill("Actually, let’s stay an extra night.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("status")).toContainText("Message sent");
  expect(await page.evaluate(() => window.planningFixture.calls.at(-1).method)).toBe("submitPrivateMessage");
  await expect(page.getByLabel("Message your agent")).toHaveValue("");
  await page.screenshot({ path: info.outputPath("planning-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByLabel("Message your agent").scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("planning-mobile.png"), fullPage: true });
});

test("inline question answers carry their offered constraint and stale questions cannot be answered", async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.planningFixture.setTables({ my_questions: [{ id: 10n, roomId: 1n, version: 2, state: "OPEN", kind: "MISSING_FACT", prompt: "Are you happy to share?", createdAt: 10n,
    actionJson: JSON.stringify({ nudge: { yes: { key: "own_bedroom", value: false, hardness: "HARD", unit: "" }, no: { key: "own_bedroom", value: true, hardness: "HARD", unit: "" } } }) }] }));
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  const answer = await page.evaluate(() => JSON.parse(window.planningFixture.calls.at(-1).args.answerJson));
  expect(answer).toEqual({ answer: true, constraint: { key: "own_bedroom", value: false, hardness: "HARD", unit: "" } });
  await page.evaluate(() => window.planningFixture.setTables({ my_questions: [{ id: 10n, roomId: 1n, state: "SUPERSEDED", prompt: "Are you happy to share?" }] }));
  await expect(page.getByRole("button", { name: "Yes", exact: true })).toHaveCount(0);
});

test("draft survives disconnect and send failure, and room switches clear private content", async ({ page }) => {
  await open(page);
  const composer = page.getByLabel("Message your agent");
  await composer.fill("My private change");
  await page.evaluate(() => window.planningFixture.setConnected(false));
  await expect(composer).toBeDisabled();
  await expect(composer).toHaveValue("My private change");
  await expect(page.getByText("We could drive down the coast and camp for the weekend.")).toHaveCount(0);
  await page.evaluate(() => { window.planningFixture.setConnected(true); window.planningFixture.failNext = true; });
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("alert")).toContainText("Please retry");
  await expect(composer).toHaveValue("My private change");
  await page.evaluate(() => window.planningFixture.switchRoom(2n));
  await expect(composer).toHaveValue("");
  await expect(page.getByText("We could drive down the coast and camp for the weekend.")).toHaveCount(0);
});

test("initial shared idea prepares a brief without collecting fixed inputs", async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.planningFixture.setTables({ room_trip_brief: [], my_intake: [], my_private_messages: [], room_messages: [], room_events: [] }));
  await expect(page.getByText("First trip idea · shared with travellers")).toBeVisible();
  await page.getByLabel("Message your agent").fill("Let’s drive to Big Sur and camp.");
  await page.getByRole("button", { name: "Send message" }).click();
  expect(await page.evaluate(() => window.planningFixture.calls.at(-1).method)).toBe("setTripDescription");
  expect(await page.evaluate(() => window.planningFixture.calls.at(-1).args.description)).toBe("Let’s drive to Big Sur and camp.");
});

test("proposal review binds approval to its displayed version and permits withdrawal", async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.planningFixture.setTables({ room_proposals: [{ id: 40n, roomId: 1n, version: 3, termsDigest: "exact-terms", state: "AWAITING_REVIEW", isReviewer: true,
    sharedSummaryJson: '{"text":"A campsite for the group"}', termsJson: '{"selected":[]}', createdAt: 40n }] }));
  await page.getByRole("button", { name: "Accept exact terms" }).click();
  const action = await page.evaluate(() => { const c = window.planningFixture.calls.at(-1); return { method: c.method, version: c.args.version, digest: c.args.termsDigest, response: c.args.response }; });
  expect(action).toEqual({ method: "respondToProposal", version: 3, digest: "exact-terms", response: "ACCEPT" });
  await page.evaluate(() => window.planningFixture.setTables({ my_consents: [{ roomId: 1n, proposalId: 40n, response: "ACCEPTED" }] }));
  await page.getByRole("button", { name: "Withdraw my acceptance" }).click();
  expect(await page.evaluate(() => window.planningFixture.calls.at(-1).method)).toBe("withdrawConsent");
});

test("editing a suggestion does not confirm it and keeps the original blocked", async ({ page }) => {
  await open(page);
  await page.evaluate(() => window.planningFixture.setTables({ my_constraint_patches: [{ id: 20n, roomId: 1n, state: "PENDING", createdAt: 20n, patchJson: '{"key":"trip_budget_total","value":50000,"hardness":"SOFT","authorized_range":{"max":70000}}' }] }));
  await expect(page.getByRole("heading", { name: "Whole-trip budget: $500.00" })).toBeVisible();
  await expect(page.getByText("Preference · approved flexibility up to $700.00")).toBeVisible();
  await page.getByRole("button", { name: "Edit in chat", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm detail" })).toBeDisabled();
  await expect(page.getByLabel("Message your agent")).toBeFocused();
  await page.getByLabel("Message your agent").fill("Make that $600, firm.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("Correction sent · awaiting an updated response")).toBeVisible();
  expect(await page.evaluate(() => window.planningFixture.calls.some((c: any) => c.method === "confirmConstraintPatch"))).toBe(false);
});
