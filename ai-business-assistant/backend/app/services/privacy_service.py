import re
from collections.abc import Callable

PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}\d(?!\d)")
API_KEY_RE = re.compile(r"(?i)(?:api[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{12,})")
ACCESS_KEY_RE = re.compile(r"(?i)(?:access[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{12,})")
TOKEN_RE = re.compile(r"(?i)(?:token|bearer|jwt)\s*[:=]\s*['\"]?([A-Za-z0-9._\-]{16,})")
COOKIE_RE = re.compile(r"(?i)(?:cookie|set-cookie)\s*[:=]\s*[^\s;]+")
PASSWORD_RE = re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{4,})")
SECRET_RE = re.compile(r"(?i)(?:secret|client_secret)\s*[:=]\s*['\"]?([A-Za-z0-9._\-]{8,})")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")

KEYWORD_PATTERNS = {
    "address": re.compile(r"地址|住址|收货地址|联系地址|省|市|区|街道|门牌号"),
    "contract": re.compile(r"合同|协议|甲方|乙方|签约|违约|保密协议|NDA"),
    "finance": re.compile(r"财务|发票|税号|银行卡|银行账户|付款|收款|利润|成本|营收|流水"),
    "customer_list": re.compile(r"客户名单|客户资料|联系人|手机号|邮箱|CRM|会员名单"),
}

Masker = Callable[[str], str]


def _mask_phone(value: str) -> str:
    normalized = re.sub(r"\D", "", value)
    if len(normalized) >= 11:
        return f"{normalized[:3]}****{normalized[-4:]}"
    return "PHONE_****"


def _mask_email(value: str) -> str:
    name, _, domain = value.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "EMAIL_****"


def _mask_keep_edges(value: str) -> str:
    compact = value.strip()
    if len(compact) <= 8:
        return "****"
    return f"{compact[:4]}****{compact[-4:]}"


def _collect_regex_items(text: str, item_type: str, pattern: re.Pattern[str], masker: Masker, group: int = 0) -> dict | None:
    matches = []
    for match in pattern.finditer(text):
        value = match.group(group)
        if item_type == "bank_card" and not re.sub(r"\D", "", value).isdigit():
            continue
        matches.append(value)
    if not matches:
        return None
    examples = []
    for value in matches[:3]:
        masked = masker(value)
        if masked not in examples:
            examples.append(masked)
    return {"type": item_type, "count": len(matches), "examples": examples}


def _privacy_level(detected_types: set[str]) -> str:
    high_risk = {"id_card", "bank_card", "api_key", "token", "cookie", "password", "secret", "access_key", "private_key"}
    if detected_types & high_risk:
        return "highly_sensitive"
    if detected_types:
        return "sensitive"
    return "normal"


def detect_sensitive_text(text: str) -> dict:
    """Detect sensitive information without returning raw sensitive values."""
    safe_text = text or ""
    detected_items = []
    regex_checks = [
        ("phone", PHONE_RE, _mask_phone, 0),
        ("email", EMAIL_RE, _mask_email, 0),
        ("id_card", ID_CARD_RE, _mask_keep_edges, 0),
        ("bank_card", BANK_CARD_RE, _mask_keep_edges, 0),
        ("api_key", API_KEY_RE, _mask_keep_edges, 1),
        ("access_key", ACCESS_KEY_RE, _mask_keep_edges, 1),
        ("token", TOKEN_RE, _mask_keep_edges, 1),
        ("cookie", COOKIE_RE, lambda _: "COOKIE_****", 0),
        ("password", PASSWORD_RE, lambda _: "PASSWORD_****", 1),
        ("secret", SECRET_RE, _mask_keep_edges, 1),
        ("private_key", PRIVATE_KEY_RE, lambda _: "PRIVATE_KEY_****", 0),
    ]
    for item_type, pattern, masker, group in regex_checks:
        item = _collect_regex_items(safe_text, item_type, pattern, masker, group)
        if item:
            detected_items.append(item)

    for item_type, pattern in KEYWORD_PATTERNS.items():
        count = len(pattern.findall(safe_text))
        if count:
            detected_items.append({"type": item_type, "count": count, "examples": [f"{item_type}_keyword"]})

    detected_types = {item["type"] for item in detected_items}
    privacy_level = _privacy_level(detected_types)
    is_sensitive = privacy_level in {"sensitive", "highly_sensitive"}
    suggested_action = "desensitize_before_upload" if is_sensitive else "upload_allowed"
    if privacy_level == "highly_sensitive":
        suggested_action = "confirm_or_local_only"
    return {
        "is_sensitive": is_sensitive,
        "privacy_level": privacy_level,
        "detected_items": detected_items,
        "suggested_action": suggested_action,
    }


def _replace_with_mapping(text: str, pattern: re.Pattern[str], prefix: str, mapping: dict[str, str], group: int = 0) -> str:
    counter = len([key for key in mapping if key.startswith(prefix)])

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        raw = match.group(group)
        counter += 1
        placeholder = f"{prefix}_{counter:03d}"
        mapping[placeholder] = raw
        if group == 0:
            return placeholder
        start, end = match.span(group)
        return match.string[match.start():start] + placeholder + match.string[end:match.end()]

    return pattern.sub(repl, text)


def desensitize_text(text: str) -> dict:
    mapping: dict[str, str] = {}
    result = text or ""
    replacements = [
        (PHONE_RE, "PHONE", 0),
        (EMAIL_RE, "EMAIL", 0),
        (ID_CARD_RE, "ID_CARD", 0),
        (BANK_CARD_RE, "BANK_CARD", 0),
        (API_KEY_RE, "API_KEY", 1),
        (ACCESS_KEY_RE, "ACCESS_KEY", 1),
        (TOKEN_RE, "TOKEN", 1),
        (COOKIE_RE, "COOKIE", 0),
        (PASSWORD_RE, "PASSWORD", 1),
        (SECRET_RE, "SECRET", 1),
        (PRIVATE_KEY_RE, "PRIVATE_KEY", 0),
    ]
    for pattern, prefix, group in replacements:
        result = _replace_with_mapping(result, pattern, prefix, mapping, group)
    return {"desensitized_text": result, "mapping": mapping}
