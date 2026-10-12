/* grid_check.js - answer-sheet tab must follow the group / year dropdowns.

   Bug (reported): switching 范围 (数学一/二/三) while the 答题卡 tab is open
   leaves the grid unchanged, because drawGrid() is only invoked from the tab
   click handler -- the #group / #year onchange path calls reb(), which
   re-renders the question panel but never the grid.

   Ports: CDP 9358, page 8899. ASCII labels; CJK via \uXXXX. */
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
const PORT = Number(process.env.CDP_PORT || 9358);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = path.join(PROJ, 'docs', '_grid_report.json');

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
  const ud = path.join(os.tmpdir(), 'udt_grid_' + Date.now());
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
  const setGroup = g => ev(`(function(){ var s=document.getElementById('group'); s.value=${JSON.stringify(g)};
      s.dispatchEvent(new Event('change',{bubbles:true})); return s.value; })()`);
  const gridInfo = () => ev(`(function(){
    var cells=[...document.querySelectorAll('#gridbox .cell')];
    var head=document.querySelector('#gridbox .small');
    return { n:cells.length,
             head: head?head.textContent.replace(/\\s+/g,' ').trim():'',
             // 关键：三组题目数量与序号完全一样（都是 1..408），
             // 只有 data-qid 能区分，所以必须比对它。
             firstQids: cells.slice(0,5).map(c=>c.dataset.qid),
             lastQid: cells.length?cells[cells.length-1].dataset.qid:null,
             titles: cells.slice(0,3).map(c=>c.title) };
  })()`);

  /* ---- 1. open the 答题卡 tab, record baseline ---- */
  await ev(`document.querySelector('.tab[data-tab="grid"]').click()`);
  await sleep(500);
  const g8 = await gridInfo();
  check('grid renders on 数学一', g8 && g8.n === 408, g8);

  /* ---- 2. THE BUG: switch group while sitting on the grid tab ----
     三组题量相同、序号也相同（都是 1..408），只有 data-qid 不同，
     所以必须按 firstQids / lastQid 判等。

     注：题库当前只保留数学一（group 9/10 已按用户要求删除）。
     若这些分组还在，就验证切换会改变 qid；若不在，则验证下拉里
     确实没有它们 —— 两种情况都算通过，避免断言硬绑定分组存在。 */
  const avail = await ev(`[...document.getElementById('group').options].map(o=>o.value)`);
  const hasMulti = avail.includes('9') && avail.includes('10');

  if (hasMulti) {
    await setGroup('9');
    await sleep(600);
    const g9 = await gridInfo();
    check('grid follows 范围 -> 数学二 (qid 变了)',
      g9 && g9.n === 408 && JSON.stringify(g9.firstQids) !== JSON.stringify(g8.firstQids), g9);

    await setGroup('10');
    await sleep(600);
    const g10 = await gridInfo();
    check('grid follows 范围 -> 数学三 (qid 变了)',
      g10 && g10.n === 408 && JSON.stringify(g10.firstQids) !== JSON.stringify(g9.firstQids), g10);
  } else {
    check('single-group build: dropdown omits 数学二', !avail.includes('9'), avail.slice(0, 6));
    check('single-group build: dropdown omits 数学三', !avail.includes('10'));
  }

  /* ---- 3. year filter must narrow the grid too ---- */
  await setGroup('8');
  await sleep(600);
  const years = await ev(`[...document.getElementById('year').options].map(o=>o.value).filter(Boolean).slice(0,4)`);
  check('year dropdown populated', Array.isArray(years) && years.length > 0, years);
  if (years && years.length) {
    const yv = years[0];
    await ev(`(function(){ var s=document.getElementById('year'); s.value=${JSON.stringify(yv)};
        s.dispatchEvent(new Event('change',{bubbles:true})); return s.value; })()`);
    await sleep(600);
    const gy = await gridInfo();
    check('grid follows 年份 filter (narrows below 408)', gy && gy.n > 0 && gy.n < 408, { year: yv, n: gy && gy.n });
    // reset year
    await ev(`(function(){ var s=document.getElementById('year'); s.value='';
        s.dispatchEvent(new Event('change',{bubbles:true})); return null; })()`);
    await sleep(500);
  }

  /* ---- 4. grid must reload correctly when re-entering the tab ---- */
  await ev(`document.querySelector('.tab[data-tab="q"]').click()`);
  await sleep(300);
  await ev(`document.querySelector('.tab[data-tab="grid"]').click()`);
  await sleep(500);
  const gBack = await gridInfo();
  check('grid correct after leaving and re-entering tab', gBack && gBack.n === 408, gBack);

  /* ---- 5. clicking a cell still jumps to that question ---- */
  const clicked = await ev(`(function(){
    var c=document.querySelectorAll('#gridbox .cell')[5];
    if(!c) return null;
    var qid=c.dataset.qid; c.click();
    return { qid:qid, cur:S.list[S.i]&&S.list[S.i].id, tab:document.querySelector('.tab.on').dataset.tab };
  })()`);
  check('cell click jumps to that question',
    clicked && clicked.cur === clicked.qid && clicked.tab === 'q', clicked);

  /* ---- 6. no JS errors ---- */
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
