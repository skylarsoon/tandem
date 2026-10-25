"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import type { FaceDetector, HandLandmarker } from '@mediapipe/tasks-vision';
import { initializeVision, withVisionDiagnostics } from '../lib/vision-diagnostics';
import { FacePresence, restingGaze, type Gaze } from '../lib/face-presence';
import type { Character } from '../lib/agents';
import { AgentAvatar } from './agent-avatar';
import { HandWave } from '../lib/hand-wave';

type CameraState = 'off' | 'starting' | 'searching' | 'tracking' | 'error';
export function CompanionPortrait({ variant, connected }: { variant: Character; connected: boolean }) {
  const [waveToken, setWaveToken] = useState(0);
  const [gaze, setGaze] = useState<Gaze>(restingGaze);
  const [state, setState] = useState<CameraState>('off');
  const [error, setError] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | undefined>(undefined);
  const detector = useRef<FaceDetector | undefined>(undefined);
  const handDetector = useRef<HandLandmarker | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const generation = useRef(0);
  const release = useCallback(() => {
    generation.current++;
    clearTimeout(timer.current);
    stream.current?.getTracks().forEach(track => track.stop()); stream.current = undefined;
    handDetector.current?.close(); handDetector.current = undefined;
    detector.current?.close(); detector.current = undefined;
    if (video.current) video.current.srcObject = null;
  }, []);
  const stop = useCallback(() => { release(); setState('off'); setGaze(restingGaze); }, [release]);
  useEffect(() => {
    const hidden = () => { if (document.hidden) stop(); };
    document.addEventListener('visibilitychange', hidden);
    return () => { document.removeEventListener('visibilitychange', hidden); release(); };
  }, [release, stop]);
  useEffect(() => { if (!connected) stop(); }, [connected, variant, stop]);

  async function start() {
    release(); const run = generation.current;
    setError(''); setState('starting');
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera access needs localhost or HTTPS in a supported browser.');
      const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15, max: 20 } } });
      if (run !== generation.current) { media.getTracks().forEach(t => t.stop()); return; }
      stream.current = media;
      media.getVideoTracks().forEach(track => track.addEventListener('ended', stop, { once: true }));
      const { FilesetResolver, FaceDetector, HandLandmarker } = await import('@mediapipe/tasks-vision');
      const files = await FilesetResolver.forVisionTasks('/vision/wasm');
      if (run !== generation.current) return;
      const faceDetector = await initializeVision(() => FaceDetector.createFromOptions(files, {
        baseOptions: { modelAssetPath: '/vision/face-detector.tflite', delegate: 'CPU' },
        runningMode: 'VIDEO', minDetectionConfidence: .6,
      }));
      if (run !== generation.current) { faceDetector.close(); return; }
      detector.current = faceDetector;
      try {
        const hands = await initializeVision(() => HandLandmarker.createFromOptions(files, {
          baseOptions: { modelAssetPath: '/vision/hand-landmarker.task', delegate: 'CPU' },
          runningMode: 'VIDEO', numHands: 1, minHandDetectionConfidence: .5, minHandPresenceConfidence: .5, minTrackingConfidence: .5,
        }));
        if (run !== generation.current) { hands.close(); return; }
        handDetector.current = hands;
      } catch {
        if (run !== generation.current) return;
        setError('Hand tracking is unavailable. Restart the camera to try again.');
      }
      const camera = video.current;
      if (!camera) { stop(); return; }
      camera.srcObject = media; await camera.play();
      if (run !== generation.current) return;
      setState('searching');
      const presence = new FacePresence(); const handWave = new HandWave(); let lastFrame = -1, lastHandFrame = 0;
      const tick = () => {
        if (run !== generation.current) return;
        try {
          if (camera.readyState >= 2 && camera.currentTime !== lastFrame) {
            lastFrame = camera.currentTime;
            const now = performance.now();
            const result = withVisionDiagnostics(() => faceDetector.detectForVideo(camera, now));
            const current = presence.update(result.detections.flatMap(d => d.boundingBox ? [d.boundingBox] : []), camera.videoWidth, camera.videoHeight, now);
            setGaze(current.gaze); setState(current.gaze.present ? 'tracking' : 'searching');
            if (handDetector.current && now-lastHandFrame >= 90) {
              lastHandFrame = now;
              try {
                const hands = withVisionDiagnostics(() => handDetector.current!.detectForVideo(camera, now));
                if (handWave.update(hands.landmarks, now)) setWaveToken(n => n + 1);
              } catch {
                handDetector.current.close(); handDetector.current = undefined;
                setError('Hand tracking paused. Restart the camera to try again.');
              }
            }
          }
          timer.current = setTimeout(tick, 70);
        } catch { release(); setGaze(restingGaze); setState('error'); setError('Eye contact paused. Try enabling the camera again.'); }
      };
      tick();
    } catch (e) {
      if (run !== generation.current) return;
      release(); setState('error'); setGaze(restingGaze);
      setError(e instanceof DOMException && e.name === 'NotAllowedError'
        ? 'Camera permission was not granted. Allow it in your browser to enable eye contact.'
        : e instanceof DOMException && e.name === 'NotFoundError' ? 'No camera was found.' : 'Could not start eye contact. Check your camera and try again.');
    }
  }
  const cameraOn = ['starting', 'searching', 'tracking'].includes(state);
  return <div className="companion-portrait">
    <div className="agent-hello-button">
      <AgentAvatar variant={variant} size="hero" animated={connected} waveToken={waveToken} gaze={gaze} />
    </div>
    {<div className="eye-contact-control">
      <button type="button" className="camera-icon-button" onClick={cameraOn ? stop : () => void start()}
        disabled={!connected} aria-label={cameraOn ? 'Turn off camera' : 'Enable eye contact'}
        aria-pressed={cameraOn} data-state={error ? 'error' : state}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="6" width="12" height="12" rx="3" />
          <path d="m15 10 6-3v10l-6-3" />
          {!cameraOn && <path d="m3 3 18 18" />}
        </svg>
      </button>
      {error && <span className="vision-sr-only" role="alert">{error}</span>}
      <video ref={video} className="eye-contact-video" muted playsInline aria-hidden="true" />
    </div>}
  </div>;
}
