"""
Alt-In 대체투자 뉴스 스크래퍼
-------------------------------
의존 패키지 설치:
    pip install requests beautifulsoup4 anthropic python-dotenv

.env 파일 설정:
    ANTHROPIC_API_KEY=sk-ant-...
"""

import os
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

KEYWORDS = [
    "대체투자", "사모펀드", "인프라 투자", "부동산 펀드",
    "벤처캐피탈", "PEF", "블라인드펀드", "리츠 투자",
    "세컨더리 펀드", "해외 인프라",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

CATEGORY_MAP = {
    "부동산": ["부동산 펀드", "리츠 투자"],
    "인프라":  ["인프라 투자", "해외 인프라"],
    "PE":     ["사모펀드", "PEF", "블라인드펀드", "세컨더리 펀드"],
    "VC":     ["벤처캐피탈"],
}

REQUEST_DELAY  = 1.5   # 요청 간 대기(초) — 과도한 크롤링 방지
MAX_ARTICLES   = 5     # 키워드당 최대 수집 기사 수
MIN_BODY_LEN   = 200   # 본문 최소 길이(자)


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class Article:
    title:    str
    url:      str
    source:   str
    keyword:  str
    category: str
    body:     str       = ""
    summary:  list[str] = field(default_factory=list)
    scraped_at: str     = field(default_factory=lambda: datetime.now().isoformat())


# ──────────────────────────────────────────────
# 뉴스 검색 (네이버 뉴스)
# ──────────────────────────────────────────────

def search_naver_news(keyword: str, max_results: int = MAX_ARTICLES) -> list[dict]:
    """네이버 뉴스 검색 결과에서 제목·링크·출처를 반환합니다."""
    url = "https://search.naver.com/search.naver"
    params = {"where": "news", "query": keyword, "sm": "tab_jum"}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("네이버 검색 실패 [%s]: %s", keyword, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for item in soup.select("div.news_wrap")[:max_results]:
        a_tag = item.select_one("a.news_tit")
        press = item.select_one("a.info.press")
        if not a_tag:
            continue
        results.append({
            "title":  a_tag.get_text(strip=True),
            "url":    a_tag["href"],
            "source": press.get_text(strip=True) if press else "출처 불명",
        })

    log.info("  네이버 [%s] → %d건 수집", keyword, len(results))
    return results


# ──────────────────────────────────────────────
# 본문 추출
# ──────────────────────────────────────────────

# 언론사별 본문 CSS 셀렉터
SOURCE_SELECTORS = {
    "한국경제":  ["div#articlebody", "div.article-body"],
    "매일경제":  ["div#article_body", "div.news_cnt_detail_wrap"],
    "조선비즈":  ["div#news_body_id"],
    "더벨":     ["div.article_view"],
    "딜사이트":  ["div.view_con"],
}
FALLBACK_SELECTORS = [
    "div#articleBodyContents", "div#articeBody", "div#newsct_article",
    "article", "div.article_body", "div.news-content", "div#content",
]


def extract_body(url: str, source: str) -> str:
    """기사 URL에서 본문 텍스트를 추출합니다."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("    본문 요청 실패 [%s]: %s", url[:60], e)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # 광고·스크립트 제거
    for tag in soup(["script", "style", "figure", "iframe", "aside"]):
        tag.decompose()

    # 언론사별 셀렉터 우선 시도
    selectors = SOURCE_SELECTORS.get(source, []) + FALLBACK_SELECTORS
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= MIN_BODY_LEN:
                return text

    # 최후 수단: <p> 태그 전체 수집
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40]
    return "\n".join(paras)


# ──────────────────────────────────────────────
# LLM 요약 (Anthropic Claude)
# ──────────────────────────────────────────────

_client: Anthropic | None = None

def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        _client = Anthropic(api_key=api_key)
    return _client


SUMMARIZE_SYSTEM = """
당신은 대체투자(부동산·인프라·PE·VC) 전문 금융 애널리스트입니다.
주어진 기사 본문을 대체투자 취준생이 이해하기 쉽게 핵심 3줄로 요약하세요.

규칙:
- 반드시 JSON 배열 형식으로만 응답하세요: ["요약1", "요약2", "요약3"]
- 각 요약은 1~2문장, 60자 이내로 작성하세요.
- 투자 규모·수익률·IRR 등 수치가 있으면 반드시 포함하세요.
- 마크다운이나 추가 설명 없이 JSON 배열만 출력하세요.
""".strip()


def summarize_with_llm(title: str, body: str) -> list[str]:
    """Claude API로 기사 본문을 3줄 요약합니다."""
    # 토큰 절약: 본문 앞 2,000자만 사용
    truncated = body[:2000]
    prompt = f"제목: {title}\n\n본문:\n{truncated}"

    try:
        msg = get_client().messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            system=SUMMARIZE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        summaries = json.loads(raw)
        if isinstance(summaries, list) and len(summaries) == 3:
            return summaries
    except (json.JSONDecodeError, IndexError, Exception) as e:
        log.warning("    LLM 요약 실패: %s", e)

    # 파싱 실패 시 기본 요약 반환
    return [
        f"{title}에 관한 기사입니다.",
        "본문 요약을 가져오지 못했습니다.",
        "원문 링크를 통해 전체 내용을 확인하세요.",
    ]


# ──────────────────────────────────────────────
# 카테고리 분류
# ──────────────────────────────────────────────

def assign_category(keyword: str) -> str:
    for cat, kws in CATEGORY_MAP.items():
        if keyword in kws:
            return cat
    return "기타"


# ──────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────

def run_pipeline(keywords: list[str] = KEYWORDS) -> list[Article]:
    """전체 스크래핑·요약 파이프라인을 실행합니다."""
    seen_urls: set[str] = set()
    articles: list[Article] = []

    for keyword in keywords:
        log.info("키워드 처리 중: [%s]", keyword)
        raw_items = search_naver_news(keyword)

        for item in raw_items:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            art = Article(
                title=item["title"],
                url=url,
                source=item["source"],
                keyword=keyword,
                category=assign_category(keyword),
            )

            # 1) 본문 추출
            log.info("  본문 추출: %s", art.title[:40])
            art.body = extract_body(url, art.source)
            time.sleep(REQUEST_DELAY)

            if len(art.body) < MIN_BODY_LEN:
                log.warning("    본문 부족 — 스킵 (%d자)", len(art.body))
                continue

            # 2) LLM 요약
            log.info("  LLM 요약 중...")
            art.summary = summarize_with_llm(art.title, art.body)
            time.sleep(REQUEST_DELAY)

            articles.append(art)
            log.info("  완료: %s", art.title[:50])

    return articles


def save_results(articles: list[Article], path: str = "alt_in_news.json") -> None:
    """결과를 JSON 파일로 저장합니다."""
    data = [asdict(a) for a in articles]
    # 프론트엔드용: body 필드 제거
    for d in data:
        d.pop("body", None)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log.info("저장 완료: %s (%d건)", path, len(data))


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== Alt-In 스크래퍼 시작 ===")
    results = run_pipeline()
    save_results(results)

    # 결과 미리보기
    print(f"\n총 {len(results)}건 수집 완료\n")
    for i, art in enumerate(results[:3], 1):
        print(f"[{i}] {art.title}")
        print(f"    출처: {art.source} | 카테고리: {art.category}")
        for s in art.summary:
            print(f"    • {s}")
        print()
