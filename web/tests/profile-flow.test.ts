import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ProfileOnboarding } from "../components/profile";
import { core } from "../lib/profile";

function renderProfile(completed: string[], state?: string) {
  const snapshot = {
    status: "ready",
    tables: {
      my_profile_calibration: state ? [{ state, jobId: 1n }] : [],
      my_profile_answers: completed.map((questionId) => ({
        questionId,
        status: questionId === "C01" ? "ANSWERED" : "SKIPPED",
        valueJson: questionId === "C01" ? JSON.stringify("Sam") : "null",
      })),
    },
  };
  const client = {
    session: {
      subscribe: () => () => {},
      getSnapshot: () => snapshot,
    },
    connection: { reducers: {} },
  } as unknown as Parameters<typeof ProfileOnboarding>[0]["client"];
  return renderToStaticMarkup(createElement(ProfileOnboarding, { client }));
}

describe("profile flow ordering", () => {
  it("starts with the first core question for a new profile", () => {
    const html = renderProfile([]);
    expect(html).toContain("QUESTION 1 OF 9");
    expect(html).not.toContain("profile-group");
  });

  it.each([1, 2, 8])("resumes core questions after %i saved or skipped answers", (count) => {
    const html = renderProfile(core.slice(0, count).map((q) => q.id));
    expect(html).toContain(`QUESTION ${count + 1} OF 9`);
    expect(html).not.toContain("profile-group");
    expect(html).not.toContain("Add more about me");
  });

  it("resumes the first missing core question even when later answers exist", () => {
    expect(renderProfile(["C01", "C03", "A01"])).toContain("QUESTION 2 OF 9");
  });

  it("shows review and an optional extension after all core questions are addressed", () => {
    const html = renderProfile(core.map((q) => q.id));
    expect(html).not.toContain("QUESTION 1 OF 9");
    expect(html).toContain("profile-group");
    expect(html).toContain("Add more about me (optional)");
    expect(html).not.toContain("profile-section");
  });
});


it("shows a retry for a failed summary instead of claiming it is still writing", () => {
  const html = renderProfile(core.map(q => q.id), "FAILED");
  expect(html).toContain("Retry summary");
  expect(html).toContain("Your answers are saved");
  expect(html).not.toContain("a few seconds");
});
