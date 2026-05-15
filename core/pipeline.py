"""
SCFI 자동보고 전체 파이프라인 오케스트레이션
scrape → validate → news → calculate → save → llm → report → mail
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pytz

from core.scraper    import scrape_both, ScraperError, ValidationError
from core.news       import crawl_news
from core.calculator import calculate
from core.storage    import get_previous_week_data, save_week_data, save_week_data_for_date
from core.llm        import generate_comment
from core.reporter   import render_report, get_email_subject
from core.mailer     import send_report, send_alert

logger = logging.getLogger(__name__)
KST    = pytz.timezone("Asia/Seoul")


@dataclass
class PipelineResult:
    success:     bool
    raw_data:    dict         = field(default_factory=dict)
    calc_result: dict         = field(default_factory=dict)
    news:        list[dict]   = field(default_factory=list)
    graph_data:  list[dict]   = field(default_factory=list)
    comment:     str          = ""
    html_report: str          = ""
    week_year:   int          = 0
    week_no:     int          = 0
    email_sent:  bool         = False
    error:       str          = ""
    ran_at:      str          = ""


def run(send_email: bool = True) -> PipelineResult:
    now       = datetime.now(KST)
    week_year = now.isocalendar()[0]
    week_no   = now.isocalendar()[1]
    result    = PipelineResult(success=False, week_year=week_year,
                               week_no=week_no, ran_at=now.strftime("%Y-%m-%d %H:%M:%S"))

    # ── 1. 스크래핑 (현재 + 이전 주차 동시 수집) ───────────────────────────
    logger.info("=== 파이프라인 시작 ===")
    try:
        raw_data, prev_raw, prev_date, graph_data = scrape_both()
        result.raw_data   = raw_data
        result.graph_data = graph_data
        logger.info("스크래핑 완료")
    except (ScraperError, ValidationError) as e:
        result.error = f"데이터 수집 실패: {e}"
        logger.error(result.error)
        send_alert("스크래핑 실패", result.error)
        return result

    # ── 2. 뉴스 크롤링 (실패해도 계속) ─────────────────────────────────────
    news = crawl_news()
    result.news = news

    # ── 3. 전주 데이터 확인 — 없으면 API 이전 주차값으로 자동 초기화 ────────
    prev_data = get_previous_week_data()
    if prev_data is None and prev_raw and prev_date:
        save_week_data_for_date(prev_raw, prev_date)
        prev_data = prev_raw
        logger.info(f"이전 주차({prev_date}) 데이터 초기화 완료")

    # ── 4. 전주 대비 계산 ──────────────────────────────────────────────────
    calc_result = calculate(raw_data, prev_data)
    result.calc_result = calc_result
    logger.info("계산 완료")

    # ── 5. 이력 저장 ───────────────────────────────────────────────────────
    save_week_data(raw_data)

    # ── 6. LLM 코멘트 생성 (실패 시 기본 템플릿 사용) ────────────────────
    comment = generate_comment(calc_result, news)
    result.comment = comment

    # ── 7. 보고서 HTML 렌더링 ──────────────────────────────────────────────
    html_report = render_report(calc_result, news, comment, week_year, week_no)
    result.html_report = html_report

    # ── 8. 이메일 발송 ─────────────────────────────────────────────────────
    if send_email:
        subject = get_email_subject(week_year, week_no)
        sent    = send_report(html_report, subject)
        result.email_sent = sent
        if not sent:
            send_alert("이메일 발송 최종 실패", f"주차: {week_year}년 {week_no}주차")

    result.success = True
    logger.info("=== 파이프라인 완료 ===")
    return result
