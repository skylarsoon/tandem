export type FaceBox = { originX: number; originY: number; width: number; height: number };
export type Gaze = { x: number; y: number; present: boolean };
export const restingGaze: Gaze = { x: 0, y: 0, present: false };
const clamp = (n: number) => Math.max(-1, Math.min(1, n));

/** Position only: no face identity, biometric template, frames or history are retained. */
export class FacePresence {
  private lastSeen = 0;
  private gaze: Gaze = { ...restingGaze };
  private center?: { x: number; y: number };
  update(boxes: FaceBox[], width: number, height: number, now: number) {
    const valid = boxes.filter(b => b.width > 0 && b.height > 0);
    const previous = this.center;
    const box = valid.sort((a, b) => previous
      ? Math.hypot((a.originX + a.width / 2) / width - previous.x, (a.originY + a.height / 2) / height - previous.y)
        - Math.hypot((b.originX + b.width / 2) / width - previous.x, (b.originY + b.height / 2) / height - previous.y)
      : b.width * b.height - a.width * a.height)[0];
    if (box && width > 0 && height > 0) {
      const center = { x: (box.originX + box.width / 2) / width, y: (box.originY + box.height / 2) / height };
      this.center = center; this.lastSeen = now;
      // Mirror the camera, matching how people see themselves in a selfie preview.
      const targetX = clamp((1 - center.x * 2) * 1.8), targetY = clamp((center.y * 2 - 1) * 1.4);
      this.gaze = { x: this.gaze.x + (targetX - this.gaze.x) * .55, y: this.gaze.y + (targetY - this.gaze.y) * .55, present: true };
      return { gaze: { ...this.gaze }, departed: false };
    }
    const departed = this.gaze.present && now - this.lastSeen >= 1800;
    if (departed) { this.gaze = { ...restingGaze }; this.center = undefined; }
    return { gaze: { ...this.gaze }, departed };
  }
}
