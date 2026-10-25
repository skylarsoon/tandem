import { expect, it, vi } from 'vitest';
import { initializeVision, withVisionDiagnostics } from '../lib/vision-diagnostics';
it('routes the exact TFLite initialization notice to info and preserves real errors', () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    withVisionDiagnostics(() => { console.error('INFO: Created TensorFlow Lite XNNPACK delegate for CPU.'); console.error('Real inference failure', { code: 3 }); });
    expect(info).toHaveBeenCalledOnce(); expect(error).toHaveBeenCalledExactlyOnceWith('Real inference failure', { code: 3 });
    expect(console.error).toBe(error);
    expect(() => withVisionDiagnostics(() => { throw new Error('failed'); })).toThrow('failed');
    expect(console.error).toBe(error);
  } finally { error.mockRestore(); info.mockRestore(); }
});

it('routes the logger captured by asynchronous WASM initialization and restores the console', async () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const info = vi.spyOn(console, 'info').mockImplementation(() => {});
  try {
    const logger = await initializeVision(async () => { await Promise.resolve(); return console.error.bind(console); });
    expect(console.error).toBe(error);
    logger('INFO: Created TensorFlow Lite XNNPACK delegate for CPU.');
    logger('Actual WASM error');
    expect(info).toHaveBeenCalledOnce();
    expect(error).toHaveBeenCalledExactlyOnceWith('Actual WASM error');
    await expect(initializeVision(async () => { throw new Error('init failed'); })).rejects.toThrow('init failed');
    expect(console.error).toBe(error);
  } finally { error.mockRestore(); info.mockRestore(); }
});
