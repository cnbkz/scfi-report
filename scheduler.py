"""
APScheduler 기반 자동 폴링 스케줄러
매주 금요일 15:00~16:59 사이 5분마다 실행
별도 프로세스로 실행: python scheduler.py
"""
import logging
import os
import sys

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    handlers=[
        logging.FileHandler("logs/run.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KST              = pytz.timezone("Asia/Seoul")
POLL_START_HOUR  = int(os.getenv("POLL_START_HOUR",  "15"))
POLL_END_HOUR    = int(os.getenv("POLL_END_HOUR",    "17"))
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL_MIN", "5"))


def poll_and_run() -> None:
    from core.storage  import is_new_data_available
    from core.pipeline import run as run_pipeline

    logger.info("폴링 실행 — 신규 데이터 확인 중")
    if not is_new_data_available():
        logger.info("이번 주차 데이터 이미 존재 — 건너뜀")
        return

    logger.info("신규 데이터 감지 — 파이프라인 시작")
    result = run_pipeline(send_email=True)

    if result.success:
        logger.info(f"파이프라인 완료 | 이메일 발송: {result.email_sent}")
    else:
        logger.error(f"파이프라인 실패: {result.error}")


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone=KST)
    hour_range = f"{POLL_START_HOUR}-{POLL_END_HOUR - 1}"

    scheduler.add_job(
        poll_and_run,
        trigger="cron",
        day_of_week="fri",
        hour=hour_range,
        minute=f"*/{POLL_INTERVAL}",
        id="scfi_poll",
        misfire_grace_time=60,
    )

    logger.info(
        f"스케줄러 시작 — 매주 금요일 {POLL_START_HOUR}:00~{POLL_END_HOUR}:00 "
        f"{POLL_INTERVAL}분 간격 폴링"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
