"""
주간 SCFI 데이터 CSV 이력 저장/조회 모듈
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

KST      = pytz.timezone("Asia/Seoul")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CSV_PATH = os.path.join(DATA_DIR, "history.csv")

COLUMNS = [
    "week_year", "week_no", "collected_at",
    "scfi_composite", "scfi_north_america_east", "scfi_north_america_west",
    "scfi_europe", "scfi_australia",
]


def _ensure_csv() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CSV_PATH):
        pd.DataFrame(columns=COLUMNS).to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        logger.info("history.csv 초기 생성")


def load_history() -> pd.DataFrame:
    _ensure_csv()
    try:
        df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
        return df if not df.empty else pd.DataFrame(columns=COLUMNS)
    except Exception as e:
        logger.warning(f"이력 로드 실패: {e}")
        return pd.DataFrame(columns=COLUMNS)


def get_previous_week_data() -> dict | None:
    """현재 주차를 제외한 직전 수집 행 반환. 없으면 None."""
    now = datetime.now(KST)
    iso = now.isocalendar()
    df  = load_history()
    if df.empty:
        return None
    prev_df = df[~((df["week_year"] == iso[0]) & (df["week_no"] == iso[1]))]
    if prev_df.empty:
        return None
    last = prev_df.iloc[-1]
    return {col: last[col] for col in COLUMNS[3:]}


def _save_row(raw_data: dict, week_year: int, week_no: int, collected_at: str) -> bool:
    _ensure_csv()
    df = load_history()
    if not df.empty:
        dup = df[(df["week_year"] == week_year) & (df["week_no"] == week_no)]
        if not dup.empty:
            logger.info(f"{week_year}년 {week_no}주차 이미 저장됨 — 건너뜀")
            return False
    row = {
        "week_year":    week_year,
        "week_no":      week_no,
        "collected_at": collected_at,
        **{k: raw_data.get(k) for k in COLUMNS[3:]},
    }
    pd.DataFrame([row]).to_csv(CSV_PATH, mode="a", header=False,
                               index=False, encoding="utf-8-sig")
    logger.info(f"{week_year}년 {week_no}주차 저장 완료")
    return True


def save_week_data(raw_data: dict) -> bool:
    """현재 주차(KST 기준) 데이터 저장."""
    now       = datetime.now(KST)
    iso       = now.isocalendar()
    return _save_row(raw_data, iso[0], iso[1], now.strftime("%Y-%m-%d %H:%M:%S"))


def save_week_data_for_date(raw_data: dict, date_str: str) -> bool:
    """특정 날짜의 데이터를 이전 주차로 저장 (히스토리 초기화용)."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        logger.warning(f"날짜 파싱 실패: {date_str}")
        return False
    iso = dt.isocalendar()
    return _save_row(raw_data, iso[0], iso[1], date_str)


def is_new_data_available() -> bool:
    """이번 주차 데이터가 아직 없으면 True."""
    now  = datetime.now(KST)
    iso  = now.isocalendar()
    df   = load_history()
    if df.empty:
        return True
    return df[(df["week_year"] == iso[0]) & (df["week_no"] == iso[1])].empty


# ── 파이프라인 결과 디스크 캐시 ───────────────────────────────────────────────

_CACHE_PATH = Path(DATA_DIR) / "pipeline_cache.json"


def save_pipeline_cache(result) -> None:
    """PipelineResult를 JSON으로 직렬화하여 캐시. 새로고침 후 세션 복원에 사용."""
    try:
        data = {
            "cached_at":  datetime.now(KST).isoformat(),
            "week_year":  result.week_year,
            "week_no":    result.week_no,
            "ran_at":     result.ran_at,
            "raw_data":   result.raw_data,
            "calc_result": result.calc_result,
            "news":       result.news,
            "graph_data": result.graph_data,
            "comment":    result.comment,
        }
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("파이프라인 결과 캐시 저장 완료")
    except Exception as e:
        logger.warning(f"캐시 저장 실패: {e}")


def load_pipeline_cache() -> dict | None:
    """캐시에서 파이프라인 결과 복원. 파일 없거나 오류면 None."""
    if not _CACHE_PATH.exists():
        return None
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"캐시 로드 실패: {e}")
        return None


def save_comment_only(comment: str) -> None:
    """파이프라인 캐시의 comment 필드만 갱신. 수동 편집 즉시 저장에 사용."""
    try:
        if _CACHE_PATH.exists():
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
        data["comment"] = comment
        data["comment_edited_at"] = datetime.now(KST).isoformat()
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"코멘트 저장 실패: {e}")
