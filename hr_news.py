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

NEWSLETTER_TITLE  = "HR Morning Briefing"
NEWSLETTER_SUBTLT = "Top 10 Trends to Start Your Day"
EMAIL_FROM_NAME   = "상상인그룹 인재경영실"
LOGO_URL          = ""   # GitHub Pages 배포 후 로고 URL 입력 (예: https://user.github.io/repo/logo.png)

# ──────────────────────────────────────────────────────────────
# 2. 카테고리별 목표 건수
# ──────────────────────────────────────────────────────────────
TARGET = {
    "ai_hr":       3,
    "hr_insight":  3,
    "labor":       2,
    "org_culture": 2,
}
TOTAL_TARGET = sum(TARGET.values())  # 10

# ──────────────────────────────────────────────────────────────
# 3. 카테고리별 검색 쿼리
# ──────────────────────────────────────────────────────────────
QUERIES = {
    "ai_hr": [
        "AI HR 인공지능 인사관리",
        "AI 채용 면접 자동화",
        "HR Tech AI 기업 도입",
        "생성형 AI 직원 업무 활용",
        "AX AI 전환 HR 인사",
    ],
    "hr_insight": [
        "인사제도 HR 트렌드",
        "채용 공채 방식 변화",
        "성과평가 OKR KPI 기업",
        "조직문화 직원경험 EX",
        "인재개발 HRD 교육 연수",
    ],
    "labor": [
        "노동시장 고용 동향",
        "임금 연봉 인상 현황",
        "유연근무 재택 하이브리드",
        "노동법 근로기준법 개정",
    ],
    "org_culture": [
        "조직문화 기업문화 사례",
        "리더십 경영 트렌드",
        "ESG 인사 경영 기업",
        "MZ세대 직장 세대 문화",
    ],
}

# ──────────────────────────────────────────────────────────────
# 4. 섹션 메타 (이메일 + Pages 공통)
# ──────────────────────────────────────────────────────────────
SECTION_META = {
    "ai_hr":       {
        "icon": "🤖",
        "title": "AI·AX × HR 인사이트",
        "desc": "AI Transformation이 HR 영역에 미치는 영향 및 기업 적용 사례",
        "color": "#1d6f42",
    },
    "hr_insight":  {
        "icon": "📋",
        "title": "HR 인사이트",
        "desc": "인사기획·평가·조직문화 담당자 필수 이슈",
        "color": "#1d6f42",
    },
    "labor":       {
        "icon": "💼",
        "title": "노동·고용 트렌드",
        "desc": "노동시장·임금·고용 정책 동향",
        "color": "#1d6f42",
    },
    "org_culture": {
        "icon": "🏢",
        "title": "조직문화·리더십",
        "desc": "기업문화·리더십·ESG 경영 트렌드",
        "color": "#1d6f42",
    },
}
SECTION_ORDER = ["ai_hr", "hr_insight", "labor", "org_culture"]


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
        "color:#1a6b3c; font-weight:700; text-decoration:none;"
        "border-bottom:1px solid #a7d9b8;"
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


def load_recent_archive_keys(archive: list) -> set:
    """최근 ARCHIVE_DAYS일 이내 기사 제목+URL 집합 (중복 방지)"""
    cutoff = (datetime.now(KST) - timedelta(days=ARCHIVE_DAYS)).strftime("%Y-%m-%d")
    keys = set()
    for entry in archive:
        if entry.get("date", "") >= cutoff:
            for item in entry.get("news", []):
                if isinstance(item, dict):
                    if item.get("title"):
                        keys.add(item["title"].strip())
                    if item.get("url"):
                        keys.add(item["url"].strip())
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

            candidates.append({
                "title":       title,
                "url":         url,
                "source":      source,
                "description": description,
                "section":     section_key,
            })

    # 중복 제거
    seen = set()
    result = []
    for a in candidates:
        key = a["title"]
        if key in seen or key in archive_keys or a.get("url", "") in archive_keys:
            continue
        seen.add(key)
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
    """기사 1건 → 키워드링크+요약 형식의 HTML 행"""
    title       = item.get("title", "")
    url         = item.get("url", "#")
    source      = item.get("source", "")
    description = item.get("description", "")

    linked_text = make_linked_text(title, url, description)

    return f"""
        <div style="padding:12px 0; border-bottom:1px solid #f0f2f4;">
          <p style="margin:0 0 4px 0; font-size:14px; line-height:1.75;
                    color:#1f2937; letter-spacing:-0.2px;">
            • {linked_text}
          </p>
          <span style="font-size:11px; color:#9ca3af;">{source}</span>
        </div>"""


def build_section_html(section_key: str, items: list) -> str:
    """카테고리 섹션 HTML 생성"""
    meta  = SECTION_META.get(section_key, {})
    icon  = meta.get("icon", "📌")
    title = meta.get("title", section_key)
    desc  = meta.get("desc", "")

    rows = "".join(build_article_row(item) for item in items)

    return f"""
  <div style="background:#ffffff; border-radius:12px; margin-bottom:20px;
              overflow:hidden; box-shadow:0 1px 6px rgba(0,0,0,0.07);">

    <!-- 섹션 헤더 -->
    <div style="background:#f0f7f3; padding:13px 20px;
                border-left:4px solid #2d8653;">
      <div style="font-size:15px; font-weight:700; color:#1a3a2a;">
        {icon} {title}
      </div>
      <div style="font-size:12px; color:#6b8c7a; margin-top:2px;">{desc}</div>
    </div>

    <!-- 기사 목록 -->
    <div style="padding:4px 20px 8px;">
      {rows}
    </div>
  </div>"""


def build_email_html(news_items: list, today_str: str) -> str:
    """전체 이메일 HTML 생성"""
    by_section: dict = {}
    for item in news_items:
        s = item.get("section", "etc")
        by_section.setdefault(s, []).append(item)

    sections_html = "".join(
        build_section_html(k, by_section[k])
        for k in SECTION_ORDER
        if k in by_section and by_section[k]
    )

    total = len(news_items)

    # 로고: LOGO_URL이 있으면 이미지, 없으면 텍스트
    logo_html = (
        f'<img src="{LOGO_URL}" alt="상상인그룹" style="height:48px; margin-bottom:10px;">'
        if LOGO_URL
        else '<div style="font-size:20px; font-weight:900; color:#2d8653; margin-bottom:6px;">상상인그룹</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{NEWSLETTER_TITLE}</title>
</head>
<body style="margin:0; padding:0; background:#f3f4f6;
             font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif;">
  <div style="max-width:680px; margin:0 auto; padding:24px 16px;">

    <!-- 헤더 -->
    <div style="background:linear-gradient(135deg,#e7f0e9 0%,#c8e6d0 100%);
                border-radius:16px; padding:30px 32px 24px; margin-bottom:24px;
                text-align:center; border:1px solid #b2d8bc;">
      {logo_html}
      <div style="font-size:26px; font-weight:900; color:#1a3a2a;
                  letter-spacing:-0.5px; margin-bottom:4px;">
        {NEWSLETTER_TITLE}
      </div>
      <div style="font-size:13px; color:#4a7a5a; font-weight:500;
                  margin-bottom:10px;">
        {NEWSLETTER_SUBTLT}
      </div>
      <div style="display:inline-block; background:#2d8653; color:#fff;
                  font-size:12px; font-weight:600; padding:4px 14px;
                  border-radius:20px; letter-spacing:0.3px;">
        {today_str} · 총 {total}선
      </div>
    </div>

    <!-- 뉴스 섹션들 -->
    {sections_html}

    <!-- 푸터 -->
    <div style="text-align:center; padding:20px 0 10px; color:#9ca3af; font-size:11px;
                border-top:1px solid #e5e7eb; margin-top:8px;">
      본 메일은 {EMAIL_FROM_NAME}에서 자동 발송됩니다.<br>
      뉴스 출처: Google News RSS · 수신 거부 문의: HR 담당자
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
    today_str = now_kst.strftime("%Y년 %m월 %d일 (%a)").replace(
        "Mon", "월").replace("Tue", "화").replace("Wed", "수").replace(
        "Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일")
    today_key = now_kst.strftime("%Y-%m-%d")

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
        html_body = build_email_html(news_items, today_str)
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
