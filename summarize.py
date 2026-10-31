"""Quick sanity summary of data/records.json (run after run_pipeline.py).

    python summarize.py
Paste the output back for review. Shows lead mix, ages, owner coverage, the
highest-scoring leads, and a few random ones to eyeball.
"""
import json
import random
from collections import Counter

import config


def main():
    leads = list(json.loads(config.RECORDS_JSON.read_text())["leads"].values())
    print(f"leads: {len(leads)}   with owner: {sum(1 for l in leads if l['owner_name'])}   "
          f"with mailing: {sum(1 for l in leads if l['mailing_address'])}   with parcel: {sum(1 for l in leads if l['parcel'])}")
    print("\nby signal type:")
    for k, v in Counter(t for l in leads for t in l["lead_types"]).most_common():
        print(f"  {v:6d}  {k}")
    ages = [l["oldest_open_days"] for l in leads]
    buckets = Counter("0-30" if a <= 30 else "31-60" if a <= 60 else "61-180" if a <= 180
                      else "181-365" if a <= 365 else ">365" for a in ages)
    print("\noldest open case age (days):", dict(buckets))
    dates = sorted(l["latest_date"] for l in leads if l["latest_date"])
    if dates:
        print("latest_date range:", dates[0], "->", dates[-1])
    scores = Counter(min(l["score"] // 20 * 20, 80) for l in leads)
    print("score bands:", {f"{k}-{k + 19}": scores[k] for k in sorted(scores)})
    print("out-of-state owners:", sum(1 for l in leads if l["out_of_state_owner"]),
          "  entity owners:", sum(1 for l in leads if l["owner_is_entity"]))

    def show(l):
        print(f"  [{l['score']:3d}] {l['property_address']} | {l['owner_name'] or '-'} | "
              f"{'; '.join(l['lead_types'])} | {l['oldest_open_days']}d | {'; '.join(l['violation_types'][:2])}")

    print("\ntop 10:")
    for l in sorted(leads, key=lambda x: -x["score"])[:10]:
        show(l)
    print("\n10 random:")
    for l in random.Random(1).sample(leads, min(10, len(leads))):
        show(l)


if __name__ == "__main__":
    main()
