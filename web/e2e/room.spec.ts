import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

test.skip(
  !process.env.TANDEM_STDB_WORKER_TOKEN ||
    !process.env.TANDEM_STDB_URL?.startsWith("http://127.0.0.1:"),
  "Run scripts/web-integration.sh against a disposable local database",
);
async function openRoom(
  page: Page,
  name: string,
  goal?: string,
  invite?: string,
) {
  await page.goto("/");
  await expect(page.locator(".connection")).toContainText("ready");
  await page.getByLabel("Your display name").fill(name);
  if (goal) {
    await page.getByLabel("New room goal").fill(goal);
    await page
      .getByRole("button", { name: "Create room", exact: true })
      .click();
  } else {
    await page.getByLabel("Invite code", { exact: true }).fill(invite!);
    await page.getByRole("button", { name: "Join room", exact: true }).click();
  }
  const roomButton = page
    .locator(".card")
    .first()
    .getByRole("button", { name: goal || /browser trip/ });
  await roomButton.click();
  await expect(
    page.getByRole("heading", { name: "My settings", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm participation preferences" })
    .click();
  await page
    .getByLabel("Flight budget (USD)", { exact: true })
    .fill(name === "p1" ? "501" : "500");
  await page
    .getByRole("button", { name: "Confirm constraints", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Confirm constraints", exact: true }),
  ).toBeEnabled();
}
function runWorker(room: string, state: string, scenario = "clarify") {
  return execFileSync(
    path.resolve("../worker/.venv/bin/python"),
    [
      path.resolve("e2e/worker.py"),
      "--room",
      room,
      "--state",
      state,
      "--scenario",
      scenario,
    ],
    {
      env: { ...process.env, PYTHONPATH: path.resolve("../worker/src") },
      encoding: "utf8",
    },
  );
}
async function pair(browser: any, goal: string) {
  const a = await browser.newContext(),
    b = await browser.newContext();
  const pa = await a.newPage(),
    pb = await b.newPage();
  await openRoom(pa, "p1", goal);
  const invite = await pa.locator("code").innerText();
  await openRoom(pb, "p2", undefined, invite);
  await expect(pa.locator(".room-head")).toContainText("p2");
  const room = (await pa.locator(".room-head .eyebrow").innerText()).replace(
    "ROOM ",
    "",
  );
  return { a, b, pa, pb, room };
}
test("subscription worker plans, pauses, resumes after answers, and people approve", async ({
  browser,
}, testInfo) => {
  const { a, b, pa, pb, room } = await pair(
    browser,
    `browser trip ${Date.now()}`,
  );
  const state = testInfo.outputPath("worker-state");
  await expect(
    pa.getByRole("button", { name: "Start planning", exact: true }),
  ).toHaveCount(0);
  await expect(pa.locator(".private")).toContainText("PRIVATE_NOTE_P1");
  await expect(pb.locator(".private")).not.toContainText("PRIVATE_NOTE_P1");
  await expect(
    pa.getByLabel("Flight budget (USD)", { exact: true }),
  ).toHaveValue("501");
  await expect(
    pb.getByLabel("Flight budget (USD)", { exact: true }),
  ).toHaveValue("500");
  await expect(
    pa.getByText("PRIVATE_P1: Is this departure date OK?", { exact: true }),
  ).toBeVisible();
  await expect(
    pb.getByText("PRIVATE_P1: Is this departure date OK?", { exact: true }),
  ).toHaveCount(0);
  await expect(
    pa.getByRole("button", { name: "Accept exact terms" }),
  ).toHaveCount(0);
  await pa.reload();
  await expect(pa.locator(".connection")).toContainText("ready");
  await pa
    .locator(".card")
    .first()
    .getByRole("button", { name: /browser trip/ })
    .click();
  await expect(
    pa.getByText("PRIVATE_P1: Is this departure date OK?", { exact: true }),
  ).toHaveCount(1);
  await pa.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(
    pa.getByText("No open questions. Unanswered questions stay pending."),
  ).toBeVisible();
  await expect(
    pa.getByText("0 accepted · 2 pending · 0 declined · 2 reviewers", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    pb.getByRole("button", { name: "Accept exact terms" }),
  ).toBeVisible();
  await expect(pa.locator(".private")).toContainText("Your flight costs");
  await expect(pb.locator(".private")).toContainText("Your flight costs");
  await pb.getByRole("button", { name: "Decide later", exact: true }).click();
  await expect(
    pb.getByText("Your response: PENDING", { exact: true }),
  ).toBeVisible();
  await pa.getByRole("button", { name: "Accept exact terms" }).click();
  await pb.getByRole("button", { name: "Accept exact terms" }).click();
  await expect(pa.locator("article .badge")).toHaveText("AGREED");
  await expect(pb.locator("article .badge")).toHaveText("AGREED");
  await expect(
    pa.getByText("2 accepted · 0 pending · 0 declined · 2 reviewers", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(pa.getByRole("region", { name: "Live workflow" })).toBeVisible();
  await expect(
    pa.locator(".stage").filter({ hasText: "Agreed" }),
  ).toContainText("completed");
  expect(
    await pa
      .locator("h1")
      .evaluate((el: HTMLElement) => getComputedStyle(el).fontFamily),
  ).toContain("Manrope");
  expect(
    await pa
      .locator("label")
      .first()
      .evaluate((el: HTMLElement) => getComputedStyle(el).fontFamily),
  ).toContain("IBM Plex Mono");
  expect(
    await pa
      .getByRole("button", { name: "Withdraw my acceptance" })
      .evaluate((el: HTMLElement) => getComputedStyle(el).fontFamily),
  ).toContain("Manrope");
  expect(
    await pa.evaluate(() =>
      performance
        .getEntriesByType("resource")
        .some((e) => e.name.includes("fonts.googleapis.com")),
    ),
  ).toBe(false);
  expect(
    await pa.evaluate(() => document.fonts.check("400 16px Manrope")),
  ).toBe(true);
  await pa.locator(".agent-node").first().click();
  await expect(pa.locator(".workflow details[open]")).toContainText(
    "Inspect agent",
  );
  await expect(pa.locator(".workflow details[open]")).not.toContainText(
    "PRIVATE_NOTE_P1",
  );
  await pa.emulateMedia({ reducedMotion: "reduce" });
  await pa.screenshot({
    path: testInfo.outputPath("room-desktop.png"),
    fullPage: true,
  });
  await pa.setViewportSize({ width: 390, height: 844 });
  await pa.screenshot({
    path: testInfo.outputPath("room-mobile.png"),
    fullPage: true,
  });
  await pb.getByRole("button", { name: "Withdraw my acceptance" }).click();
  await expect(pa.locator("article .badge")).toHaveText("AWAITING_REVIEW");
  await a.close();
  await b.close();
});
test("declining a concession re-solves and asks the other owner", async ({
  browser,
}, testInfo) => {
  const { a, b, pa, pb, room } = await pair(
    browser,
    `browser trip concession ${Date.now()}`,
  );
  const state = testInfo.outputPath("worker-state");
  expect(runWorker(room, state, "concession")).toContain("WAITING_FOR_INPUT");
  await expect(
    pb.getByRole("button", { name: "No", exact: true }),
  ).toBeVisible();
  await expect(pa.getByRole("button", { name: "No", exact: true })).toHaveCount(
    0,
  );
  await pb.getByRole("button", { name: "No", exact: true }).click();
  await expect(pb.getByRole("button", { name: "No", exact: true })).toHaveCount(
    0,
  );
  expect(runWorker(room, state, "concession")).toContain("WAITING_FOR_INPUT");
  await expect(
    pa.getByRole("button", { name: "Yes", exact: true }),
  ).toBeVisible();
  await pa.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(
    pa.getByRole("button", { name: "Yes", exact: true }),
  ).toHaveCount(0);
  expect(runWorker(room, state, "concession")).toContain("AWAITING_REVIEW");
  await expect(
    pa.getByRole("button", { name: "Accept exact terms" }),
  ).toBeVisible();
  await expect(
    pb.getByRole("button", { name: "Accept exact terms" }),
  ).toBeVisible();
  await a.close();
  await b.close();
});
