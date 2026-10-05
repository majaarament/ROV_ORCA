// The phone's microphone, slowed down to the 16 kHz that Whisper wants and sent on in 100 ms pieces.
class Mic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.out = new Int16Array(1600);
    this.n = 0;
    this.sum = 0;
    this.count = 0;
    this.phase = 0;
  }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i];
      this.count++;
      this.phase += 16000;
      if (this.phase >= sampleRate) {           // one 16 kHz sample = the average of the ones it replaces
        this.phase -= sampleRate;
        const s = Math.max(-1, Math.min(1, this.sum / this.count));
        this.out[this.n++] = s * 32767;
        this.sum = this.count = 0;
        if (this.n === this.out.length) {
          this.port.postMessage(this.out.slice().buffer);
          this.n = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("mic", Mic);
