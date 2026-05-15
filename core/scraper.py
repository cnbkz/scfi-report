"""
surff.kr 스크래핑 모듈
실제 데이터: cms.surff.kr REST API (JSON)
DEMO_MODE=true 환경변수 설정 시 샘플 데이터 반환
"""
import logging
import os
import random
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

_CMS_BASE = "https://cms.surff.kr"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://surff.kr/indices",
    "Origin": "https://surff.kr",
}

# SCFI description → 필드명 매핑
_SCFI_ROUTE_MAP = {
    "COMPOSITE INDEX":                     "scfi_composite",
    "USEC":                                "scfi_north_america_east",
    "USWC":                                "scfi_north_america_west",
    "EUROPE":                              "scfi_europe",
    "AUSTRALIA/NEW ZEALAND (Melbourne)":   "scfi_australia",
}

_DEMO_CURRENT = {
    "scfi_composite":          1954.21,
    "scfi_north_america_east": 3812.0,
    "scfi_north_america_west": 2826.0,
    "scfi_europe":             1596.0,
    "scfi_australia":          1206.0,
}

_DEMO_PREVIOUS = {
    "scfi_composite":          1911.4,
    "scfi_north_america_east": 3691.0,
    "scfi_north_america_west": 2722.0,
    "scfi_europe":             1521.0,
    "scfi_australia":          1167.0,
}


class ScraperError(Exception):
    pass


class ValidationError(Exception):
    pass


def scrape_demo() -> tuple[dict, dict, str]:
    logger.info("[DEMO] 샘플 데이터 사용")
    noise = lambda v: round(v * (1 + random.uniform(-0.02, 0.02)), 2)
    current  = {k: noise(v) for k, v in _DEMO_CURRENT.items()}
    previous = dict(_DEMO_PREVIOUS)
    return current, previous, "2026-04-30"


# ── API 호출 ────────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None, retries: int = 3, delay: int = 10) -> dict:
    url = f"{_CMS_BASE}{path}"
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"API 요청 {attempt}/{retries}: {url}")
            resp = requests.get(url, headers=_HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            body = resp.json()
            if body.get("resultCode") != 100:
                raise ScraperError(f"API 오류 ({body.get('resultCode')}): {body.get('resultMessage')}")
            return body["resultObject"]
        except (requests.RequestException, ScraperError) as e:
            logger.warning(f"요청 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(delay)
    raise ScraperError(f"API 접속 실패 3회 초과: {url}")


def _fetch_scfi() -> tuple[dict, dict, str | None, list[dict]]:
    """
    SCFI 지수 수집.
    Returns: (current_data, previous_data, previous_date_str, graph_data)
    graph_data: 최근 26주 SCFI 복합지수 시계열 [{date, scfi_composite}, ...]
    """
    today = datetime.today()
    start = (today - timedelta(weeks=26)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    obj = _get("/api/freight/indicator", params={
        "freightIndex": "SCFI",
        "startDate": start,
        "endDate": end,
    })

    scfi_data     = obj.get("scfiData", {})
    previous_date = scfi_data.get("previousIndexDate")
    current_result  = {}
    previous_result = {}

    for item in scfi_data.get("data", []):
        desc  = item.get("description", "")
        field = _SCFI_ROUTE_MAP.get(desc)
        if field is None:
            continue
        cur  = item.get("currentIndex")
        prev = item.get("previousIndex")
        if cur  is not None: current_result[field]  = float(cur)
        if prev is not None: previous_result[field] = float(prev)

    graph_data = sorted(
        [{"date": d["indexDate"], "scfi_composite": float(d["scfiIndex"])}
         for d in obj.get("graphData", []) if d.get("scfiIndex") is not None],
        key=lambda x: x["date"],
    )

    return current_result, previous_result, previous_date, graph_data


# ── 검증 ────────────────────────────────────────────────────────────────────

def _validate(data: dict) -> None:
    required = list(_SCFI_ROUTE_MAP.values())
    for key in required:
        val = data.get(key)
        if val is None:
            raise ValidationError(f"수집 실패: '{key}' 값 없음")
        if val <= 0:
            raise ValidationError(f"이상값: '{key}' = {val} (양수 필요)")
    composite = data["scfi_composite"]
    if not (500 <= composite <= 10_000):
        raise ValidationError(f"SCFI 종합지수 범위 초과: {composite}")


# ── 퍼블릭 인터페이스 ────────────────────────────────────────────────────────

def scrape_both() -> tuple[dict, dict | None, str | None, list[dict]]:
    """
    현재 주차 + 이전 주차 데이터 + 26주 시계열 동시 반환.
    Returns: (current_data, previous_data_or_none, previous_date_str_or_none, graph_data)
    """
    if os.getenv("DEMO_MODE", "false").lower() == "true":
        current, previous, prev_date = scrape_demo()
        return current, previous, prev_date, []

    current, previous, prev_date, graph_data = _fetch_scfi()
    _validate(current)
    logger.info(f"스크래핑 완료: {current}")
    return current, previous if previous else None, prev_date, graph_data


def scrape() -> dict:
    """현재 주차 데이터만 반환 (하위 호환)"""
    current, _, _ = scrape_both()
    return current
