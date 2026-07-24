"""Simple example of using KGAgent."""

from kgagent import KGAgentSystem

# Create system
system = KGAgentSystem()

# Example 1: Extract triples from text
print("Example 1: Relation Triples")
print("=" * 50)
result = system.extract(
    "Alice works at Acme Corporation. Bob is the CEO of Acme.",
    extraction_type="triples",
)
print(result)
print()

# Example 2: Extract from file
print("Example 2: Extract from JSON file")
print("=" * 50)
result = system.extract(
    "examples/test_data.json",
    extraction_type="hyper",
)
print(result)
print()

# Example 3: Extract with validation
print("Example 3: Extract with validation")
print("=" * 50)
result = system.extract(
    "The meeting happened on January 15, 2024.",
    extraction_type="temporal",
    validate=True,
)
print(result)
