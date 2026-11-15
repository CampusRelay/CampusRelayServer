import hashlib
import hmac
import os
import re
import secrets


def get_pepper() -> bytes:
    value = os.getenv("JOIN_CODE_PEPPER")
    if not value:
        raise RuntimeError("JOIN_CODE_PEPPER 未配置")
    return value.encode("utf-8")


def normalize_join_code(code: str) -> str:
    value = code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", value):
        raise ValueError("注册码格式不正确")
    return value


def generate_join_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def hash_join_code(code: str) -> str:
    normalized = normalize_join_code(code)
    return hmac.new(
        get_pepper(),
        ("join-code:" + normalized).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_join_code(code: str, stored_hash: str) -> bool:
    try:
        candidate = hash_join_code(code)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, stored_hash)


def hash_registration_ip(ip_address: str) -> str:
    return hmac.new(
        get_pepper(),
        ("registration-ip:" + ip_address).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
