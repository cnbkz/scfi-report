"""
해상시황 뉴스 수집 모듈
- surff.kr/blog (주간 선사 동향, 주간 물류 동향, 데일리스크랩) — 기본 소스
- RSS 피드 (fallback)
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_CMS_BASE = "https://cms.surff.kr"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://surff.kr/blog",
    "Origin": "https://surff.kr",
}

# cateSeq → 카테고리명
BLOG_CATEGORIES = {
    2: "주간 선사 동향",
    3: "주간 물류 동향",
    9: "데일리스크랩",
}

MAX_ITEMS_PER_CATEGORY = 5


def _fetch_blog_post(cate_seq: int) -> dict | None:
    """해당 카테고리의 최신 게시글 1건 반환."""
    try:
        resp = requests.get(
            f"{_CMS_BASE}/api/board/getAll",
            headers=_HEADERS,
            params={"pageNo": 1, "pageSize": 1,
                    "searchValue1": "N", "searchValue6": "N",
                    "searchValue2": cate_seq},
            timeout=15,
        )
        posts = resp.json().get("resultObject", {}).get("list", [])
        return posts[0] if posts else None
    except Exception as e:
        logger.warning(f"blog 수집 실패 (cateSeq={cate_seq}): {e}")
        return None


def _parse_blocknote(content_json: str) -> list[dict]:
    """
    BlockNote JSON에서 뉴스 링크와 한줄 요약 추출.
    구조: link 블록 → 다음 text 블록(요약)
    """
    try:
        blocks = json.loads(content_json)
    except Exception:
        return []

    items = []
    for i, block in enumerate(blocks):
        for item in block.get("content", []):
            if item.get("type") != "link":
                continue
            href = item.get("href", "")
            title = "".join(c.get("text", "") for c in item.get("content", []))
            if not href or not title:
                continue

            # 번호 접두어 제거 (예: "1. 기사 제목" → "기사 제목")
            if len(title) > 3 and title[0].isdigit() and title[1:3] in (". ", ". "):
                title = title[3:].strip()

            summary = ""
            if i + 1 < len(blocks):
                next_items = blocks[i + 1].get("content", [])
                for ni in next_items:
                    if ni.get("type") == "text":
                        text = ni.get("text", "").strip()
                        if text.startswith("-"):
                            text = text[1:].strip()
                        summary = text[:120]
                        break

            items.append({"title": title, "url": href, "summary": summary})
            if len(items) >= MAX_ITEMS_PER_CATEGORY:
                break
        if len(items) >= MAX_ITEMS_PER_CATEGORY:
            break

    return items


def crawl_blog_news() -> list[dict]:
    """
    surff.kr/blog 3개 카테고리에서 최신 뉴스 수집.
    반환: [{"category", "post_title", "post_url", "post_date", "items": [...]}]
    """
    results = []
    for cate_seq, cate_name in BLOG_CATEGORIES.items():
        post = _fetch_blog_post(cate_seq)
        if not post:
            continue

        slug       = post.get("boardSlug", "")
        post_url   = f"https://surff.kr/blog/{slug}" if slug else ""
        post_title = post.get("boardTitle", "")
        post_date  = post.get("boardPostDate", "")
        items      = _parse_blocknote(post.get("boardContent", "[]"))

        results.append({
            "category":   cate_name,
            "post_title": post_title,
            "post_url":   post_url,
            "post_date":  post_date,
            "items":      items,
        })
        logger.info(f"[{cate_name}] {len(items)}건 수집 ({post_date})")

    return results


# ── RSS fallback (pipeline용 pr.news) ──────────────────────────────────────

KEYWORDS = ["SCFI", "컨테이너 운임", "해상 운임", "container freight", "해운", "운임지수"]

RSS_SOURCES = [
    ("한국해운신문",    "https://www.ksg.co.kr/rss/allArticle.xml"),
    ("코리아쉬핑가제트", "https://www.ksg.co.kr/rss/scfi.xml"),
    ("FreightWaves",  "https://www.freightwaves.com/news/feed"),
]

MAX_ARTICLES = 5
WEEK_AGO     = datetime.now(tz=timezone.utc) - timedelta(days=7)


def _is_recent(date_str: str) -> bool:
    if not date_str:
        return True
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= WEEK_AGO
    except Exception:
        return True


def _has_keyword(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in KEYWORDS)


def _strip_html(raw: str) -> str:
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ").strip()


def _fetch_rss(name: str, url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries:
            title   = entry.get("title", "")
            summary = _strip_html(entry.get("summary", ""))[:200]
            link    = entry.get("link", "")
            pub     = entry.get("published", "")
            if _has_keyword(title + " " + summary) and _is_recent(pub):
                results.append({
                    "title":   title,
                    "url":     link,
                    "summary": summary,
                    "date":    pub[:16] if pub else "",
                    "source":  name,
                })
        logger.info(f"[{name}] {len(results)}건 수집")
        return results
    except Exception as e:
        logger.warning(f"RSS 수집 실패 ({name}): {e}")
        return []


def crawl_news() -> list[dict]:
    """키워드 관련 금주 뉴스 최대 5건 반환 (RSS fallback)."""
    articles: list[dict] = []
    seen_urls: set[str] = set()
    for name, url in RSS_SOURCES:
        for item in _fetch_rss(name, url):
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                articles.append(item)
    articles.sort(key=lambda x: x["date"], reverse=True)
    result = articles[:MAX_ARTICLES]
    logger.info(f"뉴스 수집 완료: 총 {len(result)}건")
    return result
