"use client";
import { useState, useSyncExternalStore } from "react";
import type { connect } from "../lib/client";
import { RankedPriorities } from "./ranked-priorities";
import { CharacterPicker } from "./agent-companions";
import { myCharacter } from "../lib/agents";
import {
  AVATARS,
  answersFrom,
  catalog,
  core,
  describe,
  question,
  ready,
  toggle,
  type Answer,
  type Question,
} from "../lib/profile";

type Client = ReturnType<typeof connect>;
type Mode = "core" | "done" | "more" | "review";

/** "Let's help your agent get to know you": a short core flow, optional
 *  sections, a review page, and the agent's summary to confirm. */
export function ProfileOnboarding({ client }: { client: Client }) {
  const state = useSyncExternalStore(client.session.subscribe, client.session.getSnapshot, client.session.getSnapshot);
  const rows = (name: string) => state.tables[name] || [];
  const answers = answersFrom(rows("my_profile_answers"));
  const calibration = rows("my_profile_calibration")[0];
  const [mode, setMode] = useState<Mode | undefined>();
  const [selectedStep, setStep] = useState<number>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const reducers = client.connection.reducers;
  const ready_ = state.status === "ready";
  const firstUnanswered = core.findIndex((q) => !answers[q.id]);
  const step = selectedStep ?? Math.max(0, firstUnanswered);
  const current: Mode = mode ?? (firstUnanswered === -1 ? "review" : "core");

  /** Runs one action; resolves to whether it succeeded (errors are shown, not thrown). */
  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setError("");
    setPending(true);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save. Please try again.");
      return false;
    } finally {
      setPending(false);
    }
  }
  const save = (q: Question, value: unknown) =>
    act(() => reducers.saveProfileAnswer({ questionId: q.id, valueJson: JSON.stringify(value) }));
  const skip = (q: Question) => act(() => reducers.skipProfileQuestion({ questionId: q.id }));
  const clear = (q: Question) => act(() => reducers.clearProfileAnswer({ questionId: q.id }));

  if (!ready_) return <p>Connecting…</p>;
  return (
    <div className="profile">
      <details className="card character-profile"><summary>Your agent’s look</summary>
        <p>The same companion greets you at home and represents you in every trip. People in your trips can see this character.</p>
        {state.appearanceStatus === "ready" ? <CharacterPicker value={myCharacter(state.tables)} connected={ready_} save={variant => reducers.setAgentAppearance({ variant })} /> : <p>Character customization is temporarily unavailable.</p>}
      </details>
      {error && <p role="alert" className="notice">{error}</p>}
      {current === "core" && (
        <section className="card">
          <p className="eyebrow">QUESTION {step + 1} OF {core.length}</p>
          <div className="profile-progress" aria-hidden>
            {core.map((q, i) => <span key={q.id} className={i <= step ? "on" : ""} />)}
          </div>
          {step === 0 && <p>{catalog.intro}</p>}
          <QuestionEditor
            key={core[step].id}
            q={core[step]}
            answer={answers[core[step].id]}
            disabled={pending}
            primary={step === core.length - 1 ? "Save and finish" : "Save and continue"}
            onSave={(v) => save(core[step], v).then((ok) => ok && next())}
            onSkip={() => skip(core[step]).then((ok) => ok && next())}
            onBack={step > 0 ? () => setStep(step - 1) : undefined}
          />
        </section>
      )}
      {current === "done" && (
        <section className="card">
          <h2>Thanks{answers.C01 ? `, ${answers.C01.value}` : ""}.</h2>
          <p>{catalog.outro}</p>
          <Calibration client={client} calibration={calibration} act={act} disabled={pending} />
          <div className="profile-actions">
            <button onClick={() => setMode("review")}>Finish</button>
            <button onClick={() => setMode("more")}>Add more about me (optional)</button>
          </div>
        </section>
      )}
      {current === "more" && (
        <>
          <div className="profile-actions">
            <button onClick={() => setMode("review")}>← Back to my summary</button>
          </div>
          {catalog.sections.filter((s) => s.optional).map((section) => (
            <details key={section.id} className="card profile-section">
              <summary><h2>{section.title}</h2></summary>
              {section.private_note && <p className="hint">{section.private_note}</p>}
              {catalog.questions.filter((q) => q.section === section.id).map((q) => (
                <div key={q.id} className="profile-question">
                  <QuestionEditor q={q} answer={answers[q.id]} disabled={pending} primary="Save"
                    onSave={(v) => save(q, v)} onSkip={() => skip(q)} onClear={answers[q.id] ? () => clear(q) : undefined} />
                </div>
              ))}
            </details>
          ))}
        </>
      )}
      {current === "review" && (
        <Review answers={answers} disabled={pending} onSave={save} onSkip={skip} onClear={clear}
          header={
            <>
              <Calibration client={client} calibration={calibration} act={act} disabled={pending} />
              <div className="profile-actions">
                <button onClick={() => setMode("more")}>Add more about me (optional)</button>
                <button onClick={() => { setStep(0); setMode("core"); }}>Go through the core questions again</button>
              </div>
            </>
          } />
      )}
    </div>
  );

  function next() {
    if (step < core.length - 1) {
      setStep(step + 1);
      setMode("core");
    }
    else setMode("done");
  }
}

function Calibration({ client, calibration, act, disabled }: {
  client: Client; calibration: any; act: (fn: () => Promise<unknown>) => Promise<boolean>; disabled: boolean;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState("");
  const reducers = client.connection.reducers;
  const state = calibration?.state;
  return (
    <div className="profile-calibration">
      <h3>Does this sound like you?</h3>
      {!state && (
        <>
          <p className="hint">Your agent can write a short summary of what it understood, so you can correct anything before it represents you.</p>
          <button disabled={disabled} onClick={() => act(() => reducers.requestProfileSummary({}))}>Write my summary</button>
        </>
      )}
      {state === "PENDING" && <p className="hint">Your agent is writing it… this takes a few seconds.</p>}
      {(state === "PROPOSED" || state === "CONFIRMED") && calibration.summary && (
        <>
          <blockquote>{calibration.summary}</blockquote>
          {state === "CONFIRMED" ? (
            <p className="tag">You confirmed this</p>
          ) : (
            <div className="profile-actions">
              <button disabled={disabled}
                onClick={() => act(() => reducers.confirmProfileSummary({ profileRevision: calibration.profileRevision }))}>
                Yes, that's me
              </button>
              <button disabled={disabled} onClick={() => setCorrecting(true)}>Not quite</button>
              <button disabled={disabled} onClick={() => act(() => reducers.requestProfileSummary({}))}>Rewrite with my latest answers</button>
            </div>
          )}
          {correcting && (
            <form onSubmit={(e) => {
              e.preventDefault();
              act(() => reducers.correctProfileSummary({ correction })).then((ok) => { if (ok) { setCorrecting(false); setCorrection(""); } });
            }}>
              <label>What should your agent understand differently?
                <textarea rows={3} maxLength={1000} value={correction} onChange={(e) => setCorrection(e.target.value)} />
              </label>
              <p className="hint">This only changes the wording. Anything you need us to always respect, add as a need so it's checked.</p>
              <button disabled={disabled || !correction.trim()}>Send to my agent</button>
            </form>
          )}
        </>
      )}
    </div>
  );
}

function Review({ answers, header, disabled, onSave, onSkip, onClear }: {
  answers: Record<string, Answer>; header: React.ReactNode; disabled: boolean;
  onSave: (q: Question, v: unknown) => Promise<boolean>; onSkip: (q: Question) => Promise<boolean>; onClear: (q: Question) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<string>();
  return (
    <section className="card">
      <h2>{answers.C01 ? `${answers.C01.value}'s agent` : "Your agent"}</h2>
      {header}
      {catalog.review_groups.map((group) => {
        const shared = group.questions.filter((id) => answers[id]);
        return (
          <details key={group.id} className="profile-group" open={group.id !== "about"}>
            <summary><h3>{group.title}</h3> <span className="hint">{shared.length ? `${shared.length} shared` : "Not shared yet"}</span></summary>
            {group.questions.map((id) => {
              const q = question(id);
              return (
                <div key={id} className="profile-row">
                  {editing === id ? (
                    <QuestionEditor q={q} answer={answers[id]} disabled={disabled} primary="Save"
                      onSave={(v) => onSave(q, v).then((ok) => ok && setEditing(undefined))}
                      onSkip={() => onSkip(q).then((ok) => ok && setEditing(undefined))}
                      onClear={answers[id] && !q.required ? () => onClear(q).then((ok) => ok && setEditing(undefined)) : undefined}
                      onBack={() => setEditing(undefined)} />
                  ) : (
                    <>
                      <div>
                        <p className="profile-prompt">{q.prompt}{q.private && <span className="tag">Private</span>}</p>
                        <p className={answers[id] ? "" : "hint"}>{describe(q, answers[id])}</p>
                      </div>
                      <button onClick={() => setEditing(id)}>Edit</button>
                    </>
                  )}
                </div>
              );
            })}
          </details>
        );
      })}
    </section>
  );
}

function QuestionEditor({ q, answer, disabled, primary, onSave, onSkip, onClear, onBack }: {
  q: Question; answer?: Answer; disabled: boolean; primary: string;
  onSave: (v: unknown) => Promise<unknown>; onSkip: () => Promise<unknown>; onClear?: () => Promise<unknown>; onBack?: () => void;
}) {
  const [draft, setDraft] = useState<any>(answer?.status === "ANSWERED" ? answer.value : undefined);
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSave(draft); }}>
      <h3>{q.prompt}</h3>
      {q.helper && <p className="hint">{q.helper}</p>}
      <Field q={q} value={draft} onChange={setDraft} disabled={disabled} />
      <div className="profile-actions">
        {onBack && <button type="button" onClick={onBack}>Back</button>}
        {!q.required && <button type="button" disabled={disabled} onClick={() => onSkip()}>Skip for now</button>}
        {onClear && <button type="button" disabled={disabled} onClick={() => onClear()}>Remove my answer</button>}
        <button className="primary" disabled={disabled || !ready(q, draft)}>{primary}</button>
      </div>
    </form>
  );
}

function Chips({ options, picked, onToggle }: { options: { id: string; label: string; hint?: string }[]; picked: string[]; onToggle: (id: string) => void }) {
  return (
    <div className="chips">
      {options.map((o) => (
        <button type="button" key={o.id} aria-pressed={picked.includes(o.id)} onClick={() => onToggle(o.id)} title={o.hint}>
          {o.label}{o.hint && <small>{o.hint}</small>}
        </button>
      ))}
    </div>
  );
}

function Strength({ value, onChange }: { value?: string; onChange: (s: string) => void }) {
  return (
    <span className="strength">
      {[["REQUIRED", "A must"], ["PREFERRED", "A preference"]].map(([id, text]) => (
        <button type="button" key={id} aria-pressed={value === id} onClick={() => onChange(id)}>{text}</button>
      ))}
    </span>
  );
}

/** The input for one question, by its catalog input type. */
function Field({ q, value, onChange, disabled }: { q: Question; value: any; onChange: (v: any) => void; disabled: boolean }) {
  const options = q.options ?? [];
  switch (q.input) {
    case "text":
      return <input value={value ?? ""} maxLength={q.max_length} onChange={(e) => onChange(e.target.value)} />;
    case "long_text":
      return <textarea rows={4} maxLength={q.max_length} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
    case "single": {
      const v = value ?? {};
      const chosen = options.find((o) => o.id === v.selected);
      return (
        <>
          <Chips options={options} picked={v.selected ? [v.selected] : []}
            onToggle={(id) => onChange({ ...(q.text_field && v.details ? { details: v.details } : {}), selected: id })} />
          {chosen?.needs_strength && (
            <p>Is that a must or a preference? <Strength value={v.strength} onChange={(s) => onChange({ ...v, strength: s })} /></p>
          )}
          {q.text_field && (
            <label>{q.text_field.label}
              <input maxLength={300} value={v.details ?? ""} onChange={(e) => onChange({ ...v, details: e.target.value || undefined })} />
            </label>
          )}
        </>
      );
    }
    case "multi": {
      const v = value ?? { selected: [] };
      const withOther = v.selected.some((id: string) => options.find((o) => o.id === id)?.other_text);
      return (
        <>
          {q.max && <p className="hint">Choose up to {q.max}.</p>}
          <Chips options={options} picked={v.selected}
            onToggle={(id) => {
              const selected = toggle(options, v.selected, id, q.max);
              const keepOther = selected.some((s) => options.find((o) => o.id === s)?.other_text);
              onChange({ ...v, selected, other: keepOther ? v.other : undefined,
                avoid: v.avoid?.filter((a: string) => !selected.includes(a)) });
            }} />
          {withOther && (
            <label>Other
              <input maxLength={100} value={v.other ?? ""} onChange={(e) => onChange({ ...v, other: e.target.value || undefined })} />
            </label>
          )}
          {q.avoid_list && (
            <>
              <p className="profile-prompt">{q.avoid_list.label}</p>
              <Chips options={options.filter((o) => !o.exclusive && !v.selected.includes(o.id))} picked={v.avoid ?? []}
                onToggle={(id) => {
                  const avoid = (v.avoid ?? []).includes(id) ? v.avoid.filter((a: string) => a !== id) : [...(v.avoid ?? []), id];
                  onChange({ ...v, avoid: avoid.length ? avoid : undefined });
                }} />
            </>
          )}
          {q.text_field && (
            <label>{q.text_field.label}
              <input maxLength={300} value={v.text ?? ""} onChange={(e) => onChange({ ...v, text: e.target.value || undefined })} />
            </label>
          )}
        </>
      );
    }
    case "rank":
      return <RankedPriorities q={q} value={value} onChange={onChange} disabled={disabled} />;
    case "groups": {
      const v = value ?? {};
      return (
        <>
          {(q.groups ?? []).map((g) => (
            <div key={g.id}>
              <p className="profile-prompt">{g.label}</p>
              <Chips options={g.options} picked={v[g.id] ? [v[g.id]] : []} onToggle={(id) => onChange({ ...v, [g.id]: id })} />
            </div>
          ))}
        </>
      );
    }
    case "avatar": {
      const v = value ?? {};
      return (
        <Chips options={[...AVATARS.map((a) => ({ id: a, label: a })), { id: "initials", label: "Keep my initials" }]}
          picked={v.initials ? ["initials"] : v.avatar ? [v.avatar] : []}
          onToggle={(id) => onChange(id === "initials" ? { initials: true } : { avatar: id })} />
      );
    }
    case "home_base": {
      const v = value ?? {};
      const set = (k: string, val: any) => onChange({ ...v, [k]: val || undefined });
      return (
        <div className="entry">
          <label>City<input maxLength={80} value={v.city ?? ""} onChange={(e) => set("city", e.target.value)} /></label>
          <label>Region (optional)<input maxLength={80} value={v.region ?? ""} onChange={(e) => set("region", e.target.value)} /></label>
          <label>Country<input maxLength={60} value={v.country ?? ""} onChange={(e) => set("country", e.target.value)} /></label>
          <label>Usual airports (optional, e.g. SFO, OAK)
            <input value={(v.airports ?? []).join(", ")}
              onChange={(e) => set("airports", e.target.value.toUpperCase().split(/[\s,]+/).filter(Boolean).slice(0, 5))} />
          </label>
        </div>
      );
    }
    case "needs": {
      const items: any[] = value?.items ?? [];
      const picked = items.map((i) => i.id);
      const update = (next: any[]) => onChange(next.length ? { ...value, items: next } : undefined);
      return (
        <>
          <Chips options={options} picked={picked}
            onToggle={(id) => update(picked.includes(id) ? items.filter((i) => i.id !== id) : [...items, { id }])} />
          {items.map((item) => (
            <div key={item.id} className="profile-item">
              <strong>{options.find((o) => o.id === item.id)?.label}</strong>
              <Strength value={item.strength} onChange={(s) => update(items.map((i) => (i.id === item.id ? { ...i, strength: s } : i)))} />
              <input placeholder="Practical details (optional)" maxLength={300} value={item.details ?? ""}
                onChange={(e) => update(items.map((i) => (i.id === item.id ? { ...i, details: e.target.value || undefined } : i)))} />
            </div>
          ))}
          {q.ask_privately && items.length > 0 && (
            <label className="check">
              <input type="checkbox" checked={!!value?.ask_privately} onChange={(e) => onChange({ ...value, ask_privately: e.target.checked || undefined })} />
              Please ask me privately about the details
            </label>
          )}
        </>
      );
    }
    case "allergies": {
      const items: any[] = value?.items ?? [];
      const update = (next: any[]) => onChange(next.length ? { items: next } : undefined);
      return (
        <>
          {items.map((item, n) => (
            <div key={n} className="profile-item">
              <input placeholder="Food or ingredient" maxLength={80} value={item.food ?? ""}
                onChange={(e) => update(items.map((i, k) => (k === n ? { ...i, food: e.target.value } : i)))} />
              <Chips options={q.checks ?? []} picked={item.checks ?? []}
                onToggle={(id) => update(items.map((i, k) => (k === n ? { ...i, checks: (i.checks ?? []).includes(id) ? i.checks.filter((c: string) => c !== id) : [...(i.checks ?? []), id] } : i)))} />
              <button type="button" onClick={() => update(items.filter((_, k) => k !== n))}>Remove</button>
            </div>
          ))}
          <button type="button" onClick={() => update([...items, { food: "", checks: ["avoid_ingredient"] }])}>Add an allergy</button>
        </>
      );
    }
    case "boundaries": {
      const items: any[] = value?.items ?? [];
      const update = (next: any[]) => onChange(next.length ? { items: next } : undefined);
      return (
        <>
          {items.map((item, n) => (
            <div key={n} className="profile-item">
              <input placeholder="e.g. smoke-free accommodation" maxLength={200} value={item.text ?? ""}
                onChange={(e) => update(items.map((i, k) => (k === n ? { ...i, text: e.target.value } : i)))} />
              <Strength value={item.strength} onChange={(s) => update(items.map((i, k) => (k === n ? { ...i, strength: s } : i)))} />
              <button type="button" onClick={() => update(items.filter((_, k) => k !== n))}>Remove</button>
            </div>
          ))}
          <button type="button" onClick={() => update([...items, { text: "", strength: "REQUIRED" }])}>Add one</button>
        </>
      );
    }
    default:
      return null;
  }
}
