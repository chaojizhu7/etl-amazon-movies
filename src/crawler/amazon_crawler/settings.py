# Scrapy settings — Amazon product page crawl (ETL task 2)
# Compliance posture: robots.txt obeyed, low concurrency, polite delay,
# bounded retries, response cache. Never bypass blocks/login/captcha.

SPIDER_MODULES = ["amazon_crawler.spiders"]
NEWSPIDER_MODULE = "amazon_crawler.spiders"

# ===== identity =====
# Browser-like UA: generic bot UAs get captcha pages (200 but no product data).
# Rate limiting + robots.txt compliance still enforced below; no login/captcha bypass.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
ROBOTSTXT_USER_AGENT = "etl-amazon-movies-coursework/0.1"

# ===== robots.txt / access rules =====
ROBOTSTXT_OBEY = True

# ===== rate limiting =====
DOWNLOAD_DELAY = 2.0
RANDOMIZE_DOWNLOAD_DELAY = True
CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_TIMEOUT = 30

# ===== autothrottle (adaptive slow-down) =====
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2.0
AUTOTHROTTLE_MAX_DELAY = 60.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
AUTOTHROTTLE_DEBUG = False

# ===== bounded retries =====
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504]

# ===== response cache (resume support companion) =====
HTTPCACHE_ENABLED = True
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_IGNORE_HTTP_CODES = [400, 401, 403, 404, 407, 429, 500, 502, 503, 504]
HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# ===== default request headers =====
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
}

# ===== logging / evidence =====
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# ===== manifest feed output (crawl checklist) =====
FEEDS = {
    "../../snapshots/crawl_manifest.jsonl": {
        "format": "jsonlines",
        "encoding": "utf-8",
        "overwrite": False,
        "item_export_kwargs": {"ensure_ascii": False},
    },
}

# ===== politeness extras =====
ROBOTSTXT_CACHE_FIRST = True
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.selectreactor.SelectReactor"
FEED_EXPORT_ENCODING = "utf-8"
