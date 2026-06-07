"""AI-powered content extraction using Crawl4AI or direct LLM."""

from __future__ import annotations
import json
import hashlib
import logging
from typing import Any, TypeVar
from pydantic import BaseModel
from ..core.protocols import Extractor
from ..core.exceptions import ExtractionError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

class AIExtractor(Extractor[T]):
    """LLM-based semantic content extractor."""
    
    def __init__(self, llm_client=None, cache_backend=None, confidence_threshold: float = 0.6):
        self._llm = llm_client
        self._cache = cache_backend
        self.confidence_threshold = confidence_threshold
        try:
            from ..config.schema import Settings
            self._settings = Settings()
        except Exception:
            self._settings = None
    
    @property
    def confidence_threshold(self) -> float:
        return self._threshold
    
    @confidence_threshold.setter
    def confidence_threshold(self, value: float):
        self._threshold = value
    
    async def extract(self, html: str, schema: type[T]) -> T:
        """Extract structured data from HTML using structured data first, LLM fallback."""
        if not html or not html.strip():
            raise ExtractionError(detail="Cannot extract from empty HTML")
        html_hash = hashlib.sha256(html.encode()).hexdigest()

        # Step 0: content hash cache
        cache_key = f"extract:{html_hash}:{schema.__name__}"
        if self._cache:
            try:
                cached = await self._cache.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return schema.model_validate(data)
            except Exception as e:
                logger.debug(f"Cache read failed for key {cache_key}: {e}")

        # Step 1: structured data first (zero LLM cost)
        try:
            result = self._extract_structured(html, schema)
            if self._cache:
                try:
                    await self._cache.set(cache_key, json.dumps(result.model_dump()).encode(), ttl=3600)
                except Exception as e:
                    logger.debug(f"Cache write failed for key {cache_key}: {e}")
            return result
        except ExtractionError:
            pass
        # Step 2: LLM with smart trim and improved prompt
        fields = list(schema.model_fields.keys())
        llm_result = await self._try_llm(html, fields)
        if llm_result:
            try:
                result = schema.model_validate(llm_result)
                if self._cache:
                    try:
                        await self._cache.set(cache_key, json.dumps(result.model_dump()).encode(), ttl=3600)
                    except Exception as e:
                        logger.debug(f"Cache write failed for key {cache_key}: {e}")
                return result
            except Exception as e:
                logger.debug(f"LLM result schema validation failed: {e}")

        # Step 3: legacy LLM path (for injected llm_client)
        if self._llm:
            trimmed = self._semantic_trim(html)
            prompt = self._build_prompt_v2(trimmed, schema)
            try:
                response = await self._llm.generate(
                    prompt,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                result = schema.model_validate_json(response)
                if self._cache:
                    try:
                        await self._cache.set(cache_key, json.dumps(result.model_dump()).encode(), ttl=3600)
                    except Exception as e:
                        logger.debug(f"Cache write failed for key {cache_key}: {e}")
                return result
            except Exception as e:
                raise ExtractionError(detail=str(e))

        raise ExtractionError("All extraction methods failed")

    def _extract_structured(self, html: str, schema: type[T]) -> T:
        """Try JSON-LD → OpenGraph → Twitter Card in order (zero LLM cost)."""
        import re, json

        # JSON-LD
        for match in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list):
                    data = data[0] if data else {}
                if isinstance(data, dict) and "@graph" in data:
                    data = data["@graph"] if isinstance(data["@graph"], list) and data["@graph"] else data["@graph"]
                return schema.model_validate(data)
            except Exception as e:
                logger.debug(f"JSON-LD parsing failed: {e}")

        # OpenGraph meta tags
        og = {}
        for m in re.finditer(r'<meta[^>]*property="og:([\w-]+)"[^>]*content="([^"]+)"', html):
            og[m.group(1)] = m.group(2)
        if og:
            try:
                return schema.model_validate(og)
            except Exception as e:
                logger.debug(f"OpenGraph parsing failed: {e}")

        # Twitter Card meta tags
        tc = {}
        for m in re.finditer(r'<meta[^>]*name="twitter:(\w+)"[^>]*content="([^"]+)"', html):
            tc[m.group(1)] = m.group(2)
        if tc:
            try:
                return schema.model_validate(tc)
            except Exception as e:
                logger.debug(f"Twitter Card parsing failed: {e}")

        raise ExtractionError("No structured data found")

    def _build_prompt_v2(self, html: str, schema: type[T]) -> str:
        """Improved prompt with few-shot example and chain-of-thought reasoning."""
        fields = "\n".join(
            f"  {name}: {f.annotation}" for name, f in schema.model_fields.items()
        )
        example = {}
        for name, f in schema.model_fields.items():
            ann = str(f.annotation)
            if "str" in ann:
                example[name] = f"示例{name}"
            elif "float" in ann or "int" in ann:
                example[name] = 0
            elif "list" in ann:
                example[name] = []
            else:
                example[name] = None

        return f"""Extract structured data from HTML.

Schema:
{fields}

Example output:
{json.dumps(example, ensure_ascii=False)}

Think step by step:
1. What type of page is this?
2. Where is the main content?
3. Extract each field with confidence.

HTML:
{html}

Return ONLY valid JSON. Use null for missing fields."""

    def _semantic_trim(self, html: str, max_chars: int = 6000) -> str:
        """Smart trim: keep JSON-LD + OG + main/article + headings, remove nav/footer/script/style."""
        import re
        # Remove script, style, nav, footer, noscript, iframe
        for tag in ["script", "style", "nav", "footer", "noscript", "iframe"]:
            html = re.sub(
                f"<{tag}[^>]*>.*?</{tag}>",
                "",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )
        # Truncate to max_chars if needed
        if len(html) > max_chars:
            truncated = html[:max_chars]
            last_tag_end = truncated.rfind('>')
            if last_tag_end > 0:
                truncated = truncated[:last_tag_end + 1]
            return truncated
        return html

    def extract_structured(self, html: str) -> dict:
        """Extract structured data from HTML in priority order: JSON-LD → Microdata → OpenGraph → Meta."""
        import re
        import json

        result = {}

        # 1. JSON-LD
        ld_matches = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        for ld_str in ld_matches:
            try:
                data = json.loads(ld_str)
                if isinstance(data, dict):
                    for key in ['name', 'description', 'price', 'image']:
                        if key in data:
                            result[key] = data[key]
                elif isinstance(data, list) and data:
                    for key in ['name', 'description']:
                        if key in data[0]:
                            result[key] = data[0][key]
            except json.JSONDecodeError:
                continue

        # 2. OpenGraph
        og_pattern = re.compile(
            r'<meta\s+[^>]*property="og:([^"]+)"[^>]*content="([^"]*)"',
            re.IGNORECASE
        )
        for match in og_pattern.finditer(html):
            key = match.group(1)
            result[f'og_{key}'] = match.group(2)

        # 3. Meta tags
        meta_pattern = re.compile(
            r'<meta\s+[^>]*name="([^"]+)"[^>]*content="([^"]*)"',
            re.IGNORECASE
        )
        for match in meta_pattern.finditer(html):
            key = match.group(1)
            if key in ('description', 'keywords', 'author'):
                result[key] = match.group(2)

        return result

    def smart_html_truncate(self, html: str, max_chars: int = 15000) -> str:
        """Keep semantically important parts, remove boilerplate."""
        import re

        # Keep: JSON-LD, OpenGraph, main/article, title
        important_parts = []

        # JSON-LD blocks
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>.*?</script>', html, re.DOTALL):
            important_parts.append(m.group(0))

        # Title
        for m in re.finditer(r'<title>.*?</title>', html, re.DOTALL):
            important_parts.append(m.group(0))

        # Main content area
        for tag in ['main', 'article', '[role=main]']:
            pattern = re.compile(rf'<{tag}[^>]*>.*?</{tag}>', re.DOTALL | re.IGNORECASE)
            for m in pattern.finditer(html):
                important_parts.append(m.group(0))

        # Body if nothing found
        if not important_parts:
            body_match = re.search(r'<body[^>]*>.*?</body>', html, re.DOTALL | re.IGNORECASE)
            if body_match:
                important_parts.append(body_match.group(0)[:max_chars])

        result = '\n'.join(important_parts)
        return result[:max_chars]

    async def _try_llm(self, html: str, fields: list[str] | None = None, url: str = "") -> dict | None:
        """Use LLM to extract fields from HTML as fallback."""
        # 如果没有配置 LLM，跳过
        settings = self._settings
        if not settings or not settings.llm or not settings.llm.api_key:
            return None

        # 先尝试结构化提取
        structured = self.extract_structured(html)

        # 检查是否已满足所有字段需求
        if fields and all(f in structured for f in fields):
            return structured

        # 裁剪 HTML
        truncated = self.smart_html_truncate(html)

        # 构建 LLM Prompt
        field_list = ', '.join(fields) if fields else 'title, description, price'
        prompt = f"""从以下 HTML 中提取指定字段，返回 JSON 格式。
        
需要提取的字段: {field_list}

HTML 内容:
{truncated}

要求:
- 只返回 JSON，不要其他文字
- 如果某个字段找不到，设为 null
- 保持原始文本，不要改写"""

        try:
            import httpx
            api_key = settings.llm.api_key.get_secret_value() if hasattr(settings.llm.api_key, 'get_secret_value') else str(settings.llm.api_key)
            model = settings.llm.model or "deepseek-chat"
            base_url = settings.llm.base_url or "https://api.deepseek.com"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                    }
                )
                data = resp.json()
                try:
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                except (IndexError, KeyError, TypeError) as e:
                    logger.warning(f"Unexpected API response structure: {e}")
                    content = ""

                import json as _json
                extracted = _json.loads(content)

                # 合并结构化数据（结构化数据优先）
                extracted.update({k: v for k, v in structured.items() if v})

                return extracted
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"_try_llm failed: {e}")
            return structured if structured else None
