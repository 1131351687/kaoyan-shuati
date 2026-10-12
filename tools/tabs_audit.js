/* tabs_audit.js - check every tab renders under the v2 skin.

   vis_audit.js only looks at the question page. This walks all six tabs via
   real clicks and measures each one for layout sanity: no zero-height panels,
   no elements overflowing their card, no console errors per tab.

   Ports: CDP 9356, page 8899. ASCII labels; CJK via \uXXXX. */
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
const PORT = Number(process.env.CDP_PORT || 9356);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = path.join(PROJ, 'docs', '_tabs_report.json');

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

const TABS = ['q', 'paper', 'grid', 'stats', 'hard', 'wrong'];

async function main() {
  const ud = path.join(os.tmpdir(), 'udt_tabs_' + Date.now());
  const ch = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + ud,
    '--window-size=1600,1100', 'about:blank'], { stdio: 'ignore' });
  let bound = false;
  for (let i = 0; i < 40; i++) { await sleep(300); try { await put('/json/version', 'GET'); bound = true; break; } catch (e) {} }
  if (!bound) throw new Error('CDP never bound');

  const tab = JSON.parse(await put('/json/new?' + encodeURIComponent(PAGE), 'PUT'));
  const ws = await connect(tab.webSocketDebuggerUrl);
  await ws.send('Runtime.enable');
  await sleep(3200);

  const ev = async e => {
    const r = await ws.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 220) };
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const R = [];
  const check = (name, ok, detail) => {
    R.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : ''));
  };

  // baseline: how many tabs are declared
  const declared = await ev('[...document.querySelectorAll(".tab")].map(t=>t.dataset.tab)');
  check('six tabs declared', Array.isArray(declared) && declared.length === 6, declared);

  for (const t of TABS) {
    // real click, not a direct function call
    await ev(`(function(){
      var b=[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='${t}');
      if(b) b.click();
      return null;
    })()`);
    await sleep(600);
    const m = await ev(`(function(){
      var pane=document.getElementById('tab-${t}');
      if(!pane) return { missing:true };
      var cs=getComputedStyle(pane);
      var pr=pane.getBoundingClientRect();
      var o={ hiddenClass: pane.classList.contains('hidden'), display: cs.display,
              paneH: Math.round(pr.height), paneW: Math.round(pr.width),
              activeTab: (document.querySelector('.tab.on')||{}).dataset ?
                         document.querySelector('.tab.on').dataset.tab : null,
              children: pane.children.length,
              textLen: pane.textContent.trim().length,
              cards: pane.querySelectorAll('.card').length,
              overflowX: pane.scrollWidth > pane.clientWidth + 2,
              zeroH: [] };
      // any direct child with zero height where content exists?
      [...pane.children].forEach(function(c){
        var b=c.getBoundingClientRect();
        if(b.height < 2 && c.textContent.trim().length > 0) o.zeroH.push(c.className||c.tagName);
      });
      return o;
    })()`);
    if (m && m.missing) { check('tab ' + t + ' exists', false, 'no #tab-' + t); continue; }
    check('tab ' + t + ' visible', m.display !== 'none' && !m.hiddenClass, { display: m.display });
    check('tab ' + t + ' has height', m.paneH > 60, m.paneH);
    check('tab ' + t + ' active marker', m.activeTab === t, m.activeTab);
    check('tab ' + t + ' no zero-height blocks', m.zeroH.length === 0, m.zeroH);
    check('tab ' + t + ' no h-overflow', m.overflowX === false, m.overflowX);
  }

  // switching back and forth must not break
  await ev(`[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='q').click()`);
  await sleep(500);
  const back = await ev(`(function(){
    var o={};
    o.on=document.querySelector('.tab.on').dataset.tab;
    o.qVisible=getComputedStyle(document.getElementById('tab-q')).display!=='none';
    o.stem=!!document.querySelector('.stembox');
    o.hidden=['paper','grid','stats','hard','wrong'].every(function(t){
      return document.getElementById('tab-'+t).classList.contains('hidden');});
    return o;
  })()`);
  check('back to q tab', back.on === 'q' && back.qVisible, back);
  check('question still renders after tab cycling', back.stem === true);
  check('other tabs hidden again', back.hidden === true);

  // no errors across the whole walk
  const errs = await ev('(window.__errs||[]).slice(0,10)');
  check('no page JS errors across all tabs', Array.isArray(errs) && errs.length === 0, errs);

  // screenshots of the three visually distinct tabs
  for (const t of ['paper', 'wrong', 'stats']) {
    await ev(`[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='${t}').click()`);
    await sleep(600);
    const s = await ws.send('Page.captureScreenshot', { format: 'png' });
    const d = s.result && s.result.data;
    if (d) {
      const p = path.join(PROJ, 'docs', 'tab_' + t + '.png');
      fs.writeFileSync(p, Buffer.from(d, 'base64'));
      console.log('  wrote ' + p + ' (' + Math.round(fs.statSync(p).size / 1024) + ' KB)');
    }
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const pass = R.filter(r => r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({ total: R.length, passed: pass, failed: R.length - pass, results: R }, null, 2), 'utf8');
  console.log('\n  ' + pass + '/' + R.length + ' passed  ->  ' + OUT);
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
  // teardown noise: waiters still pending when the socket is destroyed
  console.error('unhandledRejection (ignored during teardown):', e && (e.message || e));
});
