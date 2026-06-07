"""OCR engine with confidence scoring for font-based anti-crawl protections.

Supports multiple backends:
- ddddocr (lightweight, offline, Chinese-focused)
- PaddleOCR (high accuracy, many languages)
- Tesseract (legacy, widely available)

Confidence scoring uses a weighted combination of:
- OCR engine native confidence
- Text coherence (dictionary word matching)
- Character distribution entropy
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """Result from OCR engine with confidence metadata."""
    text: str
    confidence: float          # 0.0–1.0 overall confidence
    engine_confidence: float   # 0.0–1.0 raw engine confidence
    coherence_score: float     # 0.0–1.0 text coherence
    entropy_score: float       # 0.0–1.0 normalized entropy (higher = more random)
    engine_name: str = ""
    processing_time_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class OCREngine:
    """Multi-backend OCR with confidence scoring for anti-crawl font cracking.

    Usage:
        engine = OCREngine(backend="ddddocr")
        result = await engine.recognize(image_bytes)
        if result.confidence > 0.7:
            logger.debug(result.text)
    """

    # Common English words for coherence scoring
    _COMMON_WORDS: frozenset[str] = frozenset({
        "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
        "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
        "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
        "or", "an", "will", "my", "one", "all", "would", "there", "their",
        "what", "so", "up", "out", "if", "about", "who", "get", "which",
        "go", "me", "when", "make", "can", "like", "time", "no", "just",
        "him", "know", "take", "people", "into", "year", "your", "good",
        "some", "could", "them", "see", "other", "than", "then", "now",
        "look", "only", "come", "its", "over", "think", "also", "back",
        "after", "use", "two", "how", "our", "work", "first", "well",
        "way", "even", "new", "want", "because", "any", "these", "give",
        "day", "most", "us",
    })

    # High-frequency Chinese characters for coherence scoring
    _COMMON_CHINESE_WORDS: frozenset[str] = frozenset({
        "的", "是", "在", "了", "不", "人", "我", "有", "他", "这",
        "中", "大", "来", "上", "国", "个", "到", "说", "们", "为",
        "子", "和", "你", "地", "出", "道", "也", "时", "年", "得",
        "就", "那", "要", "下", "以", "生", "会", "自", "着", "去",
        "之", "过", "家", "学", "对", "可", "她", "里", "后", "小",
        "么", "心", "多", "天", "而", "能", "好", "都", "然", "没",
        "日", "于", "起", "还", "发", "成", "事", "只", "作", "当",
        "想", "看", "文", "无", "开", "手", "十", "用", "主", "行",
        "方", "又", "如", "前", "所", "本", "见", "经", "头", "面",
        "公", "同", "三", "已", "老", "从", "动", "两", "长", "知",
    })

    def __init__(self, backend: str = "paddleocr", config: dict[str, Any] | None = None):
        """Initialize OCR engine.

        Args:
            backend: "paddleocr" (recommended, 95%+ Chinese), "ddddocr" (captcha), "tesseract" (legacy).
            config: Backend-specific configuration dict.
        """
        self._backend_name = backend
        self._config = config or {}
        self._engine: Any = None
        self._initialized = False

    async def _init_backend(self) -> None:
        """Lazy-initialize the selected OCR backend."""
        if self._initialized:
            return

        if self._backend_name == "ddddocr":
            try:
                import ddddocr
                self._engine = ddddocr.DdddOcr(show_ad=False)
            except ImportError:
                raise ImportError("ddddocr not installed. Install with: pip install ddddocr")
        elif self._backend_name == "paddleocr":
            try:
                from paddleocr import PaddleOCR
                lang = self._config.get("lang", "ch")
                self._engine = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            except ImportError:
                raise ImportError("paddleocr not installed. Install with: pip install paddleocr")
        elif self._backend_name == "tesseract":
            try:
                import pytesseract
                self._engine = pytesseract
            except ImportError:
                raise ImportError("pytesseract not installed. Install with: pip install pytesseract")
        else:
            raise ValueError(f"Unknown OCR backend: {self._backend_name}")

        self._initialized = True
        logger.info(f"OCR backend initialized: {self._backend_name}")

    async def recognize(self, image_bytes: bytes, *, language: str = "en",
                         backend: str | None = None) -> OCRResult:
        """Recognize text from an image with confidence scoring.

        Args:
            image_bytes: PNG/JPEG image bytes.
            language: Language hint for the OCR engine.
            backend: Override backend for this call (used by voting).

        Returns:
            OCRResult with recognized text and confidence scores.
        """
        import time

        # Use backend override for this call (e.g. when voting)
        effective_backend = backend or self._backend_name

        await self._init_backend()
        start = time.perf_counter()

        raw_text, engine_conf = await self._run_ocr(image_bytes, language, effective_backend)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Compute derived confidence scores
        coherence = self._compute_coherence(raw_text)
        entropy = self._compute_entropy(raw_text)

        # Weighted overall confidence
        # Engine confidence: 40%, Coherence: 40%, Entropy: 20%
        overall = (engine_conf * 0.4) + (coherence * 0.4) + ((1.0 - entropy) * 0.2)
        overall = max(0.0, min(1.0, overall))

        return OCRResult(
            text=raw_text,
            confidence=round(overall, 4),
            engine_confidence=round(engine_conf, 4),
            coherence_score=round(coherence, 4),
            entropy_score=round(entropy, 4),
            engine_name=effective_backend,
            processing_time_ms=round(elapsed_ms, 2),
        )

    async def _run_ocr(self, image_bytes: bytes, language: str,
                       backend_name: str | None = None) -> tuple[str, float]:
        """Run the actual OCR inference."""
        bn = backend_name or self._backend_name
        if bn == "ddddocr":
            import io
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            if backend_name and backend_name != self._backend_name:
                # Voting: create temporary engine
                import ddddocr as _ddddocr
                engine = _ddddocr.DdddOcr(show_ad=False)
            else:
                engine = self._engine
            result = engine.classification(img_bytes=image_bytes)
            if isinstance(result, (list, tuple)):
                text = "".join(str(r) for r in result) if result else ""
            else:
                text = str(result) if result else ""
            # ddddocr doesn't provide per-character confidence.
            # Check results against common character ranges instead of
            # using a naive len(text)/10 estimate.
            if text:
                common = sum(1 for c in text if (
                    ('a' <= c <= 'z') or ('A' <= c <= 'Z') or
                    ('0' <= c <= '9') or ('\u4e00' <= c <= '\u9fff') or
                    c in ' !@#$%^&*()_+-=[]{}|;:\'",.<>?/~`'
                ))
                engine_conf = common / max(1, len(text))
            else:
                engine_conf = 0.0
            return text, engine_conf

        elif bn == "paddleocr":
            import io
            import numpy as np
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            img_array = np.array(img)

            # Lazy-init PaddleOCR for voting calls
            try:
                from paddleocr import PaddleOCR
            except ImportError:
                raise ImportError("paddleocr not installed. Install with: pip install paddleocr")
            paddle = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)

            results = await asyncio.to_thread(paddle.ocr, img_array, cls=True)
            if not results or not results[0]:
                return "", 0.0

            texts: list[str] = []
            confidences: list[float] = []
            for line in results[0]:
                if line and len(line) >= 2:
                    texts.append(line[1][0])
                    confidences.append(line[1][1])

            text = " ".join(texts)
            engine_conf = sum(confidences) / len(confidences) if confidences else 0.0
            return text, engine_conf

        elif bn == "tesseract":
            import io
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            data = self._engine.image_to_data(img, output_type=self._engine.Output.DICT, lang=language)

            texts: list[str] = []
            confidences: list[float] = []
            for i, conf in enumerate(data.get("conf", [])):
                text = data["text"][i].strip()
                if text and conf != "-1":
                    texts.append(text)
                    try:
                        confidences.append(float(conf) / 100.0)
                    except ValueError:
                        confidences.append(0.5)

            text = " ".join(texts)
            engine_conf = sum(confidences) / len(confidences) if confidences else 0.0
            return text, engine_conf

        return "", 0.0

    def _compute_coherence(self, text: str) -> float:
        """Score text coherence by checking against common dictionary words.

        Higher score = more dictionary words, suggesting correct OCR output.
        Uses Chinese dictionary when the text contains CJK characters,
        English dictionary otherwise.

        Args:
            text: OCR output text.

        Returns:
            Coherence score from 0.0 (random characters) to 1.0 (all dictionary words).
        """
        if not text or not text.strip():
            return 0.0

        # Detect Chinese text (any CJK unified ideograph range)
        has_cjk = any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf'
                      for c in text)

        if has_cjk:
            # Chinese: extract individual characters
            chars = [c for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf']
            if not chars:
                return 0.0
            match_count = sum(1 for c in chars if c in self._COMMON_CHINESE_WORDS)
            return match_count / len(chars)
        else:
            # English: extract words
            words = re.findall(r"[a-zA-Z]+", text.lower())
            if not words:
                return 0.0
            match_count = sum(1 for w in words if w in self._COMMON_WORDS)
            return match_count / len(words)

    def _compute_entropy(self, text: str) -> float:
        """Compute normalized character distribution entropy.

        Low entropy = repetitive text (good OCR output).
        High entropy = random/scattered characters (likely OCR noise).

        Args:
            text: OCR output text.

        Returns:
            Normalized entropy score from 0.0 (low entropy) to 1.0 (high entropy).
        """
        if not text:
            return 1.0

        char_counts = Counter(text)
        total = len(text)
        entropy = 0.0
        for count in char_counts.values():
            p = count / total
            entropy -= p * math.log2(p)

        # Normalize: max entropy is log2(unique chars), bounded to [0, 1]
        unique = len(char_counts)
        if unique <= 1:
            return 0.0
        max_entropy = math.log2(unique)
        if max_entropy == 0:
            return 0.0

        return min(1.0, entropy / max_entropy)

    # ── Batch Recognition ───────────────────────────────────

    async def recognize_batch(
        self, images: list[bytes], *, language: str = "en", min_confidence: float = 0.0
    ) -> list[OCRResult]:
        """Recognize text from multiple images.

        Args:
            images: List of image byte arrays.
            language: Language hint.
            min_confidence: Filter out results below this confidence threshold.

        Returns:
            List of OCRResult (filtered if min_confidence > 0).
        """
        results: list[OCRResult] = []
        for img in images:
            result = await self.recognize(img, language=language)
            if result.confidence >= min_confidence:
                results.append(result)
        return results

    # ── Dual-Engine Voting ───────────────────────────────────

    async def recognize_with_voting(self, image_bytes: bytes) -> OCRResult:
        """Recognize text using both ddddocr and paddleocr, voting on result.

        If both engines agree, return the result directly.
        Otherwise, return the result with higher confidence.

        Args:
            image_bytes: PNG/JPEG image bytes.

        Returns:
            OCRResult with voted text and confidence.
        """
        r1 = await self.recognize(image_bytes, backend="ddddocr")
        r2 = await self.recognize(image_bytes, backend="paddleocr")

        if r1.text == r2.text:
            return r1

        return r1 if r1.confidence >= r2.confidence else r2

    # ── Glyph-to-Character Mapping ──────────────────────────

    async def build_glyph_map(self, glyph_images: list[tuple[str, bytes]]) -> dict[str, str]:
        """Build a glyph-name to character mapping via OCR.

        Each glyph image is OCR'd individually, and confidence must exceed
        the configured threshold (default 0.8).

        Args:
            glyph_images: List of (glyph_name, image_bytes) tuples.

        Returns:
            Dict mapping glyph names to recognized characters.
        """
        mapping: dict[str, str] = {}
        threshold = self._config.get("glyph_confidence_threshold", 0.8)

        for glyph_name, img_bytes in glyph_images:
            result = await self.recognize(img_bytes)
            if result.confidence >= threshold and len(result.text.strip()) == 1:
                mapping[glyph_name] = result.text.strip()
            elif result.text.strip():
                logger.debug(f"Glyph '{glyph_name}': low confidence ({result.confidence:.2f}) for '{result.text}'")

        return mapping
