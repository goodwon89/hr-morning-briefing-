"""
상상인그룹 인재경영실 Morning Briefing - 자동화 뉴스레터
================================================
개선사항:
  - Google News RSS description에서 기사 핵심 문장 자동 발췌
  - 기사 제목 전체를 본문에 하이퍼링크로 자동 삽입 (글자수 제한 포함)
  - 각 쿼리별 최상단(대장) 기사만 수집 후 최신순으로 정렬하여 트래픽 반영
"""

import os, re, json, base64, smtplib, html as html_lib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import feedparser
import requests

# ──────────────────────────────────────────────────────────────
# 1. 기본 설정
# ──────────────────────────────────────────────────────────────
KST          = timezone(timedelta(hours=9))
ARCHIVE_FILE = "news_archive.json"
ARCHIVE_DAYS = 7

NEWSLETTER_TITLE  = "인재경영실 Morning Briefing"
NEWSLETTER_SUBTLT = "Top Trends to Start Your Day"
EMAIL_FROM_NAME   = "상상인그룹 인재경영실"
LOGO_URL          = ""   # GitHub Pages 배포 후 로고 URL 입력 (예: https://user.github.io/repo/logo.png)

# ─── 버튼 링크 ───
INTRO_URL         = "https://ssihr.oopy.io"           # 인재경영실 소개
ADMIN_EMAIL       = "jangkeunwon@gmail.com"            # 구독 신청·취소 수신 담당자
SUBSCRIBE_SUBJ    = "%EC%9D%B8%EC%9E%AC%EA%B2%BD%EC%98%81%EC%8B%A4%20Morning%20Briefing%20%EA%B5%AC%EB%8F%85%20%EC%8B%A0%EC%B2%AD"
UNSUBSCRIBE_SUBJ  = "%EC%9D%B8%EC%9E%AC%EA%B2%BD%EC%98%81%EC%8B%A4%20Morning%20Briefing%20%EA%B5%AC%EB%8F%85%20%EC%B7%A8%EC%86%8C"

# 피드백 mailto 양식
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
NEWS_MAX_AGE_DAYS = 3   # 발행 후 이 일수 이내 기사만 수집

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
# 3. 카테고리별 검색 쿼리
# ──────────────────────────────────────────────────────────────
QUERIES = {
    "hr": [
        # 1. 채용·인재 확보 (광범위 OR 구조)
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
        # 1. AI 도입 및 업무 생산성
        "(AI OR 인공지능 OR 생성형AI OR 챗GPT OR 클로드) (도입 OR 업무활용 OR 생산성 OR 혁신 OR 사례) -정치 -테마주",
        # 2. 디지털 전환 및 인프라
        "(디지털전환 OR DX OR 클라우드 OR SaaS) (기업전환 OR 인프라 OR B2B OR 도입) -공공기관 -선거",
        # 3. 자동화 기술 적용
        "(로봇 OR RPA OR AI에이전트) (자동화 OR 제조 OR 업무혁신 OR 효율화) -주식 -종목추천"
    ],
    "macro_industry": [
        # 1. 국내외 핵심 경제 지표
        "(거시경제 OR 경제지표 OR 소비자물가 OR 환율 OR 금리) (동향 OR 전망 OR 통계 OR 발표) -정치 -선거",
        # 2. 부동산 및 건설 시장 (홍보/분양 기사 철저 배제)
        "(부동산 OR 건설업 OR 주택시장) (시장동향 OR 전망 OR 지표 OR PF OR 통계) -사업설명회 -분양 -청약 -견본주택 -단지",
        # 3. 주요 수출 및 제조업 동향
        "(수출 OR 제조업 OR 반도체 OR 배터리) (경기동향 OR 실적 OR 지표 OR 사이클) -주가 -종목추천",
        # 4. 글로벌 경제 정책 및 파급 효과
        "(미국 OR 중국 OR 글로벌) (경제정책 OR 관세 OR 금리인상) (한국영향 OR 파급효과 OR 수출) -정치공방",
        # 5. 원자재 및 에너지
        "(에너지 OR 원자재 OR 국제유가) (가격동향 OR 인플레이션 OR 공급망) -테마주 -급등"
    ],
    "invest_ma": [
        # 1. 스타트업 및 벤처 투자
        "(스타트업 OR 벤처 OR 유니콘) (투자유치 OR 시리즈 OR 펀딩 OR 밸류에이션 OR VC) -코인 -가상화폐",
        # 2. 기업 인수합병 및 지분 투자
        "(인수합병 OR M&A OR 딜클로징 OR 지분인수) (전략적투자 OR 파트너십 OR PE OR 사모펀드) -정치 -경영권분쟁",
        # 3. IPO 및 상장 동향
        "(IPO OR 상장 OR 기업공개) (수요예측 OR 공모가 OR 예비심사 OR 흥행) -주가조작 -테마주",
        # 4. 해외 및 크로스보더 투자
        "(해외투자 OR 글로벌투자 OR 크로스보더) (유치 OR 진출 OR 펀드결성) -외교 -순방"
    ],
    "innovation": [
        # 1. 신규 비즈니스 및 플랫폼
        "(스타트업 OR 신사업 OR 플랫폼) (비즈니스모델 OR 서비스출시 OR 상용화) -이벤트 -프로모션",
        # 2. 산업 규제 및 샌드박스
        "(규제샌드박스 OR 규제완화 OR 산업혁신) (특례 OR 실증 OR 승인 OR 생태계) -정쟁 -국회파행",
        # 3. 딥테크 및 미래 산업
        "(딥테크 OR 바이오 OR 신산업) (연구개발 OR 혁신 OR 원천기술) -임상실패 -주가급락",
        # 4. K-스타트업 글로벌 진출
        "(글로벌진출 OR 해외진출 OR K스타트업) (현지법인 OR 수출계약 OR 파트너십 OR 현지화) -외교"
    ]
}

# ──────────────────────────────────────────────────────────────
# 4. 섹션 메타 (이메일 + Pages 공통)
# ──────────────────────────────────────────────────────────────
SECTION_META = {
    "hr": {
        "icon": "👥",
        "title": "HR",
        "desc": "인사기획·평가·조직문화·노동법 및 Data 기반 HR 핵심 이슈",
        "color": "#1d6f42",
    },
    "ai_tech": {
        "icon": "🤖",
        "title": "AI / 기술",
        "desc": "AI·디지털 전환(DX)이 기업 생산성과 조직에 미치는 영향",
        "color": "#1d6f42",
    },
    "macro_industry": {
        "icon": "🌐",
        "title": "거시 / 산업",
        "desc": "국내외 주요 경제 지표 및 그룹사 연관 산업의 핵심 동향",
        "color": "#1d6f42",
    },
    "invest_ma": {
        "icon": "💰",
        "title": "투자 / M&A",
        "desc": "국내외 스타트업 투자·VC·IPO 및 주요 기업 인수합병 동향",
        "color": "#1d6f42",
    },
    "innovation": {
        "icon": "🚀",
        "title": "혁신 생태계",
        "desc": "신규 비즈니스 모델·해외 진출·규제 및 창업 생태계 이슈",
        "color": "#1d6f42",
    },
}
SECTION_ORDER = ["hr", "ai_tech", "macro_industry", "invest_ma", "innovation"]


# ──────────────────────────────────────────────────────────────
# 헬퍼: 텍스트 정규화
# ──────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """RSS 제목 정규화 — 괄호 및 끝 출처명 제거"""
    title = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    title = re.sub(r"\s*-\s*[^-]+$", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def enrich_title(title: str, description: str) -> str:
    """제목이 너무 짧으면 description으로 보완 (기관명만 있는 경우 등)"""
    if len(title) < 10 or " " not in title.strip():
        if description and len(description) >= 15:
            first_sentence = description.split(".")[0].strip()
            snippet = first_sentence if len(first_sentence) >= 15 else description[:70]
            return snippet[:80].strip()
    return title


def title_tokens(title: str) -> frozenset:
    """제목 → 핵심 토큰 집합 (2글자 이상만 추출, Jaccard 유사도용)"""
    t = normalize_title(title)
    return frozenset(tok for tok in re.split(r'[\s,·\-\|/…「」『』<>]+', t) if len(tok) >= 2)


def char_bigrams(title: str) -> frozenset:
    """문자 단위 2-gram 집합."""
    s = re.sub(r'[\s\W]+', '', normalize_title(title))
    return frozenset(s[i:i+2] for i in range(len(s) - 1))


def is_similar_title(a: str, b: str,
                     word_thr: float = 0.45,
                     char_thr: float = 0.35) -> bool:
    """단어 Jaccard OR 문자 bigram Jaccard 중 하나라도 임계값 이상이면 동일 사건 판단."""
    s1, s2 = title_tokens(a), title_tokens(b)
    if s1 and s2 and len(s1 & s2) / len(s1 | s2) >= word_thr:
        return True
    b1, b2 = char_bigrams(a), char_bigrams(b)
    if b1 and b2 and len(b1 & b2) / len(b1 | b2) >= char_thr:
        return True
    return False


def strip_html(text: str) -> str:
    """HTML 태그 제거 + 엔티티 디코딩"""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_rss_description(entry) -> str:
    """RSS entry에서 기사 요약문 추출."""
    raw = ""
    if hasattr(entry, "summary"):
        raw = entry.summary or ""
    elif hasattr(entry, "description"):
        raw = entry.description or ""

    if not raw:
        return ""

    if "<li>" in raw.lower():
        first = re.search(r"<li>(.*?)</li>", raw, re.IGNORECASE | re.DOTALL)
        raw = first.group(1) if first else ""

    text = strip_html(raw)

    if len(text) < 15:
        return ""
    if re.fullmatch(r"[\w\s\.\-·,]+\d{4}", text):
        return ""

    return text[:200]  


# ──────────────────────────────────────────────────────────────
# 헬퍼: 하이퍼링크 삽입
# ──────────────────────────────────────────────────────────────

def make_linked_text(title: str, url: str, description: str) -> str:
    """기사 전체 텍스트에 링크를 걸고, 한 줄을 초과하지 않도록 글자 수를 제한합니다."""
    MAX_CHARS = 38 
    
    display_text = title
    if len(display_text) > MAX_CHARS:
        display_text = display_text[:MAX_CHARS] + "..."
        
    esc_text = display_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    LINK_STYLE = (
        "color:#1a1a2a; text-decoration:none; font-weight:500; "
        "display:block; width:100%; white-space:nowrap; "
        "overflow:hidden; text-overflow:ellipsis;"
    )

    return f'<a href="{url}" target="_blank" style="{LINK_STYLE}">{esc_text}</a>'


# ──────────────────────────────────────────────────────────────
# 뉴스 수집
# ──────────────────────────────────────────────────────────────

def load_archive() -> list:
    """GitHub API로 news_archive.json 로드"""
    owner = os.environ.get("GITHUB_OWNER", "")
    repo  = os.environ.get("GITHUB_REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    if not (owner and repo and token):
        return []

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{ARCHIVE_FILE}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        return json.loads(content)
    return []


def _norm_key(text: str) -> str:
    """비교용 정규화: 소문자 + 공백 제거"""
    return re.sub(r"\s+", "", text).lower()


def load_recent_archive_keys(archive: list) -> set:
    """전체 아카이브의 기사 제목+URL 집합 반환 (중복 방지 강화)"""
    keys = set()
    for entry in archive:
        for item in entry.get("news", []):
            if isinstance(item, dict):
                if item.get("title"):
                    t = item["title"].strip()
                    keys.add(t)
                    keys.add(_norm_key(t))
                if item.get("url"):
                    u = item["url"].strip()
                    keys.add(u)
                    keys.add(u.split("?")[0])
    return keys


def fetch_section_news(section_key: str, queries: list, target_count: int,
                       archive_keys: set, top_n: int = 3, max_age: int = NEWS_MAX_AGE_DAYS) -> list:
    """Google News RSS에서 카테고리별 핵심 기사 수집 (조건 완화 기능 추가)"""
    candidates = []

    for q in queries:
        encoded_q = requests.utils.quote(q)
        rss_url = (
            f"https://news.google.com/rss/search"
            f"?q={encoded_q}&hl=ko&gl=KR&ceid=KR:ko"
        )
        try:
            feed = feedparser.parse(rss_url)
        except Exception:
            continue

        # [수정] 기본 3개, 조건 완화 시 top_n에 지정된 개수만큼 유동적으로 추출
        top_entries = feed.entries[:top_n]

        for entry in top_entries:
            raw_title = entry.get("title", "")
            title = normalize_title(raw_title)
            if not title:
                continue

            url = entry.get("link", "")
            source_obj = entry.get("source", {})
            source = (
                source_obj.get("title", "")
                if isinstance(source_obj, dict)
                else str(source_obj)
            )
            description = get_rss_description(entry)
            title = enrich_title(title, description)

            pub_date = ""
            pub_dt_ts = datetime.now(KST).timestamp() 
            is_recent = False

            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub_dt = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).astimezone(KST)
                    days_old = (datetime.now(KST) - pub_dt).days
                    
                    # [수정] 전역 변수 대신 매개변수로 전달받은 max_age(기본 3일, 완화 시 7일 등) 적용
                    if days_old <= max_age:
                        is_recent = True
                    pub_date = pub_dt.strftime("%y.%m.%d")
                    pub_dt_ts = pub_dt.timestamp() 
                except Exception:
                    is_recent = True
            else:
                is_recent = True

            if not is_recent:
                continue

            candidates.append({
                "title":       title,
                "url":         url,
                "source":      source,
                "description": description,
                "pub_date":    pub_date,
                "pub_dt_ts":   pub_dt_ts, 
                "section":     section_key,
            })

    candidates.sort(key=lambda x: x["pub_dt_ts"], reverse=True)

    seen = set()
    seen_titles = []
    result = []
    
    for a in candidates:
        t       = a["title"]
        t_norm  = _norm_key(t)
        u       = a.get("url", "")
        u_base  = u.split("?")[0]

        if (t      in seen or t      in archive_keys or
            t_norm in seen or t_norm in archive_keys or
            u      in archive_keys or u_base in archive_keys):
            continue

        if any(is_similar_title(t, st) for st in seen_titles):
            continue

        seen.add(t)
        seen.add(t_norm)
        seen_titles.append(t)
        
        a.pop("pub_dt_ts", None) 
        
        result.append(a)
        if len(result) >= target_count:
            break

    return result


def collect_all_news(archive_keys: set) -> list:
    """전체 카테고리 뉴스 수집"""
    all_news = []
    for key in SECTION_ORDER:
        if key not in TARGET:
            continue
            
        # 1. 기본 수집 (최근 3일, 쿼리당 상위 3개)
        items = fetch_section_news(key, QUERIES.get(key, []), TARGET[key], archive_keys, top_n=3, max_age=NEWS_MAX_AGE_DAYS)
        
        # 2. [추가] HR 섹션이 4개 미만일 경우 보완 로직 작동 (중단 없음)
        if key == "hr" and len(items) < 4:
            print(f"  [안내] HR 기사가 {len(items)}건으로 4건 미만입니다. 탐색 범위를 넓혀 추가 수집을 시도합니다.")
            
            # 이미 수집한 기사가 중복으로 들어가지 않도록 임시 아카이브에 추가
            temp_archive = archive_keys.copy()
            for item in items:
                temp_archive.add(item["title"])
                temp_archive.add(item.get("url", "").split("?")[0])
            
            # 조건 완화: 부족한 개수만큼 추가 수집 (쿼리당 10개 검토, 최근 7일 치 기사 허용)
            shortfall = TARGET[key] - len(items)
            fallback_items = fetch_section_news(key, QUERIES.get(key, []), shortfall, temp_archive, top_n=10, max_age=7)
            
            items.extend(fallback_items)
            print(f"  [안내] 보완 수집 완료. HR 최종 확보: {len(items)}건")

        all_news.extend(items)
        print(f"  [{key}] 최종 {len(items)}건 수집 완료")
        
    return all_news


# ──────────────────────────────────────────────────────────────
# 이메일 HTML 생성
# ──────────────────────────────────────────────────────────────

def build_article_row(item: dict) -> str:
    """기사 1건 → 제목(좌)+출처·날짜(우) 테이블 행 (Neusral 스타일)"""
    title    = item.get("title", "")
    url      = item.get("url", "#")
    source   = item.get("source", "")
    desc     = item.get("description", "")
    pub_date = item.get("pub_date", "")

    linked_text = make_linked_text(title, url, desc)

    source_date = ""
    if source and pub_date:
        source_date = f"{source} | {pub_date}"
    elif source:
        source_date = source
    elif pub_date:
        source_date = pub_date

    return f"""
      <tr>
        <td style="padding:11px 16px 11px 0; border-bottom:1px solid #f0f0f0;
                   font-size:13.5px; line-height:1.65; color:#1a1a2a;
                   vertical-align:top; width:75%;">
          {linked_text}
        </td>
        <td style="padding:11px 0 11px 8px; border-bottom:1px solid #f0f0f0;
                   font-size:12px; color:#9ca3af; white-space:nowrap;
                   vertical-align:top; text-align:right; width:25%;">
          {source_date}
        </td>
      </tr>"""


def build_section_html(section_key: str, items: list) -> str:
    """카테고리 섹션 HTML 생성 (Neusral 스타일: 굵은 제목(좌) + desc 설명(우))"""
    meta       = SECTION_META.get(section_key, {})
    icon       = meta.get("icon", "📌")
    title      = meta.get("title", section_key)
    desc       = meta.get("desc", "")

    rows = "".join(build_article_row(item) for item in items)

    return f"""
  <div style="margin-bottom:8px;">
    <div style="border-top:2px solid #1a1a2a; padding:14px 0 8px;">
      <span style="font-size:15px; font-weight:800; color:#1a1a2a;
                   letter-spacing:-0.3px; vertical-align:baseline;">
        {icon}&nbsp; {title}
      </span>
      <span style="font-size:11px; color:#9ca3af; font-weight:400;
                   margin-left:8px; vertical-align:baseline;">
        {desc}
      </span>
    </div>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse; border-top:1px solid #e5e7eb;">
      {rows}
    </table>
  </div>"""


def build_email_html(news_items: list, today_str: str,
                     logo_url: str = "", pages_url: str = "#") -> str:
    """전체 이메일 HTML 생성 (Neusral 레이아웃)"""
    by_section: dict = {}
    for item in news_items:
        s = item.get("section", "etc")
        by_section.setdefault(s, []).append(item)

    sections_html = "".join(
        build_section_html(k, by_section[k])
        for k in SECTION_ORDER
        if k in by_section and by_section[k]
    )

    logo_img = (
        f'<img src="{logo_url}" alt="상상인그룹" style="height:29px; display:block;">'
        if logo_url
        else '<span style="font-size:12px; font-weight:800; color:#1a1a2a;">상상인그룹</span>'
    )

    subscribe_href   = f"mailto:{ADMIN_EMAIL}?subject={SUBSCRIBE_SUBJ}"
    unsubscribe_href = f"mailto:{ADMIN_EMAIL}?subject={UNSUBSCRIBE_SUBJ}"
    feedback_href    = f"mailto:{ADMIN_EMAIL}?subject={FEEDBACK_SUBJ}&body={FEEDBACK_BODY}"

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
             font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',
             'Noto Sans KR',sans-serif;">
  <div style="max-width:660px; margin:0 auto; padding:32px 28px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;">
      <tr>
        <td style="vertical-align:bottom;">
          <div style="font-size:24px; font-weight:900; color:#1a1a2a;
                      letter-spacing:-0.5px; line-height:1.2;
                      font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            {NEWSLETTER_TITLE}
          </div>
          <div style="font-size:12px; color:#9ca3af; margin-top:6px;
                      font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            뉴스레터 &nbsp;|&nbsp; {today_str}
          </div>
        </td>
        <td style="vertical-align:top; text-align:right; white-space:nowrap;
                   padding-left:20px; width:1%;">
          {logo_img}
        </td>
      </tr>
    </table>

    <div style="height: 20px;"></div>

    {sections_html}

    <hr style="border:none; border-top:1px solid #e5e7eb; margin:24px 0 20px;">

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
      <tr>
        <td style="padding:4px;">
          <a href="{pages_url}" target="_blank"
             style="display:inline-block; background:#fef9c3; color:#1a1a2a;
                    font-size:13px; font-weight:700; padding:10px 18px;
                    border-radius:8px; text-decoration:none; border:1px solid #fde047;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            📁 전체 뉴스 아카이브 보기
          </a>
        </td>
        <td style="padding:4px; text-align:right; white-space:nowrap;">
          <a href="{subscribe_href}"
             style="display:inline-block; background:#1a1a2a; color:#ffffff;
                    font-size:13px; font-weight:700; padding:10px 16px;
                    border-radius:8px; text-decoration:none; margin-right:6px;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            ✉ 구독 신청
          </a>
          <a href="{unsubscribe_href}"
             style="display:inline-block; background:#f3f4f6; color:#6b7280;
                    font-size:13px; font-weight:600; padding:10px 14px;
                    border-radius:8px; text-decoration:none; border:1px solid #e5e7eb;
                    margin-right:8px;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            구독 취소
          </a>
          <a href="{INTRO_URL}" target="_blank"
             style="display:inline-block; background:none; color:#1a1a2a;
                    font-size:13px; font-weight:800; padding:10px 4px;
                    text-decoration:none;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            🏢 인재경영실 소개
          </a>
        </td>
      </tr>
    </table>

    <!-- 피드백 버튼 -->
    <div style="text-align:center; margin-bottom:20px;">
      <a href="{feedback_href}"
         style="display:inline-block; background:#00A7A7; color:#ffffff;
                font-size:13px; font-weight:700; padding:11px 28px;
                border-radius:8px; text-decoration:none;
                font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
        💬 Morning Briefing 피드백 보내기
      </a>
    </div>

    <hr style="border:none; border-top:1px solid #e5e7eb; margin:0 0 16px;">

    <div style="font-size:11px; color:#9ca3af; line-height:1.7;
                font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
      Copyright 2026 {EMAIL_FROM_NAME}, All rights reserved.
    </div>

  </div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# 이메일 발송
# ──────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str, recipients: list) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASS"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{EMAIL_FROM_NAME} <{gmail_user}>"
    
    # '받는 사람'을 발신자(본인)로 설정하여 다른 수신자가 보이지 않게 처리 (스팸 방지 효과)
    msg["To"]      = f"{EMAIL_FROM_NAME} <{gmail_user}>" 
    
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        # 겉으로 보이는 To 헤더와 무관하게 recipients 전체에게 숨은참조(Bcc) 형태로 메일이 발송됨
        server.sendmail(gmail_user, recipients, msg.as_string())
    print(f"  이메일 발송 완료: {len(recipients)}명")


# ──────────────────────────────────────────────────────────────
# GitHub 아카이브 업데이트
# ──────────────────────────────────────────────────────────────

def push_file_to_github(content_str: str, path: str, message: str) -> None:
    """GitHub API로 파일 생성/업데이트"""
    owner = os.environ["GITHUB_OWNER"]
    repo  = os.environ["GITHUB_REPO"]
    token = os.environ["GITHUB_TOKEN"]

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    r   = requests.get(api_url, headers=headers, timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        print(f"  GitHub 업데이트 완료: {path}")
    else:
        print(f"  GitHub 업데이트 실패 ({resp.status_code}): {resp.text[:200]}")


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────

def main():
    now_kst   = datetime.now(KST)
    day_map   = {"Mon":"(Mon)","Tue":"(Tue)","Wed":"(Wed)",
                 "Thu":"(Thu)","Fri":"(Fri)","Sat":"(Sat)","Sun":"(Sun)"}
    day_abbr  = now_kst.strftime("%a")
    today_str = now_kst.strftime("%y.%m.%d") + " " + day_map.get(day_abbr, "")
    today_key = now_kst.strftime("%Y-%m-%d")

    # 이메일용 URL 구성
    owner      = os.environ.get("GITHUB_OWNER", "")
    repo       = os.environ.get("GITHUB_REPO", "")
    logo_url   = f"https://raw.githubusercontent.com/{owner}/{repo}/main/logo.png" if owner and repo else ""
    pages_url  = f"https://{owner}.github.io/{repo}" if owner and repo else "#"

    print("=" * 50)
    print(f"  인재경영실 Morning Briefing 자동화 시작")
    print(f"  날짜: {today_str}")
    print("=" * 50)

    # 1. 아카이브 로드
    print("\n[1] 아카이브 로드 중...")
    archive      = load_archive()
    archive_keys = load_recent_archive_keys(archive)
    print(f"  최근 {ARCHIVE_DAYS}일 기사 {len(archive_keys)}건 캐시")

    # 2. 뉴스 수집
    print("\n[2] 뉴스 수집 중...")
    news_items = collect_all_news(archive_keys)
    print(f"  총 {len(news_items)}건 수집 완료")

    if not news_items:
        print("  수집된 기사가 없어 종료합니다.")
        return

    # 3. 이메일 발송
    print("\n[3] 이메일 발송 중...")
    recipients_env = os.environ.get("EMAIL_RECIPIENTS", "")
    recipients = [r.strip() for r in recipients_env.split(",") if r.strip()]

    if recipients:
        subject   = f"[Morning Briefing] {today_str} 주요 뉴스 {len(news_items)}선"
        html_body = build_email_html(news_items, today_str, logo_url, pages_url)
        send_email(subject, html_body, recipients)
    else:
        print("  EMAIL_RECIPIENTS 미설정 — 이메일 발송 건너뜀")

    # 4. 아카이브 업데이트
    print("\n[4] GitHub 아카이브 업데이트 중...")
    new_entry = {"date": today_key, "news": news_items}
    archive   = [e for e in archive if e.get("date") != today_key]
    archive.insert(0, new_entry)

    push_file_to_github(
        content_str=json.dumps(archive, ensure_ascii=False, indent=2),
        path=ARCHIVE_FILE,
        message=f"chore: update news archive {today_key}",
    )

    print("\n=== 완료 ===")


if __name__ == "__main__":
    main()
