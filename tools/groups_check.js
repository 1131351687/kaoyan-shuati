// groups_check.js -- verify math-2/3 removal is reflected in the UI.
// Drives the real page over CDP (no --single-process, /json/new via PUT).
// Assertion names are ASCII; CJK literals use \uXXXX escapes.
const http = require('http');
const net = require('net');
const crypto = require('crypto');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const PORT = Number(process.env.CDP_PORT || 9362);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const CHROME = process.env.ZTQ_CHROME ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PROJ = path.resolve(__dirname, '..');

const UD = path.join(process.env.TEMP || '/tmp', 'udt_groups_' + Date.now());
fs.mkdirSync(UD, { recursive: true });

const SHUYI = '\u6570\u5b66\u4e00';   // 数学一
const SHUER = '\u6570\u5b66\u4e8c';   // 数学二
const SHUSAN = '\u6570\u5b66\u4e09';  // 数学三

let pass = 0, fail = 0;
const results = [];
function ok(name, cond, extra) {
  if (cond) { pass++; results.push('  PASS  ' + name + (extra ? '  ' + extra : '')); }
  else { fail++; results.push('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}

// ---- minimal WebSocket client (handshake + accumulated-frame parsing) ----
function wsConnect(url) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const key = crypto.randomBytes(16).toString('base64');
    const sock = net.connect(Number(u.port || 80), u.hostname);
    sock.setTimeout(10000);
    let buf = Buffer.alloc(0), ready = false;
    const waiters = new Map();
    let mid = 0;
    sock.on('connect', () => {
      sock.write(
        'GET ' + u.pathname + (u.search || '') + ' HTTP/1.1\r\n' +
        'Host: ' + u.hostname + ':' + (u.port || 80) + '\r\n' +
        'Upgrade: websocket\r\nConnection: Upgrade\r\n' +
        'Sec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n');
    });
    sock.on('data', (d) => {
      buf = Buffer.concat([buf, d]);
      if (!ready) {
        const i = buf.indexOf('\r\n\r\n');
        if (i < 0) return;
        ready = true;
        buf = buf.slice(i + 4);
      }
      while (buf.length >= 2) {
        const op = buf[0] & 0x0f;
        let len = buf[1] & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (buf.length < off + len) return;
        const payload = buf.slice(off, off + len);
        buf = buf.slice(off + len);
        if (op === 8) return;
        if (op !== 1) continue;
        let msg; try { msg = JSON.parse(payload.toString('utf8')); } catch (e) { continue; }
        const w = waiters.get(msg.id);
        if (w) { waiters.delete(msg.id); w(msg); }
      }
    });
    sock.on('error', reject);
    const send = (method, params) => {
      mid++;
      const id = mid;
      const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }), 'utf8');
      const mask = crypto.randomBytes(4);
      let hdr;
      if (data.length < 126) {
        hdr = Buffer.from([0x81, 0x80 | data.length]);
      } else if (data.length <= 65535) {
        hdr = Buffer.alloc(4); hdr[0] = 0x81; hdr[1] = 0xFE;
        hdr.writeUInt16BE(data.length, 2);
      } else {
        hdr = Buffer.alloc(10); hdr[0] = 0x81; hdr[1] = 0xFF;
        hdr.writeBigUInt64BE(BigInt(data.length), 2);
      }
      const masked = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) masked[i] = data[i] ^ mask[i & 3];
      return new Promise((res, rej) => {
        waiters.set(id, res);
        setTimeout(() => { if (waiters.has(id)) { waiters.delete(id); rej(new Error('timeout ' + method)); } }, 15000);
        sock.write(Buffer.concat([hdr, mask, masked]));
      });
    };
    resolve({ send, sock });
  });
}

function httpReq(method, p) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method },
      (r) => { let s = ''; r.on('data', c => s += c); r.on('end', () => resolve(s)); });
    req.setTimeout(8000, () => { req.destroy(new Error('http timeout')); });
    req.on('error', reject);
    req.end();
  });
}

const ch = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + UD, 'about:blank'
], { stdio: 'ignore' });

function waitPort() {
  return new Promise((resolve, reject) => {
    let n = 0;
    const t = setInterval(async () => {
      try { await httpReq('GET', '/json/version'); clearInterval(t); resolve(); }
      catch (e) { if (++n > 60) { clearInterval(t); reject(new Error('cdp not up')); } }
    }, 300);
  });
}

async function main() {
  await waitPort();
  const tab = JSON.parse(await httpReq('PUT', '/json/new?' + encodeURIComponent(PAGE)));
  const { send, sock } = await wsConnect(tab.webSocketDebuggerUrl);
  await new Promise(r => sock.on('connect', r)).catch(() => {});
  await new Promise(r => setTimeout(r, 500));
  await send('Runtime.enable');
  await send('Page.enable');
  await new Promise(r => setTimeout(r, 2800));

  const ev = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.text || 'eval error');
    }
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const errors = [];
  await send('Log.enable').catch(() => {});

  // ---- 1. dropdown contents ----
  const opts = await ev(`Array.from(document.querySelectorAll('#group option')).map(o=>({v:o.value,t:o.textContent.trim()}))`);
  const vals = opts.map(o => o.v);
  const labels = opts.map(o => o.t);

  ok('dropdown has no group 9', !vals.includes('9'), 'vals=' + JSON.stringify(vals.slice(0, 5)));
  ok('dropdown has no group 10', !vals.includes('10'));
  ok('dropdown still has group 8', vals.includes('8'));
  ok('group 8 label is shuyi', labels.some(t => t.includes(SHUYI)), 'label=' + (labels.find(t => t.includes(SHUYI)) || ''));
  ok('no shuer anywhere in dropdown', !labels.some(t => t.includes(SHUER)));
  ok('no shusan anywhere in dropdown', !labels.some(t => t.includes(SHUSAN)));
  ok('old papers still present', vals.some(v => v.startsWith('old')), 'old count=' + vals.filter(v => v.startsWith('old')).length);
  ok('mock papers still present', vals.some(v => v.startsWith('mock')), 'mock count=' + vals.filter(v => v.startsWith('mock')).length);

  // ---- 2. group 8 question count ----
  const n8 = await ev(`(S.data.questions.filter(q=>q.group==='8')||[]).length`);
  ok('group 8 has 408 questions', n8 === 408, 'n=' + n8);
  const nAll = await ev(`S.data.questions.length`);
  ok('index has 1007 questions', nAll === 1007, 'n=' + nAll);
  const anyDeleted = await ev(`S.data.questions.some(q=>q.group==='9'||q.group==='10')`);
  ok('no question from group 9/10 in index', anyDeleted === false);

  // ---- 3. papers array ----
  const badPapers = await ev(`(S.data.papers||[]).filter(p=>p.group==='9'||p.group==='10').length`);
  ok('no paper for group 9/10', badPapers === 0, 'bad=' + badPapers);
  ok('18 papers remain', (await ev(`(S.data.papers||[]).length`)) === 18);

  // ---- 4. switching to group 8 still renders ----
  await ev(`(()=>{const s=document.querySelector('#group'); s.value='8'; s.onchange&&s.onchange(); })()`);
  await new Promise(r => setTimeout(r, 900));
  const firstQid = await ev(`S.list && S.list[0] ? String(S.list[0].id) : null`);
  ok('group 8 first qid is 1942', firstQid === '1942', 'qid=' + firstQid);
  const listLen = await ev(`S.list ? S.list.length : 0`);
  ok('group 8 list length 408', listLen === 408, 'n=' + listLen);

  // ---- 5. grid tab ids are group-8 only ----
  await ev(`(()=>{const t=Array.from(document.querySelectorAll('.tab')).find(x=>x.dataset.tab==='grid'); t&&t.click();})()`);
  await new Promise(r => setTimeout(r, 700));
  const gq = await ev(`Array.from(document.querySelectorAll('#tab-grid .cell')).slice(0,4).map(c=>c.dataset.qid)`);
  ok('grid starts with group-8 qids', Array.isArray(gq) && gq[0] === '1942', 'qids=' + JSON.stringify(gq));

  // ---- 6. no page errors ----
  const errs = await ev(`window.__errs || []`);
  ok('no page errors', !errs || errs.length === 0, JSON.stringify(errs || []));

  // screenshot
  await ev(`(()=>{const t=Array.from(document.querySelectorAll('.tab')).find(x=>x.dataset.tab==='q'); t&&t.click();})()`);
  await new Promise(r => setTimeout(r, 500));
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result && shot.result.data) {
    fs.writeFileSync(path.join(PROJ, 'docs', 'groups_only_math1.png'),
      Buffer.from(shot.result.data, 'base64'));
    ok('screenshot written', true);
  } else { ok('screenshot written', false); }

  sock.destroy();
  console.log('--- groups_check ---');
  console.log(results.join('\n'));
  console.log('--- ' + pass + '/' + (pass + fail) + ' passed ---');
  if (fail) process.exitCode = 2;
}

process.on('unhandledRejection', () => {});
main().catch(e => { console.log('ERROR ' + e.message); process.exitCode = 9; })
  .finally(() => { setTimeout(() => { try { ch.kill(); } catch (e) {} }, 300); });
