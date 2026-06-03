# Core exceptions for ApexCrawler
# All framework errors inherit from ApexCrawlerError

class ApexCrawlerError(Exception):
    """Root exception for all ApexCrawler errors."""
    pass


# --- Retryable Errors: may succeed on retry ---

class RetryableError(ApexCrawlerError):
    """Base for errors that may succeed on retry."""
    pass


class ProxyError(RetryableError):
    """Proxy connection or authentication failure."""
    def __init__(self, proxy: str, detail: str = ""):
        self.proxy = proxy
        self.detail = detail
        super().__init__(f"Proxy error [{proxy}]: {detail}")


class RateLimitError(RetryableError):
    """Target is rate-limiting requests."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")


class EngineError(RetryableError):
    """Browser engine runtime error (crash, timeout, etc.)."""
    def __init__(self, engine: str, detail: str = ""):
        self.engine = engine
        self.detail = detail
        super().__init__(f"Engine [{engine}] error: {detail}")


class ExtractionError(RetryableError):
    """Data extraction failed but may succeed with different strategy."""
    def __init__(self, field: str = "", detail: str = ""):
        self.field = field
        self.detail = detail
        super().__init__(f"Extraction failed{f' for {field}' if field else ''}: {detail}")


class CaptchaDetected(RetryableError):
    """CAPTCHA or challenge page encountered."""
    def __init__(self, captcha_type: str = "unknown"):
        self.captcha_type = captcha_type
        super().__init__(f"CAPTCHA detected: {captcha_type}")


# --- Non-Retryable Errors: will not succeed on retry ---

class NonRetryableError(ApexCrawlerError):
    """Base for errors that retry will not fix."""
    pass


class ConfigurationError(NonRetryableError):
    """Invalid or missing configuration."""
    pass


class SchemaValidationError(NonRetryableError):
    """Extracted data failed Pydantic schema validation."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Schema validation failed: {', '.join(errors)}")


class NotSupportedError(NonRetryableError):
    """Feature or target not supported."""
    pass


# --- Fatal Errors: stop the entire crawl ---

class FatalError(ApexCrawlerError):
    """Fatal error — crawl should stop immediately."""
    pass


class AntiCrawlDetected(FatalError):
    """IP or device has been banned / blocked."""
    def __init__(self, target: str, signal: str = ""):
        self.target = target
        self.signal = signal
        super().__init__(f"Anti-crawl detected at {target}: {signal}")


class EnginePoolExhausted(FatalError):
    """All engine instances busy or failed."""
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        super().__init__(f"Engine pool exhausted (size={pool_size})")
