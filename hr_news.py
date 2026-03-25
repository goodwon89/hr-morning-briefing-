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
        # 1. 보상/리텐션 (삼성/엔비디아 기사 타겟)
        "(퇴사율 OR 퇴직률 OR 이직률 OR 핵심인재) (RSU OR 스톡옵션 OR 보상 OR 성과급 OR 황금족쇄 OR 황금수갑) -정치 -선거",
        # 2. 채용/평가/조직문화
        "(인사제도 OR 조직문화 OR 성과평가 OR 인사고과) (개편 OR 도입 OR 데이터 OR 트렌드) -정치 -청문회 -장관 -대통령",
        # 3. 근로제도/생산성
        "(유연근무 OR 재택근무 OR 주4일제) (생산성 OR 통계 OR 효율 OR 도입) -정치 -선거",
        # 4. 구조조정/인력효율화
        "(구조조정 OR 희망퇴직 OR 권고사직 OR 임금피크제) (규모 OR 인력 OR 통계 OR 확산) -정치 -선거",
        # 5. 노사/컴플라이언스
        "(근로기준법 OR 중대재해처벌법 OR 최저임금 OR 임단협) (판례 OR 타결 OR 위반 OR 통계) -정치 -국회 -여야 -공공기관",
        # 6. HR 시스템/데이터 분석
        "(피플애널리틱스 OR HR데이터 OR HR SaaS OR 인사 솔루션) 도입",
        # 7. 그룹사/경영진 인사
        "(지주사 OR 재계 OR 대기업) (조직개편 OR 임원인사 OR 세대교체) -청문 -정부 -대통령,
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
                       archive_keys: set) -> list:
    """Google News RSS에서 카테고리별 핵심 기사 수집 (화제성 상위 기사 우선)"""
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

        # [핵심 개선] 각 쿼리당 화제성(관련성)이 가장 높은 상위 3개 기사만 추출
        top_entries = feed.entries[:3]

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
            pub_dt_ts = datetime.now(KST).timestamp() # 에러 방지를 위한 Timestamp 변환
            is_recent = False

            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub_dt = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).astimezone(KST)
                    days_old = (datetime.now(KST) - pub_dt).days
                    if days_old <= NEWS_MAX_AGE_DAYS:
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
                "pub_dt_ts":   pub_dt_ts, # 시간 정렬용 숨김 데이터 (숫자)
                "section":     section_key,
            })

    # [핵심 개선] 모인 '각 쿼리별 화제성 1~3위 기사'들을 최신 발행 시간순으로 정렬
    candidates.sort(key=lambda x: x["pub_dt_ts"], reverse=True)

    # ── 중복 제거 로직 ──
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
        
        # 정렬용으로 썼던 데이터는 최종 결과에서 제거
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
