"""Brotli content-encoding support for reduced transfer sizes."""

import logging

logger = logging.getLogger(__name__)


def supports_brotli() -> bool:
    """Check if brotli decompression is available."""
    try:
        import brotli  # noqa: F401
        return True
    except ImportError:
        return False


def decompress_brotli(data: bytes) -> bytes:
    """Decompress brotli-encoded response body."""
    try:
        import brotli
        return brotli.decompress(data)
    except ImportError:
        logger.warning("brotli not installed — returning None")
        return None


# Brotli-aware Accept-Encoding header
BROTLI_ACCEPT_ENCODING = "gzip, deflate, br"
