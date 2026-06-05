"""
SMTP / IMAP 설정 영구 저장·로드 모듈
비밀번호는 Fernet 키로 암호화하여 data/smtp_config.json에 보관
"""
import json
import logging
import os
from pathlib import Path

from core.secrets import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "smtp_config.json"

_DEFAULTS = {
    "smtp_host":       "smtp.gmail.com",
    "smtp_port":       "587",
    "smtp_user":       "",
    "smtp_pass":       "",
    "recipients":      "",
    "reviewer_emails": "",   # 검수자(담당자) 이메일 — 초안 먼저 발송
    "imap_host":       "imap.gmail.com",
    "imap_port":       "993",
}


def load_smtp_config() -> dict:
    """
    저장된 SMTP/IMAP 설정 반환.
    파일 없거나 항목 누락 시 .env 환경변수 → 기본값 순으로 폴백.
    비밀번호는 복호화된 평문으로 반환.
    """
    saved: dict = {}
    if _CONFIG_PATH.exists():
        try:
            saved = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"SMTP 설정 파일 읽기 실패: {e}")

    return {
        "smtp_host":       saved.get("smtp_host")       or os.getenv("SMTP_HOST",       _DEFAULTS["smtp_host"]),
        "smtp_port":       saved.get("smtp_port")       or os.getenv("SMTP_PORT",       _DEFAULTS["smtp_port"]),
        "smtp_user":       saved.get("smtp_user")       or os.getenv("SMTP_USER",       _DEFAULTS["smtp_user"]),
        "smtp_pass":       _dec(saved.get("smtp_pass_enc", ""))
                           or os.getenv("SMTP_PASS",   _DEFAULTS["smtp_pass"]),
        "recipients":      saved.get("recipients")      or os.getenv("EMAIL_RECIPIENTS", _DEFAULTS["recipients"]),
        "reviewer_emails": saved.get("reviewer_emails") or os.getenv("REVIEWER_EMAILS", _DEFAULTS["reviewer_emails"]),
        "imap_host":       saved.get("imap_host")       or os.getenv("IMAP_HOST",       _DEFAULTS["imap_host"]),
        "imap_port":       saved.get("imap_port")       or os.getenv("IMAP_PORT",       _DEFAULTS["imap_port"]),
    }


def save_smtp_config(smtp_host: str, smtp_port: str, smtp_user: str,
                     smtp_pass: str, recipients: str,
                     reviewer_emails: str = "",
                     imap_host: str = "imap.gmail.com",
                     imap_port: str = "993") -> None:
    """SMTP/IMAP 설정을 JSON 파일에 저장. 비밀번호는 암호화."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    existing_enc = ""
    if _CONFIG_PATH.exists():
        try:
            existing_enc = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")).get("smtp_pass_enc", "")
        except Exception:
            pass

    # 비밀번호가 변경된 경우에만 재암호화 (마스킹된 값 그대로 저장 방지)
    new_enc = encrypt_value(smtp_pass.strip()) if smtp_pass.strip() else existing_enc

    data = {
        "smtp_host":       smtp_host.strip(),
        "smtp_port":       smtp_port.strip(),
        "smtp_user":       smtp_user.strip(),
        "smtp_pass_enc":   new_enc,
        "recipients":      recipients.strip(),
        "reviewer_emails": reviewer_emails.strip(),
        "imap_host":       imap_host.strip(),
        "imap_port":       imap_port.strip(),
    }
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("SMTP/IMAP 설정 저장 완료")


def config_exists() -> bool:
    return _CONFIG_PATH.exists()


def _dec(enc: str) -> str:
    if not enc:
        return ""
    return decrypt_value(enc)
