// 验证「随机组卷」标签页：生成试卷、切换难度、开始作答。
// 复用 render_check.js 的 CDP 方案。注意 **不要加 --single-process**：该模式下
// Chrome 打印 "DevTools listening" 却不 bind 端口，CDP 全部 ECONNREFUSED。
const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9334);
const URL = process.env.BANK_URL || 'http://127.0.0.1:8899/';

function put(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p,
                               method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d);
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { res(b); } });
    });
    req.on('error', rej); req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const udd = path.join(os.tmpdir(), 'crp_' + Date.now());
  const ch = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + udd, 'about:blank'
  ], { stdio: 'ignore' });

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = await put('/json/version'); break; } catch (e) { await sleep(400); }
  }
  if (!ver) { console.log('CHROME_FAIL'); ch.kill(); process.exit(2); }
  console.log('CDP:', ver['Browser']);

  // /json/new 偶发返回没有 webSocketDebuggerUrl（CDP 侧竞态），重试几次。
  let tab = null, ws = null;
  for (let i = 0; i < 5 && !ws; i++) {
    tab = await put('/json/new?' + encodeURIComponent(URL));
    ws = tab && tab.webSocketDebuggerUrl;
    if (!ws) await sleep(600);
  }
  if (!ws) { console.log('NO_WS', JSON.stringify(tab)); ch.kill(); process.exit(4); }

  const net = require('net'), crypto = require('crypto');
  const u = new (require('url').URL)(ws);
  const sock = net.connect(Number(u.port), u.hostname);
  const key = crypto.randomBytes(16).toString('base64');
  sock.write(`GET ${u.pathname} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\n` +
    `Connection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);

  let buf = Buffer.alloc(0), ready = false;
  const waiters = new Map();
  let mid = 0;
  sock.on('connect', () => {});
  sock.on('error', e => {
    console.log('WS_SOCKET_ERROR:', e.code || e.message);
    try { ch.kill(); } catch (_) {}
    process.exit(5);
  });
  sock.on('data', d => {
    buf = Buffer.concat([buf, d]);
    if (!ready) {
      const i = buf.indexOf('\r\n\r\n');
      if (i < 0) return;
      buf = buf.slice(i + 4); ready = true;
    }
    while (buf.length >= 2) {
      const op = buf[0] & 0x0f;
      let len = buf[1] & 0x7f, off = 2;
      if (len === 126) { len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len);
      buf = buf.slice(off + len);
      if (op === 8) return;
      if (op !== 1) continue;
      try {
        const m = JSON.parse(payload.toString());
        if (m.id && waiters.has(m.id)) {
          waiters.get(m.id)(m); waiters.delete(m.id);
        }
      } catch (e) {}
    }
  });

  function send(method, params) {
    mid++;
    const msg = JSON.stringify({ id: mid, method, params: params || {} });
    const p = Buffer.from(msg);
    const mask = crypto.randomBytes(4);
    let head;
    if (p.length < 126) head = Buffer.from([0x81, 0x80 | p.length]);
    else if (p.length < 65536) {
      head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0xfe;
      head.writeUInt16BE(p.length, 2);
    } else {
      head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0xff;
      head.writeBigUInt64BE(BigInt(p.length), 2);
    }
    const masked = Buffer.alloc(p.length);
    for (let i = 0; i < p.length; i++) masked[i] = p[i] ^ mask[i % 4];
    const id = mid;
    return new Promise(res => {
      waiters.set(id, res);
      sock.write(Buffer.concat([head, mask, masked]));
    });
  }

  await new Promise(r => sock.on('connect', r));
  await sleep(500);
  await send('Runtime.enable');
  await send('Page.enable');
  await sleep(2800);

  const ev = async expr => {
    const r = await send('Runtime.evaluate',
      { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  // 收集页面错误
  await ev(`window.__ERRORS__=[];window.addEventListener('error',e=>window.__ERRORS__.push(String(e.message)));`);

  console.log('\n=== 1) 标签页存在性 ===');
  console.log(JSON.stringify(await ev(`(function(){
    return {
      tabs: [...document.querySelectorAll('.tab')].map(t=>t.textContent.trim()),
      hasPaperTab: !!document.querySelector('.tab[data-tab="paper"]'),
      hasPaperBox: !!document.querySelector('#tab-paper')
    };
  })()`), null, 1));

  console.log('\n=== 2) 切到组卷页并生成 ===');
  console.log(JSON.stringify(await ev(`(function(){
    document.querySelector('.tab[data-tab="paper"]').click();
    return { clicked:true, ctl: !!document.querySelector('#pGo'),
             kpChips: document.querySelectorAll('#pKp input').length,
             typeChips: document.querySelectorAll('#pType input').length };
  })()`), null, 1));

  await sleep(800);

  console.log('\n=== 3) 默认（混合难度）生成结果 ===');
  const g1 = await ev(`(function(){
    document.querySelector('#pGo').click();
    return null;})()`);
  await sleep(900);
  console.log(JSON.stringify(await ev(`(function(){
    const rows=[...document.querySelectorAll('#pOut tbody tr')];
    const head=(document.querySelector('#pOut .small.muted')||{}).innerText||'';
    return { rows: rows.length, summary: head.replace(/\\s+/g,' ').slice(0,150),
             firstRow: rows[0]?rows[0].innerText.replace(/\\s+/g,' ').slice(0,110):null,
             lastRow: rows.length?rows[rows.length-1].innerText.replace(/\\s+/g,' ').slice(0,110):null };
  })()`), null, 1));

  console.log('\n=== 4) 切「困难」难度 + 22 题 ===');
  await ev(`(function(){
    document.querySelector('#pLevel').value='hard';
    document.querySelector('#pN').value='22';
    document.querySelector('#pGo').click(); return null;})()`);
  await sleep(900);
  const hard = await ev(`(function(){
    const rows=[...document.querySelectorAll('#pOut tbody tr')];
    const hs=rows.map(r=>{const t=r.children[7].innerText;return parseFloat(t)||0;});
    return { rows: rows.length,
             minH: Math.min(...hs).toFixed(2), maxH: Math.max(...hs).toFixed(2),
             allHard: hs.every(h=>h>=0.70),
             summary:(document.querySelector('#pOut .small.muted')||{}).innerText
                     .replace(/\\s+/g,' ').slice(0,130) };
  })()`);
  console.log(JSON.stringify(hard, null, 1));

  console.log('\n=== 5) 切「简单」难度 ===');
  await ev(`(function(){
    document.querySelector('#pLevel').value='easy';
    document.querySelector('#pGo').click(); return null;})()`);
  await sleep(900);
  console.log(JSON.stringify(await ev(`(function(){
    const rows=[...document.querySelectorAll('#pOut tbody tr')];
    const hs=rows.map(r=>parseFloat(r.children[7].innerText)||0);
    return { rows: rows.length, minH:Math.min(...hs).toFixed(2),
             maxH:Math.max(...hs).toFixed(2), allEasy: hs.every(h=>h<0.38) };
  })()`), null, 1));

  console.log('\n=== 6) 限定知识点「二次型」 ===');
  await ev(`(function(){
    document.querySelector('#pLevel').value='mixed';
    document.querySelectorAll('#pKp input').forEach(x=>x.checked=false);
    const cb=[...document.querySelectorAll('#pKp input')]
      .find(x=>x.value.includes('二次型'));
    if(cb) cb.checked=true;
    document.querySelector('#pGo').click(); return null;})()`);
  await sleep(900);
  console.log(JSON.stringify(await ev(`(function(){
    const rows=[...document.querySelectorAll('#pOut tbody tr')];
    const kps=rows.map(r=>r.children[8].innerText.trim());
    return { rows:rows.length, allErCi: kps.every(k=>k.includes('二次型')),
             sample:kps.slice(0,4) };
  })()`), null, 1));

  console.log('\n=== 7) 「开始作答」是否接管题目区 ===');
  await ev(`document.querySelector('#pKpAll').click()`);
  await sleep(200);
  await ev(`(function(){document.querySelector('#pGo').click();return null;})()`);
  await sleep(700);
  await ev(`document.querySelector('#pQuiz').click()`);
  await sleep(1200);
  console.log(JSON.stringify(await ev(`(function(){
    return { activeTab: (document.querySelector('.tab.on')||{}).dataset.tab,
             qbodyLen: (document.querySelector('#qbody')||{}).innerHTML.length,
             listLen: (window.S&&S.list)?S.list.length:null,
             hasChoice: !!document.querySelector('.opt'),
             qbodyHead: (document.querySelector('#qbody')||{}).innerText
                        .replace(/\\s+/g,' ').slice(0,120) };
  })()`), null, 1));

  console.log('\n=== 8) 页面错误 ===');
  console.log(await ev(`JSON.stringify(window.__ERRORS__||[])`));

  sock.destroy(); ch.kill();
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {}
  console.log('\nDONE');
  process.exit(0);
})().catch(e => { console.log('ERR', e.message); process.exit(1); });
