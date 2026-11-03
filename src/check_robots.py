# -*- coding: utf-8 -*-
"""Verify Amazon robots.txt permissions for product-page URLs. Read-only."""
import os
import sys
from urllib.robotparser import RobotFileParser

ROBOTS = os.path.expandvars(r"%TEMP%\amazon_robots.txt")
if not os.path.isfile(ROBOTS):
    print(f"ERROR: missing {ROBOTS}")
    sys.exit(1)

rp = RobotFileParser()
rp.parse(open(ROBOTS, encoding="utf-8", errors="replace").read().splitlines())

tests = [
    ("product page", "https://www.amazon.com/dp/B003AI2VGA"),
    ("product page 2", "https://www.amazon.com/dp/B00006HAXW"),
    ("shipping subpath", "https://www.amazon.com/dp/shipping/B003AI2VGA"),
    ("rate subpath", "https://www.amazon.com/dp/rate-this-item/B003AI2VGA"),
    ("review page", "https://www.amazon.com/product-reviews/B003AI2VGA"),
]
print(f"robots file: {ROBOTS} ({os.path.getsize(ROBOTS)} bytes)")
crawl_delay = rp.crawl_delay("*")
print(f"crawl_delay('*'): {crawl_delay}")
for label, url in tests:
    path = url.split("amazon.com")[1]
    print(f"{label:<20} can_fetch('*', {path:<40}) = {rp.can_fetch('*', url)}")
