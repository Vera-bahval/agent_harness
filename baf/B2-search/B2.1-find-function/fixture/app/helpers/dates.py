from datetime import date

def parse_date(value: str):
    return date.fromisoformat(value)
