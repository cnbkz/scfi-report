"""
주간 SCFI 분석보고서 HTML 렌더링 모듈 (Jinja2)
"""
import logging
import os
from datetime import datetime

import pytz
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

KST          = pytz.timezone("Asia/Seoul")
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


def render_report(
    calc_result: dict,
    news: list[dict],
    comment: str,
    week_year: int,
    week_no: int,
) -> str:
    env      = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = env.get_template("report.html")
    now      = datetime.now(KST)

    from core.calculator import scfi_rows

    html = template.render(
        week_year    = week_year,
        week_no      = week_no,
        generated_at = now.strftime("%Y-%m-%d %H:%M"),
        scfi_rows    = scfi_rows(calc_result),
        news         = news,
        comment      = comment,
    )
    logger.info("보고서 HTML 렌더링 완료")
    return html


def get_email_subject(week_year: int, week_no: int) -> str:
    now = datetime.now(KST)
    return f"[SCFI 주간시황] {week_year}년 {week_no}주차 ({now.strftime('%m/%d')} 기준)"
