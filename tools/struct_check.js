// 真题结构组卷检查：走真实点击路径（选 卷面结构 → 点 🎲 生成试卷），
// 验证题型序列为 选择→填空→解答 且题量与真卷一致。
// 依赖：服务在 8899 运行。用法: node tools/struct_check.js
const { spawn } = require('child_process');
const http = require('http'), net = require('net'), crypto = require('crypto'), fs = require('fs');
const path = require('path');

const PROJ = path.resolve(__dirname, '..');
const PORT = Number(process.env.CDP_PORT || 9342);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const CHROME = process.env.CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const UDD = path.join(require('os').tmpdir(), 'udt_struct_' + Date.now());

const sleep = ms => new Promise(r => setTimeout(r, ms));
function get(p, raw) {
  return new Promise((res, rej) => {
    const r = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'GET' }, x => {
      let d = ''; x.on('data', c => d += c); x.on('end', () => res(raw ? d : JSON.parse(d)));
    });
    r.on('error', rej); r.setTimeout(8000, () => r.destroy(new Error('timeout ' + p))); r.end();
  });
}
function put(p) {
  return new Promise((res, rej) => {
    const r = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, x => {
      let d = ''; x.on('data', c => d += c);
      x.on('end', () => {
        if (x.statusCode >= 400) return rej(new Error('HTTP ' + x.statusCode + ' ' + d.slice(0, 200)));
        res(JSON.parse(d));
      });
    });
    r.on('error', rej); r.setTimeout(8000, () => r.destroy(new Error('timeout ' + p))); r.end();
  });
}
// 极简 CDP WebSocket 客户端（含 HTTP Upgrade 握手、累积缓冲、完整长度编码）
function ws(url) {
  const u = new URL(url);
  const key = crypto.randomBytes(16).toString('base64');
  const sock = net.connect(Number(u.port || PORT), u.hostname || '127.0.0.1');
  let buf = Buffer.alloc(0), hand = false, mid = 0;
  const waiters = new Map();
  const api = {
    ready: new Promise((res, rej) => {
      sock.setTimeout(10000, () => rej(new Error('ws connect timeout')));
      sock.on('error', rej);
      sock.on('data', d => {
        buf = Buffer.concat([buf, d]);
        if (!hand) {
          const i = buf.indexOf('\r\n\r\n');
          if (i < 0) return;
          buf = buf.slice(i + 4); hand = true; res();
        }
        while (buf.length >= 2) {
          const b1 = buf[1], masked = (b1 & 0x80) !== 0;
          let len = b1 & 0x7f, off = 2;
          if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
          else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
          if (masked) off += 4;
          if (buf.length < off + len) return;
          const payload = buf.slice(off, off + len).toString();
          buf = buf.slice(off + len);
          let m; try { m = JSON.parse(payload); } catch (e) { continue; }
          if (m.id && waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
        }
      });
      sock.write(
        `GET ${u.pathname} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\n` +
        `Connection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
    }),
    send(method, params) {
      const id = ++mid;
      const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }));
      const mask = crypto.randomBytes(4);
      let head;
      if (data.length < 126) head = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length < 65536) { head = Buffer.alloc(4); head[0] = 0x81; head[1] = 0xFE; head.writeUInt16BE(data.length, 2); }
      else { head = Buffer.alloc(10); head[0] = 0x81; head[1] = 0xFF; head.writeBigUInt64BE(BigInt(data.length), 2); }
      const masked = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) masked[i] = data[i] ^ mask[i % 4];
      sock.write(Buffer.concat([head, mask, masked]));
      return new Promise((res, rej) => {
        waiters.set(id, res);
        setTimeout(() => { if (waiters.has(id)) { waiters.delete(id); rej(new Error('cdp timeout ' + method)); } }, 15000);
      });
    },
    close() { try { sock.destroy(); } catch (e) {} },
  };
  return api;
}

let ch = null, sock = null, done = false;
async function ev(c, expr, awaitP) {
  const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: !!awaitP });
  if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
  return r.result && r.result.result ? r.result.result.value : undefined;
}

async function main() {
  ch = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${UDD}`,
    '--disable-gpu', '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
  for (let i = 0; i < 60; i++) { try { await get('/json/version'); break; } catch (e) { await sleep(250); } }
  const t = await put('/json/new?' + encodeURIComponent(PAGE));
  sock = ws(t.webSocketDebuggerUrl);
  await sock.ready;
  await sock.send('Page.enable');
  await sock.send('Runtime.enable');
  await sleep(2500);

  const errs = [];
  await ev(sock, `window.__errs=[]; window.addEventListener('error',e=>window.__errs.push(String(e.message)));`);

  // 切到组卷页
  await ev(sock, `document.querySelector('.tab[data-tab="paper"]').click(); 1`);
  await sleep(700);

  const probe = `(()=>{
    const Q = S.data.questions;
    const rows = [...document.querySelectorAll('#pOut tr[data-qid]')].map(tr=>{
      const q = Q.find(x=>String(x.id)===String(tr.dataset.qid));
      return q ? {type:q.type, h:+qHardness(q).toFixed(3), lv:qLevel(qHardness(q)),
                  src:qSrc(q), score:+(parseFloat(q.score)||0)} : null;
    }).filter(Boolean);
    const seq = rows.map(r=>r.type[0]).join('');
    const cnt = {选择题:0,填空题:0,解答题:0};
    rows.forEach(r=>cnt[r.type]++);
    // 分段判定：题型序列是否为 选择*→填空*→解答*（每种题型只出现一段）
    const order = ['选择题','填空题','解答题'];
    let si = 0, segmented = true;
    for(const r of rows){
      while(si<3 && order[si]!==r.type) si++;
      if(si>=3){ segmented=false; break; }
    }
    // 段内难度是否由难到易
    let mono = true;
    order.forEach(tp=>{
      const hs = rows.filter(r=>r.type===tp).map(r=>r.h);
      for(let i=1;i<hs.length;i++) if(hs[i] > hs[i-1] + 1e-9) mono = false;
    });
    return {n:rows.length, seq, cnt, segmented, mono, total:rows.reduce((a,r)=>a+r.score,0),
            struct:$('#pStruct').value, head:rows.map(r=>r.type+'/'+r.h+'/'+r.src).slice(0,6)};
  })()`;

  const out = {};

  // ① 真题结构（自动）— 全部题库
  await ev(sock, `$('#pStruct').value='auto'; $('#pLevel').value='mixed'; $('#pSrc').value='all';
                 $('#pN').value='22'; $('#pSeed').value='7'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(700);
  out.autoAll = await ev(sock, probe);

  // ② 老卷 + 自动结构 → 应为旧结构 8+6+9
  await ev(sock, `$('#pSrc').value='old'; $('#pN').value='23'; $('#pSeed').value='11'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(700);
  out.autoOld = await ev(sock, probe);

  // ③ 强制新结构，仅站点真题
  await ev(sock, `$('#pStruct').value='new'; $('#pSrc').value='site'; $('#pN').value='22'; $('#pSeed').value='3'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(900);
  out.forceNew = await ev(sock, probe);

  // ④ 强制旧结构，仅老卷
  await ev(sock, `$('#pStruct').value='old'; $('#pSrc').value='old'; $('#pN').value='23'; $('#pSeed').value='5'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(700);
  out.forceOld = await ev(sock, probe);

  // ⑤ 关掉结构 → 回到旧的难度排序（题型应混乱）
  await ev(sock, `$('#pStruct').value='off'; $('#pLevel').value='mixed'; $('#pSrc').value='all';
                 $('#pN').value='22'; $('#pSeed').value='9'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(700);
  out.offMode = await ev(sock, probe);

  // ⑥ 困难档 + 真题结构（难度与配额同时生效）
  await ev(sock, `$('#pStruct').value='new'; $('#pLevel').value='hard'; $('#pSrc').value='old';
                 $('#pN').value='22'; $('#pSeed').value='13'; 1`);
  await ev(sock, `document.querySelector('#pGo').click(); 1`);
  await sleep(700);
  out.hardStruct = await ev(sock, probe);

  out.errors = await ev(sock, `JSON.stringify(window.__errs)`);

  const report = JSON.stringify(out, null, 2);
  console.log(report);
  fs.writeFileSync(path.join(PROJ, 'docs', '_struct_report.json'), report);

  // 判定
  const A = out.autoAll, B = out.autoOld, C = out.forceNew, D = out.forceOld, E = out.offMode, F = out.hardStruct;
  const tests = [
    ['auto+all 题量 22', A.n === 22],
    ['auto+all 结构 10+6+6', A.cnt.选择题 === 10 && A.cnt.填空题 === 6 && A.cnt.解答题 === 6],
    ['auto+all 分段 S→F→J', A.segmented === true],
    ['auto+all 段内由难到易', A.mono === true],
    ['auto+老卷 题量 23', B.n === 23],
    ['auto+老卷 结构 8+6+9', B.cnt.选择题 === 8 && B.cnt.填空题 === 6 && B.cnt.解答题 === 9],
    ['auto+老卷 分段 S→F→J', B.segmented === true],
    ['强制 new+站点 10+6+6', C.cnt.选择题 === 10 && C.cnt.填空题 === 6 && C.cnt.解答题 === 6],
    ['强制 new+站点 分段', C.segmented === true],
    ['强制 old+老卷 8+6+9', D.cnt.选择题 === 8 && D.cnt.填空题 === 6 && D.cnt.解答题 === 9],
    ['强制 old+老卷 分段', D.segmented === true],
    ['hard+结构 仍满足配额', F.cnt.选择题 === 10 && F.cnt.填空题 === 6 && F.cnt.解答题 === 6],
    ['hard+结构 分段', F.segmented === true],
    ['off 模式题库数不变', E.n > 0],
    ['页面错误为空', out.errors === '[]'],
  ];
  let ok = true;
  console.log('\n--- 判定 ---');
  tests.forEach(([n, g]) => { console.log(`   ${g ? '✅' : '❌'} ${n}`); ok = ok && g; });
  console.log(ok ? '\n✅ 真题结构组卷正常' : '\n❌ 存在问题');
  done = true;
  return ok ? 0 : 3;
}

main().then(c => { process.exitCode = c; })
  .catch(e => { console.error('ERR', e.message); process.exitCode = 9; })
  .finally(() => {
    done = true;
    if (sock) sock.close();
    try { if (ch) ch.kill(); } catch (e) {}
    try { fs.rmSync(UDD, { recursive: true, force: true }); } catch (e) {}
  });


process.on('unhandledRejection', (e) => {
  // 收尾阶段 socket 被销毁时，仍挂着的 waiter 会变成 unhandled rejection，
  // 那会把已置好的 exitCode=0 顶成非 0。测试已结束就忽略。
  if (typeof done !== 'undefined' && done) return;
  if (typeof pass !== 'undefined' && pass !== null) return;
  if (typeof bad !== 'undefined' && bad !== null) return;
  console.error('❌ 未处理的 Promise 拒绝:', e && (e.message || e));
  process.exitCode = 9;
});
