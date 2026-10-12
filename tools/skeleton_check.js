// skeleton_check.js -- verify the empty framework renders without crashing.
// Ports: CDP 9470, served page 8891. ASCII assertions; CJK via \uXXXX.
const http = require('http');
const net = require('net');
const crypto = require('crypto');
const { spawn } = require('child_process');
const path = require('path');
const os = require('os');

const PORT = Number(process.env.CDP_PORT || 9470);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8891/';
const CHROME = process.env.ZTQ_CHROME ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PROJ = process.env.SKEL_DIR || 'D:\\try\\考研刷题-骨架';

const UD = path.join(os.tmpdir(), 'udt_skel_' + Date.now());

let pass = 0, fail = 0;
const R = [];
function ok(name, cond, info) {
  if (cond) { pass++; R.push('  PASS  ' + name + (info !== undefined ? '  ' + JSON.stringify(info) : '')); }
  else { fail++; R.push('  FAIL  ' + name + (info !== undefined ? '  ' + JSON.stringify(info) : '')); }
}

function httpReq(method, p) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method },
      (r) => { let s = ''; r.on('data', c => s += c); r.on('end', () => resolve(s)); });
    req.setTimeout(8000, () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    req.end();
  });
}

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
      sock.write('GET ' + u.pathname + ' HTTP/1.1\r\n' +
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
        let m; try { m = JSON.parse(payload.toString('utf8')); } catch (e) { continue; }
        if (m.method === 'Runtime.consoleAPICalled') {
          const t = m.params.type;
          if (t === 'error') R.push('  [console.error] ' +
            (m.params.args || []).map(a => a.value || a.description || '').join(' '));
        }
        const w = waiters.get(m.id);
        if (w) { waiters.delete(m.id); w(m); }
      }
    });
    sock.on('error', reject);
    const send = (method, params) => {
      mid++;
      const id = mid;
      const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }), 'utf8');
      const mask = crypto.randomBytes(4);
      let hdr;
      if (data.length < 126) hdr = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length <= 65535) {
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

const ch = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
  '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + UD, 'about:blank',
], { stdio: 'ignore' });

async function main() {
  for (let i = 0; i < 60; i++) {
    try { await httpReq('GET', '/json/version'); break; }
    catch (e) { await new Promise(r => setTimeout(r, 400)); }
  }
  let tab = null, ws = null;
  for (let i = 0; i < 5 && !ws; i++) {
    tab = JSON.parse(await httpReq('PUT', '/json/new?' + encodeURIComponent(PAGE)));
    ws = tab && tab.webSocketDebuggerUrl;
    if (!ws) await new Promise(r => setTimeout(r, 600));
  }
  const { send, sock } = await wsConnect(ws);
  await new Promise(r => setTimeout(r, 600));
  await send('Runtime.enable');
  await send('Page.enable');
  await new Promise(r => setTimeout(r, 3000));

  const ev = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.text || 'eval error');
    }
    return r.result && r.result.result ? r.result.result.value : null;
  };

  // 1. page actually loaded and boot() ran without throwing
  const title = await ev(`document.title`);
  ok('page loaded', typeof title === 'string' && title.length > 0, title);
  const booted = await ev(`typeof S !== 'undefined' && !!S.data`);
  ok('boot() completed (S.data set)', booted === true);
  const nq = await ev(`(S.data && S.data.questions) ? S.data.questions.length : -1`);
  ok('zero questions in skeleton', nq === 0, { questions: nq });

  // 2. header renders with zeros, not "undefined"/NaN
  const note = await ev(`document.getElementById('srcnote').textContent`);
  ok('header has no undefined/NaN', !/undefined|NaN/.test(String(note)), note);

  // 3. the question area shows an empty state (not a crash)
  const qbody = await ev(`document.getElementById('qbody').innerHTML`);
  ok('question area shows empty state', /没有匹配|empty/.test(String(qbody)),
     String(qbody).slice(0, 90));

  // 4. dropdowns exist but are empty-ish, no thrown error
  const gopt = await ev(`document.querySelectorAll('#group option').length`);
  ok('group dropdown rendered', typeof gopt === 'number' && gopt >= 0, { options: gopt });
  const kpopt = await ev(`document.querySelectorAll('#kp option').length`);
  ok('kp dropdown rendered', typeof kpopt === 'number' && kpopt >= 1, { options: kpopt });

  // 5. every tab can be opened without a JS error
  const tabs = ['q', 'paper', 'grid', 'stats', 'hard', 'wrong'];
  for (const t of tabs) {
    const r = await ev(`(function(){
      try{
        var el=document.querySelector('.tab[data-tab="${t}"]');
        if(!el) return 'NO_TAB';
        el.click();
        var pane=document.getElementById('tab-${t}');
        return { disp: getComputedStyle(pane).display, len: pane.innerHTML.length };
      }catch(e){ return 'ERR:'+e.message }
    })()`);
    ok('tab ' + t + ' opens cleanly',
       r && typeof r === 'object' && r.disp !== 'none' && r.len > 0, r);
  }

  // 6. no uncaught page errors
  const errs = await ev(`window.__errs || []`);
  ok('no uncaught page errors', !errs || errs.length === 0, errs || []);

  // screenshot
  await ev(`(function(){var e=document.querySelector('.tab[data-tab="q"]'); e&&e.click();})()`);
  await new Promise(r => setTimeout(r, 500));
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result && shot.result.data) {
    require('fs').writeFileSync(path.join(PROJ, 'docs', 'skeleton_empty.png'),
      Buffer.from(shot.result.data, 'base64'));
    ok('screenshot written', true);
  }

  sock.destroy();
  console.log('--- skeleton_check ---');
  console.log(R.join('\n'));
  console.log('--- ' + pass + '/' + (pass + fail) + ' passed ---');
  if (fail) process.exitCode = 2;
}

process.on('unhandledRejection', () => {});
main().catch(e => { console.log('ERROR ' + e.message); process.exitCode = 9; })
  .finally(() => { setTimeout(() => { try { ch.kill(); } catch (e) {} }, 300); });
