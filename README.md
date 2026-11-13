# 인재경영실 Morning Briefing

평일 아침 Google News에서 HR·AI·거시·투자·혁신 뉴스를 모아 메일로 보내고, GitHub Pages에 아카이브를 남기는 자동화입니다.

## 구성
| 파일 | 역할 |
|---|---|
| `hr_news.py` | 수집 → (선택) AI 선별 → 메일 발송 → 아카이브 저장 |
| `.github/workflows/hr_news.yml` | 실행 스케줄·트리거 |
| `data/weekly.json` | 메일 상단 '이주의 HR 데이터' (이 파일만 고치면 반영) |
| `archive/YYYY-MM.json` | 월별 발송 기록 (전체 보관, 아카이브 페이지가 읽음) |
| `index.html` | GitHub Pages 아카이브 페이지 |

## 발송 규칙
- 평일 06:30(KST)경 발송. 05:47에 1차 실행 후 06:30까지 대기, 07:17에 2차 실행(1차 누락 대비)
- 같은 날은 한 번만 발송 (오늘자 아카이브가 있으면 다음 실행은 바로 종료)
- 주말·한국 공휴일(대체공휴일·선거일 포함)은 발송 안 함
- 기사가 15건 미만이거나 오류가 나면 발송하지 않고 관리자에게 경고 메일 + 워크플로 실패 표시

## 설정 (Settings → Secrets and variables → Actions)
| 이름 | 필수 | 설명 |
|---|---|---|
| `GMAIL_USER`, `GMAIL_APP_PASS` | ✅ | 발신 Gmail과 앱 비밀번호 |
| `EMAIL_RECIPIENTS` | ✅ | 수신자, 쉼표로 구분 (90명 단위로 나눠 발송) |
| `ANTHROPIC_API_KEY` | 선택 | 넣으면 AI가 관련도 높은 기사를 고르고 1줄 시사점(▸)을 붙임. 없거나 실패하면 규칙 기반으로 자동 진행 |
| `ANTHROPIC_MODEL` (Variables) | 선택 | 기본값 `claude-haiku-5-5` |

## 운영 웹앱 (클릭 추적·구독 관리)
`apps_script/Code.gs`를 Google Apps Script 웹앱으로 배포해 연결합니다. 연결하지 않으면 기존 방식 그대로 동작합니다.
- **클릭 추적**: 메일 링크 → `r.html`(GitHub Pages) → 웹앱이 서명 확인·기록 → 원문 이동. 기사·리포트 클릭을 '운영 데이터' 시트에 기록 (누가 눌렀는지는 저장하지 않음, 메일 보안 스캐너의 자동 접속은 제외)
- **요약 탭**: 섹션별 클릭, 발행일별 수신자당 클릭, 많이 읽은 기사 TOP 20 자동 계산
- **구독 취소**: 수신자별 링크 → 확인 버튼 → 시트에 `unsubscribed` 기록 → 다음 발송부터 제외 (Gmail '구독 취소' 버튼도 지원)
- **구독 신청**: 신청 페이지 → 시트에 `pending` + 관리자 메일 → 상태를 `active`로 바꾸면 다음 발송부터 포함
- 발송 명단 = `EMAIL_RECIPIENTS` + 시트 `active` − 시트 `unsubscribed`

연결 설정: Secrets `TRACK_SECRET`(setup 실행 로그의 비밀키). 웹앱 주소는 코드 기본값(`DEFAULT_TRACK_URL`)이며 Variables `TRACK_URL`로 바꿀 수 있음. 비밀키가 없으면 기존 방식으로 동작

## 수동 실행·테스트
Actions → "인재경영실 Morning Briefing 자동 발송" → Run workflow
- `dry_run` 체크(기본): 발신 계정에만 `[TEST]` 메일, 아카이브 저장 안 함
- `force_send` 체크: 공휴일·중복 확인 없이 전체 수신자에게 즉시 발송

## 정시 발송용 외부 트리거 (설정 완료)
GitHub 예약 실행은 지연·누락될 수 있어 cron-job.org에서도 호출합니다. 여러 번 실행돼도 하루 한 번만 발송됩니다.
- cron-job.org 작업: "Morning Briefing 정시 발송 (평일 06:25)", 평일 06:25 Asia/Seoul
- 호출: `POST https://api.github.com/repos/goodwon89/hr-morning-briefing-/actions/workflows/hr_news.yml/dispatches`
  - Body: `{"ref":"main","inputs":{"dry_run":"false","force_send":"false"}}`
  - Headers: `Content-Type: application/json`, `Accept: application/vnd.github+json`, `Authorization: Bearer <토큰>`
- 토큰: GitHub fine-grained token `morning-briefing-cron` (이 저장소, Actions 읽기·쓰기만) — **2027-11-12 만료, 만료 전 재발급 후 cron-job.org 헤더 교체**

## 품질 필터 조정
`hr_news.py` 상단의 `BLOCK_SOURCES`(차단 언론사), `BLOCK_TITLE_PATTERNS`(지자체·정치·인물동정 등), `SECTION_BLOCK_WORDS`, `QUERIES`를 수정합니다.
