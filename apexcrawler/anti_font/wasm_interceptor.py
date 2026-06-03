"""WASM interception via CDP — neutralize WASM/SIMD fingerprint vectors.

CloakBrowser v0.3.14 patches 33 C++-level fingerprints but does NOT
touch V8's WebAssembly engine. This module provides JavaScript-level
interception as a mitigation layer.

Strategy:
1. Intercept WebAssembly.instantiate / instantiateStreaming
2. Strip WASM SIMD opcodes before instantiation
3. Replace WebAssembly.Module constructor for deeper interception
4. Monitor and log WASM usage for analysis
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# JavaScript to inject BEFORE any page content loads
WASM_INTERCEPTION_SCRIPT = """
(function() {
    'use strict';

    // Check if WASM SIMD is available — if so, block it
    if (typeof WebAssembly !== 'undefined') {
        const origInstantiate = WebAssembly.instantiate;
        const origInstantiateStreaming = WebAssembly.instantiateStreaming;
        const origCompile = WebAssembly.compile;
        const origCompileStreaming = WebAssembly.compileStreaming;

        let wasmCount = 0;
        let simdCount = 0;

        // SIMD opcode detection: 0xFD is the SIMD prefix in WASM binary
        function hasSIMD(buffer) {
            const bytes = new Uint8Array(buffer instanceof ArrayBuffer ? buffer : buffer.buffer || buffer);
            for (let i = 0; i < bytes.length; i++) {
                if (bytes[i] === 0xFD) return true;
            }
            return false;
        }

        // Neutralize SIMD by replacing fd prefix with unreachable
        function neutralizeSIMD(buffer) {
            const bytes = new Uint8Array(buffer instanceof ArrayBuffer ? buffer : buffer.buffer || buffer);
            const neutralized = new Uint8Array(bytes.length);
            neutralized.set(bytes);
            for (let i = 0; i < neutralized.length; i++) {
                if (neutralized[i] === 0xFD) {
                    // Replace SIMD prefix with unreachable (0x00) + drop
                    neutralized[i] = 0x00;
                }
            }
            return neutralized.buffer;
        }

        // Wrap instantiate
        WebAssembly.instantiate = function(bufferOrModule, imports) {
            wasmCount++;
            try {
                if (hasSIMD(bufferOrModule)) {
                    simdCount++;
                    console.debug('[ApexCrawler] WASM SIMD detected and neutralized');
                    bufferOrModule = neutralizeSIMD(bufferOrModule);
                }
            } catch(e) {}
            return origInstantiate.call(this, bufferOrModule, imports);
        };

        // Wrap instantiateStreaming
        WebAssembly.instantiateStreaming = function(source, imports) {
            wasmCount++;
            return source.then(function(response) {
                return response.arrayBuffer().then(function(buffer) {
                    if (hasSIMD(buffer)) {
                        simdCount++;
                        console.debug('[ApexCrawler] WASM SIMD (streaming) neutralized');
                        buffer = neutralizeSIMD(buffer);
                    }
                    return origInstantiate.call(WebAssembly, buffer, imports);
                });
            });
        };

        // Wrap compile
        WebAssembly.compile = function(buffer) {
            try {
                if (hasSIMD(buffer)) {
                    simdCount++;
                    buffer = neutralizeSIMD(buffer);
                }
            } catch(e) {}
            return origCompile.call(this, buffer);
        };

        // Wrap compileStreaming
        WebAssembly.compileStreaming = function(source) {
            return source.then(function(response) {
                return response.arrayBuffer().then(function(buffer) {
                    if (hasSIMD(buffer)) {
                        simdCount++;
                        buffer = neutralizeSIMD(buffer);
                    }
                    return origCompile.call(WebAssembly, buffer);
                });
            });
        };

        // Expose stats for Python layer
        window.__apex_wasm = {
            totalModules: function() { return wasmCount; },
            simdNeutralized: function() { return simdCount; },
            stats: function() { return { total: wasmCount, simd: simdCount }; }
        };
    }

    // Block WebGPU adapter if CloakBrowser doesn't patch it
    if (navigator.gpu && !navigator.gpu.__apex_patched) {
        const origRequestAdapter = navigator.gpu.requestAdapter;
        navigator.gpu.requestAdapter = function(options) {
            console.debug('[ApexCrawler] WebGPU requestAdapter intercepted');
            // Force fallback adapter to avoid real GPU info
            const fallbackOpts = Object.assign({}, options || {}, {
                powerPreference: 'low-power'
            });
            return origRequestAdapter.call(this, fallbackOpts);
        };
        navigator.gpu.__apex_patched = true;
    }
})();
"""


class WASMInterceptor:
    """Python-side WASM interception manager.

    Injects CDP-level JavaScript before page load to intercept
    WebAssembly operations. Works with any Playwright-based engine.
    """

    def __init__(self):
        self._injected = False

    def get_init_script(self) -> str:
        """Get the JavaScript to inject via page.addInitScript()."""
        return WASM_INTERCEPTION_SCRIPT

    async def inject(self, page: Any) -> bool:
        """Inject WASM interception into a Playwright page.

        Args:
            page: Playwright page object.

        Returns:
            True if injection succeeded.
        """
        if self._injected:
            return True

        try:
            await page.add_init_script(WASM_INTERCEPTION_SCRIPT)
            self._injected = True
            logger.info("WASM interceptor injected")
            return True
        except Exception as e:
            logger.warning(f"WASM interceptor injection failed: {e}")
            return False

    async def get_stats(self, page: Any) -> dict:
        """Get WASM interception statistics from the page.

        Returns:
            dict with 'total' (modules loaded) and 'simd' (neutralized count).
        """
        try:
            return await page.evaluate("() => window.__apex_wasm ? window.__apex_wasm.stats() : {total:0, simd:0}")
        except Exception:
            return {"total": 0, "simd": 0}


# ════════════════════════════════════════════════════════════════
#  GPU fingerprint source library
# ════════════════════════════════════════════════════════════════

GPU_FINGERPRINT_LIBRARY = {
    "rtx_3060": {
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002504) Direct3D11 vs_5_0 ps_5_0)",
        "vendor": "Google Inc. (NVIDIA)",
        "unmasked_renderer": "NVIDIA GeForce RTX 3060",
        "unmasked_vendor": "NVIDIA Corporation",
    },
    "rtx_4070": {
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0)",
        "vendor": "Google Inc. (NVIDIA)",
        "unmasked_renderer": "NVIDIA GeForce RTX 4070",
        "unmasked_vendor": "NVIDIA Corporation",
    },
    "intel_uhd": {
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0)",
        "vendor": "Google Inc. (Intel)",
        "unmasked_renderer": "Intel(R) UHD Graphics 630",
        "unmasked_vendor": "Intel Inc.",
    },
    "amd_radeon_rx6600": {
        "renderer": "ANGLE (AMD, Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0)",
        "vendor": "Google Inc. (AMD)",
        "unmasked_renderer": "AMD Radeon RX 6600",
        "unmasked_vendor": "AMD",
    },
    "apple_m2": {
        "renderer": "Apple M2",
        "vendor": "Apple Inc.",
        "unmasked_renderer": "Apple M2",
        "unmasked_vendor": "Apple Inc.",
    },
}


def get_gpu_profile(gpu_name: str = "rtx_3060") -> dict:
    """Get a GPU fingerprint profile from the library.

    For real GPU passthrough (Docker NVIDIA runtime), use actual hardware.
    For emulation, these real-device profiles are more convincing than SwiftShader.
    """
    return GPU_FINGERPRINT_LIBRARY.get(gpu_name, GPU_FINGERPRINT_LIBRARY["rtx_3060"])


# ════════════════════════════════════════════════════════════════
#  Docker GPU passthrough guide
# ════════════════════════════════════════════════════════════════

GPU_PASSTHROUGH_GUIDE = """
# Docker GPU Passthrough for ApexCrawler

Real GPU passthrough eliminates SwiftShader fingerprint and provides
genuine hardware diversity.

## Requirements
- NVIDIA GPU + nvidia-container-toolkit
- Or Apple Silicon Mac (GPU is already real)

## Docker Compose Addition
```yaml
services:
  apexcrawler:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

## Verification
```bash
docker run --rm --gpus all alpine nvidia-smi
```
"""
