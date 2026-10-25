import { describe as group, expect, it } from "vitest";
import { answersFrom, catalog, core, describe, move, question, ready, toggle } from "../lib/profile";

group("profile onboarding helpers", () => {
  it("renders the shared catalog: nine core questions, display name first", () => {
    expect(core.map((q) => q.id)).toEqual(["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09"]);
    expect(question("C01").required).toBe(true);
    expect(catalog.review_groups.map((g) => g.title)).toContain("When to ask me");
  });

  it("keeps exclusive choices alone and never exceeds a limit", () => {
    const options = question("C08").options!;
    expect(toggle(options, ["food_allergies", "dietary"], "none")).toEqual(["none"]);
    expect(toggle(options, ["none"], "dietary")).toEqual(["dietary"]);
    const c02 = question("C02");
    const five = ["food", "nature", "hiking", "beaches", "wellness"];
    expect(toggle(c02.options!, five, "sports", c02.max)).toEqual(five);
  });

  it("moves ranked items and needs an explicit confirmation to save", () => {
    expect(move(["a", "b", "c"], "c", -1)).toEqual(["a", "c", "b"]);
    expect(move(["a", "b"], "a", -1)).toEqual(["a", "b"]);
    expect(ready(question("C03"), { ranked: ["affordability"], confirmed: false })).toBe(false);
    expect(ready(question("C03"), { ranked: ["affordability"], confirmed: true })).toBe(true);
    const all = question("C03").options!.map(option => option.id);
    expect(ready(question("C03"), { ranked: all.slice(0, 5), confirmed: true })).toBe(true);
    expect(ready(question("C03"), { ranked: all, confirmed: true })).toBe(false);
    expect(ready(question("C03"), { ranked: [...all.slice(1), all[1]], confirmed: true })).toBe(false);
  });

  it("asks must-or-preference before an own-room answer can be saved", () => {
    expect(ready(question("T06"), { selected: "own_room" })).toBe(false);
    expect(ready(question("T06"), { selected: "own_room", strength: "REQUIRED" })).toBe(true);
  });

  it("keeps unanswered, skipped and answered distinct in plain words", () => {
    const answers = answersFrom([
      { questionId: "C04", status: "SKIPPED", valueJson: "null" },
      { questionId: "C08", status: "ANSWERED", valueJson: JSON.stringify({ selected: ["none"] }) },
    ]);
    expect(describe(question("C05"), answers.C05)).toBe("Not shared yet");
    expect(describe(question("C04"), answers.C04)).toBe("Skipped for now");
    expect(describe(question("C08"), answers.C08)).toBe("None to add right now");
    expect(describe(question("A06"), { status: "ANSWERED", value: { city: "Seattle", country: "USA", airports: ["SEA"] } }))
      .toBe("Seattle, USA (SEA)");
  });
});
