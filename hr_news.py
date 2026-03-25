"""
상상인그룹 HR Morning Briefing - 자동화 뉴스레터
================================================
개선사항:
  - Google News RSS description에서 기사 핵심 문장 자동 발췌
  - 기사 제목의 핵심 키워드를 본문에 하이퍼링크로 자동 삽입
  - 번호+버튼 방식 → 자연스러운 문장+키워드링크 방식으로 전환
"""

import os, re, json, base64, smtplib, html as html_lib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests

# ──────────────────────────────────────────────────────────────
# 1. 기본 설정
# ──────────────────────────────────────────────────────────────
KST          = timezone(timedelta(hours=9))
ARCHIVE_FILE = "news_archive.json"
ARCHIVE_DAYS = 7

NEWSLETTER_TITLE  = "인재경영실 Morning Briefing"
NEWSLETTER_SUBTLT = "Top 10 Trends to Start Your Day"
EMAIL_FROM_NAME   = "상상인그룹 인재경영실"
LOGO_URL          = ""   # GitHub Pages 배포 후 로고 URL 입력 (예: https://user.github.io/repo/logo.png)

# ─── 버튼 링크 ───
INTRO_URL         = "https://ssihr.oopy.io"           # 인재경영실 소개
SUBSCRIBE_SUBJ    = "HR%20Morning%20Briefing%20%EA%B5%AC%EB%8F%85%20%EC%8B%A0%EC%B2%AD"
UNSUBSCRIBE_SUBJ  = "HR%20Morning%20Briefing%20%EA%B5%AC%EB%8F%85%20%EC%B7%A8%EC%86%8C"
NEWS_MAX_AGE_DAYS = 14   # 발행 후 이 일수 이내 기사만 수집 (7일은 너무 좁음)

# ──────────────────────────────────────────────────────────────
# 2. 카테고리별 목표 건수
# ──────────────────────────────────────────────────────────────
TARGET = {
    "hr":               3,
    "ai_tech":          3,
    "macro_industry":   3,
    "invest_ma":        3,
    "innovation":       3,
}
TOTAL_TARGET = sum(TARGET.values())  # 15

# ──────────────────────────────────────────────────────────────
# 3. 카테고리별 검색 쿼리
# ──────────────────────────────────────────────────────────────
QUERIES = {
    "hr": [
        "인사기획 HR 제도 기업",
        "성과평가 인사평가 개편",
        "조직문화 기업문화 변화",
        "노동법 근로기준법 개정",
        "HR 데이터 분석 인재",
        "채용 전략 인사 트렌드",
        "직원경험 EX 몰입도",
        "인재경영 HRD 역량개발",
        "고용노동부 정책 인사",
        "임금 보상 인사제도",
    ],
    "ai_tech": [
        "AI 기업 생산성 도입",
        "디지털 전환 DX 기업",
        "생성형 AI 업무 활용",
        "AI 자동화 업무 혁신",
        "기업 AI 도입 사례",
        "AI 에이전트 기업 적용",
        "챗GPT 클로드 기업 활용",
        "AI 기술 혁신 조직",
        "로봇 자동화 제조 기업",
        "클라우드 SaaS 기업 전환",
    ],
    "macro_industry": [
        "국내 경제 지표 전망",
        "금리 환율 경제 동향",
        "부동산 건설 시장 동향",
        "금융 투자 산업 트렌드",
        "글로벌 경제 한국 영향",
        "수출 제조업 경기 동향",
        "소비자 물가 경제 지표",
        "반도체 배터리 산업 동향",
        "에너지 원자재 가격 동향",
        "미국 중국 경제 정책 영향",
    ],
    "invest_ma": [
        "스타트업 투자 유치 라운드",
        "벤처캐피털 VC 투자",
        "기업 인수합병 M&A",
        "IPO 상장 기업",
        "시리즈 투자 스타트업",
        "PE 사모펀드 투자",
        "유니콘 기업 투자",
        "해외 투자 유치 한국 기업",
        "전략적 투자 기업 파트너십",
        "딜 클로징 기업 인수",
    ],
    "innovation": [
        "스타트업 새 비즈니스 모델",
        "해외 진출 글로벌 한국 기업",
        "규제 샌드박스 혁신",
        "창업 생태계 스타트업",
        "신사업 플랫폼 서비스 출시",
        "디지털 혁신 새 서비스",
        "규제 완화 산업 혁신",
        "딥테크 바이오 신산업",
        "글로벌 진출 K스타트업",
        "정부 지원 창업 혁신 정책",
    ],
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


def strip_html(text: str) -> str:
    """HTML 태그 제거 + 엔티티 디코딩"""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_rss_description(entry) -> str:
    """
    RSS entry에서 기사 요약문 추출.
    Google News는 종종 <ol><li> 형태로 관련 기사 묶음을 반환하므로,
    그 경우 첫 번째 <li> 텍스트만 사용하거나 빈 문자열 반환.
    """
    raw = ""
    if hasattr(entry, "summary"):
        raw = entry.summary or ""
    elif hasattr(entry, "description"):
        raw = entry.description or ""

    if not raw:
        return ""

    # Google News 클러스터 형태(<ol>/<li>) → 리스트 첫 항목 텍스트만 추출
    if "<li>" in raw.lower():
        first = re.search(r"<li>(.*?)</li>", raw, re.IGNORECASE | re.DOTALL)
        raw = first.group(1) if first else ""

    text = strip_html(raw)

    # 너무 짧거나 출처명만 남은 경우 제거
    if len(text) < 15:
        return ""
    # 제목과 동일하거나 출처+날짜만 있는 경우 제거
    if re.fullmatch(r"[\w\s\.\-·,]+\d{4}", text):
        return ""

    return text[:200]  # 최대 200자


# ──────────────────────────────────────────────────────────────
# 헬퍼: 핵심 키워드 추출 + 하이퍼링크 삽입
# ──────────────────────────────────────────────────────────────

def extract_key_phrase(title: str) -> str:
    """
    기사 제목에서 링크 걸기 가장 적합한 핵심 구절을 추출.
    우선순위: 숫자+단위 > 큰따옴표 내용 > 구체적 영어 고유명사 > 앞 2어절
    """
    # 1. 숫자+단위 (예: 62%, 10년차, 30억원)
    m = re.search(r"\d+[%억만년차개명건배]\S{0,3}", title)
    if m:
        return m.group()

    # 2. 큰따옴표 또는 작은따옴표 안의 내용
    m = re.search(r'["\'"](.*?)["\'""]', title)
    if m and 3 < len(m.group(1)) < 20:
        return m.group(1)

    # 3. 영어 대문자 고유명사 (AI·HR·ESG 제외, 3자 이상 구체적 명사)
    skip = {"AI", "HR", "ESG", "IT", "CEO", "MZ", "HRD", "OKR", "KPI", "AX"}
    eng_words = re.findall(r"\b[A-Z][A-Za-z]{2,}\b", title)
    for w in eng_words:
        if w not in skip:
            return w

    # 4. 두 번째 ·, 로, 가, 이, 은, 는 앞 구절 (한국어 주어부)
    m = re.search(r"^(.{4,15}?)[이가은는][\s,]", title)
    if m:
        return m.group(1)

    # 5. 첫 두 어절 (최종 fallback)
    words = title.split()
    return " ".join(words[:2]) if len(words) >= 2 else title[:12]


def make_linked_text(title: str, url: str, description: str) -> str:
    """
    description에 title의 핵심 키워드를 하이퍼링크로 삽입.
    description이 없거나 키워드 매칭 실패 시 title 자체를 링크로 반환.
    """
    LINK_STYLE = (
        "color:#1a1a2a; font-weight:600; text-decoration:none;"
    )

    esc_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    title_link = f'<a href="{url}" target="_blank" style="{LINK_STYLE}">{esc_title}</a>'

    if not description or len(description) < 20:
        return title_link

    key = extract_key_phrase(title)
    esc_desc = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # description 안에 핵심 키워드가 있으면 그것을 링크로 교체
    if key and key in esc_desc:
        linked = esc_desc.replace(
            key,
            f'<a href="{url}" target="_blank" style="{LINK_STYLE}">{key}</a>',
            1,
        )
        return linked

    # 매칭 안 되면: [title 링크] — description
    return f"{title_link} — {esc_desc}"


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
    """
    전체 아카이브의 기사 제목+URL 집합 반환 (중복 방지 강화).
    - 원본 문자열 + 정규화 문자열 둘 다 등록해 유사 제목도 차단
    """
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
                    # URL에서 쿼리스트링 제거한 버전도 등록
                    keys.add(u.split("?")[0])
    return keys


def fetch_section_news(section_key: str, queries: list, target_count: int,
                       archive_keys: set) -> list:
    """Google News RSS에서 카테고리별 기사 수집"""
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

        for entry in feed.entries:
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

            # ── 기사 발행 날짜 추출 + 최신성 필터 ──────────────────
            pub_date    = ""
            is_recent   = False   # 날짜 확인 불가 기사는 기본 제외

            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub_dt   = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).astimezone(KST)
                    days_old = (datetime.now(KST) - pub_dt).days
                    if days_old <= NEWS_MAX_AGE_DAYS:
                        is_recent = True
                    pub_date = pub_dt.strftime("%y.%m.%d")
                except Exception:
                    is_recent = True   # 파싱 오류 시 일단 포함
            else:
                # published_parsed 없으면 포함 (날짜 알 수 없음)
                is_recent = True

            if not is_recent:
                continue   # 7일 초과 기사 제외

            candidates.append({
                "title":       title,
                "url":         url,
                "source":      source,
                "description": description,
                "pub_date":    pub_date,
                "section":     section_key,
            })

    # ── 중복 제거 (제목 원본 + 정규화 + URL 쿼리스트링 제거 4중 체크) ──
    seen = set()
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

        seen.add(t)
        seen.add(t_norm)
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
        items = fetch_section_news(key, QUERIES.get(key, []), TARGET[key], archive_keys)
        all_news.extend(items)
        print(f"  [{key}] {len(items)}건 수집")
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
  <!-- 섹션: {title} -->
  <div style="margin-bottom:8px;">
    <!-- 섹션 헤더 -->
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

    # 헤더 로고 HTML (36px × 80% ≈ 29px)
    logo_img = (
        f'<img src="{logo_url}" alt="상상인그룹" style="height:29px; display:block;">'
        if logo_url
        else '<span style="font-size:12px; font-weight:800; color:#1a1a2a;">상상인그룹</span>'
    )

    # 발신자 이메일 (구독/취소 mailto용)
    sender_email = os.environ.get("GMAIL_USER", "")
    subscribe_href   = f"mailto:{sender_email}?subject={SUBSCRIBE_SUBJ}"
    unsubscribe_href = f"mailto:{sender_email}?subject={UNSUBSCRIBE_SUBJ}"

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

    <!-- 헤더: 제목(좌) + 로고(우) -->
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

    <!-- 상단 구분선 -->
    <hr style="border:none; border-top:2px solid #1a1a2a; margin:14px 0 20px;">

    <!-- 뉴스 섹션들 -->
    {sections_html}

    <!-- 하단 구분선 -->
    <hr style="border:none; border-top:1px solid #e5e7eb; margin:24px 0 20px;">

    <!-- 액션 버튼 3개 (아카이브 보기 / 구독 신청 / 구독 취소 / 인재경영실 소개) -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
      <tr>
        <td style="padding:4px;">
          <!-- 전체 뉴스 아카이브 보기 -->
          <a href="{pages_url}" target="_blank"
             style="display:inline-block; background:#fef9c3; color:#1a1a2a;
                    font-size:13px; font-weight:700; padding:10px 18px;
                    border-radius:8px; text-decoration:none; border:1px solid #fde047;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            📁 전체 뉴스 아카이브 보기
          </a>
        </td>
        <td style="padding:4px; text-align:right; white-space:nowrap;">
          <!-- 구독 신청 -->
          <a href="{subscribe_href}"
             style="display:inline-block; background:#1a1a2a; color:#ffffff;
                    font-size:13px; font-weight:700; padding:10px 16px;
                    border-radius:8px; text-decoration:none; margin-right:6px;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            ✉ 구독 신청
          </a>
          <!-- 구독 취소 -->
          <a href="{unsubscribe_href}"
             style="display:inline-block; background:#f3f4f6; color:#6b7280;
                    font-size:13px; font-weight:600; padding:10px 14px;
                    border-radius:8px; text-decoration:none; border:1px solid #e5e7eb;
                    margin-right:8px;
                    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;">
            구독 취소
          </a>
          <!-- 인재경영실 소개: 배경·테두리 없이 검정 볼드 텍스트 -->
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

    <!-- 최종 구분선 -->
    <hr style="border:none; border-top:1px solid #e5e7eb; margin:0 0 16px;">

    <!-- 푸터: 저작권만 -->
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
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
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
    print(f"  HR Morning Briefing 자동화 시작")
    print(f"  날짜: {today_str}")
    print("=" * 50)

    # 1. 아카이브 로드
    print("\n[1] 아카이브 로드 중...")
    archive     = load_archive()
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
        subject   = f"[HR 브리핑] {today_str} 주요 뉴스 {len(news_items)}선"
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
