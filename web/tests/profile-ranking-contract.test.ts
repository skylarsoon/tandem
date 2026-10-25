import { expect, it, vi } from "vitest";
// Exercise the database's real validator without loading the server runtime.
vi.mock("../../spacetimedb/src/guards", () => ({
  fail: (code: string, message: string) => { throw new Error(`${code}: ${message}`); },
}));
it("accepts all six priorities while still rejecting duplicate or unconfirmed rankings", async () => {
  // Keep frontend typechecking independent of the backend's server dependencies.
  const validatorModule = "../../spacetimedb/src/profile_rules";
  const { profileQuestion, validateAnswer } = await import(validatorModule);
  const q = profileQuestion("C03");
  const ranked = q.options.map((option: { id: string }) => option.id);
  expect(ranked).toHaveLength(6);
  expect(() => validateAnswer(q, { ranked, confirmed: true })).not.toThrow();
  expect(() => validateAnswer(q, { ranked, confirmed: false })).toThrow("confirm the order");
  expect(() => validateAnswer(q, { ranked: [...ranked.slice(1), ranked[1]], confirmed: true })).toThrow("repeated choices");
  // Older saved profiles remain valid during the transition.
  expect(() => validateAnswer(q, { ranked: ranked.slice(0, 2), confirmed: true })).not.toThrow();
});
