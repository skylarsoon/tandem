/** WASM captures console.error during initialization. Route only its successful
 * CPU startup notice to info; preserve every real diagnostic unchanged. */
let users = 0;
let restore: (() => void) | undefined;
function routeDiagnostics() {
  if (users++ === 0) {
    const error = console.error;
    const routed = (...args: unknown[]) => {
      if (args.length === 1 && typeof args[0] === 'string'
        && args[0].trim() === 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.') console.info(args[0]);
      else error.apply(console, args);
    };
    console.error = routed;
    restore = () => { if (console.error === routed) console.error = error; };
  }
  return () => { if (--users === 0) { restore?.(); restore = undefined; } };
}
export function withVisionDiagnostics<T>(run: () => T): T {
  const release = routeDiagnostics();
  try { return run(); } finally { release(); }
}
export async function initializeVision<T>(run: () => Promise<T>): Promise<T> {
  const release = routeDiagnostics();
  try { return await run(); } finally { release(); }
}
