# -*- coding: utf-8 -*-
"""Product-page crawl spider (task 2) with snapshot saving + status classification.

Reads ASIN list, crawls https://www.amazon.com/dp/<ASIN>, saves gzip snapshot,
emits manifest item with content SHA-256.

Usage (from src/crawler):
    python -m scrapy crawl product -a asin_file=../../data/raw/asin_trial20.txt \
        -O ../../snapshots/crawl_manifest_trial.jsonl
"""
import gzip
import hashlib
import os
from datetime import datetime, timezone

import scrapy

CAPTCHA_MARKERS = [
    "Type the characters you see",
    "Enter the characters you see below",
    "automated access to Amazon",
    "api-services-support@amazon.com",
    "Robot Check",
    "validateCaptcha",
]
PRODUCT_MARKERS = [
    'id="dp"',
    "dp-container",
    "productTitle",
    '"ASIN"',
    "dpID",
]


def classify(status: int, body: bytes, final_url: str) -> str:
    """Map HTTP status + content to assignment status vocabulary (EN code)."""
    if status == 404:
        return "page_not_found"
    if status in (403, 429, 503):
        return "access_restricted"
    if status != 200:
        return "http_error"
    text = body[:200_000].decode("utf-8", errors="replace")
    if any(m in text for m in CAPTCHA_MARKERS):
        return "access_restricted"  # 200 but bot-verification page
    if any(m in text for m in PRODUCT_MARKERS):
        return "success"
    return "parse_failed"  # 200 but unrecognized template


class ProductSpider(scrapy.Spider):
    name = "product"
    allowed_domains = ["amazon.com", "www.amazon.com"]

    def __init__(self, asin_file=None, snapshot_dir=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.asin_file = asin_file or os.path.join("data", "raw", "asin_list.txt")
        # project-root-relative so manifest paths work from any cwd
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        self.snapshot_dir = snapshot_dir or os.path.join(root, "snapshots", "pages")

    async def start(self):
        """Scrapy 2.13+ entry point (start_requests is ignored on 2.19)."""
        if not os.path.isfile(self.asin_file):
            self.logger.error("asin file not found: %s", self.asin_file)
            return
        os.makedirs(self.snapshot_dir, exist_ok=True)
        with open(self.asin_file, encoding="utf-8") as f:
            for line in f:
                asin = line.strip()
                if not asin or asin.startswith("#"):
                    continue
                url = f"https://www.amazon.com/dp/{asin}"
                yield scrapy.Request(
                    url,
                    callback=self.parse_product,
                    meta={"asin": asin},
                    errback=self.errback,
                )

    def parse_product(self, response):
        asin = response.meta["asin"]
        body = response.body
        attempts = response.meta.get("retry_times", 0) + 1
        status = classify(response.status, body, response.url)

        snap_path = None
        if status in ("success", "parse_failed", "access_restricted"):
            snap_path = os.path.join(self.snapshot_dir, f"{asin}.html.gz")
            with gzip.open(snap_path, "wb", compresslevel=6) as g:
                g.write(body)

        yield {
            "asin": asin,
            "requested_url": f"https://www.amazon.com/dp/{asin}",
            "final_url": response.url,
            "http_status": response.status,
            "processing_status": status,
            "content_sha256": hashlib.sha256(body).hexdigest(),
            "content_length": len(body),
            "snapshot_path": snap_path,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempts": attempts,
        }

    def errback(self, failure):
        asin = failure.request.meta.get("asin")
        exc = failure.value.__class__.__name__
        status = "timeout" if "Timeout" in exc else "fetch_failed"
        yield {
            "asin": asin,
            "requested_url": failure.request.url,
            "final_url": None,
            "http_status": None,
            "processing_status": status,
            "content_sha256": None,
            "content_length": None,
            "snapshot_path": None,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempts": failure.request.meta.get("retry_times", 0) + 1,
            "error": exc,
        }
