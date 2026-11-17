import { cp, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
await mkdir(`${root}/static/vendor`, {recursive:true});
for (const item of ['build/pdf.mjs', 'build/pdf.worker.mjs', 'cmaps', 'standard_fonts', 'wasm', 'LICENSE']) {
  await cp(`${root}/node_modules/pdfjs-dist/${item}`, `${root}/static/vendor/${item}`, {recursive:true});
}
