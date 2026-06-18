import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def short_slug(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def ref_token(length: int = 8) -> str:
    """Compact 8-char token used in the WA prefilled message bracket [c_xxxxxxxx]."""
    return "c_" + "".join(secrets.choice(_ALPHABET) for _ in range(length))
