// 错题本 + 答题计时 的 CDP 真实点击路径回归（端口 9343）
// 走真实点击：先造错题数据 -> 点「错题本」标签 -> 校验表格 -> 点「只练这些错题」
const http = require('http');
const net = require('net');
const crypto = require('crypto');
const path = require('path');
const { spawn } = require('child_process');

const PROJ = path.resolve(__dirname, '..');
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = Number(process.env.CDP_PORT || 9343);
const PAGE = process.env.BANK_URL || 'http://127.0.0.1:8899/';
const TRACE = !!process.env.TRACE;
const UD = path.join(process.env.TEMP || '.', 'udt_wrong_' + Date.now());

function log(...a) { if (TRACE) console.log('[t]', ...a); }

function put(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'PUT' }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(b));
    });
    req.on('error', rej); req.setTimeout(8000, () => req.destroy(new Error('put timeout'))); req.end();
  });
}
function get(p) {
  return new Promise((res, rej) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p }, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(b));
    });
    req.on('error', rej); req.setTimeout(8000, () => req.destroy(new Error('get timeout'))); req.end();
  });
}

class CDP {
  constructor(wsUrl) {
    const u = new URL(wsUrl);
    this.host = u.hostname || '127.0.0.1'; this.port = Number(u.port || PORT);
    this.path = u.pathname; this.buf = Buffer.alloc(0); this.waiters = new Map(); this.mid = 0;
  }
  connect() {
    return new Promise((res, rej) => {
      const key = crypto.randomBytes(16).toString('base64');
      this.sock = net.connect(this.port, this.host, () => {
        this.sock.write(`GET ${this.path} HTTP/1.1\r\nHost: ${this.host}:${this.port}\r\n` +
          `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
          `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      });
      this.sock.setTimeout(10000, () => rej(new Error('sock timeout')));
      let handshook = false;
      this.sock.on('data', d => {
        if (!handshook) {
          this.buf = Buffer.concat([this.buf, d]);
          const i = this.buf.indexOf('\r\n\r\n');
          if (i < 0) return;
          const head = this.buf.slice(0, i).toString();
          this.buf = this.buf.slice(i + 4);
          if (!/101/.test(head)) return rej(new Error('handshake failed: ' + head.split('\r\n')[0]));
          handshook = true; res(); this.pump();
          return;
        }
        this.buf = Buffer.concat([this.buf, d]); this.pump();
      });
      this.sock.on('error', e => { if (!handshook) rej(e); });
    });
  }
  pump() {
    for (;;) {
      const buf = this.buf;
      if (buf.length < 2) return;
      const len0 = buf[1] & 0x7f;
      let off = 2, len = len0;
      if (len0 === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
      else if (len0 === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len).toString();
      this.buf = buf.slice(off + len);
      let msg; try { msg = JSON.parse(payload); } catch (e) { continue; }
      const w = this.waiters.get(msg.id);
      if (w) { this.waiters.delete(msg.id); msg.error ? w.rej(new Error(JSON.stringify(msg.error))) : w.res(msg.result); }
    }
  }
  send(method, params) {
    const id = ++this.mid;
    const data = Buffer.from(JSON.stringify({ id, method, params: params || {} }));
    const mask = crypto.randomBytes(4);
    let hdr;
    if (data.length < 126) { hdr = Buffer.alloc(6); hdr[1] = 0x80 | data.length; }
    else if (data.length < 65536) { hdr = Buffer.alloc(8); hdr[1] = 0x80 | 126; hdr.writeUInt16BE(data.length, 2); }
    else { hdr = Buffer.alloc(14); hdr[1] = 0x80 | 127; hdr.writeBigUInt64BE(BigInt(data.length), 2); }
    hdr[0] = 0x81; mask.copy(hdr, hdr.length - 4);
    const out = Buffer.concat([hdr, data]);
    for (let i = 0; i < data.length; i++) out[hdr.length + i] = data[i] ^ mask[i % 4];
    this.sock.write(out);
    return new Promise((res, rej) => this.waiters.set(id, { res, rej }));
  }
  close() { try { this.sock.destroy(); } catch (e) { } }
}

let ch, cdp, done = false;
async function ev(expr) {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' :: ' + (r.exceptionDetails.exception || {}).description);
  return r.result.value;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  log('spawn');
  ch = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox',
    '--disable-dev-shm-usage', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${UD}`, 'about:blank'], { stdio: 'ignore' });
  // 等 CDP 绑定
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try { const v = await get('/json/version'); if (/Chrome/.test(v)) break; } catch (e) { }
  }
  log('cdp up');
  const tab = JSON.parse(await put('/json/new?about:blank'));
  cdp = new CDP(tab.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: PAGE });
  await sleep(2500);
  log('navigated');

  const R = {};
  // ── 1. 造错题：用页面自己的 record() 上报 3 次作答（2 错 1 对）──
  R.seeded = await ev(`(async()=>{
    const qs = S.data.questions.filter(q=>q.gradeable && q.group==='8').slice(0,3);
    const out = [];
    for(const [i,q] of qs.entries()){
      const right = q.answer;
      const wrong = ['A','B','C','D'].find(x=>x!==right);
      if(i===0){                        // 错两次再对 → 已攻克
        await record(q.id, wrong, false, 12000);
        await record(q.id, wrong, false, 31000);
        await record(q.id, right, true, 8000);
      } else if(i===1){                 // 只错一次 → 未攻克
        await record(q.id, wrong, false, 95400);
      } else {                          // 只对 → 不进错题本
        await record(q.id, right, true, 4100);
      }
      out.push(q.id);
    }
    return out;
  })()`);
  if (!Array.isArray(R.seeded) || R.seeded.length !== 3) throw new Error('seed failed: ' + JSON.stringify(R.seeded));
  log('seeded', R.seeded);

  // ── 2. 点「错题本」标签（真实点击路径）──
  R.beforeClick = await ev(`(()=>{
    const el = document.getElementById('tab-wrong');
    return {cls: el.className, disp: getComputedStyle(el).display};
  })()`);
  await ev(`document.querySelector('.tab[data-tab="wrong"]').click()`);
  await sleep(1200);
  R.afterClick = await ev(`(()=>{
    const el = document.getElementById('tab-wrong');
    const on = [...document.querySelectorAll('.tab.on')].map(x=>x.dataset.tab);
    return {cls: el.className, disp: getComputedStyle(el).display, onTab: on,
            rows: document.querySelectorAll('#wrongbox tbody tr').length,
            hasTable: !!document.querySelector('#wrongbox table'),
            header: (document.querySelector('#wrongbox .paperctl')||{}).textContent||''};
  })()`);

  // ── 3. 校验表格内容：应恰好 2 行（第 3 题只答对，不该进）──
  R.tableDetail = await ev(`(()=>{
    return [...document.querySelectorAll('#wrongbox tbody tr')].map(tr=>{
      const td=[...tr.children].map(x=>x.textContent.trim());
      return {qid:td[0], wtimes:td[5], right:td[6], elapsed:td[7], status:td[9]};
    });
  })()`);

  // ── 4. 只看未攻克 ──
  await ev(`(()=>{const c=document.getElementById('wUnsolved');c.checked=true;c.onchange({target:c});})()`);
  await sleep(900);
  R.unsolvedRows = await ev(`document.querySelectorAll('#wrongbox tbody tr').length`);
  R.unsolvedQids = await ev(`[...document.querySelectorAll('#wrongbox tbody tr')].map(t=>t.children[0].textContent.trim())`);

  // ── 5. 点「只练这些错题」→ 应切到题目页且列表=错题 ──
  await ev(`document.getElementById('wDrill').click()`);
  await sleep(900);
  R.drill = await ev(`(()=>{
    const on=[...document.querySelectorAll('.tab.on')].map(x=>x.dataset.tab);
    return {onTab:on, listLen:S.list.length, mode:S.mode,
            poolSize:S.wrongPool?S.wrongPool.size:0,
            firstId:S.list[0]?S.list[0].id:'',
            qbodyLen:(document.getElementById('qbody').innerHTML||'').length};
  })()`);

  // ── 6. 计时：S.qShownAt 应在切题时被设置 ──
  R.timer = await ev(`(()=>{
    const a = S.qShownAt;
    return {setAtRender: a>0, val: Math.round(a)};
  })()`);
  await ev(`next()`); await sleep(400);
  R.timerAfterNext = await ev(`Math.round(S.qShownAt)>0`);

  // ── 7. 页面错误 ──
  R.errors = await ev(`window.__ERR || []`);

  // 清理：删掉本次造的 record/srs
  R.cleaned = await ev(`fetch('/api/cleanup_test',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({qids:${JSON.stringify(R.seeded)}})})
      .then(r=>r.json()).then(d=>d.ok).catch(()=>'no-api')`);

  // ── 判定 ──
  const checks = [
    ['错题本点击前隐藏', R.beforeClick.disp === 'none'],
    ['错题本点击后显示', R.afterClick.disp !== 'none'],
    ['标签切换正确', JSON.stringify(R.afterClick.onTab) === '["wrong"]'],
    ['错题本表格已渲染', R.afterClick.hasTable === true],
    ['行数=2（只对的那题不入本）', R.afterClick.rows === 2],
    ['统计文案含错率', /错率/.test(R.afterClick.header)],
    ['未攻克标记正确', R.tableDetail.some(x => /未攻克/.test(x.status))],
    ['已攻克标记正确', R.tableDetail.some(x => /已攻克/.test(x.status))],
    ['错次统计正确(2/1)', R.tableDetail.some(x => x.wtimes === '2') && R.tableDetail.some(x => x.wtimes === '1')],
    ['耗时已显示', R.tableDetail.every(x => /秒|分/.test(x.elapsed))],
    ['只看未攻克=1 行', R.unsolvedRows === 1],
    ['只练错题切到题目页', JSON.stringify(R.drill.onTab) === '["q"]'],
    ['只练错题列表=1', R.drill.listLen === 1],
    ['模式=wrong', R.drill.mode === 'wrong'],
    ['计时起点已设置', R.timer.setAtRender === true],
    ['切题后重新计时', R.timerAfterNext === true],
    ['无页面 JS 错误', Array.isArray(R.errors) && R.errors.length === 0],
  ];
  console.log('\n=== 错题本 + 计时 CDP 回归 ===');
  for (const [n, ok] of checks) console.log(`  ${ok ? '✅' : '❌'} ${n}`);
  const bad = checks.filter(c => !c[1]).length;
  console.log(`\n${bad ? '❌ ' + bad + ' 项失败' : '✅ 全部 ' + checks.length + ' 项通过'}`);
  console.log('\n--- 明细 ---');
  console.log(JSON.stringify(R, null, 1).slice(0, 2600));
  done = true;
  process.exitCode = bad ? 3 : 0;
}

main().catch(e => { console.error('❌ 脚本错误:', e && e.message); process.exitCode = 9; })
  .finally(async () => {
    // 收尾时 socket 被销毁会让仍挂着的 waiter 变成 unhandled rejection，
    // 那会把已置好的 exitCode=0 顶成非 0，导致回归"看起来失败"。统一吞掉。
    if (cdp) { try { cdp.close(); } catch (e) { } }
    if (ch) { try { ch.kill(); } catch (e) { } }
  });

process.on('unhandledRejection', (e) => {
  if (done) return;                     // 测试已结束，收尾噪声，忽略
  console.error('❌ 未处理的 Promise 拒绝:', e && (e.message || e));
  process.exitCode = 9;
});
