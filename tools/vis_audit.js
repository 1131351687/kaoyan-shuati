/* vis_audit.js - measure real layout geometry & contrast, not just CSS parsing.

   Closes the gap in skin_check.js: that one asserts declarations were applied.
   This one asserts the rendered RESULT is actually good -- no clipped text,
   no overlapping header controls, adequate contrast on the paper panel, and
   the three question states (unanswered / answered / graded) all render.

   Ports: CDP 9352, page 8899. ASCII labels only; CJK via \uXXXX escapes. */
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
const PORT = Number(process.env.CDP_PORT || 9352);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = path.join(PROJ, 'docs', '_vis_report.json');

function put(p, m) {
  return new Promise((res, rej) => {
    const q = http.request({ host: '127.0.0.1', port: PORT, path: p, method: m || 'PUT' },
      (r) => { let b = ''; r.on('data', d => b += d); r.on('end', () =>
        r.statusCode >= 400 ? rej(new Error('HTTP ' + r.statusCode + ' ' + b.slice(0, 100))) : res(b)); });
    q.setTimeout(8000, () => { try { q.destroy(); } catch (e) {} rej(new Error('timeout')); });
    q.on('error', rej); q.end();
  });
}
function connect(url) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const key = crypto.randomBytes(16).toString('base64');
    const sock = net.connect(Number(u.port), u.hostname || '127.0.0.1');
    let buf = Buffer.alloc(0), open = false, mid = 0;
    const waiters = new Map();
    sock.on('connect', () => sock.write(
      'GET ' + (u.pathname || '/') + ' HTTP/1.1\r\nHost: ' + u.hostname + ':' + u.port +
      '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key +
      '\r\nSec-WebSocket-Version: 13\r\n\r\n'));
    sock.on('error', reject);
    sock.on('data', d => {
      buf = Buffer.concat([buf, d]);
      if (!open) {
        const i = buf.indexOf('\r\n\r\n');
        if (i < 0) return;
        buf = buf.slice(i + 4); open = true; resolve({ send, close });
      }
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
        try { const m = JSON.parse(s); if (m.id && waiters.has(m.id)) { const w = waiters.get(m.id); waiters.delete(m.id); w(m); } } catch (e) {}
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
      return new Promise(r => waiters.set(id2, r));
    }
    function close() { try { sock.destroy(); } catch (e) {} }
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const ud = path.join(os.tmpdir(), 'udt_vis_' + Date.now());
  const ch = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + ud,
    '--window-size=1600,1100', 'about:blank'], { stdio: 'ignore' });
  let bound = false;
  for (let i = 0; i < 40; i++) { await sleep(300); try { await put('/json/version', 'GET'); bound = true; break; } catch (e) {} }
  if (!bound) throw new Error('CDP never bound');

  const tab = JSON.parse(await put('/json/new?' + encodeURIComponent(PAGE), 'PUT'));
  const ws = await connect(tab.webSocketDebuggerUrl);
  await ws.send('Runtime.enable');
  await ws.send('Page.enable');
  await sleep(3000);

  const ev = async (e) => {
    const r = await ws.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return { __err: JSON.stringify(r.result.exceptionDetails).slice(0, 260) };
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const R = [];
  const check = (name, ok, detail) => {
    R.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name +
      (detail !== undefined ? '  ' + JSON.stringify(detail) : ''));
  };

  /* ---- A. header geometry: controls must not overlap or wrap badly ---- */
  const hdr = await ev(`(function(){
    var o={}, boxes=[], h=document.querySelector('header'), r=h.getBoundingClientRect();
    o.hH = Math.round(r.height);
    h.querySelectorAll('select,input,button').forEach(function(el){
      var b=el.getBoundingClientRect();
      boxes.push({id:el.id||el.tagName, x:Math.round(b.left), y:Math.round(b.top),
                  w:Math.round(b.width), h:Math.round(b.height),
                  txt:(el.textContent||'').trim().slice(0,6)});
    });
    o.boxes=boxes;
    // detect pairwise overlap
    var ov=[];
    for(var i=0;i<boxes.length;i++)for(var j=i+1;j<boxes.length;j++){
      var a=boxes[i],b=boxes[j];
      if(a.x < b.x+b.w && b.x < a.x+a.w && a.y < b.y+b.h && b.y < a.y+a.h)
        ov.push(a.id+'/'+b.id);
    }
    o.overlaps=ov;
    // count real row BANDS: cluster control tops that are within 12px of each other
    var ys=[...new Set(boxes.map(b=>b.y))].sort((p,q)=>p-q);
    var bands=[]; ys.forEach(function(y){
      if(!bands.length || y-bands[bands.length-1] > 12) bands.push(y);
    });
    o.rows=bands.length;
    // right edge should be used: no group stranded far from the right margin
    var hdrRight = r.right, maxX = Math.max.apply(null, boxes.map(b=>b.x+b.w));
    o.rightGap = Math.round(hdrRight - maxX);
    // groups on the second band, to prove they share one row
    o.band2 = bands.length>1 ? boxes.filter(b=>b.y>=bands[1] && b.y<bands[1]+12).map(b=>b.id) : [];
    return o;
  })()`);
  check('header overlaps none', (hdr.overlaps || []).length === 0, hdr.overlaps);
  check('header wraps to 2 control rows', hdr.rows === 2, { rows: hdr.rows, h: hdr.hH });
  check('header uses right edge (<60px gap)', hdr.rightGap < 60, { gap: hdr.rightGap, band2: hdr.band2 });
  check('header height compact (<110px)', hdr.hH < 110, hdr.hH);

  /* ---- B. contrast: paper panel text vs background (WCAG AA >= 4.5) ---- */
  function lum(rgb) {
    const c = rgb.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  const contrast = await ev(`(function(){
    function p(s){ var m=s.match(/\\d+(?:\\.\\d+)?/g); return m?m.slice(0,3).map(Number):null; }
    var o={};
    var stem=document.querySelector('.stembox');
    if(stem){ var sc=getComputedStyle(stem); o.paperBg=p(sc.backgroundColor); o.paperFg=p(sc.color); }
    o.bodyBg=p(getComputedStyle(document.body).backgroundColor);
    o.bodyFg=p(getComputedStyle(document.body).color);
    var mu=document.querySelector('.muted');
    if(mu) o.mutedFg=p(getComputedStyle(mu).color);
    o.theme=document.documentElement.getAttribute('data-theme');
    return o;
  })()`);
  if (contrast.paperBg) {
    const L1 = lum(contrast.paperFg), L2 = lum(contrast.paperBg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    check('stembox contrast >= 7 (paper)', ratio >= 7, {
      ratio: Math.round(ratio * 100) / 100, fg: contrast.paperFg, bg: contrast.paperBg });
  } else check('stembox contrast >= 7 (paper)', false, 'no stembox');
  if (contrast.bodyBg) {
    const L1 = lum(contrast.bodyFg), L2 = lum(contrast.bodyBg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    check('body contrast >= 7', ratio >= 7, { ratio: Math.round(ratio * 100) / 100, theme: contrast.theme });
  }
  if (contrast.mutedFg && contrast.bodyBg) {
    const L1 = lum(contrast.mutedFg), L2 = lum(contrast.bodyBg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    check('muted text contrast >= 4.5', ratio >= 4.5, Math.round(ratio * 100) / 100);
  }

  /* ---- C. no horizontal overflow / clipped text ---- */
  const ovf = await ev(`(function(){
    var o={docW:document.documentElement.clientWidth,
           scrollW:document.documentElement.scrollWidth, clipped:[]};
    document.querySelectorAll('.stembox,.ansval,button,.tab,h1').forEach(function(el){
      if(el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== 'auto'
         && getComputedStyle(el).overflowX !== 'auto')
        o.clipped.push((el.className||el.tagName)+':'+el.scrollWidth+'>'+el.clientWidth);
    });
    return o;
  })()`);
  check('no page horizontal scroll', ovf.scrollW <= ovf.docW + 1, { doc: ovf.docW, scroll: ovf.scrollW });
  check('no clipped text blocks', ovf.clipped.length === 0, ovf.clipped.slice(0, 5));

  /* ---- D. question states render: unanswered, then graded ---- */
  const st1 = await ev(`(function(){
    var o={};
    o.stembox = !!document.querySelector('.stembox');
    o.ansBtns = document.querySelectorAll('.ansbtn').length;
    o.hasTabs = document.querySelectorAll('.tab').length;
    o.tabLabels = [...document.querySelectorAll('.tab')].map(t=>t.textContent.trim());
    o.katex = document.querySelectorAll('.stembox .katex').length;
    o.texFallback = document.querySelectorAll('.stembox code.tex').length;
    var stem=document.querySelector('.stembox');
    o.stemH = stem?Math.round(stem.getBoundingClientRect().height):0;
    o.stemText = stem?stem.textContent.trim().slice(0,40):null;
    return o;
  })()`);
  check('question renders with stem', st1.stembox === true, st1.stemText);
  check('KaTeX rendered in stem', st1.katex > 0, { katex: st1.katex, fallback: st1.texFallback });
  check('six tabs present', st1.hasTabs === 6, st1.tabLabels);
  check('answer buttons present', st1.ansBtns === 4, st1.ansBtns);
  check('stembox has real height', st1.stemH > 40, st1.stemH);

  // click the first answer button -> graded state.
  // JS now updates local state before the /api/record round-trip, so the
  // verdict must appear within a couple of frames, not after a network wait.
  const t0 = Date.now();
  await ev(`(function(){ var b=document.querySelector('.ansbtn'); if(!b) return null; b.click(); return null; })()`);
  let st2 = null;
  for (let i = 0; i < 40; i++) {
    await sleep(50);
    st2 = await ev(`(function(){
      var o={};
      o.right = document.querySelectorAll('.ansbtn.right').length;
      o.wrong = document.querySelectorAll('.ansbtn.wrong').length;
      o.verdict = (document.querySelector('#qbody').textContent.match(/(\\u2705 \\u6b63\\u786e|\\u274c \\u9519\\u8bef)/)||[])[0]||null;
      o.paperStill = !!document.querySelector('.stembox');
      o.ansval = document.querySelectorAll('.ansval').length;
      return o;
    })()`);
    if (st2 && (st2.right + st2.wrong) === 1 && st2.verdict) break;
  }
  const ms = Date.now() - t0;
  check('graded: one button marked right or wrong',
    st2.right + st2.wrong === 1, { right: st2.right, wrong: st2.wrong });
  check('verdict line shown after grading', !!st2.verdict, { verdict: st2.verdict, ms });
  check('grading is instant (no network wait)', ms < 1500, ms + 'ms');
  check('stembox survives grading', st2.paperStill === true);
  check('no stray ansval on a choice question', st2.ansval === 0, st2.ansval);

  /* ---- E. collect page errors ---- */
  const errs = await ev('(window.__errs||[]).slice(0,10)');
  check('no page JS errors', Array.isArray(errs) && errs.length === 0, errs);

  /* ---- F. screenshots: question view + wrong notebook + paper gen ---- */
  const shots = [];
  for (const t of ['dark', 'light']) {
    await ev('document.documentElement.setAttribute("data-theme","' + t + '")');
    await sleep(350);
    const s = await ws.send('Page.captureScreenshot', { format: 'png' });
    const d = s.result && s.result.data;
    if (d) {
      const p = path.join(PROJ, 'docs', 'vis_q_' + t + '.png');
      fs.writeFileSync(p, Buffer.from(d, 'base64'));
      shots.push(path.basename(p) + ' ' + Math.round(fs.statSync(p).size / 1024) + 'KB');
      console.log('  wrote ' + p);
    }
  }
  check('screenshots written', shots.length === 2, shots);

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const pass = R.filter(r => r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({ total: R.length, passed: pass, failed: R.length - pass, results: R },
    null, 2), 'utf8');
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
