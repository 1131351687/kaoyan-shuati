// 用 CDP 驱动 headless Chrome，等 JS 渲染完成后抓取真实 DOM。
//
// 坑 1：**不要加 --single-process**。该模式下 Chrome 会打印
//   "DevTools listening on ws://127.0.0.1:PORT/devtools/browser/..."
//   但实际从不 bind 端口（Get-NetTCPConnection 查不到 LISTEN），
//   所有 CDP 请求都 ECONNREFUSED。多进程模式在本机可以正常起 CDP。
// 坑 2：/json/new 必须用 PUT，用 GET 会返回 405 / Invalid URL。
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9333);
const URL = process.argv[2] || 'http://127.0.0.1:8899/';

function get(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => {
        try { res(JSON.parse(b)); } catch (e) { res(b); }
      });
    });
    req.on('error', rej);
    req.end();
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const udd = path.join(os.tmpdir(), 'cr_' + Date.now());
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT,
    '--user-data-dir=' + udd,
    'about:blank'
  ], { stdio: 'ignore' });

  // 等 CDP 就绪
  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = await get('/json/version'); break; } catch (e) { await sleep(400); }
  }
  if (!ver) { console.log('CHROME_FAIL'); ch.kill(); process.exit(2); }
  console.log('CDP ready:', ver['Browser']);

  // 新建标签并导航
  const tab = await get('/json/new?' + encodeURIComponent(URL));
  const ws = tab.webSocketDebuggerUrl;
  if (!ws || typeof ws !== 'string') {
    console.log('NO_WS_URL', JSON.stringify(tab)); process.exit(4);
  }

  // 极简 WebSocket 客户端（避免依赖 ws 包）
  const net = require('net'), crypto = require('crypto');
  const u = new (require('url').URL)(ws);
  const sock = net.connect(Number(u.port), u.hostname);
  const key = crypto.randomBytes(16).toString('base64');
  sock.write(
    `GET ${u.pathname} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\n` +
    `Connection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);

  let buf = Buffer.alloc(0), handshaken = false;
  const waiters = new Map();
  let msgId = 0;

  sock.on('connect', () => {});
  sock.on('error', e => {
    console.log('WS_SOCKET_ERROR:', e.code || e.message);
    try { ch.kill(); } catch (_) {}
    process.exit(5);
  });

  sock.on('data', d => {
    buf = Buffer.concat([buf, d]);
    if (!handshaken) {
      const i = buf.indexOf('\r\n\r\n');
      if (i < 0) return;
      handshaken = true; buf = buf.slice(i + 4);
      if (!/101/.test(d.slice(0, i).toString())) { console.log('WS_HANDSHAKE_FAIL'); process.exit(3); }
    }
    // 解帧
    while (buf.length >= 2) {
      const b1 = buf[0], b2 = buf[1];
      let len = b2 & 0x7f, off = 2;
      if (len === 126) { len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len).toString();
      buf = buf.slice(off + len);
      if ((b1 & 0x0f) === 1 && payload) {
        let m; try { m = JSON.parse(payload); } catch (e) { continue; }
        if (m.id && waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
      }
    }
  });

  function send(method, params) {
    return new Promise(res => {
      const id = ++msgId;
      waiters.set(id, res);
      const p = Buffer.from(JSON.stringify({ id, method, params: params || {} }));
      const mask = crypto.randomBytes(4);
      const len = p.length;
      let head;
      if (len < 126) head = Buffer.from([0x81, 0x80 | len]);
      else if (len < 65536) { head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0x80 | 126; head.writeUInt16BE(len, 2); }
      else { head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0x80 | 127; head.writeBigUInt64BE(BigInt(len), 2); }
      const masked = Buffer.alloc(len);
      for (let i = 0; i < len; i++) masked[i] = p[i] ^ mask[i % 4];
      sock.write(Buffer.concat([head, mask, masked]));
    });
  }

  await new Promise(r => sock.on('connect', r));
  await sleep(500);

  await send('Runtime.enable');
  await send('Page.enable');
  await sleep(2500);   // 等 boot() + KaTeX 加载

  const evalJs = async expr => {
    const r = await send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true
    });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const report = await evalJs(`(function(){
    const out = {};
    out.katexLoaded = !!window.katex;
    out.katexOk = window.__KATEX_OK === true;
    out.qcount = document.querySelectorAll('#qbody *').length;
    out.groups = [...document.querySelectorAll('#group option')].map(o=>o.textContent.trim());
    out.kpOptions = [...document.querySelectorAll('#kp option')].map(o=>o.textContent.trim());
    out.title = (document.querySelector('#qbody h1, h1')||{}).textContent;
    out.qbodyLen = (document.querySelector('#qbody')||{}).innerHTML.length;
    out.katexSpans = document.querySelectorAll('.katex').length;
    out.texFallback = document.querySelectorAll('code.tex').length;
    out.errors = window.__ERRORS__ || [];
    return out;
  })()`);

  console.log('--- 初始状态 ---');
  console.log(JSON.stringify(report, null, 2));

  // 切到模拟卷 2026，看题干含 cases 的那道题
  const switched = await evalJs(`(function(){
    const g = document.querySelector('#group');
    const opt = [...g.options].find(o=>/模拟卷\\s*2026/.test(o.textContent));
    if(!opt) return {err:'no 2026 option'};
    g.value = opt.value; g.dispatchEvent(new Event('change'));
    return {ok:true, value: opt.value};
  })()`);
  console.log('--- 切组 ---', JSON.stringify(switched));

  await sleep(1200);
  const got = await evalJs(`(function(){
    const idx = S.list.findIndex(q=>/cases/.test(q.stem||''));
    if(idx<0) return {err:'cases 题未找到', n:S.list.length,
      first:(S.list[0]||{}).id, stems:S.list.slice(0,3).map(q=>q.id)};
    S.i = idx; render();
    return {ok:true, idx, id:S.list[idx].id};
  })()`);
  console.log('--- 定位含 cases 的题 ---', JSON.stringify(got));

  await sleep(1500);
  const mock = await evalJs(`(function(){
    const sb = document.querySelector('.stembox');
    return {
      hasStembox: !!sb,
      katexSpans: document.querySelectorAll('.stembox .katex').length,
      texFallback: document.querySelectorAll('.stembox code.tex').length,
      text: sb ? sb.innerText.slice(0,400) : null,
      htmlSample: sb ? sb.innerHTML.slice(0,300) : null
    };
  })()`);
  console.log('--- 模拟卷题干渲染 ---');
  console.log(JSON.stringify(mock, null, 2));

  // 检查控制台错误
  const errs = await evalJs('JSON.stringify(window.__ERRORS__||[])');
  console.log('--- 页面错误 ---', errs);

  sock.destroy(); ch.kill();
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {}
  console.log('DONE');
  process.exit(0);
})().catch(e => { console.log('ERR', e.message || e); process.exit(1); });
