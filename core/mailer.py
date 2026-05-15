"""
SMTP 이메일 자동 발송 모듈 (최대 3회 재시도)
"""
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _get_smtp_params() -> tuple[str, int, str, str, list[str]]:
    """저장된 설정 → .env 순서로 SMTP 파라미터 반환."""
    try:
        from core.smtp_config import load_smtp_config
        cfg = load_smtp_config()
        host       = cfg["smtp_host"]
        port       = int(cfg["smtp_port"])
        user       = cfg["smtp_user"]
        password   = cfg["smtp_pass"]
        recipients = [r.strip() for r in cfg["recipients"].split(",") if r.strip()]
    except Exception:
        host       = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port       = int(os.getenv("SMTP_PORT", "587"))
        user       = os.getenv("SMTP_USER", "")
        password   = os.getenv("SMTP_PASS", "")
        recipients = [r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
    return host, port, user, password, recipients


def send_report(html_body: str, subject: str, retries: int = 3) -> bool:
    """
    HTML 보고서 이메일 발송.
    성공 시 True, 최종 실패 시 False 반환.
    """
    host, port, user, password, recipients = _get_smtp_params()

    if not all([user, password, recipients]):
        logger.error("SMTP 설정 불완전 — EMAIL_RECIPIENTS, SMTP_USER, SMTP_PASS 확인")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for attempt in range(1, retries + 1):
        try:
            logger.info(f"이메일 발송 시도 {attempt}/{retries} → {recipients}")
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(user, password)
                smtp.sendmail(user, recipients, msg.as_string())
            logger.info(f"이메일 발송 완료: {recipients}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP 인증 실패 — 계정/앱 비밀번호 확인")
            return False   # 인증 실패는 재시도 불필요
        except Exception as e:
            logger.warning(f"발송 실패 ({attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(5)

    logger.error("이메일 최종 발송 실패 (3회 초과)")
    return False


def send_alert(subject: str, body: str) -> None:
    """오류 발생 시 담당자 알림 발송 (plain text)"""
    host, port, user, password, recipients = _get_smtp_params()

    if not all([user, password, recipients]):
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[SCFI 자동화 오류] {subject}"
    msg["From"]    = user
    msg["To"]      = ", ".join(recipients)

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, recipients, msg.as_string())
        logger.info("오류 알림 발송 완료")
    except Exception as e:
        logger.error(f"오류 알림 발송 실패: {e}")
