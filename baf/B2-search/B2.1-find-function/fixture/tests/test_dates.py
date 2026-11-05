from app.helpers.dates import parse_date

def test_date():
    assert parse_date("2026-11-05").year == 2026
