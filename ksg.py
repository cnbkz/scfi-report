"""
ksg.co.kr 공개 JSON → 항로별 SCFI 주간 시계열 반환
무료 제공 데이터: 종합지수, 북미서안(USWC), 북유럽
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_BASE     = "https://www.ksg.co.kr/upload/shipschedule_jsons"
_CACHE    = Path(__file__).parent.parent / "data" / "ksg_route_cache.json"
_CACHE_TTL_H = 12  # 캐시 유효시간 (시간)

_ROUTE_URLS = {
    "scfi_composite":          f"{_BASE}/scfi_total.json",
    "scfi_north_america_west": f"{_BASE}/scfi_uswc.json",
    "scfi_europe":             f"{_BASE}/scfi_europe.json",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.ksg.co.kr/shippingGraph/scfi_graph_total_free.jsp",
}


def _ts_to_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _load_cache() -> dict | None:
    if not _CACHE.exists():
        return None
    try:
        data = json.loads(_CACHE.read_text(encoding="utf-8"))
        fetched_at = data.get("_fetched_at", 0)
        age_hours = (time.time() - fetched_at) / 3600
        if age_hours < _CACHE_TTL_H:
            return data
    except Exception:
        pass
    return None


def _save_cache(route_data: dict) -> None:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(route_data)
    payload["_fetched_at"] = time.time()
    _CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def fetch_route_history(weeks: int = 26) -> dict[str, list[dict]]:
    """
    ksg.co.kr 공개 JSON에서 항로별 SCFI 주간 시계열 반환.
    캐시 유효 시 캐시 사용, 만료 시 재요청.
    Returns: {field: [{"date": "YYYY-MM-DD", "value": float}, ...]}
    """
    cached = _load_cache()
    if cached:
        logger.info("ksg 캐시 사용")
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    result = {}
    session = requests.Session()
    session.headers.update(_HEADERS)

    for field, url in _ROUTE_URLS.items():
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            items = r.json()
            recent = items[-weeks:]
            result[field] = [
                {"date": _ts_to_date(ts), "value": float(val)}
                for ts, val in recent
                if val is not None
            ]
            logger.info(f"ksg {field}: {len(result[field])}건 수집")
        except Exception as e:
            logger.warning(f"ksg {field} 조회 실패: {e}")

    if result:
        _save_cache(result)

    return result
