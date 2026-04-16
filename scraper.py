"""
Alt-In 대체투자 뉴스 스크래퍼
pip install requests beautifulsoup4
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

RSS_FEEDS = [
    {"url": "https://news.google.com/rss/search?q=부동산+펀드+투자&hl=ko&gl=KR&ceid=KR:ko",            "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=리츠+REITs+상장&hl=ko&gl=KR&ceid=KR:ko",             "category": "부동산"},
    {"url": "https://news.google.com/rss/search?q=사모펀드+PEF+운용&hl=ko&gl=KR&ceid=KR:ko",           "category": "PE"},
    {"url": "https://news.google.com/rss/search?q=바이아웃+인수합병+PE&hl=ko&gl=KR&ceid=KR:ko",        "category": "PE"},
    {"url": "https://news.google.com/rss/search?q=인프라+펀드+투자&hl=ko&gl=KR&ceid=KR:ko",            "category": "인프라"},
    {"url": "https://news.google.com/rss/search?q=신재생에너지+풍력+태양광+투자&hl=ko&gl=KR&ceid=KR:ko","category": "인프라"},
    {"url": "https://news.google.com/rss/search?q=벤처캐피탈+스타트업+투자유치&hl=ko&gl=KR&ceid=KR:ko","category": "VC"},
    {"url": "https://news.google.com/rss/search?q=모태펀드+창업투자&hl=ko&gl=KR&ceid=KR:ko",           "category": "VC"},
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MAX_PER_FEED  = 8
REQUEST_DELAY = 1.2

TAG_RULES = [
    (["리츠", "REITs"],                              "리츠"),
    (["오피스", "빌딩", "CBD", "사무"],              "오피스"),
    (["물류", "창고", "냉동"],                       "물류"),
    (["풍력", "태양광", "에너지", "수소", "발전"],   "에너지"),
    (["GTX", "도로", "철도", "공항", "교통"],        "교통"),
    (["데이터센터", "클라우드", "IDC"],              "디지털"),
    (["바이아웃", "인수", "MBO"],                    "바이아웃"),
    (["IPO", "상장", "엑시트", "매각"],              "엑시트"),
    (["세컨더리"],                                   "세컨더리"),
    (["모태펀드", "펀드 결성", "출자", "블라인드"],  "펀드결성"),
    (["바이오", "제약", "헬스케어", "신약"],         "바이오"),
    (["AI", "인공지능", "반도체"],                   "AI"),
]

def assign_tag(title):
    for keywords, tag in TAG_RULES:
        if any(kw in title for kw in keywords):
            return tag
    return "대체투자"

def format_time(pub_str):
    try:
        pub_dt = parsedate_to_datetime(pub_str).astimezone(KST)
        diff_h = int((datetime.now(KST) - pub_dt).total_seconds() // 3600)
        if diff_h < 1:  return "방금 전"
        if diff_h < 24: return f"{diff_h}시간 전"
        return f"{diff_h // 24}일 전"
    except Exception:
        return "최근"

def parse_rss(feed):
    try:
        resp = requests.get(feed["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("RSS 실패 [%s]: %s", feed["category"], e)
        return []

    # ★ xml 파서 대신 html.parser 사용 (lxml 불필요)
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
        # link 태그는 html.parser에서 다르게 파싱됨
        link_text = link.next_sibling
        if link_text:
            link_text = str(link_text).strip()
        else:
            link_text = link.get_text(strip=True)

        if not link_text or not link_text.startswith("http"):
            continue

        results.append({
            "title":    title_text,
            "url":      link_text,
            "source":   src.get_text(strip=True) if src else "구글뉴스",
            "time":     format_time(pub.get_text(strip=True)) if pub else "최근",
            "category": feed["category"],
        })

    log.info("[%s] %d건 수집", feed["category"], len(results))
    return results

BODY_SELECTORS = [
    "div#newsct_article", "div#articleBodyContents", "div#articeBody",
    "div#article_body", "div.article_body", "article",
]

def extract_body(url):
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

def auto_summarize(title, body):
    if len(body) < 80:
        return [f"{title}에 관한 기사입니다.", "본문을 불러오지 못했습니다.", "링크를 클릭해 원문을 확인하세요."]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body)
             if len(s.strip()) > 20 and "©" not in s]
    result = sents[:3]
    while len(result) < 3:
        result.append("자세한 내용은 원문을 확인하세요.")
    return result

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
    scraped_at: str  = field(default_factory=lambda: datetime.now(KST).isoformat())

def run():
    seen, articles, aid = set(), [], 1

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
            ))
            aid += 1

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
    log.info("=== Alt-In 스크래퍼 시작 ===")
    run()
