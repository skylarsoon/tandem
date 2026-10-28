"use client";
import { useEffect, useState } from "react";
import { connect } from "../../lib/client";
import { InvitationGate } from "../../components/invitation-gate";
export default function InvitePage() {
  const [client, setClient] = useState<ReturnType<typeof connect>>();
  const [error, setError] = useState("");
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_STDB_URL, db = process.env.NEXT_PUBLIC_STDB_DB;
    if (!url || !db) { setError("Tandem isn’t configured yet. Please try again later."); return; }
    try { const c = connect(url, db); setClient(c); return () => c.close(); }
    catch { setError("Could not connect. Please refresh to try again."); }
  }, []);
  return <main><a className="wordmark" href="/">tandem ↗</a>
    {error ? <p role="alert">{error}</p> : client ? <InvitationGate client={client}>
      <section className="card"><h1>You’re in good company.</h1><p>Your invitation has been accepted.</p><a className="badge" href="/profile">Meet your companion →</a><p><a href="/app">Return to your trips</a></p></section>
    </InvitationGate> : <p role="status">Connecting securely…</p>}
  </main>;
}
