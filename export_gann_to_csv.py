import json
import csv

json_path = "forex_gann_lookup_1_3.json"
csv_path = "forex_gann_lookup_1_3.csv"

with open(json_path, "r") as f:
    data = json.load(f)

# data: dict {price_str: {...}}
rows = []

for k, v in data.items():
    price = float(k)
    row = {
        "price": price,
        "buy_at": v.get("buy_at"),
        "buy_t1": v.get("buy_t1") or v.get("buyT1"),
        "buy_t2": v.get("buy_t2") or v.get("buyT2"),
        "sell_at": v.get("sell_at"),
        "sell_t1": v.get("sell_t1") or v.get("sellT1"),
        "sell_t2": v.get("sell_t2") or v.get("sellT2"),
    }
    rows.append(row)

# sort by price
rows.sort(key=lambda r: r["price"])

with open(csv_path, "w", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["price", "buy_at", "buy_t1",
                    "buy_t2", "sell_at", "sell_t1", "sell_t2"],
    )
    writer.writeheader()
    writer.writerows(rows)

print("Exported", len(rows), "rows to", csv_path)
