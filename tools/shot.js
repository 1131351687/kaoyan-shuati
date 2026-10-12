// 抓题库网页截图（CDP）。不加载任何依赖，避免 ws 包。
// 关键：不加 --single-process（否则 DevTools 端口不绑定）。
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PROJ = path.resolve(__dirname, '..');
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9336);
const OUT = process.argv[2] || path.join(PROJ, 'docs', 'screenshot.png');
const TAB = process.argv[3] || 'q';   // q | paper | grid | stats | hard

function put(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => {
        try { res(JSON.parse(b)); } catch (e) { res(b); }
      });
    });
    req.on('error', rej); req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const udd = path.join(os.tmpdir(), 'cs_' + Date.now());
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--window-size=1440,1100',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + udd, 'about:blank'
  ], { stdio: 'ignore' });

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = await put('/json/version'); break; } catch (e) { await sleep(400); }
  }
  if (!ver) { console.log('CHROME_FAIL'); ch.kill(); process.exit(2); }
  console.log('CDP ready:', ver['Browser']);

  const tab = await put('/json/new?' + encodeURIComponent('http://127.0.0.1:8899/'));
  const ws = tab.webSocketDebuggerUrl;
  if (!ws) { console.log('NO_WS'); process.exit(4); }

  const net = require('net'), crypto = require('crypto');
  const u = new (require('url').URL)(ws);
  const sock = net.connect(Number(u.port), u.hostname);
  const key = crypto.randomBytes(16).toString('base64');
  sock.write(`GET ${u.pathname} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\n` +
    `Connection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);

  let buf = Buffer.alloc(0), ready = false, mid = 0;
  const waiters = new Map();
  sock.on('connect', () => {});
  sock.on('error', e => {
    console.log('WS_SOCKET_ERROR:', e.code || e.message);
    try { ch.kill(); } catch (_) {}
    process.exit(5);
  });
  sock.on('data', d => {
    buf = Buffer.concat([buf, d]);
    if (!ready) {
      const i = buf.indexOf('\r\n\r\n');
      if (i < 0) return;
      buf = buf.slice(i + 4); ready = true;
    }
    while (buf.length >= 2) {
      const op = buf[0] & 0x0f;
      let len = buf[1] & 0x7f, off = 2;
      if (len === 126) { len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len);
      buf = buf.slice(off + len);
      if (op === 8) return;
      if (op !== 1) continue;
      try {
        const m = JSON.parse(payload.toString());
        if (m.id && waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
      } catch (e) {}
    }
  });

  function send(method, params) {
    mid++;
    const p = Buffer.from(JSON.stringify({ id: mid, method, params: params || {} }));
    const mask = crypto.randomBytes(4);
    let head;
    if (p.length < 126) head = Buffer.from([0x81, 0x80 | p.length]);
    else if (p.length < 65536) { head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0x80 | 126; head.writeUInt16BE(p.length, 2); }
    else { head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0x80 | 127; head.writeBigUInt64BE(BigInt(p.length), 2); }
    const md = Buffer.alloc(p.length);
    for (let i = 0; i < p.length; i++) md[i] = p[i] ^ mask[i % 4];
    const id = mid;
    return new Promise(r => { waiters.set(id, r); sock.write(Buffer.concat([head, mask, md])); });
  }

  await new Promise(r => sock.on('connect', r));
  await sleep(500);
  await send('Runtime.enable');
  await send('Page.enable');
  await sleep(3200);

  const ev = async e => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  // 切到指定标签页
  const clicked = await ev(`(function(){
    const t = document.querySelector('.tab[data-tab="${TAB}"]');
    if(!t) return 'no-tab';
    t.click(); return 'ok';
  })()`);
  console.log('切标签页:', clicked, TAB);
  await sleep(800);

  // 组卷页：生成一份混合卷
  if (TAB === 'paper') {
    await ev(`(function(){
      const lv=document.querySelector('#pLevel'); if(lv) lv.value='mixed';
      const g=document.querySelector('#pGo'); if(g) g.click();
    })()`);
    await sleep(1300);
  }

  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  if (!shot.result || !shot.result.data) { console.log('SHOT_FAIL', JSON.stringify(shot).slice(0,200)); process.exit(6); }
  fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
  console.log('截图已存', OUT, (fs.statSync(OUT).size / 1024).toFixed(0), 'KB');

  sock.destroy(); ch.kill();
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {}
  process.exit(0);
})().catch(e => { console.log('ERR', e.message || e); process.exit(1); });
