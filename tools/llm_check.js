// llm_check.js —— 验证 LLM 难度已接入浏览器的组卷页
// 检查点：
//   1) index.json 里确实带了 llm 字段
//   2) 组卷页难度列显示 [LLM] 而非全是 [先验]
//   3) 只从本地卷（old*/mock*）抽 hard 档，能抽出题（修复前该场景为 0）
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const crypto = require('crypto');
const http = require('http');

const PROJ = path.resolve(__dirname, '..');
const PORT = Number(process.env.CDP_PORT || 9341);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const CHROME = process.env.CHROME ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const UDD = path.join(process.env.TEMP || '.', 'udt_llm_' + Date.now());

const sleep = ms => new Promise(r => setTimeout(r, ms));

function req2(p, method) {
  return new Promise((res, rej) => {
    const r = http.request({ host: '127.0.0.1', port: PORT, path: p, method },
      resp => {
        let b = ''; resp.on('data', d => b += d);
        resp.on('end', () => {
          if (resp.statusCode >= 400) return rej(new Error('HTTP ' + resp.statusCode));
          try { res(JSON.parse(b)); } catch (e) { res(b); }
        });
      });
    r.on('error', rej);
    r.setTimeout(8000, () => r.destroy(new Error('timeout')));
    r.end();
  });
}
const get = p => req2(p, 'GET');
const put = p => req2(p, 'PUT');

// ── 极简 CDP WebSocket 客户端（正确实现见 README「自己写 CDP 脚本」）──
class WS {
  constructor(url) { this.url = url; this.mid = 0; this.waiters = new Map(); }
  connect() {
    const u = new URL(this.url);
    const key = crypto.randomBytes(16).toString('base64');
    return new Promise((res, rej) => {
      const sock = net.connect(Number(u.port) || 80, u.hostname || '127.0.0.1');
      sock.setTimeout(10000);
      let buf = Buffer.alloc(0), up = false;
      sock.on('connect', () => {
        sock.write(
          `GET ${u.pathname}${u.search} HTTP/1.1\r\n` +
          `Host: ${u.hostname}:${u.port}\r\n` +
          `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      });
      sock.on('data', d => {
        buf = Buffer.concat([buf, d]);
        if (!up) {
          const i = buf.indexOf('\r\n\r\n');
          if (i < 0) return;
          buf = buf.slice(i + 4); up = true; this.sock = sock; res();
        }
        while (true) {
          if (buf.length < 2) return;
          const fin = buf[0] & 0x80, op = buf[0] & 0x0f;
          let len = buf[1] & 0x7f, off = 2;
          if (len === 126) {
            if (buf.length < 4) return;
            len = buf.readUInt16BE(2); off = 4;
          } else if (len === 127) {
            if (buf.length < 10) return;
            len = Number(buf.readBigUInt64BE(2)); off = 10;
          }
          if (buf.length < off + len) return;
          const pay = buf.slice(off, off + len);
          buf = buf.slice(off + len);
          if (op === 1 || op === 2) {
            let j;
            try { j = JSON.parse(pay.toString('utf8')); } catch (e) { continue; }
            if (j.id != null && this.waiters.has(j.id)) {
              const w = this.waiters.get(j.id);
              this.waiters.delete(j.id);
              j.error ? w.rej(new Error(JSON.stringify(j.error))) : w.res(j.result);
            }
          } else if (op === 8) { return; }
          if (fin && op === 9) { /* ping */ }
        }
      });
      sock.on('error', e => { if (!up) rej(e); });
      sock.on('timeout', () => sock.destroy());
    });
  }
  send(method, params) {
    const id = ++this.mid;
    const s = JSON.stringify({ id, method, params: params || {} });
    const pay = Buffer.from(s, 'utf8');
    let head;
    if (pay.length < 126) {
      head = Buffer.alloc(2); head[0] = 0x81; head[1] = 0x80 | pay.length;
    } else if (pay.length < 65536) {
      head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0xFE;
      head.writeUInt16BE(pay.length, 2);
    } else {
      head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0xFF;
      head.writeBigUInt64BE(BigInt(pay.length), 2);
    }
    const mask = crypto.randomBytes(4);
    const m = Buffer.alloc(pay.length);
    for (let i = 0; i < pay.length; i++) m[i] = pay[i] ^ mask[i % 4];
    this.sock.write(Buffer.concat([head, mask, m]));
    return new Promise((res, rej) => {
      this.waiters.set(id, { res, rej });
      setTimeout(() => {
        if (this.waiters.has(id)) {
          this.waiters.delete(id);
          rej(new Error('CDP timeout: ' + method));
        }
      }, 20000);
    });
  }
  close() { try { this.sock && this.sock.destroy(); } catch (e) {} }
}

const ev = (ws, expr) =>
  ws.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    .then(r => r.result && r.result.value);

async function main() {
  const rep = {};
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + UDD,
    'about:blank'
  ], { stdio: 'ignore' });

  let wsUrl = null;
  for (let i = 0; i < 60 && !wsUrl; i++) {
    try {
      const j = await get('/json/version');
      wsUrl = j.webSocketDebuggerUrl;
    } catch (e) { await sleep(400); }
  }
  if (!wsUrl) { console.log('❌ Chrome 未就绪'); ch.kill(); process.exit(3); }

  const brow = new WS(wsUrl); await brow.connect();
  const tab = await put('/json/new?' + encodeURIComponent(PAGE));
  const ws = new WS(tab.webSocketDebuggerUrl); await ws.connect();
  await ws.send('Page.enable'); await ws.send('Runtime.enable');
  await sleep(2500);

  try {
    rep.title = await ev(ws, '(document.querySelector(".brand h1")||document.querySelector(".brand")||{}).textContent' +
                             '?document.querySelector(".brand").innerText.replace(/\\s+/g," ").trim().slice(0,90):""');

    // 收集页面错误
    await ev(ws, `window.__ERRORS__=[];window.addEventListener('error',e=>window.__ERRORS__.push(String(e.message)));`);

    // 1) index.json 里的 llm 字段
    rep.llmInData = await ev(ws, `(()=>{const q=S.data.questions;
      return {total:q.length, withLlm:q.filter(x=>x.llm&&typeof x.llm.d==='number').length,
              lvSrcOk:q.filter(x=>x.llm).every(x=>x.llm.lv==='easy'||x.llm.lv==='mid'||x.llm.lv==='hard')};})()`);

    // 2) qHardness/qSrc 在浏览器里可用
    rep.fnOk = await ev(ws, `typeof qHardness==='function' && typeof qSrc==='function'`);

    // 抽取当前 #pOut 里每行的难度列（第 8 列）与来源标注
    const readRows = () => ev(ws, `(()=>{
      const rows=[...document.querySelectorAll('#pOut tbody tr')];
      const txts=rows.map(t=>t.children[7]?t.children[7].innerText.replace(/\\s+/g,' ').trim():'');
      const srcs=txts.map(t=>(t.match(/\\/(站点|LLM|先验)/)||[])[1]).filter(Boolean);
      return { rows:rows.length,
               srcCount:{站点:srcs.filter(s=>s==='站点').length,
                         LLM:srcs.filter(s=>s==='LLM').length,
                         先验:srcs.filter(s=>s==='先验').length},
               sample:txts.slice(0,4) };})()`);

    // 切到组卷页
    await ev(ws, `document.querySelector('.tab[data-tab="paper"]').click()`);
    await sleep(400);

    // 3a) 只从本地老卷（1987-2008）抽 hard —— 修复前该场景抽不出题
    rep.oldHard = await ev(ws, `(()=>{
      const s=document.getElementById('pSrc');
      s.value = [...s.options].some(o=>o.value==='old2005') ? 'old2005' : s.value;
      document.getElementById('pLevel').value='hard';
      document.getElementById('pN').value='6';
      document.getElementById('pGo').click();
      return s.value;})()`);
    await sleep(1000);
    rep.oldHardRows = await readRows();

    // 3b) 只从模拟卷抽 hard
    rep.mockHard = await ev(ws, `(()=>{
      const s=document.getElementById('pSrc');
      s.value = [...s.options].some(o=>o.value==='mock2026') ? 'mock2026' : s.value;
      document.getElementById('pLevel').value='hard';
      document.getElementById('pN').value='6';
      document.getElementById('pGo').click();
      return s.value;})()`);
    await sleep(1000);
    rep.mockHardRows = await readRows();

    // 3c) 全库混合
    await ev(ws, `(()=>{const s=document.getElementById('pSrc');
      s.value=''; document.getElementById('pLevel').value='mixed';
      document.getElementById('pN').value='12';
      document.getElementById('pGo').click();})()`);
    await sleep(1000);
    rep.mixedRows = await readRows();

    rep.errors = await ev(ws, 'window.__ERRORS__||[]');
  } catch (e) {
    rep.error = String(e && e.message || e);
  }

  console.log(JSON.stringify(rep, null, 1));
  ws.close(); brow.close();
  try { ch.kill(); } catch (e) {}
  process.exit(0);
}

main().catch(e => { console.log('FAIL ' + (e && e.message)); process.exit(9); });
