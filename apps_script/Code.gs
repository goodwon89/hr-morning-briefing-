/**
 * 인재경영실 Morning Briefing — 운영 웹앱 (Google Apps Script)
 * ------------------------------------------------------------
 * 기능
 *   1) 기사 클릭 추적:  ?a=c  → 클릭 기록 후 원문으로 이동 (보안 스캐너의 자동 접속은 기록 안 함)
 *   2) 구독 취소:       ?a=u  → 확인 버튼을 눌러야 취소 (메일 보안 스캐너의 오작동 방지)
 *   3) 구독 신청:       ?a=s  → 신청서 입력 → '대기' 등록 + 관리자 메일
 *   4) 발송 시스템 API: POST {action:'list'|'log_send', key} → 수신자 목록 / 발송 기록
 *
 * 최초 1회: 편집기에서 setup 함수 실행 → 실행 로그의 '시트 URL'과 '비밀키'를 확인
 *           → 배포 > 새 배포 > 웹 앱 (실행: 나, 액세스: 모든 사용자)
 *
 * 개인정보: 클릭 기록에는 '누가' 눌렀는지 저장하지 않습니다(날짜·섹션·기사만).
 */

const ADMIN_EMAIL = 'jangkeunwon@gmail.com';
const NEWSLETTER = '인재경영실 Morning Briefing';
const TZ = 'Asia/Seoul';

const SHEET_SUBS = '구독자';
const SHEET_CLICKS = '클릭';
const SHEET_SENDS = '발송';
const SHEET_SUMMARY = '요약';

// ───────────────────────── 최초 설정 ─────────────────────────

function setup() {
  const props = PropertiesService.getScriptProperties();
  let sheetId = props.getProperty('SHEET_ID');
  let ss;
  if (sheetId) {
    ss = SpreadsheetApp.openById(sheetId);
  } else {
    ss = SpreadsheetApp.create('Morning Briefing 운영 데이터');
    props.setProperty('SHEET_ID', ss.getId());
  }
  ensureSheet_(ss, SHEET_SUBS, ['이메일', '상태', '경로', '변경일시', '메모']);
  ensureSheet_(ss, SHEET_CLICKS, ['클릭일시', '발행일', '섹션', '순번', '언론사·출처', '기사 제목', '원문 URL']);
  ensureSheet_(ss, SHEET_SENDS, ['발행일', '발송일시', '수신자 수', '기사 수', '큐레이션']);
  buildSummary_(ss);
  const first = ss.getSheetByName('Sheet1') || ss.getSheetByName('시트1');
  if (first && ss.getSheets().length > 1) ss.deleteSheet(first);

  if (!props.getProperty('SECRET')) {
    props.setProperty('SECRET', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  }
  Logger.log('시트 URL: ' + ss.getUrl());
  Logger.log('비밀키(TRACK_SECRET): ' + props.getProperty('SECRET'));
}

function ensureSheet_(ss, name, headers) {
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.appendRow(headers);
    sh.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#e6f6f6');
    sh.setFrozenRows(1);
  }
  return sh;
}

function buildSummary_(ss) {
  let sh = ss.getSheetByName(SHEET_SUMMARY);
  if (!sh) sh = ss.insertSheet(SHEET_SUMMARY, 0);
  sh.clear();
  const c = "'" + SHEET_CLICKS + "'!A2:G";
  const s = "'" + SHEET_SENDS + "'!A2:E";
  sh.getRange('A1').setValue('📊 Morning Briefing 운영 요약 (자동 계산)').setFontWeight('bold').setFontSize(13);
  sh.getRange('A3').setValue('섹션별 클릭 (전체 기간)').setFontWeight('bold');
  sh.getRange('A4').setFormula('=IFERROR(QUERY(' + c + ',"select C, count(A) where C is not null group by C order by count(A) desc label C \'섹션\', count(A) \'클릭수\'",0),"아직 데이터 없음")');
  sh.getRange('D3').setValue('발행일별 클릭률').setFontWeight('bold');
  sh.getRange('D4').setFormula('=IFERROR(QUERY({' + s + '},"select Col1, Col3 where Col1 is not null order by Col1 desc label Col1 \'발행일\', Col3 \'수신자\'",0),"아직 데이터 없음")');
  sh.getRange('F4').setValue('클릭수').setFontWeight('bold');
  sh.getRange('G4').setValue('수신자당 클릭').setFontWeight('bold');
  sh.getRange('F5').setFormula('=ARRAYFORMULA(IF(D5:D="",,COUNTIF(' + "'" + SHEET_CLICKS + "'!B2:B" + ',D5:D)))');
  sh.getRange('G5').setFormula('=ARRAYFORMULA(IF((D5:D="")+(E5:E=0),,ROUND(F5:F/E5:E,2)))');
  sh.getRange('I3').setValue('많이 읽은 기사 TOP 20').setFontWeight('bold');
  sh.getRange('I4').setFormula('=IFERROR(QUERY(' + c + ',"select F, C, count(A) where F is not null group by F, C order by count(A) desc limit 20 label F \'기사\', C \'섹션\', count(A) \'클릭\'",0),"아직 데이터 없음")');
  sh.setColumnWidth(9, 420);
}

// ───────────────────────── 공통 ─────────────────────────

function props_() { return PropertiesService.getScriptProperties(); }
function ss_() { return SpreadsheetApp.openById(props_().getProperty('SHEET_ID')); }
function now_() { return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd HH:mm:ss'); }

function sign_(value) {
  const raw = Utilities.computeHmacSha256Signature(String(value), props_().getProperty('SECRET'));
  return raw.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('').slice(0, 20);
}

function esc_(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function page_(title, bodyHtml) {
  const html = '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"><base target="_top">' +
    '<style>body{font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",sans-serif;background:#f7f9fa;margin:0;color:#1a1a2a}' +
    '.box{max-width:440px;margin:60px auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:28px 26px}' +
    'h1{font-size:18px;margin:0 0 6px}p{font-size:14px;line-height:1.6;color:#4b5563}' +
    'input{width:100%;box-sizing:border-box;padding:11px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;margin:8px 0}' +
    'button{background:#00A7A7;color:#fff;border:0;border-radius:8px;padding:11px 18px;font-size:14px;font-weight:700;cursor:pointer;width:100%}' +
    'button.gray{background:#f3f4f6;color:#374151}.muted{font-size:12px;color:#9ca3af}</style></head><body><div class="box">' +
    '<div class="muted">' + NEWSLETTER + '</div>' + bodyHtml + '</div></body></html>';
  return HtmlService.createHtmlOutput(html).setTitle(title)
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ───────────────────────── GET 라우팅 ─────────────────────────

function doGet(e) {
  const p = (e && e.parameter) || {};
  try {
    if (p.a === 'c') return clickPage_(p);
    if (p.a === 'u') return unsubscribePage_(p);
    if (p.a === 's') return subscribePage_();
  } catch (err) {
    console.error(err);
  }
  return page_('Morning Briefing', '<h1>요청을 처리할 수 없습니다</h1><p>링크가 올바른지 확인해 주세요.</p>');
}

// 1) 클릭 추적 — 서명된 URL만 이동 (임의 사이트로의 우회 이동 방지)
function clickPage_(p) {
  const url = String(p.u || '');
  if (!/^https?:\/\//.test(url) || sign_(url) !== p.h) {
    return page_('잘못된 링크', '<h1>잘못된 링크입니다</h1><p>메일의 링크를 다시 눌러 주세요.</p>');
  }
  const payload = JSON.stringify({ d: p.d || '', s: p.s || '', i: p.i || '', src: p.src || '', t: p.t || '', u: url });
  // 실제 브라우저에서 스크립트가 실행될 때만 기록 → 메일 보안 스캐너의 자동 접속은 집계되지 않음
  const body =
    '<h1>기사로 이동 중…</h1><p>잠시만 기다려 주세요. 이동하지 않으면 <a href="' + esc_(url) + '">여기</a>를 누르세요.</p>' +
    '<script>var done=false;function go(){if(done)return;done=true;window.top.location.replace(' + JSON.stringify(url) + ');}' +
    'google.script.run.withSuccessHandler(go).withFailureHandler(go).logClick(' + payload + ');setTimeout(go,1500);</script>';
  return page_('기사로 이동', body);
}

function logClick(c) {
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    ss_().getSheetByName(SHEET_CLICKS).appendRow([now_(), c.d, c.s, c.i, c.src, c.t, c.u]);
  } finally {
    lock.releaseLock();
  }
  return true;
}

// 2) 구독 취소 — 확인 버튼 필요
function unsubscribePage_(p) {
  const email = String(p.e || '').trim().toLowerCase();
  if (!email || sign_(email) !== p.h) {
    return page_('잘못된 링크', '<h1>잘못된 링크입니다</h1><p>메일 하단의 구독 취소 링크를 다시 눌러 주세요.</p>');
  }
  const body =
    '<div id="ask"><h1>구독을 취소할까요?</h1><p><b>' + esc_(email) + '</b> 주소로 더 이상 브리핑을 보내지 않습니다.</p>' +
    '<button id="b">구독 취소</button></div>' +
    '<div id="done" style="display:none"><h1>구독이 취소되었습니다</h1>' +
    '<p>다음 발송부터 메일이 가지 않습니다. 다시 받으시려면 구독 신청을 해 주세요.</p></div>' +
    '<p class="muted" id="m"></p>' +
    '<script>' +
    'var E=' + JSON.stringify(email) + ',H=' + JSON.stringify(String(p.h)) + ';' +
    'document.getElementById("b").addEventListener("click",function(){' +
    'this.disabled=true;google.script.run' +
    '.withSuccessHandler(function(){document.getElementById("ask").style.display="none";document.getElementById("done").style.display="block";})' +
    '.withFailureHandler(function(){document.getElementById("m").textContent="처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";})' +
    '.unsubscribe(E,H);});' +
    '</script>';
  return page_('구독 취소', body);
}

function unsubscribe(email, h) {
  email = String(email || '').trim().toLowerCase();
  if (!email || sign_(email) !== h) throw new Error('invalid');
  setStatus_(email, 'unsubscribed', '구독취소 링크');
  return true;
}

// 3) 구독 신청 — 관리자 승인 후 발송 대상에 포함
function subscribePage_() {
  const body =
    '<div id="ask"><h1>구독 신청</h1><p>받으실 이메일 주소를 입력해 주세요. 담당자 확인 후 다음 발송부터 받으실 수 있습니다.</p>' +
    '<input id="e" type="email" placeholder="name@company.com" autocomplete="email">' +
    '<button id="b">신청하기</button></div>' +
    '<div id="done" style="display:none"><h1>신청되었습니다</h1><p>담당자 확인 후 다음 발송부터 받으실 수 있습니다.</p></div>' +
    '<p class="muted" id="m"></p>' +
    '<script>' +
    'document.getElementById("b").addEventListener("click",function(){' +
    'var v=document.getElementById("e").value.trim();' +
    'if(!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(v)){document.getElementById("m").textContent="이메일 주소를 확인해 주세요.";return;}' +
    'var btn=this;btn.disabled=true;google.script.run' +
    '.withSuccessHandler(function(){document.getElementById("ask").style.display="none";document.getElementById("done").style.display="block";})' +
    '.withFailureHandler(function(){btn.disabled=false;document.getElementById("m").textContent="처리 중 오류가 발생했습니다.";})' +
    '.subscribe(v);});' +
    '</script>';
  return page_('구독 신청', body);
}

function subscribe(email) {
  email = String(email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error('invalid email');
  const current = findRow_(email);
  if (current && current.status === 'active') return true;
  setStatus_(email, 'pending', '구독신청 페이지');
  MailApp.sendEmail(ADMIN_EMAIL, '[Morning Briefing] 구독 신청: ' + email,
    email + ' 님이 구독을 신청했습니다.\n승인하려면 운영 시트 \'구독자\' 탭에서 상태를 pending → active 로 바꿔 주세요.\n' + ss_().getUrl());
  return true;
}

// ───────────────────────── POST (발송 시스템 전용) ─────────────────────────

function doPost(e) {
  const p = (e && e.parameter) || {};
  // RFC 8058 원클릭 구독 취소 (메일 앱의 '구독 취소' 버튼)
  if (p.a === 'u') {
    try { unsubscribe(p.e, p.h); } catch (err) { /* 무시 */ }
    return ContentService.createTextOutput('ok');
  }
  let body = {};
  try { body = JSON.parse((e.postData && e.postData.contents) || '{}'); } catch (err) { body = {}; }
  if (!body.key || body.key !== props_().getProperty('SECRET')) return json_({ ok: false, error: 'unauthorized' });

  if (body.action === 'list') {
    const rows = readSubs_();
    return json_({
      ok: true,
      active: rows.filter(function (r) { return r.status === 'active'; }).map(function (r) { return r.email; }),
      unsubscribed: rows.filter(function (r) { return r.status === 'unsubscribed'; }).map(function (r) { return r.email; })
    });
  }
  if (body.action === 'log_send') {
    ss_().getSheetByName(SHEET_SENDS).appendRow([body.date, now_(), body.recipients, body.articles, body.curation || '']);
    return json_({ ok: true });
  }
  return json_({ ok: false, error: 'unknown action' });
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

// ───────────────────────── 구독자 시트 ─────────────────────────

function readSubs_() {
  const values = ss_().getSheetByName(SHEET_SUBS).getDataRange().getValues();
  const out = [];
  for (let i = 1; i < values.length; i++) {
    const email = String(values[i][0] || '').trim().toLowerCase();
    if (email) out.push({ row: i + 1, email: email, status: String(values[i][1] || '').trim().toLowerCase() });
  }
  return out;
}

function findRow_(email) {
  const rows = readSubs_();
  for (let i = 0; i < rows.length; i++) if (rows[i].email === email) return rows[i];
  return null;
}

function setStatus_(email, status, source) {
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    const sh = ss_().getSheetByName(SHEET_SUBS);
    const found = findRow_(email);
    if (found) {
      sh.getRange(found.row, 2, 1, 3).setValues([[status, source, now_()]]);
    } else {
      sh.appendRow([email, status, source, now_(), '']);
    }
  } finally {
    lock.releaseLock();
  }
}
