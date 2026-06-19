from datetime import datetime
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "test")

from app.routes.prospects import _monthly_followup_target


def test_monthly_followup_starts_next_month():
    anchor = datetime(2026, 6, 20, 9, 30)
    assert _monthly_followup_target(anchor, 1) == datetime(2026, 7, 20, 9, 30)


def test_monthly_followup_uses_last_day_when_needed():
    anchor = datetime(2026, 1, 1, 9, 30)
    assert _monthly_followup_target(anchor, 1, 31) == datetime(2026, 2, 28, 9, 30)
    assert _monthly_followup_target(anchor, 1, 29) == datetime(2026, 2, 28, 9, 30)


if __name__ == "__main__":
    test_monthly_followup_starts_next_month()
    test_monthly_followup_uses_last_day_when_needed()
