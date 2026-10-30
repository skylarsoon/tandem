"use client";
import { InvitationGate } from "../../components/invitation-gate";
import { useEffect, useState } from "react";
import { ProfileOnboarding } from "../../components/profile";
import { connect } from "../../lib/client";

/** Persistent profile onboarding: "Let your agent get to know you". The same
 *  authenticated traveller as the rooms page, including linked devices. */
export default function ProfilePage() {
  const [client, setClient] = useState<ReturnType<typeof connect>>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL,
      db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) {
      setError("Set NEXT_PUBLIC_STDB_URL and NEXT_PUBLIC_STDB_DB in web/.env.local, then restart the frontend.");
      return;
    }
    try {
      const next = connect(url, db);
      setClient(next);
      return () => next.close();
    } catch {
      setError("Could not initialize the database connection.");
    }
  }, []);
  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">TANDEM / YOUR AGENT</p>
          <h1>Let your agent get to know you.</h1>
          <p>Skip anything. Change it anytime. Private unless a plan needs it.</p>
        </div>
        <nav style={{ display: "flex", gap: 16, flexWrap: "wrap" }}><a className="badge" href="/app">Your rooms →</a><a className="badge" href="/account">Account</a></nav>
      </header>
      {error ? <p role="alert" className="notice">{error}</p> : client ? <InvitationGate client={client}><ProfileOnboarding client={client} /></InvitationGate> : <p>Connecting…</p>}
    </main>
  );
}
