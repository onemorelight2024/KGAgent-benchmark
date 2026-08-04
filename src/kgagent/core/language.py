"""Language detection utilities."""

import re


def detect_language(text: str) -> str:
    """Detect if text is primarily Chinese or English.

    Args:
        text: Input text to detect

    Returns:
        "zh" for Chinese, "en" for English
    """
    if not text or not isinstance(text, str):
        return "en"

    text = text.strip()
    if not text:
        return "en"

    # Count different character types
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    # Count English letters (not just ASCII, but actual letters)
    english_chars = len(re.findall(r'[a-zA-Z]', text))

    total_meaningful_chars = chinese_chars + english_chars

    if total_meaningful_chars == 0:
        return "en"

    # If at least 15% of meaningful characters are Chinese, treat as Chinese.
    chinese_ratio = chinese_chars / total_meaningful_chars

    if chinese_ratio >= 0.15:
        return "zh"
    else:
        return "en"
