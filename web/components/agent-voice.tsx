"use client";
import { useEffect, useRef, useState } from 'react';
import { CartesiaSession } from '../lib/cartesia-client';
import { spokenText, mediatorVoice } from '../lib/voice';
import { messageCharacter, type Character } from '../lib/agents';
import type { Tables, TimelineItem } from '../lib/planning';

export type VoicePlayback = { key: string; text: string; target: string; phase: 'loading' | 'speaking' };
type Utterance = { voice?: string; key: string; text: string; variant: Character; target: string };
export function AgentVoice({ items, tables, discussion, connected, onTranscript, onPlayback }: {
  items: TimelineItem[]; tables: Tables; discussion: boolean; connected: boolean;
  onTranscript: (text: string) => Promise<boolean>;
  onPlayback?: (value: VoicePlayback | null) => void;
}) {
  const [enabled, setEnabled] = useState(false), [mic, setMic] = useState<'off' | 'starting' | 'on'>('off');
  const [key, setKey] = useState(''), [error, setError] = useState(''), [hearing, setHearing] = useState('');
  const [speaking, setSpeaking] = useState(false), [connecting, setConnecting] = useState(false);
  const [paused, setPaused] = useState(false), [phase, setPhase] = useState<'loading' | 'speaking'>('loading');
  const settings = useRef<HTMLDetailsElement>(null);
  const playbackCallback = useRef(onPlayback); playbackCallback.current = onPlayback;
  const keyRef = useRef(key); keyRef.current = key;
  const session = useRef<CartesiaSession | null>(null);
  const latest = useRef({ items, tables, discussion, connected, onTranscript }); latest.current = { items, tables, discussion, connected, onTranscript };
  const seen = useRef(new Set<string>()), queue = useRef<Utterance[]>([]), running = useRef(false), epoch = useRef(0), voiceOn = useRef(false);
  const micRun = useRef(0), submission = useRef(Promise.resolve());
  function client() { return session.current ??= new CartesiaSession(() => keyRef.current); }
  function baseline() { seen.current = new Set(latest.current.items.map(item => item.key)); }
  function stop() {
    epoch.current++; micRun.current++; voiceOn.current = false; queue.current = []; running.current = false;
    session.current?.stopMic(); session.current?.stopSpeech(); setEnabled(false); setMic('off'); setSpeaking(false); setHearing(''); playbackCallback.current?.(null); baseline();
  }
  async function drain() {
    if (running.current || !voiceOn.current) return;
    running.current = true; const run = epoch.current;
    try {
      while (queue.current.length && voiceOn.current && run === epoch.current) {
        const next = queue.current.shift()!; setSpeaking(true); setPhase('loading');
        playbackCallback.current?.({ ...next, phase: 'loading' });
        await client().say(next.text, next.variant, next.target, () => {
          if (run === epoch.current) { setPhase('speaking'); playbackCallback.current?.({ ...next, phase: 'speaking' }); }
        }, next.voice);
      }
    } catch (e) {
      if (run === epoch.current) { stop(); setError(e instanceof Error ? e.message : 'Voice stopped. Text is still available.'); }
    } finally { if (run === epoch.current) { running.current = false; setSpeaking(false); playbackCallback.current?.(null); } }
  }
  useEffect(() => {
    if (!enabled) { baseline(); return; }
    for (const item of items) {
      if (seen.current.has(item.key)) continue;
      seen.current.add(item.key);
      const text = spokenText(item, discussion);
      if (text) queue.current.push({ key: item.key, text, voice: messageCharacter(tables, item.row.senderAgentId).mediator ? mediatorVoice : undefined, variant: messageCharacter(tables, item.row.senderAgentId).variant, target: String(item.row.senderAgentId ?? tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal') });
    }
    void drain();
  });
  useEffect(() => { stop(); }, [discussion, connected]); // A privacy boundary cancels all queued audio and capture.
  useEffect(() => {
    const hide = () => { if (document.hidden && voiceOn.current) { stop(); setPaused(true); } };
    document.addEventListener('visibilitychange', hide);
    return () => { epoch.current++; micRun.current++; voiceOn.current = false; session.current?.close(); document.removeEventListener('visibilitychange', hide); };
  }, []);
  async function enable() {
    setError(''); setPaused(false); setConnecting(true); const run = ++epoch.current;
    try {
      await client().unlock(); await client().authorize();
      if (run !== epoch.current) return false;
      baseline(); voiceOn.current = true; setEnabled(true); if (settings.current) settings.current.open = false; return true;
    } catch (e) { if (run === epoch.current) setError(e instanceof Error ? e.message : 'Voice could not start.'); return false; } finally { setConnecting(false); }
  }
  async function microphone() {
    if (mic !== 'off') { micRun.current++; session.current?.stopMic(); setMic('off'); setHearing(''); return; }
    const run = ++micRun.current; setMic('starting'); setError('');
    if (!voiceOn.current && !await enable()) { if (run === micRun.current) setMic('off'); return; }
    if (run !== micRun.current) return;
    try {
      await client().listen(text => { if (run === micRun.current) setHearing(text); }, text => {
        // Turns are serialized; a second utterance cannot disappear behind an
        // in-flight reducer. Voice never invokes approval/confirmation reducers.
        submission.current = submission.current.then(async () => {
          if (run !== micRun.current || latest.current.discussion || !latest.current.connected) return;
          setHearing(text);
          if (text.length > 4000) { setError('That turn is too long. Please say it in shorter parts.'); return; }
          if (await latest.current.onTranscript(text)) setHearing('');
          else { micRun.current++; client().stopMic(); setMic('off'); setError('Your spoken message could not be sent. It is kept in the message box for retry.'); }
        }).catch(() => { setError('Your spoken message could not be sent. Please retry.'); });
      }, e => { if (run === micRun.current) { micRun.current++; setMic('off'); setError(e.message); } });
      if (run === micRun.current) setMic('on');
    } catch (e) { if (run === micRun.current) { setMic('off'); setError(e instanceof Error ? e.message : 'Microphone could not start.'); } }
  }
  async function replay() {
    const candidates = items.filter(item => spokenText(item, discussion));
    const selected = discussion ? candidates : candidates.slice(-1);
    if (!selected.length || (!voiceOn.current && !await enable())) return;
    queue.current = selected.map(item => ({ key: item.key, text: spokenText(item, discussion)!, voice: messageCharacter(tables, item.row.senderAgentId).mediator ? mediatorVoice : undefined, variant: messageCharacter(tables, item.row.senderAgentId).variant, target: String(item.row.senderAgentId ?? tables.room_members?.find(m => m.isMe)?.agentId ?? 'personal') }));
    void drain();
  }
  const hasReplies = items.some(item => spokenText(item, discussion));
  return <div className="agent-voice" data-speaking={speaking} data-listening={mic === 'on'}>
    <div className="voice-caption"><span className="voice-equalizer" aria-hidden="true"><i /><i /><i /><i /></span><div><strong>{speaking ? phase === 'loading' ? 'Preparing voice…' : discussion ? 'The floor is theirs' : 'Pip is speaking' : mic === 'on' ? 'I’m listening' : discussion ? 'Listen to your crew' : 'A little more personal'}</strong><small>{paused ? 'Audio paused when you left this window. Enable voice to resume.' : speaking ? 'Following the actual conversation, one voice at a time.' : enabled ? 'New agent replies will play aloud.' : discussion ? 'Play the conversation, or follow along in text.' : 'Talk naturally. Your agent will help with the details.'}</small></div></div>
    <div className="voice-actions">
      <button type="button" disabled={!connected || connecting} aria-pressed={enabled} onClick={() => enabled ? stop() : void enable()}>{connecting ? 'Connecting…' : enabled ? '◖ Voice on' : '◖ Enable voice'}</button>
      {!discussion && <button type="button" disabled={!connected} aria-pressed={mic !== 'off'} onClick={() => void microphone()}>{mic === 'starting' ? 'Connecting microphone…' : mic === 'on' ? 'Stop microphone' : 'Talk to Pip'}</button>}
      {hasReplies && <button type="button" className="voice-play" disabled={!connected || connecting} onClick={speaking ? () => { queue.current = []; client().stopSpeech(); } : () => void replay()}>{speaking ? 'Stop speaking' : discussion ? 'Play conversation' : 'Replay latest reply'}</button>}
      <details ref={settings}><summary aria-label="Voice settings">Voice settings</summary><label>Cartesia API key<input type="password" autoComplete="off" value={key} onChange={e => { stop(); session.current?.close(); session.current = null; setKey(e.target.value); }} placeholder="Use server key, or enter a key" /></label><small>Kept in memory only. Audio goes to Cartesia when the microphone is on.</small></details>
    </div>
    {mic === 'on' && <small className="voice-listening">{speaking ? 'Pip is replying · microphone paused' : 'Listening · pause when you finish a thought'}</small>}
    {hearing && <p className="voice-transcript">{hearing}</p>}
    {error && <p className="notice" role="alert">{error}</p>}
  </div>;
}
