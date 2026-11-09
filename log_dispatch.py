"""
Runs in GitHub Actions when the iPhone Shortcut sends a `repository_dispatch` event:

    POST /repos/<owner>/snap-to-log/dispatches
    {"event_type": "meal", "client_payload": {"line": "Laksa | 520 kcal | 21p 55c 24f"}}

The meal line is read straight from the event file and is NEVER printed: on a public repo
anyone can read the run logs, and what someone eats is their business. Nothing is written
back to the repo either, so no meal ends up in git history.
"""
import json
import os
import sys

from meal import parse_line


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        payload = json.load(f).get("client_payload") or {}
    try:
        meal = parse_line(str(payload.get("line", "")))
    except ValueError:
        print("❌ The meal line couldn't be read (expected 'name | kcal | Np Nc Nf').")
        return 2
    errors, _ = meal.validate()
    if errors:
        print(f"❌ The meal line failed {len(errors)} check(s); nothing was logged.")
        return 2

    from cronometer import log_meals
    (_, ok, err), = log_meals([meal], headless=True)
    print("✅ Logged 1 meal to today's diary." if ok else f"❌ Logging failed: {err}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
