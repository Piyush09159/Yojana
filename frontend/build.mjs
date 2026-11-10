// Vercel Build Output API: only public assets plus a server-side proxy origin.
// Never read, interpolate or bundle provider/database/WAHA secrets.
import { mkdir, cp, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const raw = process.env.BACKEND_ORIGIN;
if (!raw) throw new Error('Set BACKEND_ORIGIN to the real HTTPS EasyPanel backend origin.');
const origin = new URL(raw);
if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash)
  throw new Error('BACKEND_ORIGIN must be an HTTPS origin without credentials, path, query or fragment.');
const backend = origin.origin;
const out = path.join(root, '.vercel/output');
await rm(out, {recursive: true, force: true});
await mkdir(path.join(out, 'static'), {recursive: true});
await cp(path.join(root, 'public'), path.join(out, 'static'), {recursive: true});
const noCache = {'Cache-Control': 'no-store', 'CDN-Cache-Control': 'no-store'};
const routes = [
  {src: '^/api/(.*)$', dest: `${backend}/api/$1`, headers: noCache},
  {src: '^/(docs|openapi.json)$', dest: `${backend}/$1`, headers: noCache},
  {src: '^/chat/?$', status: 307, headers: {Location: '/app', ...noCache}},
  {src: '^/$', dest: '/index.html', headers: {'Cache-Control': 'no-cache'}},
  {src: '^/app/?$', dest: '/app.html', headers: {'Cache-Control': 'no-cache'}},
  {src: '^/voice/?$', dest: '/voice.html', headers: {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}},
  {src: '^/legacy/?$', dest: '/legacy.html', headers: {'Cache-Control': 'no-cache'}},
  {src: '^/static/(.*)$', headers: {'Cache-Control': 'public, max-age=0, must-revalidate'}, continue: true},
  {handle: 'filesystem'},
  {src: '/.*', status: 404}
];
await writeFile(path.join(out, 'config.json'), JSON.stringify({version: 3, routes}, null, 2));
console.log('Frontend built with HTTPS API proxy; no runtime secrets are bundled.');
