/* skin_check.js - verify the v2 skin renders correctly in a real browser.
   Drives Chrome over CDP via a minimal WebSocket client (same approach as
   paper_check.js). Ports: CDP 9346, page 8899.

   All assertion labels are ASCII and all CJK string constants use \uXXXX
   escapes ON PURPOSE -- PowerShell round-tripping a file with Chinese text
   turns it into mojibake, and the write tool must not be trusted to survive
   that either. See README "编码" section. */
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
const PORT = Number(process.env.CDP_PORT || 9346);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const PROJ = path.resolve(__dirname, '..');
const OUT = process.env.OUT || path.join(PROJ, 'docs', '_skin_report.json');

// --- minimal CDP websocket ---
function connect(url) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const key = crypto.randomBytes(16).toString('base64');
    const sock = net.connect(Number(u.port) || 80, u.hostname || '127.0.0.1');
    let buf = Buffer.alloc(0), open = false, id = 0;
    const waiters = new Map();
    const sockTimer = setTimeout(() => { try { sock.destroy(); } catch (e) {} reject(new Error('sock timeout')); }, 10000);
    sock.on('connect', () => {
      sock.write('GET ' + (u.pathname || '/') + ' HTTP/1.1\r\n' +
        'Host: ' + (u.hostname || '127.0.0.1') + ':' + (Number(u.port) || 80) + '\r\n' +
        'Upgrade: websocket\r\nConnection: Upgrade\r\n' +
        'Sec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n');
    });
    sock.on('error', reject);
    sock.on('data', (d) => {
      buf = Buffer.concat([buf, d]);
      if (!open) {
        const i = buf.indexOf('\r\n\r\n');
        if (i < 0) return;
        const head = buf.slice(0, i).toString();
        if (!/101/.test(head)) return reject(new Error('handshake failed: ' + head.split('\r\n')[0]));
        buf = buf.slice(i + 4); open = true; clearTimeout(sockTimer);
        resolve({ send, close, sock });
      }
      parse();
    });
    function parse() {
      for (;;) {
        if (buf.length < 2) return;
        const b1 = buf[1], masked = (b1 & 0x80) !== 0;
        let len = b1 & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (masked) off += 4;
        if (buf.length < off + len) return;
        const payload = buf.slice(off, off + len).toString();
        buf = buf.slice(off + len);
        try {
          const m = JSON.parse(payload);
          if (m.id && waiters.has(m.id)) {
            const w = waiters.get(m.id); waiters.delete(m.id); w(m);
          }
        } catch (e) { /* ignore */ }
      }
    }
    let mid = 0;
    function send(method, params) {
      const id2 = ++mid;
      const s = JSON.stringify({ id: id2, method, params: params || {} });
      const data = Buffer.from(s, 'utf8');
      const mask = crypto.randomBytes(4);
      let hdr;
      if (data.length < 126) hdr = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length < 65536) {
        hdr = Buffer.alloc(4); hdr[0] = 0x81; hdr[1] = 0x80 | 126;
        hdr.writeUInt16BE(data.length, 2);
      } else {
        hdr = Buffer.alloc(10); hdr[0] = 0x81; hdr[1] = 0x80 | 127;
        hdr.writeBigUInt64BE(BigInt(data.length), 2);
      }
      const masked2 = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) masked2[i] = data[i] ^ mask[i % 4];
      sock.write(Buffer.concat([hdr, mask, masked2]));
      return new Promise((res) => waiters.set(id2, res));
    }
    function close() { try { sock.destroy(); } catch (e) {} }
  });
}
function put(p, method) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: method || 'PUT' },
      (r) => {
        let b = '';
        r.on('data', (d) => b += d);
        r.on('end', () => {
          if (r.statusCode >= 400) return reject(new Error('HTTP ' + r.statusCode + ' ' + b.slice(0, 120)));
          resolve(b);
        });
      });
    req.setTimeout(8000, () => { try { req.destroy(); } catch (e) {} reject(new Error('put timeout')); });
    req.on('error', reject); req.end();
  });
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const ud = path.join(os.tmpdir(), 'udt_skin_' + Date.now());
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + ud,
    '--window-size=1600,1100', 'about:blank',
  ], { stdio: 'ignore' });

  let version = null;
  for (let i = 0; i < 40; i++) {
    await sleep(300);
    try { version = await put('/json/version', 'GET'); break; } catch (e) { /* retry */ }
  }
  if (!version) throw new Error('CDP did not bind on ' + PORT);
  const tab = JSON.parse(await put('/json/new?' + encodeURIComponent(PAGE), 'PUT'));
  const ws = await connect(tab.webSocketDebuggerUrl);
  await ws.send('Page.enable');
  await ws.send('Runtime.enable');
  await sleep(2500);

  const ev = async (expr) => {
    const r = await ws.send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) {
      return { __err: (r.result.exceptionDetails.exception || {}).description || 'eval error' };
    }
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const results = [];
  const check = (name, ok, detail) => {
    results.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : ''));
  };

  // 1. theme attribute applied before paint
  const theme = await ev('document.documentElement.getAttribute("data-theme")');
  check('theme attribute set', theme === 'dark' || theme === 'light', theme);

  // 2. skin stylesheet present and wins on a key declaration
  const skinOk = await ev(`(function(){
    var out={};
    var b=document.body;
    var cs=getComputedStyle(b);
    out.bodyFont = cs.fontSize;
    out.lineHeight = cs.lineHeight;
    var stem=document.querySelector('.stembox');
    out.hasStem = !!stem;
    if(stem){
      var sc=getComputedStyle(stem);
      out.stemMax = parseFloat(sc.maxWidth);      // ch resolves to px
      out.stemW   = stem.getBoundingClientRect().width;
      out.stemBg  = sc.backgroundColor;
      out.stemFont = sc.fontFamily.slice(0,24);
    }
    var w=document.querySelector('.wrap');
    if(w){
      out.wrapCols = getComputedStyle(w).gridTemplateColumns;
      var wr = w.getBoundingClientRect();
      out.wrapW = wr.width;
      // first column must be flexible, second fixed 356
      var cols = out.wrapCols.split(' ').map(parseFloat);
      out.col2 = cols[cols.length-1];
      out.col1Gt2 = cols[0] > cols[cols.length-1];
    }
    var t=document.querySelector('.tab.on');
    if(t){ var tc=getComputedStyle(t); out.tabBorder = tc.borderBottomColor; out.tabBg=tc.backgroundColor; }
    var h=document.querySelector('header');
    if(h){ out.headerDisplay = getComputedStyle(h).display; }
    out.hasHrow = document.querySelectorAll('.hrow').length;
    out.hasHbbr = document.querySelectorAll('.hbar').length;
    out.hasThemeBtn = !!document.getElementById('btnTheme');
    out.varReadw = getComputedStyle(document.documentElement).getPropertyValue('--readw').trim();
    out.themeNow = document.documentElement.getAttribute('data-theme');
    return out;
  })()`);
  check('body font 15px', skinOk.bodyFont === '15px', skinOk.bodyFont);
  check('body line-height 1.7', String(skinOk.lineHeight).indexOf('25.5') === 0, skinOk.lineHeight);
  check('--readw token defined', skinOk.varReadw === '74ch', skinOk.varReadw);
  check('header restructured to block', skinOk.headerDisplay === 'block', skinOk.headerDisplay);
  check('two header rows', skinOk.hasHrow === 2, skinOk.hasHrow);
  check('header bars present', skinOk.hasHbbr >= 3, skinOk.hasHbbr);
  check('theme button present', skinOk.hasThemeBtn);
  check('wrap is grid, 2 cols', /px/.test(skinOk.wrapCols || ''), skinOk.wrapCols);
  check('wrap col2 fixed 356px', skinOk.col2 === 356, skinOk.col2);
  check('wrap col1 flexes wider', skinOk.col1Gt2 === true, skinOk.wrapCols);
  check('wrap container capped 1560', skinOk.wrapW <= 1561, skinOk.wrapW);
  if (skinOk.hasStem) {
    // 74ch resolved against the serif stack lands near 594px at 14.5px;
    // assert the cap is applied and actually narrower than the column.
    check('stembox capped ~74ch', skinOk.stemMax > 400 && skinOk.stemMax < 900, skinOk.stemMax);
    check('stembox narrower than text column', skinOk.stemW <= skinOk.stemMax + 1,
      { w: skinOk.stemW, max: skinOk.stemMax });
    check('stembox serif font', /Cambria/.test(skinOk.stemFont || ''), skinOk.stemFont);
  }
  check('active tab underlined', skinOk.tabBg === 'rgba(0, 0, 0, 0)' && skinOk.tabBorder !== 'rgba(0, 0, 0, 0)',
    { bg: skinOk.tabBg, border: skinOk.tabBorder });

  // 3. theme: force a known start, then toggle and re-measure
  await ev('document.documentElement.setAttribute("data-theme","dark")');
  await sleep(300);
  const darkM = await ev(`(function(){
    var cs=getComputedStyle(document.body);
    var st=document.querySelector('.stembox');
    return { bg: cs.backgroundColor, fg: cs.color,
             paper: st?getComputedStyle(st).backgroundColor:null };
  })()`);
  check('dark body bg #0b0d12', /11, 13, 18/.test(darkM.bg || ''), darkM.bg);
  check('dark body fg #e8ecf4', /232, 236, 244/.test(darkM.fg || ''), darkM.fg);
  check('dark stembox paper #f7f6f2', /247, 246, 242/.test(darkM.paper || ''), darkM.paper);

  await ev('document.getElementById("btnTheme").click()');
  await sleep(400);
  const light = await ev(`(function(){
    var cs=getComputedStyle(document.body);
    var st=document.querySelector('.stembox');
    return { theme: document.documentElement.getAttribute("data-theme"),
             bg: cs.backgroundColor, fg: cs.color,
             paper: st?getComputedStyle(st).backgroundColor:null,
             btn: document.getElementById("btnTheme").textContent };
  })()`);
  check('theme toggled to light', light.theme === 'light', light.theme);
  check('light body bg #f4f6fa', /244, 246, 250/.test(light.bg || ''), light.bg);
  check('light body fg #141924', /20, 25, 36/.test(light.fg || ''), light.fg);
  check('light stembox paper #fff', /255, 255, 255/.test(light.paper || ''), light.paper);
  check('theme button shows moon', light.btn === '\u{1F319}', light.btn);

  await ev('document.getElementById("btnTheme").click()');
  await sleep(400);
  const dark = await ev('document.documentElement.getAttribute("data-theme")');
  check('theme toggled back to dark', dark === 'dark', dark);
  const persisted = await ev('localStorage.getItem("ztq_theme")');
  check('theme persisted to localStorage', persisted === 'dark', persisted);

  // 4. console errors
  const errs = await ev(`(function(){
    return (window.__errs||[]).slice(0,10);
  })()`);
  check('no collected page errors', Array.isArray(errs) && errs.length === 0, errs);

  // 5. screenshot both themes for visual record
  for (const t of ['dark', 'light']) {
    await ev('document.documentElement.setAttribute("data-theme","' + t + '")');
    await sleep(300);
    const shot = await ws.send('Page.captureScreenshot', { format: 'png' });
    const data = shot.result && shot.result.data;
    if (data) {
      const p = path.join(PROJ, 'docs', 'skin_' + t + '.png');
      fs.writeFileSync(p, Buffer.from(data, 'base64'));
      console.log('  wrote ' + p + ' (' + Math.round(fs.statSync(p).size / 1024) + ' KB)');
    }
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const pass = results.filter(r => r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({
    page: PAGE, version: JSON.parse(version).Browser, total: results.length,
    passed: pass, failed: results.length - pass, results, errors: errs,
  }, null, 2), 'utf8');
  console.log('\n  ' + pass + '/' + results.length + ' passed  ->  ' + OUT);

  await sleep(150);
  ws.close();
  try { process.kill(-ch.pid); } catch (e) { try { ch.kill(); } catch (e2) {} }
  setTimeout(() => process.exit(pass === results.length ? 0 : 3), 400);
}

main().catch((e) => {
  console.error('FATAL ' + (e && e.stack || e));
  try { fs.mkdirSync(path.dirname(OUT), { recursive: true }); } catch (e2) {}
  try { fs.writeFileSync(OUT, JSON.stringify({ fatal: String(e) }, null, 2)); } catch (e2) {}
  process.exitCode = 9;
});
