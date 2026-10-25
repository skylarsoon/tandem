// AudioWorklet keeps microphone sampling off the UI thread. The server is told
// the AudioContext's actual sample rate, so no implicit resampling is needed.
class PcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this.samples = []; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (const value of input) this.samples.push(Math.max(-1, Math.min(1, value)));
      if (this.samples.length >= sampleRate / 10) {
        const buffer = new ArrayBuffer(this.samples.length * 2), view = new DataView(buffer);
        this.samples.forEach((value, index) => view.setInt16(index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true));
        this.port.postMessage(buffer, [buffer]); this.samples = [];
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PcmCapture);
