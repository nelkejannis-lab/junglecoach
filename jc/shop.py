"""Affordable purchases along a Data Dragon recipe, with owned components credited once."""
from collections import Counter


def recipe_plan(items, target, owned, gold, occupied=None):
    inventory = Counter(owned)
    capacity = 6 - (occupied if occupied is not None else len(owned))

    def visit(item_id, remaining, ancestors):
        item = items.get(item_id) or {}
        price = (item.get("gold") or {}).get("total", 0)
        if not price or item_id in ancestors:
            return 0, {(0, 0): ()}
        if remaining[item_id]:
            remaining[item_id] -= 1
            return price, {(0, 0): ()}
        options = {(0, 0): ()}
        credit = consumed = 0
        for raw in item.get("from") or []:
            if not str(raw).isdigit():
                continue
            count = sum(remaining.values())
            part_credit, part_options = visit(int(raw), remaining, ancestors | {item_id})
            consumed += count - sum(remaining.values())
            credit += part_credit
            combined = {}
            for (spent, slots), left in options.items():
                for (cost, delta), right in part_options.items():
                    if spent + cost <= gold:
                        key = (spent + cost, slots + delta)
                        picks = left + right
                        if key not in combined or len(picks) < len(combined[key]):
                            combined[key] = picks
            options = combined
        cost = max(0, price-credit)
        if 0 < cost <= gold and item["gold"].get("purchasable", True):
            options[(cost, 1-consumed)] = (item_id,)
        return min(credit, price), options

    price = ((items.get(target) or {}).get("gold") or {}).get("total")
    if not price or inventory[target]:
        return None
    credit, options = visit(target, inventory, set())
    allowed = [(spent, parts) for (spent, slots), parts in options.items() if slots <= capacity]
    spend, parts = max(allowed, key=lambda pair: (target in pair[1], pair[0], -len(pair[1])), default=(0, ()))
    return {"cost": price-credit, "spend": spend, "parts": parts, "ready": target in parts}
