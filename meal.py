"""
A meal = a name plus calories and macros. One editable text line is the format people see,
edit and send around (from the iPhone Shortcut or the CLI):

    Laksa (ate half) | 520 kcal | 21p 55c 24f

Relaxed spellings are accepted so it's quick to fix by hand on a phone:

    Laksa 520 kcal 21p 55c 24f
    Laksa, 520kcal, 21 p 55 c 24 f
    EST | Laksa | 520 kcal | 21p 55c 24f      (the "EST" prefix from older Shortcuts is ignored)
"""
import re
from dataclasses import dataclass

MAX_NAME_LEN = 60       # Cronometer custom-food names stay readable in the diary
_N = r"\d+(?:\.\d+)?"
_SEP = r"[\s|,:;\-–]*"
LINE = re.compile(
    rf"^\s*(?:EST\b{_SEP})?(?P<name>.+?){_SEP}(?P<kcal>{_N})\s*kcal"
    rf"{_SEP}(?P<p>{_N})\s*p{_SEP}(?P<c>{_N})\s*c{_SEP}(?P<f>{_N})\s*f\s*$",
    re.IGNORECASE,
)


@dataclass
class Meal:
    name: str
    kcal: float
    protein: float
    carbs: float
    fat: float

    def line(self) -> str:
        name = self.name.replace("|", "/").strip()
        return f"{name} | {_fmt(self.kcal)} kcal | {_fmt(self.protein)}p {_fmt(self.carbs)}c {_fmt(self.fat)}f"

    def validate(self) -> tuple[list[str], list[str]]:
        """(errors, warnings). Errors block logging; warnings are logged but shown."""
        errors, warnings = [], []
        if not self.name.strip():
            errors.append("name is empty")
        elif len(self.name) > MAX_NAME_LEN:
            errors.append(f"name is {len(self.name)} characters (max {MAX_NAME_LEN})")
        if self.kcal <= 0:
            errors.append("kcal must be more than 0")
        elif self.kcal > 5000:
            errors.append(f"{_fmt(self.kcal)} kcal for one meal looks like a typo (max 5000)")
        for label, grams in (("protein", self.protein), ("carbs", self.carbs), ("fat", self.fat)):
            if grams < 0:
                errors.append(f"{label} can't be negative")
            elif grams > 500:
                errors.append(f"{_fmt(grams)} g {label} looks like a typo (max 500 g)")
        # Macros should roughly add up to the calories (4/4/9 kcal per gram). Only a warning:
        # alcohol, fibre and rounding all cause honest gaps.
        if not errors and self.kcal >= 100:
            from_macros = self.protein * 4 + self.carbs * 4 + self.fat * 9
            gap = abs(from_macros - self.kcal) / self.kcal
            if gap > 0.25:
                warnings.append(f"macros add up to ~{from_macros:.0f} kcal, not {_fmt(self.kcal)} "
                                f"({gap:.0%} apart); double-check the numbers")
        return errors, warnings


def parse_line(text: str) -> Meal:
    """Turn one meal line back into a Meal. Raises ValueError with a readable reason."""
    m = LINE.match(text.strip())
    if not m:
        raise ValueError(f"can't read {text.strip()!r}; expected e.g. "
                         "'Laksa | 520 kcal | 21p 55c 24f'")
    return Meal(m.group("name").strip(" |,"), float(m.group("kcal")),
                float(m.group("p")), float(m.group("c")), float(m.group("f")))


def _fmt(v: float) -> str:
    return f"{v:g}"
