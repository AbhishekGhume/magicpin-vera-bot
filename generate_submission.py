"""
Generate submission.jsonl with responses for 30 test pairs.
"""

import json
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

async def generate_submission():
    """Generate submission.jsonl from test pairs."""

    from bot import compose_message, contexts

    dataset_dir = Path(__file__).parent / "dataset" / "expanded"

    # Load all contexts
    print("Loading contexts...")
    context_count = 0

    # Load categories
    for cat_file in (dataset_dir / "categories").glob("*.json"):
        with open(cat_file) as f:
            data = json.load(f)
            slug = data.get("category_slug", data.get("slug", cat_file.stem))
            contexts[("category", slug)] = {"version": 1, "payload": data}
            context_count += 1
            print(f"  Loaded {cat_file.name}")

    # Load merchants
    merchants = {}
    for merchant_file in (dataset_dir / "merchants").glob("*.json"):
        with open(merchant_file) as f:
            data = json.load(f)
            merchant_id = data.get("merchant_id", merchant_file.stem)
            merchants[merchant_id] = data
            contexts[("merchant", merchant_id)] = {"version": 1, "payload": data}
            context_count += 1

    # Load customers
    for customer_file in (dataset_dir / "customers").glob("*.json"):
        with open(customer_file) as f:
            data = json.load(f)
            customer_id = data.get("customer_id", customer_file.stem)
            contexts[("customer", customer_id)] = {"version": 1, "payload": data}
            context_count += 1

    print(f"[OK] Loaded {context_count} contexts")

    # Load test pairs
    test_pairs_file = dataset_dir / "test_pairs.json"
    with open(test_pairs_file) as f:
        data = json.load(f)
        test_pairs = data.get("pairs", []) if isinstance(data, dict) else data

    print(f"[OK] Loaded {len(test_pairs)} test pairs")
    print(f"Generating 30 submissions...\n")

    results = []
    for idx, pair in enumerate(test_pairs[:30], 1):
        test_id = f"T{idx:02d}"
        merchant_id = pair["merchant_id"]
        trigger_id = pair["trigger_id"]

        # Load trigger
        trigger_file = dataset_dir / "triggers" / f"{trigger_id}.json"
        with open(trigger_file) as f:
            trigger = json.load(f)

        try:
            # Compose message
            action = await compose_message(trigger, merchant_id)

            if action and "body" in action:
                results.append({
                    "test_id": test_id,
                    "body": action["body"],
                    "cta": action.get("cta", ""),
                    "send_as": action.get("send_as", ""),
                    "suppression_key": action.get("suppression_key", ""),
                    "rationale": action.get("rationale", "")
                })
                trigger_kind = trigger.get("kind", "unknown")
                print(f"[OK] {test_id}: {trigger_kind}")
            else:
                print(f"[FAIL] {test_id}: No response")
        except Exception as e:
            print(f"[FAIL] {test_id}: {str(e)[:60]}")

    # Write submission.jsonl
    output_file = Path(__file__).parent / "submission.jsonl"
    with open(output_file, "w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(f"\n[OK] Generated {len(results)} responses")
    print(f"[OK] Saved to: submission.jsonl")
    return len(results)

if __name__ == "__main__":
    asyncio.run(generate_submission())
