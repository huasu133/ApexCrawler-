"""WASM module analysis — detect and extract encrypted content from WASM binaries.

Many anti-bot systems use WebAssembly to offload cryptography and challenge
logic to the client side. This module detects, parses, and analyzes WASM
modules found in target pages to identify anti-crawl patterns.

Status: INTEGRATED — WASM detection and header parsing ready.
"""

from __future__ import annotations

import logging
import re
import struct

logger = logging.getLogger(__name__)


class WASMAnalyzer:
    """Analyze WASM modules for anti-crawl patterns.

    Capabilities:
    - Detect WASM module URLs and inline base64 modules in HTML
    - Parse WASM binary header (magic, version)
    - Detect SIMD instruction usage
    - Extract readable ASCII strings from bytecode
    - Identify known anti-crawl WASM signatures
    """

    # Known anti-crawl patterns in WASM
    _ANTI_CRAWL_SIGNATURES: list[bytes] = [
        b"fingerprint",
        b"navigator",
        b"canvas",
        b"webgl",
        b"font",
        b"detect",
        b"bot",
        b"challenge",
        b"turnstile",     # Cloudflare
        b"_cf_chl",       # Cloudflare challenge
        b"akamai",        # Akamai
        b"datadome",      # DataDome
        b"pxCaptcha",     # PerimeterX
        b"_px",           # PerimeterX
        b"incapsula",     # Imperva
    ]

    @staticmethod
    def detect_wasm_modules(html: str) -> list[str]:
        """Find WASM module references in HTML content.

        Searches for:
        - WebAssembly.instantiateStreaming() calls
        - Direct .wasm file URLs
        - Base64 inline WASM data URIs

        Args:
            html: Raw HTML content of the target page.

        Returns:
            List of discovered WASM module URLs or data URI strings.
        """
        modules: list[str] = []

        # WebAssembly.instantiateStreaming() calls
        modules.extend(
            re.findall(r'instantiateStreaming\([^,]+,\s*imports\)', html)
        )

        # Direct .wasm file URLs (in src/href/data attributes)
        modules.extend(
            re.findall(r'"([^"]+\.wasm)"', html)
        )
        modules.extend(
            re.findall(r"'([^']+\.wasm)'", html)
        )

        # Base64 inline WASM data URIs
        modules.extend(
            re.findall(r'data:application/wasm;base64,([^"\'\s)]+)', html)
        )

        return modules

    @staticmethod
    def parse_header(wasm_bytes: bytes) -> dict:
        """Parse WASM module header and section information.

        Validates the magic number and reads the WASM binary version.

        Args:
            wasm_bytes: Raw WASM module bytes.

        Returns:
            Dictionary with magic, version, and size, or an error key.
        """
        if len(wasm_bytes) < 8:
            return {"error": "WASM module too small (need at least 8 bytes)"}

        if wasm_bytes[:4] != b'\x00asm':
            return {"error": "Not a valid WASM module (missing \\x00asm magic)"}

        version = struct.unpack('<I', wasm_bytes[4:8])[0]

        result = {
            "magic": "\\x00asm",
            "version": version,
            "size": len(wasm_bytes),
        }

        # Parse section IDs if module is large enough
        if len(wasm_bytes) > 8:
            result["sections"] = WASMAnalyzer._parse_sections(wasm_bytes)

        return result

    @staticmethod
    def detect_simd(wasm_bytes: bytes) -> bool:
        """Check if WASM module uses SIMD instructions.

        SIMD (Single Instruction Multiple Data) in WASM uses the 0xFD prefix
        opcode followed by operation-specific opcodes. SIMD is often used for
        cryptographic operations in anti-bot WASM modules.

        Args:
            wasm_bytes: Raw WASM module bytes.

        Returns:
            True if SIMD instructions are detected.
        """
        simd_prefix = 0xFD
        for byte in wasm_bytes:
            if byte == simd_prefix:
                return True
        return False

    @staticmethod
    def extract_strings(wasm_bytes: bytes, limit: int = 50) -> list[str]:
        """Extract readable ASCII strings from WASM binary bytecode.

        Useful for identifying imported function names, embedded URLs,
        and cryptographic constants.

        Args:
            wasm_bytes: Raw WASM module bytes.
            limit: Maximum number of strings to return.

        Returns:
            List of decoded ASCII strings (minimum 4 characters).
        """
        raw_strings = re.findall(b'[\x20-\x7e]{4,}', wasm_bytes)
        decoded = []
        for s in raw_strings[:limit]:
            try:
                decoded.append(s.decode('ascii', errors='ignore'))
            except UnicodeDecodeError:
                pass
        return decoded

    @staticmethod
    def detect_anti_crawl(wasm_bytes: bytes) -> list[str]:
        """Detect known anti-crawl signatures in WASM bytecode.

        Searches the raw bytecode for embedded strings matching known
        anti-bot systems (Cloudflare, Akamai, DataDome, PerimeterX, etc.).

        Args:
            wasm_bytes: Raw WASM module bytes.

        Returns:
            List of matched anti-crawl pattern names.
        """
        detected: list[str] = []
        for signature in WASMAnalyzer._ANTI_CRAWL_SIGNATURES:
            if signature in wasm_bytes:
                detected.append(signature.decode('ascii', errors='ignore'))
        return detected

    # ── Internal helpers ────────────────────────────────────

    @staticmethod
    def _parse_sections(wasm_bytes: bytes) -> list[dict]:
        """Parse WASM section headers.

        Section structure:
        - 1 byte: section ID
        - LEB128 varuint32: section size
        - content: section-specific data
        """
        section_names = {
            0: "custom",
            1: "type",
            2: "import",
            3: "function",
            4: "table",
            5: "memory",
            6: "global",
            7: "export",
            8: "start",
            9: "element",
            10: "code",
            11: "data",
            12: "data_count",
        }

        sections = []
        pos = 8  # Skip header (4 magic + 4 version)

        while pos < len(wasm_bytes) - 1:
            section_id = wasm_bytes[pos]
            pos += 1
            section_size, bytes_read = WASMAnalyzer._read_leb128(
                wasm_bytes, pos
            )
            pos += bytes_read

            section_type = section_names.get(section_id, f"unknown({section_id})")

            # For custom sections (id=0), try to read the name
            name = None
            if section_id == 0 and section_size > 0:
                try:
                    name_len, nl_bytes = WASMAnalyzer._read_leb128(
                        wasm_bytes, pos
                    )
                    name = wasm_bytes[
                        pos + nl_bytes : pos + nl_bytes + name_len
                    ].decode('ascii', errors='ignore')
                except Exception:
                    pass

            sections.append({
                "id": section_id,
                "type": section_type,
                "size": section_size,
                "name": name,
            })
            pos += section_size

        return sections

    @staticmethod
    def _read_leb128(data: bytes, offset: int) -> tuple[int, int]:
        """Read an unsigned LEB128 value from bytes.

        Returns:
            Tuple of (decoded_value, bytes_consumed).
        """
        result = 0
        shift = 0
        bytes_read = 0
        while offset + bytes_read < len(data):
            byte = data[offset + bytes_read]
            bytes_read += 1
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result, bytes_read
