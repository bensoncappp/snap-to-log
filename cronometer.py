"""
Put a Meal into TODAY's Cronometer diary as a custom food, by driving the website with
Playwright. Cronometer has no public API for personal diaries, so the browser is the
only way in.

    python cronometer.py "Laksa (ate half) | 520 kcal | 21p 55c 24f"
    python cronometer.py "..." --show        # watch the browser do it

Two steps, each checked before moving on:
  1. create a custom food with the meal's name, calories and macros (one serving);
  2. add it to today's diary, then confirm the name shows and today's total rose by its calories.

Login reuses a saved session (auth.json, git-ignored) and falls back to
CRONO_EMAIL / CRONO_PASSWORD from .env.
"""
import argparse
import fcntl
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from config import HERE, load_env
from meal import Meal, parse_line

AUTH = HERE / "auth.json"
LOGS = HERE / "logs"
LOCK = HERE / ".cronometer.lock"
LOGIN_URL = "https://cronometer.com/login/"

COOKIE_BUTTONS = [  # Cookiebot's standard ids, most private choice first
    "#CybotCookiebotDialogBodyButtonDecline",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
]


def log_run(status: str, detail: str) -> None:
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "run.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {status:<8} {detail}\n")


def dismiss_cookie_banner(page, wait_ms: int = 4000) -> None:
    """A fresh browser gets a cookie banner that covers the login button."""
    dialog = page.locator("#CybotCookiebotDialog")
    try:
        dialog.wait_for(state="visible", timeout=wait_ms)
    except Exception:
        return
    for sel in COOKIE_BUTTONS:
        btn = page.locator(sel).first
        try:
            if btn.count() and btn.is_visible():
                btn.click(timeout=5_000)
                dialog.wait_for(state="hidden", timeout=5_000)
                return
        except Exception:
            continue


def ensure_logged_in(page) -> None:
    page.goto(LOGIN_URL)
    dismiss_cookie_banner(page)
    email_box = page.get_by_role("textbox", name="Email")
    diary_link = page.get_by_role("link", name=re.compile("Diary")).first
    homepage_login = page.get_by_role("link", name=re.compile(r"^\s*Log In\s*$", re.I)).first
    # Whichever appears first says where we are: saved session works / login form / homepage.
    email_box.or_(diary_link).or_(homepage_login).first.wait_for(timeout=20_000)
    if diary_link.is_visible():
        return
    if not email_box.is_visible():
        homepage_login.click()
        email_box.wait_for(timeout=20_000)
    email, password = os.environ.get("CRONO_EMAIL"), os.environ.get("CRONO_PASSWORD")
    if not email or not password:
        raise RuntimeError("not logged in and CRONO_EMAIL / CRONO_PASSWORD are not set in .env")
    email_box.fill(email)
    page.get_by_role("textbox", name="Password").fill(password)
    dismiss_cookie_banner(page, wait_ms=1_000)
    page.get_by_role("button", name="LOG IN").click()
    try:
        diary_link.wait_for(timeout=20_000)
    except Exception:
        raise RuntimeError("login didn't reach the diary (wrong password, or a verification step)")


def open_todays_diary(page) -> None:
    page.get_by_role("link", name=re.compile("Diary")).first.click()
    try:  # never log to the wrong day
        page.get_by_text("Today", exact=True).first.wait_for(timeout=15_000)
    except Exception:
        raise RuntimeError("the diary isn't showing 'Today', so nothing was logged")


# ---------- the steps (from a `playwright codegen` recording) -------------------
# Cronometer is a GWT app: element ids (gwt-uid-1262) and class names (GJANQR2GJ) change between
# releases, so these locators lean on visible text and table rows instead.

NUTRIENT_ROWS = (  # (row label on the nutrition label, Meal attribute), in the label's top-down order
    (r"^\s*Calories\b", "kcal"),
    (r"^\s*Total Fat\b", "fat"),
    (r"^\s*Total Carbohydrate\b", "carbs"),
    (r"^\s*Protein\b", "protein"),
)


def settled(page, read, settle_ms: int = 2_500, max_ms: int = 12_000):
    """Call read(page) until its answer stops changing: the diary fills in gradually."""
    start = page.evaluate("Date.now()")
    last, stable_since = None, start
    while True:
        now = page.evaluate("Date.now()")
        value = read(page)
        if value != last:
            last, stable_since = value, now
        elif now - stable_since >= settle_ms:
            return value
        if now - start >= max_ms:
            return last
        page.wait_for_timeout(400)


def diary_kcal(page) -> float:
    """Today's total from the diary's group headers ("3809 kcal • 202 g protein • …")."""
    total = 0.0
    for text in page.get_by_text(re.compile(r"[\d,.]+\s*kcal\s*•")).all_inner_texts():
        m = re.search(r"([\d,.]+)\s*kcal", text)
        if m:
            total += float(m.group(1).replace(",", ""))
    return total


def name_shown(page, name: str) -> bool:
    return page.get_by_text(name, exact=True).count() > 0


def set_nutrient(page, label: str, value: float) -> None:
    """
    The editor is a nutrition label. An empty value shows as "-"; double-clicking that dash
    (not the table cell around it) opens a small text box in the same row. Enter saves it and
    may open the next row's box, which the next call simply reuses.
    """
    row = page.get_by_role("row", name=re.compile(label)).first
    row.wait_for(timeout=10_000)
    box = row.get_by_role("textbox")
    if not box.count():
        row.get_by_text("-", exact=True).first.dblclick()
    box.first.wait_for(timeout=5_000)
    box.first.fill(f"{value:g}")
    box.first.press("Enter")
    page.wait_for_timeout(300)  # let the label redraw before the next row


class Step:
    """Names the step in any error, so a failure says WHERE it stopped (never the meal itself)."""
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, kind, err, tb):
        if err is not None and not isinstance(err, KeyboardInterrupt):
            first = str(err).splitlines()[0] if str(err) else kind.__name__
            raise RuntimeError(f"step '{self.name}': {first}") from None


def create_custom_food(page, meal: Meal) -> None:
    with Step("open the new-food editor"):
        page.get_by_role("button", name=re.compile(r"Foods\s*$")).first.click()
        page.get_by_role("link", name="Custom Foods").click()
        page.get_by_role("button", name=re.compile(r"CREATE FOOD", re.I)).click()

    with Step("food name"):
        # The box sits right after its "Food Name" label (its generated id changes every visit).
        label = page.get_by_text("Food Name", exact=True).first
        label.wait_for(timeout=15_000)
        name_box = label.locator("xpath=following::input[1]")
        # Type it key by key: GWT ignores a pasted value (.fill) and saved "New Food" instead.
        name_box.click()
        name_box.press("ControlOrMeta+A")
        name_box.press_sequentially(meal.name, delay=20)
        name_box.press("Tab")  # GWT saves a field when focus leaves it
        if name_box.input_value().strip() != meal.name:
            raise RuntimeError("the name didn't stick in the editor")
        # The card's title follows the saved name; if it still says "New Food", stop here.
        expect(page.get_by_text(meal.name, exact=True).first).to_be_visible(timeout=5_000)

    for label_re, attr in NUTRIENT_ROWS:
        with Step(f"set {attr}"):
            set_nutrient(page, label_re, getattr(meal, attr))

    with Step("save the food"):
        save = page.get_by_role("button", name="Save Changes")
        expect(save).to_be_enabled(timeout=5_000)
        save.click()


def add_to_diary(page) -> None:
    """From the saved food: Add to Diary, then confirm in the pop-up (default: 1 serving, today)."""
    with Step("add to diary"):
        _add_to_diary(page)


def _add_to_diary(page) -> None:
    add = page.get_by_role("button", name="Add to Diary")
    add.first.click()
    expect(add).to_have_count(2, timeout=10_000)  # the pop-up brings its own button
    add.last.click()
    expect(add).to_have_count(1, timeout=10_000)  # pop-up closed


# ---------- one browser session for several meals --------------------------------

def log_meals(meals: list[Meal], headless: bool = True) -> list[tuple[Meal, bool, str]]:
    """Log each meal; returns [(meal, ok, error)]. One bad meal never stops the others."""
    load_env()
    results = []
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one run at a time: no two logins fighting
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=AUTH if AUTH.exists() else None)
            page = context.new_page()
            try:
                ensure_logged_in(page)
                context.storage_state(path=AUTH)
                for meal in meals:
                    try:
                        with Step("open today's diary"):
                            open_todays_diary(page)
                        before = settled(page, diary_kcal)
                        create_custom_food(page, meal)
                        add_to_diary(page)
                        page.reload()  # make sure the diary list is fresh before checking
                        open_todays_diary(page)
                        # Proof it landed once: the name is in today's diary AND the day's total
                        # went up by this meal's calories (catches a missing or a double entry).
                        # Counting the name alone double-counts: Cronometer also shows the newly
                        # added food in a detail panel.  Messages stay generic: run logs can be public.
                        after = settled(page, diary_kcal, settle_ms=1_500)
                        added = after - before
                        if not name_shown(page, meal.name):
                            raise RuntimeError("the new food isn't showing in today's diary")
                        if abs(added - meal.kcal) > max(2, meal.kcal * 0.01):
                            raise RuntimeError("today's total didn't rise by exactly this meal's calories "
                                               "(missing or double entry?)")
                        log_run("LOGGED", meal.line())
                        results.append((meal, True, ""))
                    except Exception as e:
                        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                        LOGS.mkdir(exist_ok=True)
                        try:
                            page.screenshot(path=LOGS / f"error-{datetime.now():%Y%m%d-%H%M%S}.png")
                        except Exception:
                            pass
                        log_run("FAILED", f"{meal.line()}  <-  {msg}")
                        results.append((meal, False, msg))
                        page.keyboard.press("Escape")
            except Exception as e:
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                log_run("FAILED", f"login  <-  {msg}")
                results += [(m, False, f"login failed: {msg}") for m in meals[len(results):]]
            finally:
                context.close()
                browser.close()
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Log one meal line into today's Cronometer diary")
    ap.add_argument("line", help='e.g. "Laksa | 520 kcal | 21p 55c 24f"')
    ap.add_argument("--show", action="store_true", help="show the browser")
    args = ap.parse_args()
    try:
        meal = parse_line(args.line)
    except ValueError as e:
        print(f"❌ {e}")
        return 2
    errors, warnings = meal.validate()
    for w in warnings:
        print(f"⚠️  {w}")
    if errors:
        print("❌ " + "; ".join(errors))
        return 2
    (_, ok, err), = log_meals([meal], headless=not args.show)
    print(f"✅ Logged: {meal.line()}" if ok else f"❌ {err} (screenshot in logs/)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())