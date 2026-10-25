import { mkdir, readdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const source = new URL('../node_modules/@mediapipe/tasks-vision/wasm/', import.meta.url);
const destination = new URL('../public/vision/wasm/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of await readdir(source)) {
  if (/^vision_wasm_.*\.(js|wasm)$/.test(name)) await copyFile(new URL(name, source), new URL(name, destination));
}
console.log('Prepared local face-tracking runtime:', fileURLToPath(destination));
