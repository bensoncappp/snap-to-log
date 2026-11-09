"""
Photo in, diary entry out: estimate a meal from a photo, let you check the numbers,
then log it to today's Cronometer diary as a custom food.

    python snap_to_log.py lunch.jpg
    python snap_to_log.py lunch.jpg --note "ate half, no rice"
    python snap_to_log.py --text "Laksa | 520 kcal | 21p 55c 24f"   # skip the photo
    python snap_to_log.py lunch.jpg --dry-run                       # estimate only
    python snap_to_log.py lunch.jpg --yes                           # no confirmation prompt
"""
import argparse
import sys
from pathlib import Path

from config import load_env
from meal import parse_line


def main() -> int:
    ap = argparse.ArgumentParser(description="Snap a meal, log it to Cronometer")
    ap.add_argument("photo", nargs="?", type=Path)
    ap.add_argument("--note", default="", help='what the photo can\'t show, e.g. "ate half"')
    ap.add_argument("--text", help="a meal line to log instead of a photo")
    ap.add_argument("--provider", choices=["gemini", "claude"])
    ap.add_argument("--yes", action="store_true", help="log without asking")
    ap.add_argument("--dry-run", action="store_true", help="estimate and stop")
    ap.add_argument("--show", action="store_true", help="show the browser while logging")
    args = ap.parse_args()
    if not args.photo and not args.text:
        ap.error("give a photo or --text")
    load_env()

    if args.text:
        line = args.text
    else:
        from estimate import estimate, show
        result = estimate(args.photo, args.note, args.provider)
        show(result)
        line = result["meal"].line()

    if args.dry_run:
        return 0
    if not args.yes:
        edited = input("\nPress Enter to log it, type a corrected line, or 'n' to cancel:\n> ").strip()
        if edited.lower() == "n":
            print("Cancelled.")
            return 0
        line = edited or line

    try:
        meal = parse_line(line)
    except ValueError as e:
        print(f"❌ {e}")
        return 2
    errors, warnings = meal.validate()
    for w in warnings:
        print(f"⚠️  {w}")
    if errors:
        print("❌ " + "; ".join(errors))
        return 2

    from cronometer import log_meals
    (_, ok, err), = log_meals([meal], headless=not args.show)
    print(f"✅ Logged to today's diary: {meal.line()}" if ok else f"❌ {err} (screenshot in logs/)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
