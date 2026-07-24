#!/usr/bin/env python
"""Smoke test for KGAgent v0.3.0 - validates all major features."""

import json
import sys


def test_imports():
    """Test that all major modules can be imported."""
    print("Testing imports...")
    try:
        from kgagent import KGAgentSystem
        from kgagent.system import ExtractionRegistry
        from kgagent.extraction import ExtractionEntry, ExtractionConfig
        from kgagent.core import get_model, setup_logging
        print("✓ All imports successful")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def test_extraction_registry():
    """Test extraction type detection."""
    print("\nTesting ExtractionRegistry...")
    try:
        from kgagent.system import ExtractionRegistry

        registry = ExtractionRegistry()

        # Test type detection
        t1 = registry.detect_type("extract triples")
        t2 = registry.detect_type("temporal facts")
        t3 = registry.detect_type("hyper-relation")

        assert t1 == "triples", f"Expected 'triples', got '{t1}'"
        assert t2 == "temporal", f"Expected 'temporal', got '{t2}'"
        assert t3 == "hyper", f"Expected 'hyper', got '{t3}'"

        print("✓ ExtractionRegistry works")
        return True
    except AssertionError as e:
        print(f"✗ ExtractionRegistry assertion failed: {e}")
        return False
    except Exception as e:
        print(f"✗ ExtractionRegistry failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_simple_extraction():
    """Test simple text extraction."""
    print("\nTesting simple extraction...")
    try:
        from kgagent import KGAgentSystem

        system = KGAgentSystem()

        result = system.extract(
            "Alice works at Acme Corporation",
            extraction_type="triples"
        )

        # Validate result
        assert isinstance(result, dict), "Result should be a dict"
        assert "entities" in result or "relations" in result or "triples" in result

        print("✓ Simple extraction works")
        print(f"  Result: {json.dumps(result, ensure_ascii=False)[:100]}...")
        return True
    except Exception as e:
        print(f"✗ Simple extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config():
    """Test configuration system."""
    print("\nTesting configuration...")
    try:
        from kgagent.core import get_model, get_api_config

        model = get_model()
        config = get_api_config()

        print(f"✓ Configuration works")
        print(f"  Model: {model}")
        print(f"  API configured: {config['api_url'] is not None}")
        return True
    except Exception as e:
        print(f"✗ Configuration failed: {e}")
        return False


def main():
    """Run all smoke tests."""
    print("=" * 60)
    print("KGAgent v0.3.0 Smoke Test")
    print("=" * 60)

    tests = [
        test_imports,
        test_config,
        test_extraction_registry,
        test_simple_extraction,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"✗ Test crashed: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("=" * 60)

    if all(results):
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
