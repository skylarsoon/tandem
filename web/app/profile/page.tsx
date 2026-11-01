"use client";
import { InvitationGate } from "../../components/invitation-gate";
import { useEffect, useState } from "react";
import { ProfileOnboarding } from "../../components/profile";
import { ProductHeader } from "../../components/product-shell";
import { connect } from "../../lib/client";
export default function ProfilePage() {
  const [client, setClient] = useState<ReturnType<typeof connect>>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL, db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) { setError("We couldn’t connect to Tandem. Please try again shortly."); return; }
    try { const next = connect(url, db); setClient(next); return () => next.close(); }
    catch { setError("Could not initialize the connection. Please refresh to try again."); }
  }, []);
  return <main className="product-app profile-app"><a className="product-skip" href="#profile-workspace">Skip to your profile</a><ProductHeader active="profile"/>
    <div id="profile-workspace">{error ? <p role="alert" className="notice">{error}</p> : client ? <InvitationGate client={client}><ProfileOnboarding client={client}/></InvitationGate> : <div className="connection-empty" role="status">Connecting to your companion…</div>}</div>
  </main>;
}
