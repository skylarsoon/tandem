import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 90000,
  use: {
    channel: process.env.PLAYWRIGHT_CHANNEL,
    baseURL: process.env.TANDEM_WEB_URL || "http://127.0.0.1:3400",
    trace: "retain-on-failure",
  },
  reporter: "list",
});
