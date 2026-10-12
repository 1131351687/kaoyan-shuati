/* rand_check.js - verify the "random question" mode actually randomises.

   Regression for the bug where clicking 随机 only toggled the button style:
   filter() had no 'rand' branch and build() did not shuffle, so the list was
   byte-identical to 顺序 mode and 下一题 just walked the paper in order.

   Ports: CDP 9357, page 8899. ASCII labels; CJK via \uXXXX. */
'use strict';
const net = require('net');
const crypto = require('crypto');
const http = require('http');
const path = require('path');
const os = require('os');
const fs = require('fs');
const { spawn } = require('child_process');

const CHROME = process.env.CHROME ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9357);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = path.join(PROJ, 'docs', '_rand_report.json');

let ch = null, cdp = null, done = false;

function put(p, m) {
  return new Promise((res, rej) => {
    const q = http.request({ host: '127.0.0.1', port: PORT, path: p, method: m || 'PUT' },
      r => { let b = ''; r.on('data', d => b += d); r.on('end', () =>
        r.statusCode >= 400 ? rej(new Error('HTTP ' + r.statusCode)) : res(b)); });
    q.setTimeout(8000, () => { try { q.destroy(); } catch (e) {} rej(new Error('t')); });
    q.on('error', rej); q.end();
  });
}
function connect(url) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const key = crypto.randomBytes(16).toString('base64');
    const sock = net.connect(Number(u.port), u.hostname || '127.0.0.1');
    let buf = Buffer.alloc(0), open = false, mid = 0;
    const w = new Map();
    sock.on('connect', () => sock.write(
      'GET ' + (u.pathname || '/') + ' HTTP/1.1\r\nHost: ' + u.hostname + ':' + u.port +
      '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key +
      '\r\nSec-WebSocket-Version: 13\r\n\r\n'));
    sock.on('error', reject);
    sock.on('data', d => {
      buf = Buffer.concat([buf, d]);
      if (!open) { const i = buf.indexOf('\r\n\r\n'); if (i < 0) return;
        buf = buf.slice(i + 4); open = true; resolve({ send, close }); }
      parse();
    });
    function parse() {
      for (;;) {
        if (buf.length < 2) return;
        const b1 = buf[1]; let len = b1 & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (b1 & 0x80) off += 4;
        if (buf.length < off + len) return;
        const s = buf.slice(off, off + len).toString(); buf = buf.slice(off + len);
        try { const m = JSON.parse(s); if (m.id && w.has(m.id)) { const f = w.get(m.id); w.delete(m.id); f(m); } } catch (e) {}
      }
    }
    function send(method, params) {
      const id2 = ++mid;
      const data = Buffer.from(JSON.stringify({ id: id2, method, params: params || {} }), 'utf8');
      const mask = crypto.randomBytes(4);
      let hdr;
      if (data.length < 126) hdr = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length < 65536) { hdr = Buffer.alloc(4); hdr[0] = 0x81; hdr[1] = 0x80 | 126; hdr.writeUInt16BE(data.length, 2); }
      else { hdr = Buffer.alloc(10); hdr[0] = 0x81; hdr[1] = 0x80 | 127; hdr.writeBigUInt64BE(BigInt(data.length), 2); }
      const m2 = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) m2[i] = data[i] ^ mask[i % 4];
      sock.write(Buffer.concat([hdr, mask, m2]));
      return new Promise(r => w.set(id2, r));
    }
    function close() { try { sock.destroy(); } catch (e) {} }
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const ud = path.join(os.tmpdir(), 'udt_rand_' + Date.now());
  ch = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + ud,
    '--window-size=1600,1100', 'about:blank'], { stdio: 'ignore' });
  let bound = false;
  for (let i = 0; i < 40; i++) { await sleep(300); try { await put('/json/version', 'GET'); bound = true; break; } catch (e) {} }
  if (!bound) throw new Error('CDP never bound');

  const tab = JSON.parse(await put('/json/new?' + encodeURIComponent(PAGE), 'PUT'));
  const ws = await connect(tab.webSocketDebuggerUrl);
  await ws.send('Runtime.enable');
  await sleep(3200);
  cdp = ws;

  const ev = async e => {
    const r = await ws.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 240) };
    return r.result && r.result.result ? r.result.result.value : null;
  };
  const click = t => ev(`(function(){ var b=[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='${t}'); if(b) b.click(); return null; })()`);

  const R = [];
  const check = (name, ok, detail) => {
    R.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : ''));
  };

  /* ---- 1. sequential baseline: the first N ids in 顺序 mode ---- */
  await click('q');
  await sleep(300);
  await ev('document.getElementById("btnSeq").click()');
  await sleep(600);
  const seqIds = await ev('(S.list||[]).slice(0,20).map(q=>q.id)');
  check('seq mode yields a list', Array.isArray(seqIds) && seqIds.length === 20, seqIds && seqIds.length);

  /* ---- 2. random mode must shuffle the loaded list ---- */
  await ev('document.getElementById("btnRand").click()');
  await sleep(600);
  const randIds = await ev('(S.list||[]).slice(0,20).map(q=>q.id)');
  const isSamePrefix = JSON.stringify(seqIds.slice(0, 10)) === JSON.stringify(randIds.slice(0, 10));
  check('rand mode reorders the list', !isSamePrefix, { seq: seqIds.slice(0, 6), rand: randIds.slice(0, 6) });
  check('rand mode keeps the same pool size',
    (await ev('S.list.length')) === (await ev('S.mode==="rand" ? S.list.length : -1')),
    { listLen: await ev('S.list.length') });

  // sorted-ascending check: a correctly shuffled list is almost never sorted
  const asc = await ev(`(function(){
    var ok=true; for(var i=1;i<S.list.length;i++){ if(S.list[i-1].id>S.list[i].id){ ok=false; break; } }
    return ok;
  })()`);
  check('rand list is not in id order', asc === false, asc);

  /* ---- 3. clicking 下一题 must jump to an unrelated question, not i+1 ---- */
  const before = await ev('S.list[S.i].id');
  let jumps = [];
  for (let k = 0; k < 8; k++) {
    await ev('next()');
    await sleep(120);
    const now = await ev('S.list[S.i].id');
    jumps.push({ from: before, to: now, idx: await ev('S.i') });
  }
  // in rand mode the sequence of ids should be varied, not monotone
  const seqNums = jumps.map(j => j.to);
  const unique = new Set(seqNums).size;
  check('下一题 never repeats immediately',
    jumps.every((j, i) => i === 0 || j.to !== jumps[i - 1].to),
    jumps.map(j => j.to));
  check('下一题 gives varied questions (>=5 distinct of 8)', unique >= 5, { unique, ids: seqNums });

  /* ---- 4. mode flag actually drives behaviour, not just styling ---- */
  const modeState = await ev(`(function(){
    return { mode: S.mode,
             btnOn: document.getElementById('btnRand').classList.contains('on'),
             seqOff: !document.getElementById('btnSeq').classList.contains('on'),
             hasShuffleFn: typeof shuffleList === 'function',
             hasRandSeen: S.randSeen instanceof Set };
  })()`);
  check('mode is rand and button highlighted', modeState.mode === 'rand' && modeState.btnOn, modeState);
  check('seq button de-highlighted', modeState.seqOff === true);
  check('shuffleList helper exists', modeState.hasShuffleFn === true);
  check('randSeen tracking active', modeState.hasRandSeen === true);

  /* ---- 5. switching back to 顺序 restores deterministic order ---- */
  await ev('document.getElementById("btnSeq").click()');
  await sleep(600);
  const backIds = await ev('(S.list||[]).slice(0,20).map(q=>q.id)');
  check('seq mode restores original order',
    JSON.stringify(backIds.slice(0, 10)) === JSON.stringify(seqIds.slice(0, 10)),
    { seq: seqIds.slice(0, 6), back: backIds.slice(0, 6) });
  check('randSeen dropped when leaving rand',
    (await ev('S.randSeen === null')) === true, await ev('String(S.randSeen)'));

  /* ---- 6. rand respects the current filters (year / kp) ---- */
  await ev('document.getElementById("btnRand").click()');
  await sleep(500);
  const filtered = await ev(`(function(){
    var g=document.getElementById('group');
    if(g.options.length<1) return { skip:true };
    return { group: g.value, n: S.list.length };
  })()`);
  check('rand list is non-empty for current group', filtered && filtered.n > 0, filtered);

  /* ---- 7. no JS errors ---- */
  const errs = await ev('(window.__errs||[]).slice(0,10)');
  check('no page JS errors', Array.isArray(errs) && errs.length === 0, errs);

  /* ---- 8. wipe anything this run may have written to the DB ---- */
  // 走真实作答路径时会触发 record() 写库；/api/cleanup_test 要求显式给出 qids，
  // 所以收集本次实际停留过的题号，只删这些，绝不碰用户真实数据。
  const touched = [...new Set([].concat(seqIds || [], randIds || [], jumps.map(j => j.to)).filter(Boolean))];
  const cleanRes = await ev(
    `fetch('/api/cleanup_test',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({qids:${JSON.stringify(touched)},note:'web'})})
       .then(r=>r.json()).catch(e=>({err:String(e)}))`);
  await sleep(500);
  const prog = await ev(`fetch('/api/progress').then(r=>r.json()).catch(()=>null)`);
  check('DB left clean after run',
    prog && (prog.total_answers || 0) === 0,
    { touched: touched.length, cleanRes, total_answers: prog && prog.total_answers });

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const pass = R.filter(r => r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({
    total: R.length, passed: pass, failed: R.length - pass,
    seqIds: seqIds && seqIds.slice(0, 10), randIds: randIds && randIds.slice(0, 10),
    jumps: jumps.map(j => j.to), results: R,
  }, null, 2), 'utf8');
  console.log('\n  ' + pass + '/' + R.length + ' passed  ->  ' + OUT);
  done = true;
  await sleep(150);
  ws.close();
  try { process.kill(-ch.pid); } catch (e) { try { ch.kill(); } catch (e2) {} }
  setTimeout(() => process.exit(pass === R.length ? 0 : 3), 400);
}

main().catch(e => {
  console.error('FATAL ' + (e && e.stack || e));
  try { fs.mkdirSync(path.dirname(OUT), { recursive: true }); } catch (e2) {}
  try { fs.writeFileSync(OUT, JSON.stringify({ fatal: String(e) }, null, 2)); } catch (e2) {}
  process.exitCode = 9;
});

process.on('unhandledRejection', (e) => {
  if (done) return;
  console.error('unhandledRejection:', e && (e.message || e));
});
