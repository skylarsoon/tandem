"use client";
import { useEffect, useState } from "react";
import { ProfileOnboarding } from "../../components/profile";
import { connect } from "../../lib/client";
import { ProfileCompanion } from "../../components/design";

/** Persistent profile onboarding: "Let your agent get to know you". The same
 *  browser identity as the rooms page, so rooms can suggest from it. */
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
    <main id="main-content">
      <header className="page-heading">
        <div>
          <p className="eyebrow">A LITTLE ABOUT YOU / PRIVATE</p>
          <h1>A little more you.</h1>
          <p>Skip anything. Change it anytime. Private unless a plan needs it.</p>
        </div>
        <a className="button secondary" href="/">Your plans →</a>
      </header>
      <div className="profile-layout">
        <ProfileCompanion />
        <div className="profile-content">
          {error ? <p role="alert" className="notice">{error}</p> : client ? <ProfileOnboarding client={client} /> : <p role="status">Connecting…</p>}
        </div>
      </div>
    </main>
  );
}
