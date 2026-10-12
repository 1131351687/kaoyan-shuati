/* panels_check.js - every panel must react to the 范围 / 年份 dropdowns.

   Companion to grid_check.js. Covers:
     - 答题卡 (grid)   : group + year
     - 统计   (stats)  : group
     - 难题榜 (hard)   : intentionally global (全站最难的 60 题), must NOT change
     - 错题本 (wrong)  : independent of group (cross-group pool)

   Also guards the `years()` regression: re-filling <option> must not reset
   the year selection (that was the bug that made 年份 filtering a no-op).

   Ports: CDP 9360, page 8899. ASCII labels; CJK via \uXXXX. */
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
const PORT = Number(process.env.CDP_PORT || 9360);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = path.join(PROJ, 'docs', '_panels_report.json');

let ch = null, done = false;

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
  const ud = path.join(os.tmpdir(), 'udt_panels_' + Date.now());
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

  const ev = async e => {
    const r = await ws.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 300) };
    return r.result && r.result.result ? r.result.result.value : null;
  };
  const R = [];
  const check = (name, ok, detail) => {
    R.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : ''));
  };
  const openTab = async k => {
    await ev(`document.querySelector('.tab[data-tab="${k}"]').click()`);
    await sleep(550);
  };
  const setGroup = async g => {
    await ev(`(function(){ var s=document.getElementById('group'); s.value=${JSON.stringify(g)};
        s.dispatchEvent(new Event('change',{bubbles:true})); return s.value; })()`);
    await sleep(650);
  };
  const setYear = async y => {
    await ev(`(function(){ var s=document.getElementById('year'); s.value=${JSON.stringify(y)};
        s.dispatchEvent(new Event('change',{bubbles:true})); return s.value; })()`);
    await sleep(650);
  };

  /* ---------- stats panel follows 范围 ---------- */
  await setGroup('8');
  await openTab('stats');
  const s8 = await ev(`(function(){
    var rows=[...document.querySelectorAll('#statsbox tbody tr')];
    var head=document.querySelector('#statsbox .small');
    return { rows:rows.length,
             first:rows.slice(0,3).map(r=>r.cells[0].textContent.trim()),
             total:rows.reduce((a,r)=>a+parseInt(r.cells[1].textContent)||0,0),
             head:head?head.textContent.replace(/\\s+/g,' ').trim():'' };
  })()`);
  check('stats renders for 数学一', s8 && s8.rows > 0 && s8.total === 408, s8);

  /* 题库当前只保留数学一（group 9/10 已删）。下面几处跨分组断言在
     多分组存在时才跑，否则退化为「下拉里确实没有它们」。 */
  const avail = await ev(`[...document.getElementById('group').options].map(o=>o.value)`);
  const hasMulti = avail.includes('9') && avail.includes('10');

  if (hasMulti) {
    await setGroup('9');
    const s9 = await ev(`(function(){
      var rows=[...document.querySelectorAll('#statsbox tbody tr')];
      return { rows:rows.length, total:rows.reduce((a,r)=>a+parseInt(r.cells[1].textContent)||0,0),
               first:rows.slice(0,3).map(r=>r.cells[0].textContent.trim()) };
    })()`);
    check('stats follows 范围 -> 数学二 (total 408, 内容改变)',
      s9 && s9.total === 408 && JSON.stringify(s9.first) !== JSON.stringify(s8.first), s9);
  } else {
    check('single-group build: dropdown omits 数学二', !avail.includes('9'), avail.slice(0, 6));
  }

  /* ---------- 难题榜 is intentionally global ---------- */
  await openTab('hard');
  const h8 = await ev(`(function(){
    var rows=[...document.querySelectorAll('#hardbox tbody tr')];
    return { rows:rows.length, first:rows.slice(0,3).map(r=>r.cells[0].textContent.trim()) };
  })()`);
  check('hard list renders (global top-60 by 熟练率)', h8 && h8.rows > 0 && h8.rows <= 60, h8);

  /* 切到最远的另一个范围，难题榜必须纹丝不动 */
  await setGroup(hasMulti ? '10' : 'old1987');
  const h10 = await ev(`(function(){
    var rows=[...document.querySelectorAll('#hardbox tbody tr')];
    return { rows:rows.length, first:rows.slice(0,3).map(r=>r.cells[0].textContent.trim()) };
  })()`);
  check('hard list is 全站 (unchanged by 范围 switch)',
    h10 && JSON.stringify(h10.first) === JSON.stringify(h8.first),
    { before: h8.first, after: h10.first });

  /* ---------- year filter persists (the years() regression) ---------- */
  await setGroup('8');
  await openTab('grid');
  await setYear('2026');
  const yKept = await ev(`document.getElementById('year').value`);
  check('year selection survives years() re-fill', yKept === '2026', { value: yKept });
  const gY = await ev(`document.querySelectorAll('#gridbox .cell').length`);
  check('grid narrowed by year', gY === 22, { cells: gY });

  /* question panel must honour the same filter */
  const qn = await ev(`S.list.length`);
  check('question list honours group+year', qn === 22, { n: qn });

  await setYear('');
  const gAll = await ev(`document.querySelectorAll('#gridbox .cell').length`);
  check('clearing year restores full range', gAll === 408, { cells: gAll });

  /* switching group with a year selected must reset year if absent */
  await setGroup(hasMulti ? '9' : 'old1987');
  const yAfterG = await ev(`document.getElementById('year').value`);
  check('year resets when the new range lacks it',
    yAfterG === '' || yAfterG === '2026', { group: hasMulti ? '9' : 'old1987', value: yAfterG });

  await openTab('q');
  const errs = await ev('(window.__errs||[]).slice(0,10)');
  check('no page JS errors', Array.isArray(errs) && errs.length === 0, errs);

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const pass = R.filter(r => r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({
    total: R.length, passed: pass, failed: R.length - pass, results: R,
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
process.on('unhandledRejection', (e) => { if (!done) console.error('unhandledRejection:', e && (e.message || e)); });
