"""
Alter-Info 대체투자 뉴스 스크래퍼
pip install requests beautifulsoup4 lxml
"""

import re, time, json, logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# ── 대체투자 관련 키워드 필터 (언론사 직접 RSS에 적용) ──────────
FILTER_KEYWORDS = [
    "리츠", "REITs", "사모펀드", "PEF", "인프라", "블라인드펀드",
    "바이아웃", "세컨더리", "엑시트", "물류센터", "데이터센터",
    "오피스", "부동산펀드", "부동산PF", "풍력", "태양광", "수소",
    "에너지저장", "ESS", "SMR", "벤처캐피탈", "스타트업", "투자유치",
    "시리즈A", "시리즈B", "모태펀드", "IPO", "M&A", "인수합병",
    "펀드결성", "LP", "GP", "카브아웃", "그린수소", "전력망",
]

def is_relevant(title: str) -> bool:
    return any(kw in title for kw in FILTER_KEYWORDS)

# ── RSS 피드 목록 ─────────────────────────────────────────────
RSS_FEEDS = [
    # 부동산
    {"url": "https://news.google.com/rss/search?q=물류센터%20(매각%20OR%20투자%20OR%20PF%20OR%20임대)&hl=ko&gl=KR&ceid=KR:ko",              "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=데이터센터%20(개발%20OR%20착공%20OR%20인프라%20OR%20전력망)&hl=ko&gl=KR&ceid=KR:ko",      "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=오피스빌딩%20(공실률%20OR%20매각%20OR%20프라임오피스)&hl=ko&gl=KR&ceid=KR:ko",           "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=리츠%20(REITs%20OR%20상장%20OR%20유상증자%20OR%20배당)&hl=ko&gl=KR&ceid=KR:ko",          "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=부동산PF%20(브릿지론%20OR%20본PF%20OR%20EOD%20OR%20경공매)&hl=ko&gl=KR&ceid=KR:ko",     "category": "부동산"},
    # 한국경제·매일경제·연합뉴스 직접 RSS (키워드 필터 적용)
    {"url": "https://rss.hankyung.com/realestate.xml",   "category": "부동산", "filter": True},
    {"url": "https://www.mk.co.kr/rss/30000041/",        "category": "부동산", "filter": True},
    {"url": "https://www.yna.co.kr/rss/real-estate.xml", "category": "부동산", "filter": True},

    # 인프라
    {"url": "https://news.google.com/rss/search?q=신재생에너지%20(태양광%20OR%20해상풍력%20OR%20수소%20OR%20그린수소)&hl=ko&gl=KR&ceid=KR:ko", "category": "인프라"},
    {"url": "https://news.google.com/rss/search?q=SMR%20(소형모듈원전%20OR%20핵융합%20OR%20원전수출)&hl=ko&gl=KR&ceid=KR:ko",               "category": "인프라"},
    {"url": "https://news.google.com/rss/search?q=폐기물%20(M%26A%20OR%20인수%20OR%20환경인프라%20OR%20수처리)&hl=ko&gl=KR&ceid=KR:ko",     "category": "인프라"},
    {"url": "https://news.google.com/rss/search?q=전력망%20(송전선로%20OR%20ESS%20OR%20에너지저장장치%20OR%20VPP)&hl=ko&gl=KR&ceid=KR:ko",  "category": "인프라"},
    {"url": "https://www.yna.co.kr/rss/economy.xml",     "category": "인프라", "filter": True},

    # PE
    {"url": "https://news.google.com/rss/search?q=사모펀드%20(PEF%20OR%20바이아웃%20OR%20경영권인수%20OR%20카브아웃)&hl=ko&gl=KR&ceid=KR:ko",  "category": "PE"},
    {"url": "https://news.google.com/rss/search?q=블라인드펀드%20(출자사업%20OR%20LP콘테스트%20OR%20펀드결성)&hl=ko&gl=KR&ceid=KR:ko",         "category": "PE"},
    {"url": "https://news.google.com/rss/search?q=기업매각%20(엑시트%20OR%20구주매각%20OR%20세컨더리%20OR%20IPO)&hl=ko&gl=KR&ceid=KR:ko",     "category": "PE"},
    {"url": "https://rss.hankyung.com/finance.xml",      "category": "PE",   "filter": True},
    {"url": "https://www.mk.co.kr/rss/40300001/",        "category": "PE",   "filter": True},

    # VC
    {"url": "https://news.google.com/rss/search?q=반도체%20(팹리스%20OR%20HBM%20OR%20NPU%20OR%20CXL)%20투자&hl=ko&gl=KR&ceid=KR:ko",                      "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=(신약개발%20OR%20바이오테크%20OR%20합성생물학%20OR%20디지털치료제)%20투자&hl=ko&gl=KR&ceid=KR:ko",        "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=(AI에이전트%20OR%20LLM%20OR%20추론인프라%20OR%20생성형AI)%20투자&hl=ko&gl=KR&ceid=KR:ko",               "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=(휴머노이드%20OR%20로보틱스%20OR%20자율주행%20OR%20스마트팩토리)%20투자&hl=ko&gl=KR&ceid=KR:ko",         "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=(핀테크%20OR%20RWA%20OR%20스테이블코인%20OR%20토큰증권)%20투자&hl=ko&gl=KR&ceid=KR:ko",                "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=(기후테크%20OR%20탄소포집%20OR%20폐배터리%20OR%20그리드)%20투자&hl=ko&gl=KR&ceid=KR:ko",               "category": "VC"},
    {"url": "https://news.google.com/rss/search?q=스타트업%20(시리즈A%20OR%20시리즈B%20OR%20팁스%20OR%20브릿지투자)&hl=ko&gl=KR&ceid=KR:ko",             "category": "VC"},
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MAX_PER_FEED  = 8
REQUEST_DELAY = 1.2

# ── 태그 자동 분류 ────────────────────────────────────────────
TAG_RULES = [
    (["리츠", "REITs"],                                  "리츠"),
    (["오피스", "빌딩", "CBD", "사무"],                   "오피스"),
    (["물류", "창고", "냉동"],                            "물류"),
    (["풍력", "태양광", "에너지", "수소", "발전", "SMR"], "에너지"),
    (["GTX", "도로", "철도", "공항", "교통"],             "교통"),
    (["데이터센터", "클라우드", "IDC"],                   "디지털"),
    (["바이아웃", "인수", "MBO", "카브아웃"],             "바이아웃"),
    (["IPO", "상장", "엑시트", "매각"],                   "엑시트"),
    (["세컨더리"],                                        "세컨더리"),
    (["모태펀드", "펀드 결성", "출자", "블라인드"],        "펀드결성"),
    (["바이오", "제약", "헬스케어", "신약"],               "바이오"),
    (["AI", "인공지능", "반도체", "LLM"],                 "AI"),
    (["로봇", "휴머노이드", "자율주행"],                   "로봇"),
    (["핀테크", "토큰", "스테이블코인"],                   "핀테크"),
    (["기후", "탄소", "폐배터리"],                        "기후테크"),
    (["폐기물", "수처리", "환경"],                        "환경"),
]

def assign_tag(title: str) -> str:
    for keywords, tag in TAG_RULES:
        if any(kw in title for kw in keywords):
            return tag
    return "대체투자"

# ── 발행 시간 포맷 ────────────────────────────────────────────
def format_time(pub_str: str) -> str:
    try:
        pub_dt = parsedate_to_datetime(pub_str).astimezone(KST)
        diff_h = int((datetime.now(KST) - pub_dt).total_seconds() // 3600)
        if diff_h < 1:  return "방금 전"
        if diff_h < 24: return f"{diff_h}시간 전"
        return f"{diff_h // 24}일 전"
    except Exception:
        return "최근"

# ── RSS 파싱 ─────────────────────────────────────────────────
def parse_rss(feed: dict) -> list[dict]:
    need_filter = feed.get("filter", False)
    try:
        resp = requests.get(feed["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("RSS 실패 [%s]: %s", feed["category"], e)
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    results = []

    for item in soup.find_all("item")[:MAX_PER_FEED]:
        title = item.find("title")
        link  = item.find("link")
        pub   = item.find("pubdate")
        src   = item.find("source")
        if not title or not link:
            continue

        title_text = re.sub(r"\s*-\s*[^-]+$", "", title.get_text(strip=True))

        # 직접 RSS는 키워드 필터 적용
        if need_filter and not is_relevant(title_text):
            continue

        link_text = link.next_sibling
        if link_text:
            link_text = str(link_text).strip()
        else:
            link_text = link.get_text(strip=True)

        if not link_text or not link_text.startswith("http"):
            continue

        try:
            pub_dt = parsedate_to_datetime(pub.get_text(strip=True)).astimezone(KST) if pub else None
            pub_date_iso = pub_dt.isoformat() if pub_dt else ""
        except Exception:
            pub_date_iso = ""

        results.append({
            "title":    title_text,
            "url":      link_text,
            "source":   src.get_text(strip=True) if src else "구글뉴스",
            "time":     format_time(pub.get_text(strip=True)) if pub else "최근",
            "pub_date": pub_date_iso,
            "category": feed["category"],
        })

    log.info("[%s] %d건 수집", feed["category"], len(results))
    return results

# ── 딜북 직접 크롤링 ──────────────────────────────────────────
def scrape_dealbook() -> list[dict]:
    try:
        resp = requests.get("https://www.dealbook.co.kr", headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        log.warning("딜북 크롤링 실패: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href  = a["href"]
        title = a.get_text(strip=True)

        if not href.startswith("https://www.dealbook.co.kr/"):
            continue
        if len(title) < 15 or href in seen:
            continue

        seen.add(href)

        cat = "부동산"
        if any(kw in title for kw in ["리츠", "오피스", "물류", "부동산", "PF"]):
            cat = "부동산"
        elif any(kw in title for kw in ["인프라", "에너지", "풍력", "수소", "ESS", "전력"]):
            cat = "인프라"
        elif any(kw in title for kw in ["스타트업", "벤처", "시리즈", "VC", "투자유치"]):
            cat = "VC"

        results.append({
            "title":    title,
            "url":      href,
            "source":   "딜북",
            "time":     "최근",
            "pub_date": "",
            "category": cat,
        })

        if len(results) >= 15:
            break

    log.info("[딜북] %d건 수집", len(results))
    return results

# ── SPI 직접 크롤링 ───────────────────────────────────────────
def scrape_spi() -> list[dict]:
    try:
        resp = requests.get("https://seoulpi.io", headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        log.warning("SPI 크롤링 실패: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href  = a["href"]
        title = a.get_text(strip=True)

        if href.startswith("/"):
            href = "https://seoulpi.io" + href
        if not href.startswith("https://seoulpi.io"):
            continue
        if len(title) < 15 or href in seen:
            continue

        seen.add(href)

        cat = "부동"
        if any(kw in title for kw in ["리츠", "오피스", "물류", "부동산", "PF"]):
            cat = "부동산"
        elif any(kw in title for kw in ["인프라", "에너지", "풍력", "수소", "ESS", "전력"]):
            cat = "인프라"
        elif any(kw in title for kw in ["스타트업", "벤처", "시리즈", "VC", "투자유치"]):
            cat = "VC"

        results.append({
            "title":    title,
            "url":      href,
            "source":   "SPI",
            "time":     "최근",
            "pub_date": "",
            "category": cat,
        })

        if len(results) >= 15:
            break

    log.info("[SPI] %d건 수집", len(results))
    return results

# ── 본문 추출 & 자동 요약 ─────────────────────────────────────
BODY_SELECTORS = [
    "div#newsct_article", "div#articleBodyContents", "div#articeBody",
    "div#article_body", "div.article_body", "div.entry-content",
    "div.post-content", "article",
]

def extract_body(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for t in soup(["script", "style", "figure", "iframe", "aside"]):
        t.decompose()
    for sel in BODY_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 80:
                return text
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
    return " ".join(paras)

def auto_summarize(title: str, body: str) -> list[str]:
    if len(body) < 80:
        return []
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body)
             if len(s.strip()) > 20 and "©" not in s]
    result = sents[:3]
    return result

# ── 데이터 모델 ───────────────────────────────────────────────
@dataclass
class Article:
    id:         int
    title:      str
    url:        str
    source:     str
    category:   str
    tag:        str
    summary:    list = field(default_factory=list)
    time:       str  = ""
    pub_date:   str  = ""
    scraped_at: str  = field(default_factory=lambda: datetime.now(KST).isoformat())

# ── 메인 ─────────────────────────────────────────────────────
def run():
    seen, articles, aid = set(), [], 1

    # RSS 피드 수집
    for feed in RSS_FEEDS:
        for item in parse_rss(feed):
            if item["url"] in seen:
                continue
            seen.add(item["url"])

            log.info("본문 추출: %s", item["title"][:40])
            body = extract_body(item["url"])
            time.sleep(REQUEST_DELAY)

            articles.append(Article(
                id=aid, title=item["title"], url=item["url"],
                source=item["source"], category=item["category"],
                tag=assign_tag(item["title"]),
                summary=auto_summarize(item["title"], body),
                time=item["time"],
                pub_date=item.get("pub_date", ""),
            ))
            aid += 1

    # 딜북·SPI 직접 크롤링
    for item in scrape_dealbook() + scrape_spi():
        if item["url"] in seen:
            continue
        seen.add(item["url"])

        log.info("본문 추출: %s", item["title"][:40])
        body = extract_body(item["url"])
        time.sleep(REQUEST_DELAY)

        articles.append(Article(
            id=aid, title=item["title"], url=item["url"],
            source=item["source"], category=item["category"],
            tag=assign_tag(item["title"]),
            summary=auto_summarize(item["title"], body),
            time=item["time"],
            pub_date=item.get("pub_date", ""),
        ))
        aid += 1

    # 최신순 정렬
    articles.sort(key=lambda a: a.pub_date or a.scraped_at or "", reverse=True)

    with open("alt_in_news.json", "w", encoding="utf-8") as f:
        json.dump([asdict(a) for a in articles], f, ensure_ascii=False, indent=2)

    log.info("완료: %d건 저장", len(articles))
    print(f"\n총 {len(articles)}건 수집!\n")
    for a in articles[:3]:
        print(f"[{a.category}] {a.title}")
        for s in a.summary:
            print(f"  • {s}")
        print()

if __name__ == "__main__":
    log.info("=== Alter-Info 스크래퍼 시작 ===")
    run()
