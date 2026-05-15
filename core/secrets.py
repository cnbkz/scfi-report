"""
자격증명 암호화 관리 모듈 (Fernet 대칭 암호화)
키 파일: .scfi.key (프로젝트 루트, gitignore 필수)
"""
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", ".scfi.key")


def _get_or_create_key() -> bytes:
    # Streamlit Cloud / 환경변수 우선 (secrets.toml의 FERNET_KEY)
    env_key = os.getenv("FERNET_KEY", "").strip()
    if env_key:
        return env_key.encode()

    key_path = os.path.abspath(_KEY_PATH)
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    try:
        with open(key_path, "wb") as f:
            f.write(key)
        os.chmod(key_path, 0o600)
        logger.info(f"암호화 키 생성: {key_path}")
    except OSError:
        logger.warning("키 파일 저장 실패 — 세션 내 임시 키 사용")
    return key


def encrypt_value(plaintext: str) -> str:
    """평문 → Fernet 암호화 문자열."""
    return Fernet(_get_or_create_key()).encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Fernet 암호화 문자열 → 평문. 실패 시 빈 문자열 반환."""
    try:
        return Fernet(_get_or_create_key()).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as e:
        logger.warning(f"복호화 실패: {e}")
        return ""


def get_ksg_credentials() -> tuple[str, str]:
    """
    KSG 자격증명 반환 (복호화 포함).
    .env에 KSG_USERNAME_ENC / KSG_PASSWORD_ENC 있으면 복호화,
    없으면 KSG_USERNAME / KSG_PASSWORD 평문 그대로 사용.
    """
    username_enc = os.getenv("KSG_USERNAME_ENC", "")
    password_enc = os.getenv("KSG_PASSWORD_ENC", "")

    if username_enc and password_enc:
        return decrypt_value(username_enc), decrypt_value(password_enc)

    # fallback: 평문 (구형 설정 호환)
    return os.getenv("KSG_USERNAME", ""), os.getenv("KSG_PASSWORD", "")
