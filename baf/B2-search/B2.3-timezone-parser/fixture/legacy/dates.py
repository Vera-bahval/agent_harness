from datetime import datetime

def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d")
