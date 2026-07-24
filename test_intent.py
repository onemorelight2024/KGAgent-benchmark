"""Test intent classification."""

import asyncio
from kgagent.intent import IntentEntry
from kgagent.extraction.config import ExtractionConfig


async def test_intent_classification():
    """Test different intent types."""

    # Create intent agent
    config = ExtractionConfig(
        model_name="gpt-5.4",  # or your model
        work_dir="./tmp_sdk",
        permission_mode="auto",
        max_turns=1,
    )

    intent_agent = IntentEntry(config)

    # Test cases
    test_inputs = [
        "hi",
        "hello, how are you?",
        "extract triples: Alice works at Acme",
        "帮我从这段文本抽取时序关系：Alice joined Acme in 2020",
        "examples/test_data.json --type event",
        "how do I use this?",
        ":quit",
    ]

    print("Testing Intent Classification")
    print("=" * 60)

    for user_input in test_inputs:
        print(f"\nInput: {user_input}")
        print("-" * 60)

        try:
            result = await intent_agent.parse_intent_async(user_input)

            print(f"Intent: {result.get('intent')}")
            print(f"Confidence: {result.get('confidence', 'N/A')}")

            if result.get('intent') == 'extract':
                params = result.get('parameters', {})
                print(f"Extraction Type: {params.get('extraction_type')}")
                print(f"Data: {params.get('data', '')[:50]}...")
                print(f"File Path: {params.get('file_path')}")
            elif result.get('intent') == 'chat':
                print(f"Response: {result.get('response', '')[:100]}...")

            print(f"Explanation: {result.get('explanation', 'N/A')}")

        except Exception as e:
            print(f"Error: {e}")

        print()


if __name__ == "__main__":
    asyncio.run(test_intent_classification())
