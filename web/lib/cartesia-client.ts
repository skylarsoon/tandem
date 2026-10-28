import { CARTESIA_VERSION, mouthAt, readSSE, voices, type Phone } from './voice';
import type { Character } from './agents';
export class CartesiaSession {
  private context?: AudioContext;
  private token?: { token: string; expiresAt: number };
  private stream?: MediaStream;
  private socket?: WebSocket;
  private capture?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private mute?: GainNode;
  private request?: AbortController;
  private playing = new Set<AudioBufferSourceNode>();
  private frame = 0;
  private epoch = 0;
  private micEpoch = 0;
  speaking = false;
  constructor(private key: () => string) {}
  async unlock() { this.context ??= new AudioContext(); await this.context.resume(); }
  async authorize() {
    if (this.token && this.token.expiresAt > Date.now()) return this.token.token;
    const identity = localStorage.getItem(`tandem:participant:${process.env.NEXT_PUBLIC_STDB_URL}:${process.env.NEXT_PUBLIC_STDB_DB}`);
    const response = await fetch('/api/voice/session', { method: 'POST', headers: { 'Content-Type': 'application/json', ...(identity ? { Authorization: `Bearer ${identity}` } : {}) }, body: JSON.stringify({ key: this.key() }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Voice could not connect.');
    this.token = data; return data.token as string;
  }
  stopSpeech() {
    this.epoch++; this.request?.abort(); this.request = undefined;
    this.playing.forEach(node => { try { node.stop(); } catch {} }); this.playing.clear();
    cancelAnimationFrame(this.frame); this.speaking = false;
    window.dispatchEvent(new CustomEvent('pip-speech', { detail: { open: 0, width: 1 } }));
  }
  async say(text: string, variant: Character, target?: string, onStart?: () => void, voiceId?: string) {
    this.stopSpeech(); const epoch = this.epoch; this.speaking = true; let started = false;
    try {
      await this.unlock(); const token = await this.authorize();
      if (epoch !== this.epoch) return;
      const context = this.context!; this.request = new AbortController();
      const response = await fetch('https://api.cartesia.ai/tts/sse', {
        method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Cartesia-Version': CARTESIA_VERSION, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: 'sonic-3.6', transcript: text, voice: { mode: 'id', id: voiceId || voices[variant] }, language: 'en',
          output_format: { container: 'raw', encoding: 'pcm_s16le', sample_rate: 24000 }, add_phoneme_timestamps: true }),
        signal: this.request.signal,
      });
      if (!response.ok || !response.body) throw new Error('Cartesia speech could not play. Check your key, credits and connection.');
      let next = 0, beginning = 0, complete = false; const phones: Phone[] = [];
      const paint = () => {
        if (epoch !== this.epoch) return;
        if (!started && context.currentTime >= beginning) { started = true; onStart?.(); }
        window.dispatchEvent(new CustomEvent('pip-speech', { detail: { variant, target, ...mouthAt(phones, context.currentTime - beginning) } }));
        this.frame = requestAnimationFrame(paint);
      };
      await readSSE(response.body, event => {
        if (epoch !== this.epoch) return;
        if (event.type === 'error' || event.status_code >= 400) throw new Error('Cartesia interrupted speech. Try again.');
        if (event.type === 'done') complete = true;
        if (event.type === 'phoneme_timestamps') {
          const timing = event.phoneme_timestamps;
          timing.phonemes.forEach((value: string, i: number) => phones.push({ value, start: timing.start[i], end: timing.end[i] }));
        }
        if (event.type === 'chunk') {
          const bytes = Uint8Array.from(atob(event.data), c => c.charCodeAt(0));
          if (!bytes.length || bytes.length % 2) throw new Error('Invalid speech audio.');
          const buffer = context.createBuffer(1, bytes.length / 2, 24000), pcm = new DataView(bytes.buffer), output = buffer.getChannelData(0);
          for (let i = 0; i < output.length; i++) output[i] = pcm.getInt16(i * 2, true) / 32768;
          if (!beginning) { beginning = context.currentTime + .18; next = beginning; paint(); }
          // Preserve the provider's audio timeline; do not silently time-stretch
          // an underrun and move the mouth out of sync.
          if (next < context.currentTime) throw new Error('Speech connection fell behind. Please replay the message.');
          const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
          this.playing.add(source); source.onended = () => this.playing.delete(source);
          source.start(next); next += buffer.duration;
        }
      });
      if (!complete || !beginning) throw new Error('Cartesia returned incomplete speech.');
      while (epoch === this.epoch && context.currentTime < next) await new Promise(resolve => setTimeout(resolve, 40));
    } catch (error) { if (epoch === this.epoch) throw error; }
    finally { if (epoch === this.epoch) this.stopSpeech(); }
  }
  stopMic() {
    this.micEpoch++; this.socket?.close(); this.socket = undefined;
    this.capture?.disconnect(); this.capture = undefined; this.source?.disconnect(); this.source = undefined;
    this.mute?.disconnect(); this.mute = undefined;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
  }
  async listen(update: (text: string) => void, final: (text: string) => void, failed: (error: Error) => void) {
    this.stopMic(); const run = this.micEpoch;
    await this.unlock(); const token = await this.authorize();
    if (run !== this.micEpoch) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    if (run !== this.micEpoch) { stream.getTracks().forEach(track => track.stop()); return; }
    this.stream = stream; const context = this.context!;
    try {
      await context.audioWorklet.addModule('/voice/pcm-capture.js');
      if (run !== this.micEpoch) return;
      const query = new URLSearchParams({ access_token: token, cartesia_version: CARTESIA_VERSION, model: 'ink-2', encoding: 'pcm_s16le', sample_rate: String(context.sampleRate) });
      const socket = new WebSocket(`wss://api.cartesia.ai/stt/turns/websocket?${query}`); this.socket = socket;
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Microphone connection timed out.')), 15000);
        socket.onopen = () => { clearTimeout(timer); resolve(); };
        socket.onerror = () => { clearTimeout(timer); reject(new Error('Cartesia transcription could not connect.')); };
      });
      if (run !== this.micEpoch) return;
      let delivered = false;
      socket.onmessage = event => {
        if (run !== this.micEpoch || this.speaking) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'error') { this.stopMic(); failed(new Error('Cartesia transcription stopped. Try enabling the microphone again.')); return; }
          if (data.type === 'turn.start') delivered = false;
          if (data.type === 'turn.update') update(data.transcript || '');
          if (data.type === 'turn.end' && data.transcript?.trim()) {
            if (!delivered) { delivered = true; final(data.transcript.trim()); }
          }
        } catch { this.stopMic(); failed(new Error('Transcription received an invalid response.')); }
      };
      socket.onclose = () => { if (run === this.micEpoch) { this.stopMic(); failed(new Error('Microphone connection ended. Enable it again to continue.')); } };
      this.capture = new AudioWorkletNode(context, 'pcm-capture'); this.source = context.createMediaStreamSource(stream);
      this.mute = context.createGain(); this.mute.gain.value = 0;
      this.capture.port.onmessage = event => {
        if (socket.readyState !== WebSocket.OPEN || run !== this.micEpoch) return;
        if (socket.bufferedAmount > context.sampleRate * 4) { this.stopMic(); failed(new Error('Microphone connection is too slow. Please reconnect.')); return; }
        // Send silence during the reply, preventing the microphone from
        // feeding Pip's own voice back into the conversation.
        socket.send(this.speaking ? new ArrayBuffer(event.data.byteLength) : event.data);
      };
      this.source.connect(this.capture); this.capture.connect(this.mute); this.mute.connect(context.destination);
      stream.getTracks().forEach(track => track.addEventListener('ended', () => { if (run === this.micEpoch) { this.stopMic(); failed(new Error('Microphone access ended.')); } }, { once: true }));
    } catch (error) { if (run === this.micEpoch) this.stopMic(); else return; throw error; }
  }
  close() { this.stopMic(); this.stopSpeech(); void this.context?.close(); this.context = undefined; this.token = undefined; }
}
