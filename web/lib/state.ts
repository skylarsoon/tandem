export type Row = Record<string, any>;
// Yours, whatever room is open: the room list and your persistent profile.
export const baseTables = [
  "my_rooms",
  "my_profile",
  "my_profile_answers",
  "my_profile_calibration",
];
export const roomTables = [
  "room_members",
  "room_agents",
  "room_messages",
  "room_threads",
  "room_events",
  "room_candidates",
  "room_milestones",
  "room_proposals",
  "room_consent_counts",
  "room_presence",
  "room_agent_activity",
  "room_trip_brief",
  "room_intake_progress",
  "my_intake",
  "my_constraint_patches",
  "my_rules",
  "my_constraints",
  "my_questions",
  "my_private_messages",
  "my_agent_messages",
  "my_proposal_impacts",
  "my_consents",
];
export const personalTables = ["my_participation_preferences"];
// Cosmetic subscriptions must never prevent access to trips on an older server.
export const appearanceTables = ["my_agent_appearance", "room_agent_appearances"];
const allTables = [...baseTables, ...roomTables, ...personalTables, ...appearanceTables];
export function queries(room?: string) {
  if (!room) return [];
  if (!/^\d+$/.test(room)) throw new Error("Invalid room ID");
  return [
    ...roomTables.map((t) => `SELECT * FROM ${t} WHERE room_id = ${room}`),
    ...personalTables.map((t) => `SELECT * FROM ${t}`),
  ];
}
export function accessor(name: string) {
  return name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}
export function json<T>(text: unknown, fallback: T): T {
  try {
    const parsed = JSON.parse(String(text));
    return parsed !== null && typeof parsed === "object" ? parsed : fallback;
  } catch {
    return fallback;
  }
}
export function money(cents: unknown) {
  return typeof cents === "number"
    ? new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(cents / 100)
    : "Unknown price";
}
export function questionAnswer(kind: string, yes: boolean, preferences: Row) {
  if (kind === "PARTICIPATION_CONFIRMATION")
    return yes
      ? {
          speak_up_for_me: !!preferences.speakUpForMe,
          ask_me_first: preferences.askMeFirst !== false,
          give_me_time: preferences.giveMeTime !== false,
          reminders_opted_in: !!preferences.remindersOptedIn,
        }
      : { confirmed: false };
  if (
    [
      "OBJECTION_DISCLOSURE",
      "CONCESSION_CONSULTATION",
      "BROADER_EXPLORATION",
    ].includes(kind)
  )
    return { granted: yes };
  if (kind === "MISSING_FACT") return { answer: yes };
  throw new Error("Unsupported question type");
}
export function questionAction(question: Row, yes: boolean, preferences: Row) {
  if (question.state !== "OPEN")
    throw new Error("This question is no longer open");
  const answer = questionAnswer(question.kind, yes, preferences);
  const nudge = json<Row>(question.actionJson, {}).nudge;
  if (question.kind === "MISSING_FACT" && nudge) {
    const choice = nudge[yes ? "yes" : "no"];
    if (!choice || typeof choice !== "object")
      throw new Error("This question has no valid offered answer");
    Object.assign(answer, { constraint: choice });
  }
  return {
    questionId: question.id,
    version: question.version,
    answerJson: JSON.stringify(answer),
  };
}
export function proposalAction(proposal: Row, response: string, note?: string) {
  if (proposal.state !== "AWAITING_REVIEW" || !proposal.isReviewer)
    throw new Error("This proposal is no longer available for review");
  return {
    proposalId: proposal.id,
    version: proposal.version,
    termsDigest: proposal.termsDigest,
    response,
    note: note?.trim() || undefined,
  };
}
export type ClientSnapshot = {
  status: string;
  error: string;
  tables: Record<string, Row[]>;
  appearanceStatus?: "loading" | "ready" | "unavailable";
};
/** Core and cosmetic subscriptions are independent. Epochs discard late callbacks. */
export class RoomSession {
  snapshot: ClientSnapshot = { status: "connecting", error: "", tables: {} };
  private listeners = new Set<() => void>();
  private room?: string;
  private epoch = 0;
  private baseReady = false;
  private roomReady = false;
  private appearanceReady = false;
  private handles: { unsubscribe: () => void }[] = [];
  private detach: (() => void)[] = [];
  private scheduled = false;
  private disposed = false;
  constructor(private connection: any) {}
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  getSnapshot = () => this.snapshot;
  private emit(patch: Partial<ClientSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((fn) => fn());
  }
  error(message: string) {
    if (this.disposed) return;
    this.emit({ status: "error", error: message });
  }
  connected() {
    if (this.disposed) return;
    this.cleanup();
    this.baseReady = false;
    this.roomReady = false;
    this.appearanceReady = false;
    this.emit({ status: "synchronizing", error: "", tables: {}, appearanceStatus: "loading" });
    const epoch = this.epoch;
    for (const name of allTables) {
      const table = this.connection.db[accessor(name)];
      const update = () => {
        if (!this.scheduled) {
          this.scheduled = true;
          queueMicrotask(() => {
            this.scheduled = false;
            this.refresh();
          });
        }
      };
      table.onInsert(update);
      table.onDelete(update);
      table.onUpdate(update);
      this.detach.push(() => {
        table.removeOnInsert(update);
        table.removeOnDelete(update);
        table.removeOnUpdate(update);
      });
    }
    this.handles.push(
      this.connection
        .subscriptionBuilder()
        .onApplied(() => {
          if (epoch === this.epoch) {
            this.baseReady = true;
            this.refresh();
          }
        })
        .onError((ctx: { event?: unknown }) => {
          if (epoch === this.epoch) {
            console.error("Room list subscription failed", ctx.event);
            this.error("Could not load your trips. Reconnect to try again.");
          }
        })
        .subscribe(baseTables.map((t) => `SELECT * FROM ${t}`)),
    );
    this.subscribeRoom();
    const appearanceQueries = ["SELECT * FROM my_agent_appearance"];
    if (this.room) appearanceQueries.push(`SELECT * FROM room_agent_appearances WHERE room_id = ${this.room}`);
    this.handles.push(this.connection.subscriptionBuilder()
      .onApplied(() => {
        if (epoch !== this.epoch) return;
        this.appearanceReady = true;
        this.emit({ appearanceStatus: "ready" });
        this.refresh();
      })
      .onError(() => {
        if (epoch !== this.epoch) return;
        this.appearanceReady = false;
        this.emit({ appearanceStatus: "unavailable" });
        this.refresh();
      })
      .subscribe(appearanceQueries));
  }
  disconnected() {
    this.baseReady = false;
    this.roomReady = false;
    this.appearanceReady = false;
    this.emit({ status: "disconnected", tables: {}, appearanceStatus: "loading" });
  }
  select(room?: string) {
    this.room = room;
    // Recreate both subscriptions to ensure no stale room cache is displayed.
    this.connected();
  }
  private subscribeRoom() {
    const epoch = this.epoch,
      sql = queries(this.room);
    if (!sql.length) {
      this.roomReady = true;
      return;
    }
    this.handles.push(
      this.connection
        .subscriptionBuilder()
        .onApplied(() => {
          if (epoch === this.epoch) {
            this.roomReady = true;
            this.refresh();
          }
        })
        .onError(() => {
          if (epoch === this.epoch) this.error("Room subscription failed");
        })
        .subscribe(sql),
    );
  }
  private refresh() {
    if (this.disposed || this.snapshot.status === "error") return;
    if (
      !this.baseReady ||
      !this.roomReady ||
      this.snapshot.status === "disconnected"
    )
      return;
    const tables: Record<string, Row[]> = {};
    for (const name of allTables) {
      tables[name] = appearanceTables.includes(name) && !this.appearanceReady ? [] : Array.from(
        this.connection.db[accessor(name)].iter(),
      ) as Row[];
      if (roomTables.includes(name) || name === "room_agent_appearances")
        tables[name] = tables[name].filter(
          (r) => String(r.roomId) === this.room,
        );
    }
    this.emit({ status: "ready", tables });
  }
  private cleanup() {
    this.epoch++;
    this.handles.splice(0).forEach((h) => h.unsubscribe());
    this.detach.splice(0).forEach((fn) => fn());
  }
  dispose() {
    this.disposed = true;
    this.cleanup();
    this.listeners.clear();
  }
}
