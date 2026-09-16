// 시연 리허설 — 평가자가 브라우저로 하는 그대로:
//   S0 비로그인 차단 → S1 회원가입 → S1b 중복 가입 → S2a 틀린 비밀번호 → S2b 로그인
//   → S3 질문 1 → S4 질문 2(문맥) → S5 공백 입력 → S6 /logs → S7 /api/me/chats
//   → S9 로그아웃 → S8 둘째 계정엔 기록 없음 → S9b 다시 차단
//
// 실행 (Aside 앱이 떠 있어야 한다. 계정은 매번 새로 만든다):
//   aside repl "$(sed 's#__BASE_URL__#https://b7-ai-chatbot-dev.vercel.app#' tests/e2e/demo_flow.js)"
//
// 단계마다 PASS/FAIL 을 출력하고, FAIL 이면 그 자리에서 멈춘다.
// pytest 가 아니라 브라우저 REPL 스크립트다 — 서버 코드는 한 줄도 건드리지 않는다.

const BASE = '__BASE_URL__';
const AI_WAIT_MS = 25000;   // 타임아웃 10초 + 재시도 1회 + 여유
const NAV_WAIT_MS = 15000;

const now = new Date();
const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
const pad = (n) => String(n).padStart(2, '0');
const stamp = `${pad(kst.getUTCMonth() + 1)}${pad(kst.getUTCDate())}-${pad(kst.getUTCHours())}${pad(kst.getUTCMinutes())}`;
const TODAY_KST = `${pad(kst.getUTCMonth() + 1)}/${pad(kst.getUTCDate())}`;
const USER1 = `demo-${stamp}`;          // 3~20자
const USER2 = `demo2-${stamp}`;
const PASSWORD = 'Demo-pass-2026!';
const WRONG_PASSWORD = 'wrong-pass-0000';
const Q1 = '파이썬에서 IndexError 는 왜 나요?';
const Q2 = '방금 내가 물어본 게 뭐였지?';

const results = [];
function pass(id, msg) { results.push(`${id} PASS`); console.log('PASS', id, msg || ''); }
async function fail(id, msg, p) {
  let ctx = '';
  try {
    ctx = await p.evaluate(`JSON.stringify({ path: location.pathname, title: document.title,
      notice: (document.querySelector('#auth-notice') || document.querySelector('#notice') || {}).textContent || '' })`);
  } catch (e) { ctx = '(context unavailable)'; }
  results.push(`${id} FAIL`);
  console.log('FAIL', id, msg, ctx);
  console.log('SUMMARY', results.join(' | '));
  throw new Error('stopped at ' + id);
}
async function until(p, expr, ms, every) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.evaluate(expr);
    if (v) return v;
    await sleep(every || 500);
  }
  return null;
}
async function open(path) {
  const p = await openTab(BASE + path);
  // openTab 은 리다이렉트가 끝나기 전에 돌아올 수 있다 (about:blank 상태). 실제 로드 완료까지 기다린다.
  await until(p, `location.href !== 'about:blank' && document.readyState === 'complete'`, NAV_WAIT_MS, 300);
  return p;
}
const pathOf = (p) => p.evaluate('location.pathname');
async function goto(p, path) {
  await p.evaluate(`location.href = ${JSON.stringify(path)}`);
  const ok = await until(p, `document.readyState === 'complete' && location.pathname === ${JSON.stringify(path)}`, NAV_WAIT_MS);
  return !!ok;
}
async function fillAuth(p, username, password) {
  // /login 과 /register 가 같은 필드 id 를 쓰므로 경로 판정만으론 이동 완료를 못 가른다. 필드가 붙을 때까지 기다린다.
  if (!(await until(p, "!!document.querySelector('#username') && !!document.querySelector('#password') && !!document.querySelector('form')", NAV_WAIT_MS, 200))) throw new Error('auth form not ready at ' + (await p.evaluate('location.href')));
  await p.locator('#username').fill(username);
  await p.locator('#password').fill(password);
  // 버튼 좌표 클릭은 브라우저 확장의 '비밀번호 저장' 배너에 가려질 수 있다.
  // requestSubmit 은 앱의 submit 핸들러(검증 → fetch)를 그대로 태운다.
  await p.evaluate("document.querySelector('form').requestSubmit()");
}
async function ask(p, question) {
  if (!(await until(p, "!!document.querySelector('#message') && !!document.querySelector('#composer')", NAV_WAIT_MS, 200))) throw new Error('composer not ready');
  const before = await p.evaluate('document.querySelectorAll(".entry").length');
  await p.locator('#message').fill(question);
  await p.evaluate("document.querySelector('#composer').requestSubmit()");
  const settled = await until(p, `(() => {
    const es = document.querySelectorAll('.entry');
    if (es.length !== ${before} + 1) return null;
    const e = es[es.length - 1];
    if (e.classList.contains('is-pending')) return null;
    const a = e.querySelector('.answer');
    return JSON.stringify({ answer: a.textContent, isError: a.classList.contains('is-error'),
      meta: e.querySelector('.entry-meta') ? e.querySelector('.entry-meta').textContent : '' });
  })()`, AI_WAIT_MS, 1000);
  return settled ? JSON.parse(settled) : null;
}

// ── S0 비로그인 차단 ─────────────────────────────────────────────
let p = await open('/chat');
if ((await pathOf(p)) !== '/login') await fail('S0', '비로그인 /chat 이 /login 으로 가지 않음', p);
const me0 = await p.evaluate(`fetch('/api/me').then(r => r.json().then(j => JSON.stringify({ status: r.status, code: j.error_code })))`);
if (me0 !== JSON.stringify({ status: 401, code: 'NOT_AUTHENTICATED' })) await fail('S0', '/api/me 가 401 NOT_AUTHENTICATED 가 아님: ' + me0, p);
pass('S0', '/chat→/login, /api/me 401');

// ── S1 회원가입 ──────────────────────────────────────────────────
if (!(await goto(p, '/register'))) await fail('S1', '/register 로 이동 실패', p);
await fillAuth(p, USER1, PASSWORD);
if (!(await until(p, `location.pathname === '/login'`, NAV_WAIT_MS))) await fail('S1', '가입 후 /login 으로 가지 않음', p);
pass('S1', `${USER1} 가입 → /login`);

// ── S1b 중복 가입 ────────────────────────────────────────────────
if (!(await goto(p, '/register'))) await fail('S1b', '/register 재이동 실패', p);
await fillAuth(p, USER1, PASSWORD);
const dup = await until(p, `document.querySelector('#auth-notice').textContent.trim() || null`, NAV_WAIT_MS);
if (dup !== '이미 사용 중인 아이디입니다.') await fail('S1b', '중복 안내 문구 불일치: ' + dup, p);
pass('S1b', '중복 가입 안내');

// ── S2a 틀린 비밀번호 ────────────────────────────────────────────
if (!(await goto(p, '/login'))) await fail('S2a', '/login 이동 실패', p);
await fillAuth(p, USER1, WRONG_PASSWORD);
const bad = await until(p, `document.querySelector('#auth-notice').textContent.trim() || null`, NAV_WAIT_MS);
if (bad !== '아이디 또는 비밀번호가 올바르지 않습니다.') await fail('S2a', '로그인 실패 문구 불일치: ' + bad, p);
if ((await pathOf(p)) !== '/login') await fail('S2a', '실패했는데 페이지가 바뀜', p);
pass('S2a', '틀린 비밀번호 안내');

// ── S2b 로그인 ───────────────────────────────────────────────────
await fillAuth(p, USER1, PASSWORD);
if (!(await until(p, `location.pathname === '/chat' && document.readyState === 'complete'`, NAV_WAIT_MS))) await fail('S2b', '로그인 후 /chat 으로 가지 않음', p);
const shown = await p.evaluate(`document.body.innerText.includes(${JSON.stringify(USER1)})`);
if (!shown) await fail('S2b', '화면에 사용자명이 없음', p);
pass('S2b', '로그인 → /chat, 사용자명 표시');

// ── S3 질문 1 ────────────────────────────────────────────────────
const a1 = await ask(p, Q1);
if (!a1) await fail('S3', `${AI_WAIT_MS}ms 안에 답변이 오지 않음`, p);
if (a1.isError) await fail('S3', '오류 답변: ' + a1.answer, p);
if (a1.answer === '답변을 만들고 있습니다…' || a1.answer.length < 10) await fail('S3', '답변 본문이 비었음: ' + a1.answer, p);
if (!/\d+ms/.test(a1.meta)) await fail('S3', '소요 시간(ms)이 표시되지 않음: ' + a1.meta, p);
pass('S3', `답변 ${a1.answer.length}자, ${a1.meta.trim()}`);

// ── S4 질문 2 — 문맥 ────────────────────────────────────────────
const a2 = await ask(p, Q2);
if (!a2) await fail('S4', '답변이 오지 않음', p);
if (a2.isError) await fail('S4', '오류 답변: ' + a2.answer, p);
if (!/IndexError/i.test(a2.answer)) await fail('S4', '직전 질문(IndexError)을 이어받지 못함: ' + a2.answer.slice(0, 120), p);
pass('S4', '문맥 동작 — 답변이 IndexError 를 언급');

// ── S5 공백 입력 ─────────────────────────────────────────────────
const entriesBefore = await p.evaluate('document.querySelectorAll(".entry").length');
await p.locator('#message').fill('   ');
await p.evaluate("document.querySelector('#composer').requestSubmit()");
await sleep(700);
const notice5 = await p.evaluate(`document.querySelector('#notice').textContent.trim()`);
const entriesAfter = await p.evaluate('document.querySelectorAll(".entry").length');
if (notice5 !== '질문을 입력해 주세요.') await fail('S5', '공백 안내 문구 불일치: ' + notice5, p);
if (entriesAfter !== entriesBefore) await fail('S5', '공백인데 요청이 나감', p);
pass('S5', '공백 입력 차단');

// ── S6 /logs ─────────────────────────────────────────────────────
if (!(await goto(p, '/logs'))) await fail('S6', '/logs 이동 실패', p);
const logs = JSON.parse(await p.evaluate(`JSON.stringify([...document.querySelectorAll('.entry')].map(e => ({
  ask: e.querySelector('.ask').textContent.trim(), meta: e.querySelector('.entry-meta').textContent.trim(),
  err: !!e.querySelector('.is-error') })))`));
if (logs.length !== 2) await fail('S6', `기록이 2건이 아님: ${logs.length}`, p);
if (logs[0].ask !== Q2) await fail('S6', '최신순이 아님: 첫 항목 = ' + logs[0].ask, p);
if (logs.some((l) => l.err)) await fail('S6', '오류 항목이 있음', p);
if (!logs.every((l) => /\d+ms/.test(l.meta))) await fail('S6', 'ms 표시 누락', p);
if (!logs.every((l) => l.meta.includes(TODAY_KST))) await fail('S6', `KST 오늘(${TODAY_KST})이 아님: ` + logs.map((l) => l.meta).join(' / '), p);
pass('S6', '/logs 2건 최신순, KST 시각');

// ── S7 /api/me/chats ─────────────────────────────────────────────
const api = JSON.parse(await p.evaluate(`fetch('/api/me/chats?limit=20').then(r => r.json()).then(j => JSON.stringify(j))`));
if (!Array.isArray(api) || api.length !== 2) await fail('S7', '배열 2건이 아님: ' + JSON.stringify(api).slice(0, 200), p);
if (!api.every((it) => it.status === 'success' && it.chat_id && it.request_id)) await fail('S7', '항목 형식 불일치: ' + JSON.stringify(api[0]), p);
if (api[0].question !== Q2) await fail('S7', 'API 도 최신순이어야 함', p);
pass('S7', `chat_id ${api.map((it) => it.chat_id).join(', ')}`);

// ── S9 로그아웃 ──────────────────────────────────────────────────
await p.evaluate("document.getElementById('logout').click()");
if (!(await until(p, `location.pathname === '/login'`, NAV_WAIT_MS))) await fail('S9', '로그아웃 후 /login 으로 가지 않음', p);
const p2 = await open('/chat');
if ((await pathOf(p2)) !== '/login') await fail('S9', '로그아웃 뒤에도 /chat 접근됨', p2);
const me9 = await p2.evaluate(`fetch('/api/me').then(r => r.status)`);
if (me9 !== 401) await fail('S9', '로그아웃 뒤 /api/me 가 401 이 아님: ' + me9, p2);
pass('S9', '로그아웃 → 차단');
await closeTab(p);

// ── S8 둘째 계정 — 남의 기록이 안 보임 ───────────────────────────
if (!(await goto(p2, '/register'))) await fail('S8', '/register 이동 실패', p2);
await fillAuth(p2, USER2, PASSWORD);
if (!(await until(p2, `location.pathname === '/login'`, NAV_WAIT_MS))) await fail('S8', '둘째 계정 가입 실패', p2);
await fillAuth(p2, USER2, PASSWORD);
if (!(await until(p2, `location.pathname === '/chat'`, NAV_WAIT_MS))) await fail('S8', '둘째 계정 로그인 실패', p2);
if (!(await goto(p2, '/logs'))) await fail('S8', '/logs 이동 실패', p2);
const empty = await p2.evaluate(`JSON.stringify({ entries: document.querySelectorAll('.entry').length,
  empty: (document.querySelector('.empty') || {}).textContent || '' })`);
if (empty !== JSON.stringify({ entries: 0, empty: '아직 기록이 없습니다.' })) await fail('S8', '둘째 계정에 기록이 보이거나 안내 문구가 다름: ' + empty, p2);
pass('S8', `${USER2} 에는 기록 없음`);

// ── S9b 정리 ─────────────────────────────────────────────────────
await p2.evaluate("document.getElementById('logout').click()");
if (!(await until(p2, `location.pathname === '/login'`, NAV_WAIT_MS))) await fail('S9b', '둘째 계정 로그아웃 실패', p2);
pass('S9b', '둘째 계정 로그아웃');
await closeTab(p2);

console.log('SUMMARY', results.join(' | '));
console.log('ACCOUNTS', USER1, USER2);
