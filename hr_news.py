"""
상상인그룹 인재경영실 Morning Briefing - 자동화 뉴스레터 (v2)
================================================================
흐름: 발송일 판정 → 중복발송 확인 → 발송 시각 대기 → 수집 → (선택) AI 큐레이션
      → 메일 발송 → 월별 아카이브 저장(archive/YYYY-MM.json)

v2 변경 요약
  - 발송 안정성: 공휴일·주말 자동 생략, 하루 1회만 발송(이중 트리거 대비), 06:30 이전 도착 방지
  - 실패 알림: 수집 건수 미달·예외 발생 시 관리자 메일 + 워크플로 실패 처리
  - 수집 품질: Google 검색 when:2d 적용, 쿼리 순환 선발(주제 쏠림 방지), 출처·잡기사 필터,
              섹션 간/최근 7일 유사 기사 중복 제거, 제목 끝 언론사명 정확히 제거
  - AI 큐레이션(선택): ANTHROPIC_API_KEY가 있으면 후보 중 관련도 높은 기사 선별 + 1줄 시사점
  - 아카이브: 7일 삭제 → 월별 파일로 전체 보관
  - 이주의 HR 데이터: 코드에서 분리 → data/weekly.json 만 수정하면 반영

환경변수
  GMAIL_USER, GMAIL_APP_PASS, EMAIL_RECIPIENTS   (필수)
  ANTHROPIC_API_KEY, ANTHROPIC_MODEL             (선택: AI 큐레이션)
  DRY_RUN=true   → 발신 계정(GMAIL_USER)에게만 [TEST] 메일, 아카이브 저장 안 함
  FORCE_SEND=true → 공휴일·중복·대기 조건 무시하고 즉시 발송
"""

import os, re, sys, json, time, smtplib, functools, html as html_lib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import feedparser
import requests

try:
    import holidays
except ImportError:  # 라이브러리 없으면 공휴일 판정만 생략
    holidays = None

print = functools.partial(print, flush=True)  # GitHub Actions 로그 실시간 출력

# ──────────────────────────────────────────────────────────────
# 1. 기본 설정
# ──────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

ARCHIVE_DIR       = "archive"
ARCHIVE_INDEX     = f"{ARCHIVE_DIR}/index.json"
WEEKLY_DATA_FILE  = "data/weekly.json"
RECENT_DAYS       = 7        # 최근 N일 기사와 중복 금지
NEWS_MAX_AGE_DAYS = 3        # 발행 후 N일 이내 기사만
FALLBACK_MAX_AGE  = 7        # 건수 부족 시 완화 기준
MIN_TOTAL         = 15       # 이 건수 미만이면 발송하지 않고 실패 알림
SEND_NOT_BEFORE   = (6, 30)  # 이 시각(KST) 이전에 실행되면 대기 후 발송
MAX_WAIT_MIN      = 75       # 최대 대기 시간(분)
SMTP_BATCH        = 90       # Gmail SMTP 메시지당 수신자 한도(100) 이하로 분할

NEWSLETTER_TITLE  = "인재경영실 Morning Briefing"
NEWSLETTER_SUBTLT = "Top Trends to Start Your Day"
EMAIL_FROM_NAME   = "상상인그룹 인재경영실"

# ─── 버튼 링크 ───
INTRO_URL         = "https://ssihr.oopy.io"           # 인재경영실 소개
ADMIN_EMAIL       = "jangkeunwon@gmail.com"            # 구독 신청·취소·실패 알림 수신
SUBSCRIBE_SUBJ    = quote("인재경영실 Morning Briefing 구독 신청")
UNSUBSCRIBE_SUBJ  = quote("인재경영실 Morning Briefing 구독 취소")

_FEEDBACK_SUBJ_RAW = "인재경영실 Morning Briefing 피드백"
_FEEDBACK_BODY_RAW = (
    "[인재경영실 Morning Briefing 피드백]\n"
    "─────────────────────────────\n\n"
    "◾ 오늘 레터 발행일 (예: 2026-04-07):\n\n"
    "◾ 전반적인 만족도 (1~5점, 5점 최고):\n\n"
    "◾ 가장 유익했던 카테고리 (해당 항목에 V 표시):\n"
    "   □ HR   □ AI/기술   □ 거시/산업   □ 투자/M&A   □ 혁신 생태계\n\n"
    "◾ 개선 제안 또는 추가되길 원하는 콘텐츠:\n\n\n"
    "◾ 자유 의견:\n\n\n"
    "─────────────────────────────\n"
    "본 피드백은 레터 콘텐츠 개선에 활용됩니다. 감사합니다."
)
FEEDBACK_SUBJ = quote(_FEEDBACK_SUBJ_RAW)
FEEDBACK_BODY = quote(_FEEDBACK_BODY_RAW)

# ─── AI 큐레이션 ───
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-5-5"
GROUP_CONTEXT = (
    "상상인그룹은 금융(저축은행·증권 등), 바이오, 네트워크/IT 분야 9개 계열사를 둔 그룹이다. "
    "독자는 그룹 오너·계열사 경영진·HR 담당자이며, 실용적이고 데이터에 근거한 의사결정을 중시한다."
)

# ──────────────────────────────────────────────────────────────
# 2. 카테고리별 목표 건수
# ──────────────────────────────────────────────────────────────
TARGET = {
    "hr":               6,
    "ai_tech":          4,
    "macro_industry":   4,
    "invest_ma":        4,
    "innovation":       4,
}
TOTAL_TARGET = sum(TARGET.values())  # 22

# ──────────────────────────────────────────────────────────────
# 3. 카테고리별 검색 쿼리 (기간 조건 when:Nd 는 코드에서 자동 추가)
# ──────────────────────────────────────────────────────────────
QUERIES = {
    "hr": [
        # 1. 채용·인재 확보
        "(채용 OR 신규채용 OR 경력채용 OR 인재확보 OR 채용시장) (기업 OR 대기업 OR 스타트업) -정치 -선거",
        # 2. 보상·성과급·연봉
        "(성과급 OR 연봉 OR 임금 OR 보상체계 OR RSU OR 스톡옵션) (기업 OR 직장인 OR 인상 OR 격차) -정치",
        # 3. 조직문화·직장환경
        "(조직문화 OR 기업문화 OR 직장환경 OR 워라밸 OR 직원경험) (사례 OR 혁신 OR 변화 OR 조사) -정치",
        # 4. 인사제도·성과평가
        "(인사제도 OR 성과평가 OR 인사고과 OR 인사관리 OR HR제도) (개편 OR 도입 OR 혁신 OR 트렌드) -정치 -장관",
        # 5. 이직·퇴사·구조조정
        "(이직 OR 퇴사 OR 구조조정 OR 희망퇴직 OR 감원 OR 권고사직) (기업 OR 증가 OR 트렌드 OR 대규모) -정치",
        # 6. 근무제도·유연근무
        "(유연근무 OR 재택근무 OR 주4일제 OR 하이브리드근무 OR 원격근무) (기업 OR 도입 OR 확산 OR 효과) -정치",
        # 7. 노동법·최저임금·임단협
        "(근로기준법 OR 최저임금 OR 임단협 OR 중대재해처벌법) (개정 OR 인상 OR 타결 OR 판결) -정치 -여야",
        # 8. 임원인사·조직개편·세대교체
        "(임원인사 OR 조직개편 OR 인사발령 OR 세대교체 OR 대표이사) (기업 OR 대기업 OR 그룹) -청문 -대통령",
        # 9. HR 기술·피플애널리틱스·AI 인사
        "(HR OR 인사) (AI OR 데이터분석 OR 피플애널리틱스 OR SaaS OR 솔루션) (도입 OR 활용 OR 혁신) -테마주",
        # 10. 직장인 트렌드·MZ·복지
        "(직장인 OR 임직원 OR MZ세대 OR 2030) (복지 OR 트렌드 OR 설문 OR 인식 OR 워크스타일) 기업 -정치",
    ],
    "ai_tech": [
        "(AI OR 인공지능 OR 생성형AI OR 챗GPT OR 클로드) (도입 OR 업무활용 OR 생산성 OR 혁신 OR 사례) -정치 -테마주",
        "(디지털전환 OR DX OR 클라우드 OR SaaS) (기업전환 OR 인프라 OR B2B OR 도입) -공공기관 -선거",
        "(로봇 OR RPA OR AI에이전트) (자동화 OR 제조 OR 업무혁신 OR 효율화) -주식 -종목추천",
    ],
    "macro_industry": [
        "(거시경제 OR 경제지표 OR 소비자물가 OR 환율 OR 금리) (동향 OR 전망 OR 통계 OR 발표) -정치 -선거",
        "(부동산 OR 건설업 OR 주택시장) (시장동향 OR 전망 OR 지표 OR PF OR 통계) -사업설명회 -분양 -청약 -견본주택 -단지",
        "(수출 OR 제조업 OR 반도체 OR 배터리) (경기동향 OR 실적 OR 지표 OR 사이클) -주가 -종목추천",
        "(미국 OR 중국 OR 글로벌) (경제정책 OR 관세 OR 금리인상) (한국영향 OR 파급효과 OR 수출) -정치공방 -정상회의",
        "(에너지 OR 원자재 OR 국제유가) (가격동향 OR 인플레이션 OR 공급망) -테마주 -급등",
    ],
    "invest_ma": [
        "(스타트업 OR 벤처 OR 유니콘) (투자유치 OR 시리즈 OR 펀딩 OR 밸류에이션 OR VC) -코인 -가상화폐",
        "(인수합병 OR M&A OR 딜클로징 OR 지분인수) (전략적투자 OR 파트너십 OR PE OR 사모펀드) -정치 -경영권분쟁",
        "(IPO OR 상장 OR 기업공개) (수요예측 OR 공모가 OR 예비심사 OR 흥행) -주가조작 -테마주",
        "(해외투자 OR 글로벌투자 OR 크로스보더) (유치 OR 진출 OR 펀드결성) -외교 -순방",
    ],
    "innovation": [
        "(스타트업 OR 신사업 OR 플랫폼) (비즈니스모델 OR 서비스출시 OR 상용화) -이벤트 -프로모션",
        "(규제샌드박스 OR 규제완화 OR 산업혁신) (특례 OR 실증 OR 승인 OR 생태계) -정쟁 -국회파행",
        "(딥테크 OR 바이오 OR 신산업) (연구개발 OR 혁신 OR 원천기술) -임상실패 -주가급락",
        "(글로벌진출 OR 해외진출 OR K스타트업) (현지법인 OR 수출계약 OR 파트너십 OR 현지화) -외교",
    ],
}

# ─── 품질 필터 ───
BLOCK_SOURCES = [  # 출처명에 포함되면 제외 (소문자 비교)
    "naver blog", "네이버 블로그", "네이버블로그", "tistory", "티스토리", "brunch", "브런치",
    "youtube", "유튜브", "daum 블로그", "다음 블로그", "시정일보", "geeknews", "긱뉴스",
    "치의신보", "로컬타임즈",
]
BLOCK_TITLE_PATTERNS = [  # 원본 제목 기준 정규식
    r"^\s*[\[【]\s*(포토|사진|영상|부고|인사|게시판|\w*게시판|오늘의\s*운세|운세|알림|공지|광고|AD|카드뉴스|모집)\s*[\]】]",
    r"(구청|군청|구의회|군의회|시의회|도의회|교육청|구청장|군수)",   # 지자체 행정·행사
    r"^\s*(\S+\s)?\S{1,4}[시구군]\s?,",                               # "부평구, ..." 형 지자체 보도자료
    r"^\s*([가-힣]{1,4}\s)?[가-힣]{1,4}(시|군|구)\s*[\.,·‘'\"“]",     # "청주시 ‘…", "부산 해운대구, …" 지자체
    r"^\s*[가-힣]{1,3}도\s*[\.,·]",                                  # "경북도, …" 광역 지자체
    r"^\s*[가-힣]{1,4}상의[\s,]",                                    # 지역 상공회의소 행사
    r"^\s*[가-힣]{2,4}\s[^,…]{0,20}(대표이사|사장|회장|부회장|대표)\s*$",  # 인물 동정
    r"(운세|부고|모집\s*공고|입찰\s*공고)",
    r"((?<!美)(?<!미국 )민주당|국민의힘|조국혁신당|개혁신당|여야|국회의원|원내대표)",  # 국내 정치
]
SECTION_BLOCK_WORDS = {
    "macro_industry": ["대통령", "정상회의", "외교전", "李"],
}

# ──────────────────────────────────────────────────────────────
# 4. 섹션 메타 (이메일 + Pages 공통)
# ──────────────────────────────────────────────────────────────
SECTION_META = {
    "hr":             {"icon": "👥", "title": "HR",          "desc": "인사기획·평가·조직문화·노동법 및 Data 기반 HR 핵심 이슈"},
    "ai_tech":        {"icon": "🤖", "title": "AI / 기술",   "desc": "AI·디지털 전환(DX)이 기업 생산성과 조직에 미치는 영향"},
    "macro_industry": {"icon": "🌐", "title": "거시 / 산업", "desc": "국내외 주요 경제 지표 및 그룹사 연관 산업의 핵심 동향"},
    "invest_ma":      {"icon": "💰", "title": "투자 / M&A",  "desc": "국내외 스타트업 투자·VC·IPO 및 주요 기업 인수합병 동향"},
    "innovation":     {"icon": "🚀", "title": "혁신 생태계", "desc": "신규 비즈니스 모델·해외 진출·규제 및 창업 생태계 이슈"},
}
SECTION_ORDER = ["hr", "ai_tech", "macro_industry", "invest_ma", "innovation"]

RUN_LOG = {"failed_queries": [], "blocked": 0, "dup": 0, "curation": "rule"}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


# ──────────────────────────────────────────────────────────────
# 헬퍼: 텍스트 정규화 · 유사도
# ──────────────────────────────────────────────────────────────
_PAREN_TAG = r"\((종합|속보|상보|단독|\d보|종합\d?보?|사진|포토|영상|인터뷰|르포|칼럼|사설|기고|일문일답|이슈분석|feat\.?[^)]*)\)"


def clean_title(raw: str, source: str = "") -> str:
    """RSS 제목 정리 — [말머리]·(종합) 등 태그, 끝의 ' - 언론사' 제거"""
    t = raw or ""
    if source:
        # ' - 머니투데이 - 머니투데이' 처럼 반복되는 경우까지 제거
        pat = r"\s*[-|:]\s*" + re.escape(source.strip()) + r"\s*$"
        for _ in range(3):
            t2 = re.sub(pat, "", t)
            if t2 == t:
                break
            t = t2
    t = re.sub(r"\s+-\s+[^-]{1,25}$", "", t)                 # 남은 ' - 출처'
    t = re.sub(r"\s*:\s*네이버\s*블로그\s*$", "", t)
    t = re.sub(r"\[[^\]]*\]|【[^】]*】", "", t)              # [말머리]
    t = re.sub(_PAREN_TAG, "", t)
    t = re.sub(r"\s+", " ", t).strip(" -·|")
    return t


def normalize_title(title: str) -> str:
    """유사도 비교용 정규화 — 괄호 내용 제거"""
    title = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def title_tokens(title: str) -> frozenset:
    t = normalize_title(title)
    return frozenset(tok for tok in re.split(r'[\s,·\-\|/…「」『』<>"\'“”‘’]+', t) if len(tok) >= 2)


def char_bigrams(title: str) -> frozenset:
    s = re.sub(r'[\s\W]+', '', normalize_title(title))
    return frozenset(s[i:i + 2] for i in range(len(s) - 1))


def is_similar_title(a: str, b: str, word_thr: float = 0.45, char_thr: float = 0.35) -> bool:
    """단어 Jaccard 또는 문자 bigram Jaccard가 임계값 이상이면 같은 사건으로 판단"""
    s1, s2 = title_tokens(a), title_tokens(b)
    if s1 and s2 and len(s1 & s2) / len(s1 | s2) >= word_thr:
        return True
    b1, b2 = char_bigrams(a), char_bigrams(b)
    if b1 and b2 and len(b1 & b2) / len(b1 | b2) >= char_thr:
        return True
    return False


def lead_entity(title: str) -> str:
    """제목 맨 앞 주체(기업·기관명) 앞 2글자 — 같은 유형의 다른 회사 기사 구분용"""
    toks = [t for t in re.split(r'[\s,·\-…"\'“”‘’「」]+', normalize_title(title)) if t]
    return toks[0][:2] if toks else ""


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def esc(s: str) -> str:
    return html_lib.escape(s or "", quote=True)


# ──────────────────────────────────────────────────────────────
# 아카이브 (월별 파일)
# ──────────────────────────────────────────────────────────────

def _month_file(month: str) -> str:
    return f"{ARCHIVE_DIR}/{month}.json"


def load_month(month: str) -> list:
    try:
        with open(_month_file(month), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise RuntimeError(f"아카이브 파일 손상: {_month_file(month)} ({e})")


def load_recent_entries(now: datetime, days: int = RECENT_DAYS) -> list:
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    months = {now.strftime("%Y-%m"), (now - timedelta(days=days)).strftime("%Y-%m")}
    entries = []
    for m in months:
        entries += [e for e in load_month(m) if e.get("date", "") >= cutoff]
    return sorted(entries, key=lambda e: e["date"], reverse=True)


def save_entry(entry: dict) -> None:
    month = entry["date"][:7]
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    entries = [e for e in load_month(month) if e.get("date") != entry["date"]]
    entries.append(entry)
    entries.sort(key=lambda e: e["date"], reverse=True)
    with open(_month_file(month), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)

    months = sorted({fn[:7] for fn in os.listdir(ARCHIVE_DIR)
                     if re.fullmatch(r"\d{4}-\d{2}\.json", fn)}, reverse=True)
    with open(ARCHIVE_INDEX, "w", encoding="utf-8") as f:
        json.dump({"months": months, "updated": entry["date"]}, f, ensure_ascii=False, indent=1)
    print(f"  아카이브 저장: {_month_file(month)}")


# ──────────────────────────────────────────────────────────────
# 뉴스 수집
# ──────────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SSI-MorningBriefing/2.0)"})


def fetch_feed(query: str, window_days: int) -> list:
    """Google News RSS 검색 — 타임아웃·재시도 포함"""
    q = f"{query} when:{window_days}d"
    url = f"https://news.google.com/rss/search?q={quote(q)}&hl=ko&gl=KR&ceid=KR:ko"
    last_err = None
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=15)
            r.raise_for_status()
            return feedparser.parse(r.content).entries
        except Exception as e:  # 네트워크·HTTP 오류
            last_err = e
            time.sleep(2 * (attempt + 1))
    RUN_LOG["failed_queries"].append(query[:40])
    print(f"  [경고] RSS 수집 실패: {query[:40]}… ({last_err})")
    return []


def is_blocked(raw_title: str, source: str, section: str) -> bool:
    s = (source or "").lower()
    if any(b in s for b in BLOCK_SOURCES):
        return True
    cleaned = clean_title(raw_title, source)
    if any(re.search(p, t) for p in BLOCK_TITLE_PATTERNS for t in (raw_title, cleaned)):
        return True
    if any(w in raw_title for w in SECTION_BLOCK_WORDS.get(section, [])):
        return True
    return False


def parse_entry(entry, section: str, now: datetime, max_age: int):
    raw_title = entry.get("title", "")
    src_obj = entry.get("source", {})
    source = src_obj.get("title", "") if isinstance(src_obj, dict) else str(src_obj or "")
    source = re.sub(r"^https?://(www\.)?|/$", "", source.strip())   # 'https://www.2news.co.kr/' → '2news.co.kr'
    if is_blocked(raw_title, source, section):
        RUN_LOG["blocked"] += 1
        return None

    title = clean_title(raw_title, source)
    if len(title) < 8:
        return None

    pub_date, ts = "", None
    if getattr(entry, "published_parsed", None):
        try:
            pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).astimezone(KST)
            if (now - pub_dt).days > max_age:
                return None
            pub_date, ts = pub_dt.strftime("%y.%m.%d"), pub_dt.timestamp()
        except Exception:
            pass

    return {
        "title": title, "url": entry.get("link", ""), "source": source,
        "pub_date": pub_date, "section": section, "_ts": ts,
    }


class Deduper:
    """오늘 선정분(전 섹션) + 최근 7일 발송분과의 중복 판정"""

    def __init__(self, recent_entries: list):
        self.keys, self.today_titles, self.recent_titles = set(), [], []
        for e in recent_entries:
            for n in e.get("news", []):
                if not isinstance(n, dict):
                    continue
                if n.get("title"):
                    self.keys.add(_norm_key(n["title"]))
                    self.recent_titles.append(n["title"])
                if n.get("url"):
                    self.keys.add(n["url"].split("?")[0])

    def is_dup(self, c: dict) -> bool:
        if _norm_key(c["title"]) in self.keys or c["url"].split("?")[0] in self.keys:
            return True
        if any(is_similar_title(c["title"], t) for t in self.today_titles):
            return True
        # 지난 7일 기사: 제목이 비슷하고 '주체'도 같을 때만 같은 사건으로 판단
        # (예: 'LS그룹 임원인사' vs '대보그룹 임원인사'는 서로 다른 기사로 유지)
        lead = lead_entity(c["title"])
        if any(lead == lead_entity(t) and is_similar_title(c["title"], t) for t in self.recent_titles):
            return True
        return False

    def add(self, c: dict) -> None:
        self.keys.add(_norm_key(c["title"]))
        self.keys.add(c["url"].split("?")[0])
        self.today_titles.append(c["title"])


def round_robin(per_query: list, count: int, deduper: Deduper, start: int, taken: list) -> list:
    """쿼리별 후보 목록을 순환하며 1건씩 선발 — 특정 주제 쏠림 방지"""
    picked, n = [], len(per_query)
    pos = [0] * n
    order = [(start + i) % n for i in range(n)] if n else []
    while len(picked) < count and any(pos[i] < len(per_query[i]) for i in range(n)):
        for qi in order:
            lst = per_query[qi]
            while pos[qi] < len(lst):
                c = lst[pos[qi]]
                pos[qi] += 1
                if deduper.is_dup(c):
                    RUN_LOG["dup"] += 1
                    continue
                deduper.add(c)
                picked.append(c)
                break
            if len(picked) >= count:
                break
    taken.extend(picked)
    return picked


def gather(section: str, now: datetime, window: int, max_age: int, per_query_n: int) -> list:
    out = []
    for q in QUERIES.get(section, []):
        items = []
        for entry in fetch_feed(q, window)[:per_query_n * 3]:
            c = parse_entry(entry, section, now, max_age)
            if c:
                items.append(c)
            if len(items) >= per_query_n:
                break
        out.append(items)
    return out


def collect_pools(now: datetime, recent_entries: list, pool_factor: int) -> dict:
    """섹션별 후보 풀 (규칙 기반 우선순위 순서). pool_factor=1이면 곧 최종 선발"""
    deduper = Deduper(recent_entries)
    day_offset = now.timetuple().tm_yday
    pools = {}
    for key in SECTION_ORDER:
        want = TARGET[key] * pool_factor
        per_query = gather(key, now, window=2, max_age=NEWS_MAX_AGE_DAYS, per_query_n=10)
        taken = []
        round_robin(per_query, want, deduper, day_offset, taken)
        if len(taken) < TARGET[key]:
            print(f"  [안내] {key} {len(taken)}건 → 기간 {FALLBACK_MAX_AGE}일로 넓혀 보완 수집")
            per_query = gather(key, now, window=FALLBACK_MAX_AGE, max_age=FALLBACK_MAX_AGE, per_query_n=20)
            round_robin(per_query, want - len(taken), deduper, day_offset, taken)
        pools[key] = taken
        print(f"  [{key}] 후보 {len(taken)}건")
    return pools


# ──────────────────────────────────────────────────────────────
# AI 큐레이션 (선택)
# ──────────────────────────────────────────────────────────────

def ai_curate(pools: dict):
    """Claude로 섹션별 선별 + 1줄 시사점. 실패 시 None → 규칙 기반 결과 사용"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    lines = []
    for key in SECTION_ORDER:
        lines.append(f"\n## {key} (선발 {TARGET[key]}건)")
        for i, c in enumerate(pools[key]):
            lines.append(f"{key}-{i} | {c['title']} | {c['source']}")

    prompt = f"""{GROUP_CONTEXT}

아래는 오늘 아침 뉴스레터 후보 기사 목록이다(ID | 제목 | 언론사). 섹션마다 지정 건수만큼 골라라.

선발 기준
1. 그룹 경영진·HR 의사결정에 쓸모 있는 기사 우선: 공식 통계·정책·법 개정, 대기업/주요 금융·바이오·IT 기업 동향, 시장 수치가 있는 기사
2. 제외: 지자체·대학 행사, 단순 홍보·MOU, 인물 동정, 해외 단신 가십, 정치 기사
3. 같은 사건은 1건만, 섹션 안에서 주제가 겹치지 않게 다양하게
4. 쓸 만한 후보가 정말 부족할 때만 지정 건수보다 1건 적게 골라도 된다

so_what: 해당 기사가 그룹 경영·HR에 주는 시사점을 30자 이내 한 줄로. 제목 반복·추측·과장 금지, 명사형 종결(예: "계열사 성과급 재원 산정 시 참고").

출력은 아래 JSON만, 다른 설명 없이:
{{"hr":[{{"id":"hr-0","so_what":"..."}}], "ai_tech":[...], "macro_industry":[...], "invest_ma":[...], "innovation":[...]}}
{chr(10).join(lines)}"""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": ANTHROPIC_MODEL, "max_tokens": 3000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        picks = json.loads(m.group(0))
    except Exception as e:
        print(f"  [경고] AI 큐레이션 실패 → 규칙 기반으로 진행: {e}")
        RUN_LOG["curation"] = f"rule (AI 실패: {str(e)[:80]})"
        return None

    result = {}
    for key in SECTION_ORDER:
        pool, chosen, used = pools[key], [], set()
        for p in picks.get(key, []) or []:
            mm = re.fullmatch(rf"{key}-(\d+)", str(p.get("id", "")).strip())
            if not mm or int(mm.group(1)) >= len(pool) or int(mm.group(1)) in used:
                continue
            idx = int(mm.group(1))
            used.add(idx)
            item = dict(pool[idx])
            sw = re.sub(r"\s+", " ", str(p.get("so_what", ""))).strip()
            if sw:
                item["so_what"] = sw[:45]
            chosen.append(item)
            if len(chosen) >= TARGET[key]:
                break
        # AI가 적게 고르면 규칙 순서로 보충 (섹션당 목표보다 최대 1건 적게까지만 허용)
        min_keep = TARGET[key] - 1
        for idx, c in enumerate(pool):
            if len(chosen) >= min_keep:
                break
            if idx not in used:
                chosen.append(dict(c))
                used.add(idx)
        result[key] = chosen
    RUN_LOG["curation"] = f"AI ({ANTHROPIC_MODEL})"
    return result


def collect_all_news(now: datetime, recent_entries: list) -> list:
    use_ai = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    pools = collect_pools(now, recent_entries, pool_factor=3 if use_ai else 1)

    selected = ai_curate(pools) if use_ai else None
    if selected is None:
        selected = {k: v[:TARGET[k]] for k, v in pools.items()}

    news = []
    for key in SECTION_ORDER:
        for c in selected.get(key, []):
            c = {k: v for k, v in c.items() if not k.startswith("_")}
            news.append(c)
        print(f"  [{key}] 최종 {len(selected.get(key, []))}건")
    return news


# ──────────────────────────────────────────────────────────────
# 이메일 HTML 생성
# ──────────────────────────────────────────────────────────────

def make_linked_text(title: str, url: str) -> str:
    """제목에 링크 — 한 줄을 넘지 않도록 글자 수 제한"""
    MAX_CHARS = 38
    display_text = title if len(title) <= MAX_CHARS else title[:MAX_CHARS] + "..."
    LINK_STYLE = (
        "color:#1a1a2a; text-decoration:none; font-weight:500; "
        "display:block; width:100%; white-space:nowrap; "
        "overflow:hidden; text-overflow:ellipsis;"
    )
    return f'<a href="{esc(url)}" target="_blank" title="{esc(title)}" style="{LINK_STYLE}">{esc(display_text)}</a>'


def build_article_row(item: dict) -> str:
    """기사 1건 → 제목(좌)+출처·날짜(우), 시사점이 있으면 제목 아래 한 줄"""
    linked_text = make_linked_text(item.get("title", ""), item.get("url", "#"))
    source_date = " | ".join(x for x in (item.get("source", ""), item.get("pub_date", "")) if x)
    so_what = item.get("so_what", "")
    so_what_html = (
        f'<div style="font-size:12px; color:#0f8a8a; line-height:1.5; margin-top:2px;">'
        f'▸ {esc(so_what)}</div>' if so_what else ""
    )
    return f"""
      <tr>
        <td style="padding:11px 16px 11px 0; border-bottom:1px solid #f0f0f0;
                   font-size:13.5px; line-height:1.65; color:#1a1a2a;
                   vertical-align:top; width:75%;">
          {linked_text}{so_what_html}
        </td>
        <td style="padding:11px 0 11px 8px; border-bottom:1px solid #f0f0f0;
                   font-size:12px; color:#9ca3af; white-space:nowrap;
                   vertical-align:top; text-align:right; width:25%;">
          {esc(source_date)}
        </td>
      </tr>"""


def build_section_html(section_key: str, items: list) -> str:
    meta = SECTION_META.get(section_key, {})
    rows = "".join(build_article_row(item) for item in items)
    return f"""
  <div style="margin-bottom:8px;">
    <div style="border-top:2px solid #1a1a2a; padding:14px 0 8px;">
      <span style="font-size:15px; font-weight:800; color:#1a1a2a;
                   letter-spacing:-0.3px; vertical-align:baseline;">
        {meta.get('icon', '📌')}&nbsp; {meta.get('title', section_key)}
      </span>
      <span style="font-size:11px; color:#9ca3af; font-weight:400;
                   margin-left:8px; vertical-align:baseline;">
        {meta.get('desc', '')}
      </span>
    </div>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse; border-top:1px solid #e5e7eb;">
      {rows}
    </table>
  </div>"""


def load_weekly_data() -> dict:
    try:
        with open(WEEKLY_DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  [경고] {WEEKLY_DATA_FILE} 로드 실패 → 데이터 섹션 생략: {e}")
        return {"enabled": False}


def build_data_insight_html(data: dict) -> str:
    """이주의 HR 데이터 섹션 (data/weekly.json)"""
    if not data.get("enabled", True):
        return ""

    week_label = data.get("week_label", "")
    metrics    = data.get("metrics", [])
    reports    = data.get("reports", [])
    FONT = "font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;"
    trend_icon  = {"up": "▲", "down": "▼", "neutral": "–"}
    trend_color = {"up": "#059669", "down": "#dc2626", "neutral": "#6b7280"}

    def _card_td(m: dict, col_count: int) -> str:
        t = m.get("trend", "neutral")
        w = 100 // col_count
        return f"""
      <td style="padding:0 6px 6px 0; vertical-align:top; width:{w}%;">
        <div style="background:#f0fafa; border:1px solid #b2e8e8; border-radius:8px;
                    padding:10px 12px; text-align:left;">
          <div style="font-size:10.5px; color:#6b7280; margin-bottom:3px; {FONT}
                      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            {esc(m.get('label', ''))}
          </div>
          <div style="font-size:17px; font-weight:800; color:#1a1a2a; {FONT} letter-spacing:-0.5px;">
            {esc(m.get('value', ''))}
          </div>
          <div style="font-size:11.5px; font-weight:700; color:{trend_color.get(t, '#6b7280')}; margin-top:2px; {FONT}">
            {trend_icon.get(t, '–')} {esc(m.get('change', ''))}
          </div>
          <div style="font-size:10px; color:#9ca3af; margin-top:3px; {FONT}">
            {esc(m.get('source', ''))}
          </div>
        </div>
      </td>"""

    n = len(metrics)
    if n <= 3:
        metric_table_rows = "<tr>" + "".join(_card_td(m, max(n, 1)) for m in metrics) + "</tr>"
    else:
        rows = []
        for i in range(0, n, 2):
            rows.append("<tr>" + "".join(_card_td(m, 2) for m in metrics[i:i + 2]) + "</tr>")
        metric_table_rows = "".join(rows)

    report_rows = ""
    for r in reports:
        org = r.get("org") or r.get("publisher", "")   # [버그 수정] 기존 코드는 publisher만 읽어 항상 공란
        report_rows += f"""
      <tr>
        <td style="padding:5px 0; border-bottom:1px solid #f0f0f0;">
          <a href="{esc(r.get('url', '#'))}" target="_blank"
             style="font-size:13px; color:#1a1a2a; text-decoration:none; font-weight:500; {FONT}
                    display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            → {esc(r.get('title', ''))}
          </a>
        </td>
        <td style="padding:5px 0 5px 10px; border-bottom:1px solid #f0f0f0;
                   font-size:11px; color:#9ca3af; white-space:nowrap; text-align:right; {FONT}">
          {esc(org)}
        </td>
      </tr>"""

    reports_block = f"""
    <div style="margin-top:14px;">
      <div style="font-size:11.5px; font-weight:700; color:#6b7280; margin-bottom:6px; {FONT} letter-spacing:0.2px;">
        📄 이주의 HR 리포트 &amp; 통계
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
        {report_rows}
      </table>
    </div>""" if reports else ""

    return f"""
  <div style="margin-bottom:20px; border-top:2px solid #00A7A7; padding-top:14px;">
    <div style="margin-bottom:12px;">
      <span style="font-size:15px; font-weight:800; color:#1a1a2a; letter-spacing:-0.3px; {FONT}">
        📊&nbsp; 이주의 HR 데이터
      </span>
      <span style="font-size:11px; color:#9ca3af; font-weight:400; margin-left:8px; {FONT}">
        {esc(week_label)} 채용시장 주요 지표
      </span>
    </div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
      {metric_table_rows}
    </table>
    {reports_block}
  </div>"""


def build_email_html(news_items: list, today_str: str, logo_url: str = "", pages_url: str = "#") -> str:
    by_section: dict = {}
    for item in news_items:
        by_section.setdefault(item.get("section", "etc"), []).append(item)

    sections_html = "".join(
        build_section_html(k, by_section[k]) for k in SECTION_ORDER if by_section.get(k)
    )
    data_insight_html = build_data_insight_html(load_weekly_data())

    logo_img = (
        f'<img src="{logo_url}" alt="상상인그룹" style="height:29px; display:block;">'
        if logo_url
        else '<span style="font-size:12px; font-weight:800; color:#1a1a2a;">상상인그룹</span>'
    )
    subscribe_href   = f"mailto:{ADMIN_EMAIL}?subject={SUBSCRIBE_SUBJ}"
    unsubscribe_href = f"mailto:{ADMIN_EMAIL}?subject={UNSUBSCRIBE_SUBJ}"
    feedback_href    = f"mailto:{ADMIN_EMAIL}?subject={FEEDBACK_SUBJ}&body={FEEDBACK_BODY}"
    FONT = "font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{NEWSLETTER_TITLE}</title>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
</head>
<body style="margin:0; padding:0; background:#ffffff;
             font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif;">
  <div style="max-width:660px; margin:0 auto; padding:32px 28px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;">
      <tr>
        <td style="vertical-align:bottom;">
          <div style="font-size:24px; font-weight:900; color:#1a1a2a; letter-spacing:-0.5px; line-height:1.2; {FONT}">
            {NEWSLETTER_TITLE}
          </div>
          <div style="font-size:12px; color:#9ca3af; margin-top:6px; {FONT}">
            뉴스레터 &nbsp;|&nbsp; {today_str}
          </div>
        </td>
        <td style="vertical-align:top; text-align:right; white-space:nowrap; padding-left:20px; width:1%;">
          {logo_img}
        </td>
      </tr>
    </table>

    <div style="height: 20px;"></div>

    {data_insight_html}

    {sections_html}

    <hr style="border:none; border-top:1px solid #e5e7eb; margin:24px 0 20px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
      <tr>
        <td style="padding:4px;">
          <a href="{pages_url}" target="_blank"
             style="display:inline-block; background:#fef9c3; color:#1a1a2a; font-size:13px; font-weight:700;
                    padding:10px 18px; border-radius:8px; text-decoration:none; border:1px solid #fde047; {FONT}">
            📁 전체 뉴스 아카이브 보기
          </a>
        </td>
        <td style="padding:4px; text-align:right; white-space:nowrap;">
          <a href="{subscribe_href}"
             style="display:inline-block; background:#1a1a2a; color:#ffffff; font-size:13px; font-weight:700;
                    padding:10px 16px; border-radius:8px; text-decoration:none; margin-right:6px; {FONT}">
            ✉ 구독 신청
          </a>
          <a href="{unsubscribe_href}"
             style="display:inline-block; background:#f3f4f6; color:#6b7280; font-size:13px; font-weight:600;
                    padding:10px 14px; border-radius:8px; text-decoration:none; border:1px solid #e5e7eb;
                    margin-right:8px; {FONT}">
            구독 취소
          </a>
          <a href="{INTRO_URL}" target="_blank"
             style="display:inline-block; background:none; color:#1a1a2a; font-size:13px; font-weight:800;
                    padding:10px 4px; text-decoration:none; {FONT}">
            🏢 인재경영실 소개
          </a>
        </td>
      </tr>
    </table>

    <div style="text-align:center; margin-bottom:20px;">
      <a href="{feedback_href}"
         style="display:inline-block; background:#00A7A7; color:#ffffff; font-size:13px; font-weight:700;
                padding:11px 28px; border-radius:8px; text-decoration:none; {FONT}">
        💬 Morning Briefing 피드백 보내기
      </a>
    </div>

    <hr style="border:none; border-top:1px solid #e5e7eb; margin:0 0 16px;">

    <div style="font-size:11px; color:#9ca3af; line-height:1.7; {FONT}">
      Copyright {datetime.now(KST).year} {EMAIL_FROM_NAME}, All rights reserved.
    </div>

  </div>
</body>
</html>"""


def build_email_text(news_items: list, today_str: str, pages_url: str) -> str:
    """텍스트 전용 메일 클라이언트·스팸 필터용 plain-text 버전"""
    lines = [f"{NEWSLETTER_TITLE} | {today_str}", ""]
    for key in SECTION_ORDER:
        items = [n for n in news_items if n.get("section") == key]
        if not items:
            continue
        lines.append(f"■ {SECTION_META[key]['title']}")
        for n in items:
            lines.append(f"- {n['title']} ({n.get('source', '')})")
            if n.get("so_what"):
                lines.append(f"  ▸ {n['so_what']}")
            lines.append(f"  {n['url']}")
        lines.append("")
    lines.append(f"전체 아카이브: {pages_url}")
    lines.append(f"구독 취소: {ADMIN_EMAIL} (제목: 구독 취소)")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 이메일 발송
# ──────────────────────────────────────────────────────────────

def _smtp_send(subject: str, html_body: str, text_body: str, recipients: list) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASS"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
        server.login(gmail_user, gmail_pass)
        for i in range(0, len(recipients), SMTP_BATCH):
            batch = recipients[i:i + SMTP_BATCH]
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{EMAIL_FROM_NAME} <{gmail_user}>"
            msg["To"]      = f"{EMAIL_FROM_NAME} <{gmail_user}>"   # 수신자 목록 비공개(BCC 발송)
            msg["List-Unsubscribe"] = f"<mailto:{ADMIN_EMAIL}?subject={UNSUBSCRIBE_SUBJ}>"
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            server.sendmail(gmail_user, batch, msg.as_string())
            print(f"  발송 {i + len(batch)}/{len(recipients)}명")


def send_alert(title: str, detail: str) -> None:
    """실패 알림 — 관리자에게 메일 (실패해도 조용히 넘어감)"""
    try:
        if not (os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASS")):
            return
        run_url = ""
        if os.environ.get("GITHUB_RUN_ID"):
            run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                       f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        body = f"{detail}\n\n실행 로그: {run_url or '(로컬 실행)'}"
        _smtp_send(f"[Morning Briefing 경고] {title}", f"<pre>{esc(body)}</pre>", body, [ADMIN_EMAIL])
    except Exception as e:
        print(f"  알림 메일 발송 실패: {e}")


# ──────────────────────────────────────────────────────────────
# 발송일·발송 시각 판정
# ──────────────────────────────────────────────────────────────

def skip_reason(now: datetime):
    if now.weekday() >= 5:
        return "주말"
    if holidays is not None:
        kr = holidays.KR(years=now.year)
        name = kr.get(now.date())
        if name:
            return f"공휴일({name})"
    return None


def wait_until_send_time(now: datetime) -> None:
    target = now.replace(hour=SEND_NOT_BEFORE[0], minute=SEND_NOT_BEFORE[1], second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= MAX_WAIT_MIN * 60:
        print(f"  {target:%H:%M} 발송을 위해 {wait / 60:.0f}분 대기")
        time.sleep(wait)


def write_step_summary(lines: list) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────

def run() -> None:
    dry_run, force = env_flag("DRY_RUN"), env_flag("FORCE_SEND")
    now = datetime.now(KST)
    today_key = now.strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"  인재경영실 Morning Briefing  |  {today_key}  |  "
          f"{'DRY RUN' if dry_run else 'LIVE'}{' / FORCE' if force else ''}")
    print("=" * 50)

    # 0. 발송일·중복 발송 확인
    reason = skip_reason(now)
    if reason and not force and not dry_run:
        print(f"  {reason} — 발송하지 않습니다.")
        write_step_summary([f"⏭️ {today_key}: {reason}로 발송 생략"])
        return

    recent = load_recent_entries(now)
    if not force and not dry_run and any(e.get("date") == today_key for e in recent):
        print("  오늘자 브리핑은 이미 발송되었습니다 — 중복 발송 방지로 종료.")
        write_step_summary([f"⏭️ {today_key}: 이미 발송됨(중복 방지)"])
        return

    if not force and not dry_run:
        wait_until_send_time(now)
        now = datetime.now(KST)

    day_map = {"Mon": "(Mon)", "Tue": "(Tue)", "Wed": "(Wed)", "Thu": "(Thu)",
               "Fri": "(Fri)", "Sat": "(Sat)", "Sun": "(Sun)"}
    today_str = now.strftime("%y.%m.%d") + " " + day_map.get(now.strftime("%a"), "")

    owner = os.environ.get("GITHUB_OWNER", "")
    repo  = os.environ.get("GITHUB_REPO", "")
    logo_url  = f"https://raw.githubusercontent.com/{owner}/{repo}/main/logo.png" if owner and repo else ""
    pages_url = f"https://{owner}.github.io/{repo}" if owner and repo else "#"

    # 1. 수집
    print(f"\n[1] 뉴스 수집 (최근 {RECENT_DAYS}일 발송분 {sum(len(e.get('news', [])) for e in recent)}건과 중복 제외)")
    news_items = collect_all_news(now, recent)
    print(f"  총 {len(news_items)}건 · 차단 {RUN_LOG['blocked']} · 중복 제외 {RUN_LOG['dup']} · 큐레이션 {RUN_LOG['curation']}")

    if len(news_items) < MIN_TOTAL:
        raise RuntimeError(
            f"수집 기사 {len(news_items)}건으로 최소 기준({MIN_TOTAL}건) 미달 — 발송 중단.\n"
            f"실패 쿼리: {RUN_LOG['failed_queries'] or '없음'}"
        )

    # 2. 발송
    print("\n[2] 이메일 발송")
    html_body = build_email_html(news_items, today_str, logo_url, pages_url)
    text_body = build_email_text(news_items, today_str, pages_url)
    subject   = f"[Morning Briefing] {today_str} 주요 뉴스 {len(news_items)}선"

    if dry_run:
        os.makedirs("preview", exist_ok=True)
        with open("preview/email.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        target = [os.environ["GMAIL_USER"]] if os.environ.get("GMAIL_USER") else []
        if target:
            _smtp_send("[TEST] " + subject, html_body, text_body, target)
        print("  DRY RUN — 발신 계정에만 테스트 발송, 아카이브 저장 생략 (preview/email.html)")
    else:
        recipients = [r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
        if not recipients:
            raise RuntimeError("EMAIL_RECIPIENTS가 비어 있습니다.")
        _smtp_send(subject, html_body, text_body, recipients)

        # 3. 아카이브 (발송 직후 기록 → 재실행 시 중복 발송 방지)
        print("\n[3] 아카이브 저장")
        save_entry({"date": today_key, "sent_at": datetime.now(KST).strftime("%H:%M"),
                    "news": news_items})

    by_sec = {k: sum(1 for n in news_items if n["section"] == k) for k in SECTION_ORDER}
    write_step_summary([
        f"### ✅ Morning Briefing {today_key} ({'DRY RUN' if dry_run else '발송 완료'})",
        f"- 발송 시각: {datetime.now(KST):%H:%M} KST · 총 {len(news_items)}건 · 큐레이션: {RUN_LOG['curation']}",
        "- 섹션별: " + ", ".join(f"{SECTION_META[k]['title']} {v}" for k, v in by_sec.items()),
        f"- 차단 {RUN_LOG['blocked']}건 · 중복 제외 {RUN_LOG['dup']}건 · 실패 쿼리 {len(RUN_LOG['failed_queries'])}개",
    ])
    if RUN_LOG["failed_queries"]:
        send_alert("일부 검색어 수집 실패", "발송은 완료됐지만 다음 검색어 수집에 실패했습니다:\n"
                   + "\n".join(RUN_LOG["failed_queries"]))
    print("\n=== 완료 ===")


def main() -> int:
    try:
        run()
        return 0
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        send_alert("발송 실패", f"{e}\n\n{tb[-1500:]}")
        write_step_summary([f"### ❌ Morning Briefing 실패", f"```\n{e}\n```"])
        return 1


if __name__ == "__main__":
    sys.exit(main())
