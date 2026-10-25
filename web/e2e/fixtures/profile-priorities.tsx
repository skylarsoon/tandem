// Browser-only fixture: saves are recorded locally, never sent to the shared DB.
import { createRoot } from "react-dom/client";
import { ProfileOnboarding } from "../../components/profile";

declare global { interface Window { profileSaves: { questionId: string; valueJson: string }[]; profileInitialRanking?: string[] } }
window.profileSaves = [];
const snapshot = { status: "ready", error: "", tables: { my_profile_answers: [
  { questionId: "C01", status: "ANSWERED", valueJson: '"Sam"' },
  { questionId: "C02", status: "SKIPPED", valueJson: "null" },
  ...(window.profileInitialRanking ? [{ questionId: "C03", status: "ANSWERED", valueJson: JSON.stringify({ ranked: window.profileInitialRanking, confirmed: true }) }] : []),
] } };
const client = {
  session: { subscribe: () => () => {}, getSnapshot: () => snapshot },
  connection: { reducers: { saveProfileAnswer: async (answer: { questionId: string; valueJson: string }) => { window.profileSaves.push(answer); } } },
} as unknown as Parameters<typeof ProfileOnboarding>[0]["client"];
createRoot(document.getElementById("root")!).render(<main>
  <header><div><p className="eyebrow">TANDEM / YOUR AGENT</p><h1>Let your agent get to know you.</h1>
    <p>Skip anything. Change it anytime. Private unless a plan needs it.</p></div><a className="badge" href="#">Your rooms →</a></header>
  <ProfileOnboarding client={client} />
</main>);
