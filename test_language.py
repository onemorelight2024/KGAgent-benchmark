"""Test language detection."""

from kgagent.core.language import detect_language


def test_language_detection():
    """Test language detection function."""

    test_cases = [
        ("Alice works at Acme Corporation", "en"),
        ("爱丽丝在Acme公司工作", "zh"),
        ("张三创办了Tech公司", "zh"),
        ("2020年3月，Bob met Carol", "zh"),  # Mixed, but >30% Chinese
        ("Hello 你好 world", "en"),  # Mixed, but <30% Chinese
        ("", "en"),  # Empty defaults to en
        ("123456", "en"),  # Numbers default to en
        ("Apollo 11 was launched on July 16, 1969", "en"),
        ("曼哈顿计划于1942年8月13日正式启动", "zh"),
    ]

    print("Language Detection Tests")
    print("=" * 60)

    for text, expected in test_cases:
        detected = detect_language(text)
        status = "✓" if detected == expected else "✗"
        print(f"{status} Input: {text[:50]}")
        print(f"  Expected: {expected}, Detected: {detected}")
        print()


if __name__ == "__main__":
    test_language_detection()
