import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meal import Meal, parse_line  # noqa: E402


@pytest.mark.parametrize("text", [
    "Laksa (ate half) | 520 kcal | 21p 55c 24f",
    "Laksa (ate half) 520 kcal 21p 55c 24f",
    "Laksa (ate half), 520kcal, 21 p 55 c 24 f",
])
def test_parse_relaxed_spellings(text):
    assert parse_line(text) == Meal("Laksa (ate half)", 520, 21, 55, 24)


def test_round_trip():
    meal = Meal("Chicken rice", 610.5, 38, 70, 18)
    assert parse_line(meal.line()) == meal


def test_pipe_in_name_cannot_break_the_line():
    assert "|" not in Meal("Fish | chips", 900, 30, 90, 45).line().split(" | ")[0]


def test_unreadable_line_explains_itself():
    with pytest.raises(ValueError, match="expected e.g."):
        parse_line("just some laksa")


def test_validation_errors_and_warnings():
    assert Meal("", 500, 20, 50, 20).validate()[0] == ["name is empty"]
    assert "typo" in Meal("Laksa", 9000, 20, 50, 20).validate()[0][0]
    errors, warnings = Meal("Laksa", 500, 10, 10, 10).validate()  # macros ~250 kcal
    assert not errors and warnings
