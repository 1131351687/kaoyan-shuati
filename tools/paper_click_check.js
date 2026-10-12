// 专项回归：模拟真实用户点击「🎲 随机组卷」标签，确认面板真的显示（这是用户报的 bug）。
// 不直接调 drawPaper，必须走标签页点击路径。
const http = require('http');
const { spawn } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');

const PROJ = path.resolve(__dirname, '..');
const TRACE = process.env.TRACE ? path.join(PROJ, 'docs', '_trace2.txt') : '';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9338);
const URL = process.env.BANK_URL || 'http://127.0.0.1:8899/';

const sleep = ms => new Promise(r => setTimeout(r, ms));
let done = false;   // 用例跑完置 true：之后的 socket 错误是收尾噪声，不算失败

// 注意：put 必须处理 HTTP 错误码，否则请求失败时 Promise 永不 settle，
// 事件循环空掉后 node 会静默退出 0（这正是本脚本此前"无输出"的原因）。
function put(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => {
        if (r.statusCode >= 400) return rej(new Error('HTTP ' + r.statusCode + ' on PUT ' + p + ': ' + b.slice(0, 120)));
        try { res(JSON.parse(b)); } catch (e) { rej(new Error('PUT ' + p + ' 非 JSON: ' + b.slice(0, 120))); }
      });
    });
    req.on('error', rej);
    req.setTimeout(8000, () => req.destroy(new Error('PUT timeout ' + p)));
    req.end();
  });
}
function get(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(b));
    });
    req.on('error', rej);
    req.setTimeout(8000, () => req.destroy(new Error('GET timeout ' + p)));
    req.end();
  });
}

async function main() {
  if (TRACE) fs.appendFileSync(TRACE, 'main:start\n');
  const udd = path.join(os.tmpdir(), 'udt_' + Date.now() + '_' + PORT);
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + udd, 'about:blank',
  ], { detached: true, stdio: 'ignore' });
  ch.on('error', e => { if (TRACE) fs.appendFileSync(TRACE, 'spawn error: ' + e.message + '\n'); });
  ch.unref();
  if (TRACE) fs.appendFileSync(TRACE, 'main:spawned pid=' + ch.pid + '\n');

  let ws = null;
  for (let i = 0; i < 60; i++) {
    await sleep(400);
    try { const v = await get('/json/version'); if (v.includes('webSocketDebuggerUrl')) { ws = JSON.parse(v).webSocketDebuggerUrl; break; } }
    catch (e) { /* 端口未起 */ }
  }
  if (TRACE) fs.appendFileSync(TRACE, 'main:cdp ws=' + (ws || 'NULL') + '\n');
  if (!ws) { console.log('FAIL: CDP 未就绪'); process.exit(3); }

  const tab = await put('/json/new?' + encodeURIComponent(URL));
  if (TRACE) fs.appendFileSync(TRACE, 'main:newtab id=' + (tab && tab.id) + '\n');
  // 注意：tab.webSocketDebuggerUrl 形如 ws://127.0.0.1:9338/devtools/page/XXX
  const wsUrl = new (require('url').URL)(tab.webSocketDebuggerUrl || ('ws://127.0.0.1:' + PORT + '/devtools/page/' + tab.id));
  if (TRACE) fs.appendFileSync(TRACE, 'main:wsUrl=' + wsUrl.href + ' host=' + wsUrl.hostname + ' port=' + wsUrl.port + '\n');
  const net = require('net'), crypto = require('crypto');
  const sock = net.connect(Number(wsUrl.port || PORT), wsUrl.hostname || '127.0.0.1');
  // 关键：必须给 connect 加超时，否则 socket 连不上时事件循环空转、进程静默退出 0
  await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('WS connect timeout')), 10000);
    sock.once('connect', () => { clearTimeout(t); if (TRACE) fs.appendFileSync(TRACE, 'main:sock connected\n'); res(); });
    sock.once('error', e => { clearTimeout(t); rej(e); });
  });
  // 必须完成 WebSocket 握手（HTTP Upgrade + Sec-WebSocket-Key），
  // 否则 Chrome 的 DevTools 端点收到裸帧后直接忽略、永不回包。
  const wsKey = crypto.randomBytes(16).toString('base64');
  sock.write('GET ' + wsUrl.pathname + ' HTTP/1.1\r\n' +
    'Host: ' + wsUrl.host + '\r\n' +
    'Upgrade: websocket\r\n' +
    'Connection: Upgrade\r\n' +
    'Sec-WebSocket-Key: ' + wsKey + '\r\n' +
    'Sec-WebSocket-Version: 13\r\n\r\n');

  // 关键：必须用「持久累积缓冲区」解析帧流，并且允许帧被 TCP 拆包。
  // 早期版本每个 data 事件独立解析，遇到跨包帧会静默丢帧 → Promise 永不 settle。
  let buf = Buffer.alloc(0), ready = false;
  let idc = 0; const pend = new Map();
  sock.on('data', d => {
    buf = Buffer.concat([buf, d]);
    if (!ready) {
      const i = buf.indexOf('\r\n\r\n');
      if (i < 0) return;
      if (TRACE) fs.appendFileSync(TRACE, 'main:handshake=' + buf.slice(0, 15).toString('latin1').replace(/\r|\n/g, ' ') + '\n');
      buf = buf.slice(i + 4); ready = true;
    }
    while (buf.length >= 2) {
      const op = buf[0] & 0x0f;
      let len = buf[1] & 0x7f, off = 2;
      if (len === 126) { len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;   // 帧还没收全，等下一个 data 事件
      const payload = buf.slice(off, off + len);
      buf = buf.slice(off + len);
      if (op === 8) return;
      if (op !== 1) continue;
      try {
        const m = JSON.parse(payload.toString());
        if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
      } catch (e) {}
    }
  });
  sock.once('error', e => {
    if (done) return;   // 收尾阶段的 ECONNRESET 属正常
    console.log('WS_SOCKET_ERROR', e.message);
    process.exitCode = 5;
  });
  await new Promise(r => setTimeout(r, 500));
  if (!ready) { console.log('FAIL: WebSocket 握手失败'); process.exit(6); }
  function send(method, params) {
    const id = ++idc; const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }));
    const mask = crypto.randomBytes(4);
    // WebSocket 帧头：长度 <126 用 7 位直接编码；126..65535 必须用 126 + 2 字节大端；
    // 否则一旦 payload ≥126 字节（Page.enable / Runtime.evaluate 都超），长度位被写坏，
    // Chrome 收到无效帧后永不回包 → Promise 不 settle → 事件循环空掉 → 进程静默退出 0。
    let hdr;
    if (data.length < 126) {
      hdr = Buffer.alloc(6);
      hdr[0] = 0x81; hdr[1] = 0x80 | data.length;
    } else if (data.length < 65536) {
      hdr = Buffer.alloc(8);
      hdr[0] = 0x81; hdr[1] = 0x80 | 126;
      hdr.writeUInt16BE(data.length, 2);
    } else {
      hdr = Buffer.alloc(14);
      hdr[0] = 0x81; hdr[1] = 0x80 | 127;
      hdr.writeBigUInt64BE(BigInt(data.length), 2);
    }
    const maskOff = hdr.length - 4;
    mask.copy(hdr, maskOff);
    for (let i = 0; i < data.length; i++) data[i] ^= mask[i & 3];
    sock.write(Buffer.concat([hdr, data]));
    return new Promise((res, rej) => {
      const t = setTimeout(() => { pend.delete(id); rej(new Error('CDP 响应超时: ' + method)); }, 15000);
      pend.set(id, m => { clearTimeout(t); res(m); });
    });
  }
  const ev = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await sleep(2000);

  // 1) 点标签前后 #tab-paper 可见性
  const before = await ev(`(function(){
    const p=document.getElementById('tab-paper');
    return {cls:p.className, disp:getComputedStyle(p).display};
  })()`);

  await ev(`(function(){
    const t=[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='paper');
    t.click(); return true;
  })()`);
  await sleep(900);

  const after = await ev(`(function(){
    const p=document.getElementById('tab-paper');
    return {cls:p.className, disp:getComputedStyle(p).display,
            on:[...document.querySelectorAll('.tab.on')].map(x=>x.dataset.tab),
            rows:document.querySelectorAll('#pOut tr').length,
            hasCtl:!!document.getElementById('pGo'),
            err:window.__err||null};
  })()`);

  // 2) 再切别的标签再切回来（反复切换不应坏）
  await ev(`[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='stats').click()`);
  await sleep(400);
  const onStats = await ev(`getComputedStyle(document.getElementById('tab-paper')).display`);
  await ev(`[...document.querySelectorAll('.tab')].find(x=>x.dataset.tab==='paper').click()`);
  await sleep(600);
  const back = await ev(`(function(){const p=document.getElementById('tab-paper');
    return {disp:getComputedStyle(p).display, rows:document.querySelectorAll('#pOut tr').length};})()`);

  const report = { 点击前: before, 点击后: after, 切到统计时: onStats, 切回后: back };
  fs.writeFileSync(path.join(PROJ, 'docs', '_click_report.json'),
                   JSON.stringify(report, null, 1), 'utf-8');
  console.log(JSON.stringify(report, null, 1));

  const pass = after.disp !== 'none' && after.rows > 0 && after.on.includes('paper')
               && back.disp !== 'none' && back.rows > 0;
  console.log(pass ? '\n✅ 组卷页点击路径正常' : '\n❌ 组卷页仍不可见');
  fs.appendFileSync(path.join(PROJ, 'docs', '_click_report.json'),
                    '\n' + (pass ? 'PASS' : 'FAIL') + '\n', 'utf-8');

  try { await put('/json/close/' + tab.id); } catch (e) {}
  sock.end();
  try { process.kill(-ch.pid); } catch (e) { try { ch.kill(); } catch (e2) {} }
  // 收尾阶段 Chrome 关闭会触发 ECONNRESET，这是正常现象，不能让它把退出码带成 5。
  // 注意：process.exit() 会立刻终止进程，可能丢掉还没 flush 的 stdout。
  // 让事件循环自然结束，用 process.exitCode 传退出码。
  done = true;
  process.exitCode = pass ? 0 : 1;
}
main().catch(e => {
  console.log('FAIL', e.message);
  try { fs.appendFileSync(path.join(PROJ, 'docs', '_click_report.json'), 'ERROR ' + e.message + '\n', 'utf-8'); } catch (e2) {}
  process.exitCode = 9;
});
