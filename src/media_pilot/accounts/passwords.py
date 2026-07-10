from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

_PASSWORD_HASHER = PasswordHasher()


class InvalidPasswordError(ValueError):
    pass


def validate_password(password: str) -> str:
    """校验完整密码输入，不裁剪空白或改变字符。"""

    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidPasswordError("password must contain 8 to 128 characters")
    return password


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(validate_password(password))


def verify_password(password_hash: str, password: str) -> bool:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        return False
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
