import csv
import statistics
import sys

with open(sys.argv[1], newline="", encoding="utf-8") as stream:
    values = [float(row["value"]) for row in csv.DictReader(stream)]
print(statistics.median(values))
