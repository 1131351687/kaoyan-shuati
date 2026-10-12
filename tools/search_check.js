// Search + notes CDP regression (default port 9344)
// All Chinese is written as \uXXXX escapes so no encoding round-trip can corrupt this file.
const http = require('http');
const net = require('net');
const crypto = require('crypto');
const path = require('path');
const { spawn } = require('child_process');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9344);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const UD = path.join(process.env.TEMP || '.', 'udt_search_' + Date.now());

function put(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(b));
    });
    req.on('error', rej); req.setTimeout(8000, () => req.destroy(new Error('put timeout'))); req.end();
  });
}
function get(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(b));
    });
    req.on('error', rej); req.setTimeout(8000, () => req.destroy(new Error('get timeout'))); req.end();
  });
}

class CDP {
  constructor(wsUrl) {
    const u = new URL(wsUrl);
    this.host = u.hostname || '127.0.0.1'; this.port = Number(u.port || PORT);
    this.path = u.pathname; this.buf = Buffer.alloc(0); this.waiters = new Map(); this.mid = 0;
  }
  connect() {
    return new Promise((res, rej) => {
      const key = crypto.randomBytes(16).toString('base64');
      this.sock = net.connect(this.port, this.host, () => {
        this.sock.write(`GET ${this.path} HTTP/1.1\r\nHost: ${this.host}:${this.port}\r\n` +
          `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      });
      this.sock.setTimeout(10000, () => rej(new Error('sock timeout')));
      let hs = false;
      this.sock.on('data', d => {
        if (!hs) {
          this.buf = Buffer.concat([this.buf, d]);
          const i = this.buf.indexOf('\r\n\r\n');
          if (i < 0) return;
          const head = this.buf.slice(0, i).toString();
          this.buf = this.buf.slice(i + 4);
          if (!/101/.test(head)) return rej(new Error('handshake failed'));
          hs = true; res(); this.pump(); return;
        }
        this.buf = Buffer.concat([this.buf, d]); this.pump();
      });
      this.sock.on('error', e => { if (!hs) rej(e); });
    });
  }
  pump() {
    for (;;) {
      const buf = this.buf;
      if (buf.length < 2) return;
      const l0 = buf[1] & 0x7f;
      let off = 2, len = l0;
      if (l0 === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
      else if (l0 === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len).toString();
      this.buf = buf.slice(off + len);
      let msg; try { msg = JSON.parse(payload); } catch (e) { continue; }
      const w = this.waiters.get(msg.id);
      if (w) { this.waiters.delete(msg.id); msg.error ? w.rej(new Error(JSON.stringify(msg.error))) : w.res(msg.result); }
    }
  }
  send(method, params) {
    const id = ++this.mid;
    const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }));
    const mask = crypto.randomBytes(4);
    let hdr;
    if (data.length < 126) { hdr = Buffer.alloc(6); hdr[1] = 0x80 | data.length; }
    else if (data.length < 65536) { hdr = Buffer.alloc(8); hdr[1] = 0x80 | 126; hdr.writeUInt16BE(data.length, 2); }
    else { hdr = Buffer.alloc(14); hdr[1] = 0x80 | 127; hdr.writeBigUInt64BE(BigInt(data.length), 2); }
    hdr[0] = 0x81; mask.copy(hdr, hdr.length - 4);
    const out = Buffer.concat([hdr, data]);
    for (let i = 0; i < data.length; i++) out[hdr.length + i] = data[i] ^ mask[i % 4];
    this.sock.write(out);
    return new Promise((res, rej) => this.waiters.set(id, { res, rej }));
  }
  close() { try { this.sock.destroy(); } catch (e) { } }
}

let ch, cdp;
async function ev(expr) {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' :: ' + (r.exceptionDetails.exception || {}).description);
  return r.result.value;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

const KW_LAGRANGE = '\u62c9\u683c\u6717\u65e5';                        // lagrange
const KW_LINALG = '\u7ebf\u6027\u4ee3\u6570';                          // linear algebra
const KW_MATRIX = '\u77e9\u9635';                                      // matrix
const KW_NOPE = 'zzz\u4e0d\u53ef\u80fd\u5339\u914dzzz';                 // impossible
const NOTE_TEXT = '\u8fd9\u662f\u6d4b\u8bd5\u7b14\u8bb0\uff1a\u6cf0\u52d2\u5c55\u5f00\u5230\u4e09\u9636';
const NOTE_MARK = '\u6cf0\u52d2\u5c55\u5f00';
const HINT_SAVED = '\u5df2\u4fdd\u5b58';

async function main() {
  ch = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox',
    '--disable-dev-shm-usage', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${UD}`, 'about:blank'], { stdio: 'ignore' });
  let up = false;
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try { const v = await get('/json/version'); if (/Chrome/.test(v)) { up = true; break; } } catch (e) { }
  }
  if (!up) throw new Error('CDP not up on port ' + PORT);
  const tab = JSON.parse(await put('/json/new?about:blank'));
  cdp = new CDP(tab.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: PAGE });
  await sleep(2500);

  const R = {};
  R.total = await ev(`S.data.questions.length`);

  R.search = await ev(`(async()=>{
    const out = {};
    const box = document.getElementById('search');
    if(!box) return {noBox:true};
    const fire = v => { box.value = v; box.oninput(); };
    const LAG=${JSON.stringify(KW_LAGRANGE)}, LA=${JSON.stringify(KW_LINALG)},
          MX=${JSON.stringify(KW_MATRIX)}, NOPE=${JSON.stringify(KW_NOPE)};

    fire(LAG); await new Promise(r=>setTimeout(r,700));
    out.lagrange = {n: S.list.length, groups: [...new Set(S.list.map(q=>q.group))].length,
                    allMatch: S.list.every(q=>((q.stem||'')+' '+(q.kp||'')).includes(LAG))};

    fire(LA+' '+MX); await new Promise(r=>setTimeout(r,700));
    out.multi = {n: S.list.length,
                 allAnd: S.list.every(q=>{
                   const h=((q.stem||'')+' '+(q.kp||'')+' '+(q.kp1||'')).toLowerCase();
                   return h.includes(LA.toLowerCase()) && h.includes(MX.toLowerCase());})};

    fire('1942'); await new Promise(r=>setTimeout(r,700));
    out.byId = {n: S.list.length, first: S.list[0]?S.list[0].id:''};

    fire(NOPE); await new Promise(r=>setTimeout(r,700));
    out.none = {n: S.list.length};

    fire(''); await new Promise(r=>setTimeout(r,700));
    out.cleared = {n: S.list.length};
    return out;
  })()`);

  R.note = await ev(`(async()=>{
    const out = {};
    S.mode='seq'; syncMode(); reb();
    await new Promise(r=>setTimeout(r,400));
    S.i = 0; render();
    await new Promise(r=>setTimeout(r,400));
    const qid = S.list[0].id;
    out.qid = qid;
    const nb = document.getElementById('noteBox');
    out.hasBox = !!nb;
    if(!nb) return out;
    nb.value = ${JSON.stringify(NOTE_TEXT)};
    nb.oninput();
    await new Promise(r=>setTimeout(r,900));
    out.saved = S.notes[qid] || '';
    out.hint = document.getElementById('noteHint').textContent;
    const ls = JSON.parse(localStorage.getItem('ztq_local')||'{}');
    out.inLS = (ls.notes||{})[qid] || '';
    next(); await new Promise(r=>setTimeout(r,400));
    prev(); await new Promise(r=>setTimeout(r,400));
    out.afterNav = document.getElementById('noteBox').value;
    S.mode='note'; syncMode(); reb();
    await new Promise(r=>setTimeout(r,400));
    out.noteMode = {n: S.list.length, allHaveNote: S.list.every(q=>(S.notes[q.id]||'').trim())};
    S.mode='seq'; syncMode();
    delete S.notes[qid]; persist();
    const ls2 = JSON.parse(localStorage.getItem('ztq_local')||'{}');
    out.cleaned = !((ls2.notes||{})[qid]);
    reb();
    return out;
  })()`);

  R.errors = await ev(`window.__ERR || []`);

  const s = R.search, n = R.note, M = NOTE_MARK;
  const checks = [
    ['search box rendered', !s.noBox],
    ['search lagrange hits', s.lagrange && s.lagrange.n > 0],
    ['search crosses groups', s.lagrange && s.lagrange.groups > 1],
    ['search hits真含关键词', s.lagrange && s.lagrange.allMatch === true],
    ['search multi-word AND', s.multi && s.multi.n > 0 && s.multi.allAnd === true],
    ['search by qid', s.byId && s.byId.n === 1 && s.byId.first === '1942'],
    ['search no-match empty', s.none && s.none.n === 0],
    ['search cleared restores', s.cleared && s.cleared.n > 100],
    ['note textarea exists', n.hasBox === true],
    ['note saved in memory', (n.saved || '').includes(M)],
    ['note hint saved', (n.hint || '').includes(HINT_SAVED)],
    ['note in localStorage', (n.inLS || '').includes(M)],
    ['note survives navigation', (n.afterNav || '').includes(M)],
    ['note mode filters', n.noteMode && n.noteMode.n === 1 && n.noteMode.allHaveNote === true],
    ['note cleanup ok', n.cleaned === true],
    ['no JS errors', Array.isArray(R.errors) && R.errors.length === 0],
  ];
  console.log('\n=== Search + Notes CDP regression ===');
  for (const [k, ok] of checks) console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${k}`);
  const bad = checks.filter(c => !c[1]).length;
  console.log(`\n${bad ? bad + ' FAILED' : 'ALL ' + checks.length + ' PASSED'}`);
  console.log('\n--- detail ---');
  console.log(JSON.stringify(R, null, 1).slice(0, 2200));
  process.exitCode = bad ? 3 : 0;
}

main().catch(e => { console.error('SCRIPT ERROR:', e && e.message); process.exitCode = 9; })
  .finally(() => {
    if (cdp) cdp.close();
    if (ch) { try { ch.kill(); } catch (e) { } }
  });


process.on('unhandledRejection', (e) => {
  // 收尾阶段 socket 被销毁时，仍挂着的 waiter 会变成 unhandled rejection，
  // 那会把已置好的 exitCode=0 顶成非 0。测试已结束就忽略。
  if (typeof done !== 'undefined' && done) return;
  if (typeof pass !== 'undefined' && pass !== null) return;
  if (typeof bad !== 'undefined' && bad !== null) return;
  console.error('❌ 未处理的 Promise 拒绝:', e && (e.message || e));
  process.exitCode = 9;
});
