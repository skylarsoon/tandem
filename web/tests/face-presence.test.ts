import { describe, it, expect } from 'vitest';
import { FacePresence } from '../lib/face-presence';
const box = (center: number) => ({ originX: center - 40, originY: 180, width: 80, height: 120 });
describe('camera-driven companion gaze', () => {
  it('follows the person in mirrored camera coordinates, not cursor coordinates', () => {
    const p = new FacePresence();
    expect(p.update([box(320)], 640, 480, 100).gaze.x).toBe(0);
    expect(p.update([box(500)], 640, 480, 200).gaze.x).toBeLessThan(0);
    for (let i = 0; i < 10; i++) p.update([box(120)], 640, 480, 300 + i * 100);
    expect(p.update([box(120)], 640, 480, 1400).gaze.x).toBeGreaterThan(.8);
  });
  it('ignores a short occlusion and waves once per departure, then rearms after returning', () => {
    const p = new FacePresence();
    expect(p.update([], 640, 480, 0).departed).toBe(false);
    p.update([box(320)], 640, 480, 100);
    expect(p.update([], 640, 480, 1000).gaze.present).toBe(true);
    expect(p.update([], 640, 480, 1900).departed).toBe(true);
    expect(p.update([], 640, 480, 5000).departed).toBe(false);
    p.update([box(320)], 640, 480, 6000);
    expect(p.update([], 640, 480, 8000).departed).toBe(true);
  });
  it('keeps following the nearby face when a second face enters the frame', () => {
    const p = new FacePresence(); p.update([box(320)], 640, 480, 100);
    const r = p.update([{ originX: 10, originY: 100, width: 200, height: 240 }, box(330)], 640, 480, 200);
    expect(Math.abs(r.gaze.x)).toBeLessThan(.1);
  });
});
