import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { admitTestBrowser } from "./invitation-admin";

test.skip(!process.env.TANDEM_STDB_WORKER_TOKEN || !process.env.TANDEM_STDB_URL?.startsWith("http://127.0.0.1:"),
  "Run scripts/web-integration.sh against a disposable local database");

async function openRoom(page: Page, name: string, goal?: string, invite?: string) {
  await admitTestBrowser(page);
  await page.goto("/app");
  await expect(page.locator(".connection")).toContainText("Connected");
  await page.getByText("Create or join a trip", { exact: true }).click();
  await page.getByLabel("Your display name").fill(name);
  if (goal) {
    await page.getByLabel("Trip name", { exact: true }).fill(goal);
    await page.getByRole("button", { name: "Create trip", exact: true }).click();
  } else {
    await page.getByLabel("Invite code", { exact: true }).fill(invite!);
    await page.getByRole("button", { name: "Join trip", exact: true }).click();
  }
  await expect(page.getByRole("heading", { name: /browser trip/ })).toBeVisible();
  const room = await page.getByLabel("YOUR TRIPS", { exact: true }).inputValue();
  const token = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(k => k.startsWith("tandem:participant:"));
    return key ? localStorage.getItem(key) : null;
  });
  // Fixture setup only: the production page has no fixed flight-budget form.
  execFileSync(path.resolve("../worker/.venv/bin/python"), [path.resolve("e2e/seed_owner.py")], {
    input: JSON.stringify({ room, name, token }), env: { ...process.env, PYTHONPATH: path.resolve("../worker/src") },
    stdio: ["pipe", "pipe", "pipe"],
  });
  return room;
}
function runWorker(room: string, state: string, scenario = "clarify") {
  return execFileSync(path.resolve("../worker/.venv/bin/python"), [path.resolve("e2e/worker.py"), "--room", room, "--state", state, "--scenario", scenario], {
    env: { ...process.env, PYTHONPATH: path.resolve("../worker/src") }, encoding: "utf8",
  });
}
async function pair(browser: any, goal: string) {
  const a = await browser.newContext(), b = await browser.newContext();
  const pa = await a.newPage(), pb = await b.newPage();
  const room = await openRoom(pa, "p1", goal);
  await pa.getByText("Invite someone", { exact: true }).click();
  const invite = await pa.locator("code").innerText();
  await openRoom(pb, "p2", undefined, invite);
  await expect(pa.locator(".traveller-list")).toContainText("p2");
  return { a, b, pa, pb, room };
}

test("subscription worker pauses, resumes through inline questions, and people approve", async ({ browser }, info) => {
  const goal = `browser trip ${Date.now()}`;
  const { a, b, pa, pb } = await pair(browser, goal);
  await expect(pa.getByText("PRIVATE_P1: Is this departure date OK?", { exact: true })).toBeVisible();
  await expect(pb.getByText("PRIVATE_P1: Is this departure date OK?", { exact: true })).toHaveCount(0);
  await expect(pa.locator(".private-item").filter({ hasText: "PRIVATE_NOTE_P1" })).toHaveCount(1);
  await expect(pb.getByText(/PRIVATE_NOTE_P1/)).toHaveCount(0);
  await expect(pa.getByLabel("Flight budget (USD)", { exact: true })).toHaveCount(0);
  await pa.reload();
  await expect(pa.locator(".connection")).toContainText("Connected");
  await pa.getByLabel("YOUR TRIPS", { exact: true }).selectOption({ label: goal });
  await pa.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(pa.getByText("0 accepted · 2 pending · 0 declined · 2 reviewers", { exact: true })).toBeVisible();
  await expect(pb.getByRole("button", { name: "Accept exact terms" })).toBeVisible();
  await pb.getByRole("button", { name: "Decide later", exact: true }).click();
  await expect(pb.getByText("Your response: pending", { exact: true })).toBeVisible();
  await pa.getByRole("button", { name: "Accept exact terms" }).click();
  await pb.getByRole("button", { name: "Accept exact terms" }).click();
  await expect(pa.locator("article .tag")).toHaveText("agreed");
  await expect(pb.locator("article .tag")).toHaveText("agreed");
  await expect(pa.getByRole("region", { name: "Workflow progress" })).toBeVisible();
  await expect(pa.getByLabel("Message your agent")).toBeEnabled();
  await pa.screenshot({ path: info.outputPath("room-desktop.png"), fullPage: true });
  await pb.getByRole("button", { name: "Withdraw my acceptance" }).click();
  await expect(pa.locator("article .tag")).toHaveText("awaiting review");
  await a.close(); await b.close();
});

test("declining a concession re-solves and asks the other owner", async ({ browser }, info) => {
  const { a, b, pa, pb, room } = await pair(browser, `browser trip concession ${Date.now()}`);
  const state = info.outputPath("worker-state");
  expect(runWorker(room, state, "concession")).toContain("WAITING_FOR_INPUT");
  await expect(pb.getByRole("button", { name: "No", exact: true })).toBeVisible();
  await pb.getByRole("button", { name: "No", exact: true }).click();
  expect(runWorker(room, state, "concession")).toContain("WAITING_FOR_INPUT");
  await pa.getByRole("button", { name: "Yes", exact: true }).click();
  expect(runWorker(room, state, "concession")).toContain("AWAITING_REVIEW");
  await expect(pa.getByRole("button", { name: "Accept exact terms" })).toBeVisible();
  await expect(pb.getByRole("button", { name: "Accept exact terms" })).toBeVisible();
  await a.close(); await b.close();
});

test("companion changes sync to another person and persist after reload", async ({ browser }) => {
  const goal = `browser trip companions ${Date.now()}`;
  const { a, b, pa, pb } = await pair(browser, goal);
  try {
    await pa.getByLabel("YOUR TRIPS", { exact: true }).selectOption("");
    await expect(pa.getByRole("heading", { name: "Hey p1, where are we going?" })).toBeVisible();
    await pa.getByText("Make this agent yours", { exact: true }).click();
    await pa.getByRole("button", { name: "Choose Butter" }).click();
    await expect(pa.getByRole("button", { name: "Choose Butter" })).toHaveAttribute("aria-pressed", "true");
    const peer = pb.locator(".room-companions-list > li").filter({ hasText: "p1’s agent" });
    await expect(peer.locator("img")).toHaveAttribute("src", "/agents/yellow.webp");
    await pa.reload();
    await expect(pa.locator(".connection")).toContainText("Connected");
    await expect(pa.locator(".agent-avatar--hero img")).toHaveAttribute("src", "/agents/yellow.webp");
    await pa.getByLabel("YOUR TRIPS", { exact: true }).selectOption({ label: goal });
    await expect(pa.locator(".is-your-agent img")).toHaveAttribute("src", "/agents/yellow.webp");
    await a.close();
    await expect(peer).toContainText("p1 · away");
    await expect(peer.locator("img")).toHaveAttribute("src", "/agents/yellow.webp");
  } finally { await a.close(); await b.close(); }
});
