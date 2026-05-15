"""
보고서 검수 승인 관리 모듈

워크플로:
1. send_review_email()  → 검수자에게 초안 발송, review_token 저장
2. check_reply()        → IMAP으로 검수자 회신 여부 확인
3. approve_manually()   → 앱 내 수동 승인(회신 없이 앱에서 직접 최종 발송)
4. reset_state()        → 발송 완료 후 상태 초기화
"""
import email as _email_module
import imaplib
import json
import logging
import smtplib
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path("data/review_state.json")


# ── 검수 메일 발송 ────────────────────────────────────────────────────────

def send_review_email(html_body: str, subject: str) -> str | None:
    """
    검수자에게 초안 발송.
    성공 시 review_token(8자리 대문자 HEX) 반환, 실패 시 None.
    """
    from core.smtp_config import load_smtp_config
    cfg = load_smtp_config()

    reviewers = [r.strip() for r in cfg.get("reviewer_emails", "").split(",") if r.strip()]
    if not reviewers:
        logger.error("검수자 이메일 미설정 (SMTP 설정 탭 확인)")
        return None
    if not cfg["smtp_user"] or not cfg["smtp_pass"]:
        logger.error("SMTP 계정 미설정")
        return None

    token = uuid.uuid4().hex[:8].upper()
    review_subj = f"[SCFI 검수요청-{token}] {subject}"

    banner = (
        "<div style='background:#fff8e1;padding:14px 18px;border-radius:8px;"
        "border-left:5px solid #f59e0b;margin-bottom:24px;font-size:14px;'>"
        "<b>📋 검수 요청 안내</b><br><br>"
        "아래 보고서 초안을 검토하신 후 다음 두 가지 방법 중 하나로 최종 발송을 진행해 주세요.<br><br>"
        "<b>① 이메일 회신:</b> 이 메일에 회신하시면 앱이 자동으로 감지하여 최종 발송 승인 상태가 됩니다.<br>"
        "<b>② 앱 직접 발송:</b> 앱에 접속하여 내용 수정 후 <em>최종 전체 발송</em> 버튼을 누르세요.<br><br>"
        f"<span style='color:#92400e;font-size:12px;'>검수 토큰: <code>{token}</code></span>"
        "</div>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = review_subj
    msg["From"]    = cfg["smtp_user"]
    msg["To"]      = ", ".join(reviewers)
    msg.attach(MIMEText(banner + html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(cfg["smtp_user"], cfg["smtp_pass"])
            smtp.sendmail(cfg["smtp_user"], reviewers, msg.as_string())

        _save_state({
            "token":            token,
            "review_subject":   review_subj,
            "original_subject": subject,
            "reviewers":        reviewers,
            "status":           "pending",
            "sent_at":          datetime.now().isoformat(),
            "approved_at":      None,
        })
        logger.info(f"검수 메일 발송 완료 → {reviewers}  (token={token})")
        return token
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 인증 실패 — 계정·앱 비밀번호 확인")
        return None
    except Exception as e:
        logger.error(f"검수 메일 발송 실패: {e}")
        return None


# ── IMAP 회신 확인 ────────────────────────────────────────────────────────

def _extract_reply_text(msg) -> str:
    """이메일 회신에서 원문 인용 전 새 내용만 추출 (plain text 우선)."""
    import re

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    from bs4 import BeautifulSoup
                    html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    body = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                    break
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

    # 원문 인용 구분선(On ... wrote: / -----Original / 발신: 등) 이전만 추출
    for pat in [
        r'\nOn .+?wrote:', r'\n[-]{3,}\s*Original', r'\n발신:', r'\n보낸\s*날짜:',
        r'\n[-]{3,}', r'\n_{3,}',
    ]:
        parts = re.split(pat, body, maxsplit=1, flags=re.IGNORECASE | re.DOTALL)
        if len(parts) > 1:
            body = parts[0]
            break

    return body.strip()


def check_reply() -> tuple[bool, str]:
    """
    IMAP으로 검수자의 회신 여부 확인.
    반환: (approved, reply_comment) — reply_comment는 회신에서 추출한 수정 코멘트.
    회신 발견 시 상태를 approved로 업데이트.
    """
    state = load_state()
    if not state:
        return False, ""
    if state.get("status") == "approved":
        return True, state.get("reply_comment", "")
    if state.get("status") != "pending":
        return False, ""

    from core.smtp_config import load_smtp_config
    cfg = load_smtp_config()

    imap_host = cfg.get("imap_host", "").strip()
    imap_port = int(cfg.get("imap_port", "993") or "993")
    imap_user = cfg.get("smtp_user", "").strip()
    imap_pass = cfg.get("smtp_pass", "").strip()

    if not all([imap_host, imap_user, imap_pass]):
        logger.info("IMAP 설정 미완성 — 회신 확인 건너뜀")
        return False, ""

    token     = state.get("token", "")
    reviewers = [r.lower() for r in state.get("reviewers", [])]
    search_kw = f"SCFI 검수요청-{token}"

    try:
        with imaplib.IMAP4_SSL(imap_host, imap_port) as imap:
            imap.login(imap_user, imap_pass)
            imap.select("INBOX")
            _, nums = imap.search(None, f'SUBJECT "{search_kw}"')
            if not nums or not nums[0]:
                logger.info(f"IMAP: 검수 토큰 {token} 관련 회신 없음")
                return False, ""

            for num in nums[0].split():
                _, data = imap.fetch(num, "(RFC822)")
                raw = data[0][1] if data and data[0] else b""
                msg = _email_module.message_from_bytes(raw)
                sender = msg.get("From", "").lower()
                subj   = msg.get("Subject", "")
                if subj.lower().startswith("re:") and any(r in sender for r in reviewers):
                    reply_comment = _extract_reply_text(msg)
                    state["status"]        = "approved"
                    state["approved_at"]   = datetime.now().isoformat()
                    state["reply_comment"] = reply_comment
                    _save_state(state)
                    logger.info(f"검수 승인 감지 (회신): {sender}, 코멘트 길이={len(reply_comment)}")
                    return True, reply_comment
    except imaplib.IMAP4.error as e:
        logger.warning(f"IMAP 로그인 실패: {e}")
    except Exception as e:
        logger.warning(f"IMAP 회신 확인 오류: {e}")

    return False, ""


# ── 상태 관리 ─────────────────────────────────────────────────────────────

def approve_manually() -> None:
    """앱에서 '직접 발송' 선택 시 수동 승인."""
    state = load_state() or {}
    state["status"]      = "approved"
    state["approved_at"] = datetime.now().isoformat()
    _save_state(state)


def reset_state() -> None:
    """최종 발송 완료 후 검수 상태 초기화."""
    if _STATE_PATH.exists():
        _STATE_PATH.unlink()


def load_state() -> dict | None:
    if not _STATE_PATH.exists():
        return None
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
