"""Fingerprint consistency: single source of truth for all 6 layers.

Provides DeviceProfile definitions and CDP-level injection for:
  1. TLS (JA4)
  2. HTTP Headers (UA, Sec-CH-UA, Accept-Language)
  3. JS navigator (userAgent, platform, hardwareConcurrency, deviceMemory)
  4. Canvas (randomised hash seed)
  5. WebGL (renderer, vendor)
  6. AudioContext (oscillator fingerprint seed)
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    ja4_prefix: str = "t13d1516h2"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    platform: str = "Windows"
    alpn: tuple = ("h2", "http/1.1")
    sec_ch_ua: str = (
        '"Google Chrome";v="124", "Chromium";v="124", "Not=A?Brand";v="24"'
    )
    sec_ch_ua_platform: str = '"Windows"'
    hardware_concurrency: int = 8
    device_memory: int = 8
    vendor: str = "Google Inc."
    webgl_renderer: str = (
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"
    )
    webgl_vendor: str = "Google Inc. (NVIDIA)"
    screen_w: int = 1920
    screen_h: int = 1080
    timezone: str = "America/New_York"
    language: str = "en-US"
    accept_language: str = "en-US,en;q=0.9"

    def validate(self) -> list[str]:
        errors = []
        if "Chrome" in self.user_agent and self.hardware_concurrency < 2:
            errors.append("Chrome UA but low hardwareConcurrency")
        if "Mac" in self.platform and "Apple" not in self.webgl_renderer and "Apple" not in self.webgl_vendor:
            errors.append("macOS platform but non-Apple GPU")
        if "Win64" in self.user_agent and "NVIDIA" not in self.webgl_renderer and "Intel" not in self.webgl_renderer and "AMD" not in self.webgl_renderer:
            errors.append("Win64 but no known GPU vendor in renderer")
        return errors

    def cdp_inject_script(self) -> str:
        """Generate CDP addInitScript JavaScript for 6-layer fingerprint injection.

        Injects navigator overrides, WebGL renderer/vendor spoofing,
        Canvas hash seed, and AudioContext fingerprint seed.
        """
        canvas_seed = int(hashlib.sha256(f"canvas:{self.name}".encode()).hexdigest()[:8], 16)
        audio_seed = int(hashlib.sha256(f"audio:{self.name}".encode()).hexdigest()[:8], 16)

        return f"""
// ApexCrawler DeviceProfile fingerprint injection
// Profile: {self.name}
// Requires TZ=America/New_York or equivalent environment variable for timezone consistency
(function() {{
'use strict';

// ── Layer 0: webdriver flag (most critical bot detection) ──
Object.defineProperty(Navigator.prototype, 'webdriver', {{
    get: () => undefined,
    configurable: false,
}});

// ── Layer 0.5: window.chrome (headless detection) ──
if (!window.chrome) {{
    window.chrome = {{
        runtime: {{}},
        loadTimes: function() {{}},
        csi: function() {{}},
        app: {{}}
    }};
}}

// ── Layer 1: JS navigator ──
Object.defineProperty(navigator, 'userAgent', {{
    get: () => '{self.user_agent}',
    configurable: false,
}});
Object.defineProperty(navigator, 'platform', {{
    get: () => '{self.platform}',
    configurable: false,
}});
Object.defineProperty(navigator, 'hardwareConcurrency', {{
    get: () => {self.hardware_concurrency},
    configurable: false,
}});
Object.defineProperty(navigator, 'deviceMemory', {{
    get: () => {self.device_memory},
    configurable: false,
}});
Object.defineProperty(navigator, 'vendor', {{
    get: () => '{self.vendor}',
    configurable: false,
}});
Object.defineProperty(navigator, 'language', {{
    get: () => '{self.language}',
    configurable: false,
}});

// ── Layer 1.5: plugins / mimeTypes (93% fingerprint libs check this) ──
Object.defineProperty(navigator, 'plugins', {{
    get: () => {{
        const arr = [1, 2, 3, 4, 5];
        arr.item = (i) => arr[i];
        arr.namedItem = () => arr[0];
        arr.refresh = () => {{}};
        return arr;
    }},
    configurable: false,
}});

// ── Layer 2: Screen ──
Object.defineProperty(screen, 'width', {{ get: () => {self.screen_w}, configurable: false }});
Object.defineProperty(screen, 'height', {{ get: () => {self.screen_h}, configurable: false }});

// ── Layer 3: WebGL renderer/vendor ──
const origGetParameter = WebGLRenderingContext.prototype.getParameter;
const spoofRenderer = '{self.webgl_renderer}';
const spoofVendor = '{self.webgl_vendor}';
WebGLRenderingContext.prototype.getParameter = function(p) {{
    if (p === 37445) return spoofVendor;   // UNMASKED_VENDOR_WEBGL
    if (p === 37446) return spoofRenderer; // UNMASKED_RENDERER_WEBGL
    return origGetParameter.call(this, p);
}};
if (typeof WebGL2RenderingContext !== 'undefined') {{
    WebGL2RenderingContext.prototype.getParameter = WebGLRenderingContext.prototype.getParameter;
}}
// Block WEBGL_debug_renderer_info extension
const origGetExtension = WebGLRenderingContext.prototype.getExtension;
WebGLRenderingContext.prototype.getExtension = function(name) {{
    if (name === 'WEBGL_debug_renderer_info') return null;
    return origGetExtension.call(this, name);
}};
// OffscreenCanvas / Worker WebGL context
if (typeof OffscreenCanvas !== 'undefined') {{
    const origOffscreenGetContext = OffscreenCanvas.prototype.getContext;
    OffscreenCanvas.prototype.getContext = function(type, opts) {{
        const ctx = origOffscreenGetContext.call(this, type, opts);
        if (ctx && (type === 'webgl' || type === 'webgl2')) {{
            ctx.getParameter = WebGLRenderingContext.prototype.getParameter;
            ctx.getExtension = WebGLRenderingContext.prototype.getExtension;
        }}
        return ctx;
    }};
}}

// ── Layer 4: Canvas fingerprint seed ──
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
const canvasSeed = {canvas_seed};
let state = canvasSeed;
function pcgRandom() {{
    state = (state * 6364136223846793005n + 1442695040888963407n) & 0xFFFFFFFFFFFFFFFFn;
    return Number(state >> 33n) / 2147483648; // range [-1, 1]
}}
CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
    const data = origGetImageData.call(this, x, y, w, h);
    // PCG-based nonlinear noise (avoids linear detectability)
    for (let i = 0; i < data.data.length; i += 4) {{
        const noise = Math.round(pcgRandom() * 2 - 1); // -1, 0, or +1
        data.data[i] = Math.min(255, Math.max(0, data.data[i] + noise));
    }}
    return data;
}};

// ── Layer 5: AudioContext fingerprint seed ──
const audioSeed = {audio_seed};
if (typeof AudioContext !== 'undefined' || typeof webkitAudioContext !== 'undefined') {{
    const AC = AudioContext || webkitAudioContext;
    const origCreateOscillator = AC.prototype.createOscillator;
    AC.prototype.createOscillator = function() {{
        const osc = origCreateOscillator.call(this);
        const origStart = osc.start;
        osc.start = function(when) {{
            // Inject seed-based frequency drift
            const drift = (audioSeed % 100) / 10000;
            osc.frequency.value += drift;
            return origStart.call(this, when);
        }};
        return osc;
    }};
    // Override sampleRate, baseLatency, outputLatency, channelCount
    const origGetProp = Object.getOwnPropertyDescriptor(AudioContext.prototype, 'sampleRate') ||
                        Object.getOwnPropertyDescriptor(webkitAudioContext.prototype, 'sampleRate');
    if (origGetProp) {{
        Object.defineProperty(AC.prototype, 'sampleRate', {{
            get: () => 44100, configurable: false
        }});
    }}
    Object.defineProperty(AC.prototype, 'baseLatency', {{
        get: () => 0.005 + (audioSeed % 20) / 10000, configurable: false
    }});
    Object.defineProperty(AC.prototype, 'outputLatency', {{
        get: () => 0.01 + (audioSeed % 30) / 10000, configurable: false
    }});
    AC.prototype.destination.__defineGetter__('maxChannelCount', function() {{ return 2; }});
}}

// ── Layer 6: Timezone ──
if (typeof Intl !== 'undefined' && Intl.DateTimeFormat) {{
    const origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
        const opts = origResolved.call(this);
        opts.timeZone = '{self.timezone}';
        return opts;
    }};
}}

console.debug('[ApexCrawler] DeviceProfile injected: {self.name}');
}})();
"""


DEVICE_PROFILES = [
    DeviceProfile(name="win_chrome_124"),
    DeviceProfile(
        name="win_chrome_131",
        ja4_prefix="t13d1616h2",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua=(
            '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"'
        ),
        screen_w=2560,
        screen_h=1440,
    ),
    DeviceProfile(
        name="mac_chrome_124",
        ja4_prefix="t13d1516h2",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        platform="MacIntel",
        sec_ch_ua=(
            '"Google Chrome";v="124", "Chromium";v="124", "Not=A?Brand";v="24"'
        ),
        sec_ch_ua_platform='"macOS"',
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
        webgl_vendor="Google Inc. (Apple)",
        screen_w=1680,
        screen_h=1050,
        timezone="America/Los_Angeles",
    ),
    DeviceProfile(
        name="win_firefox_124",
        ja4_prefix="t13d1715h2",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
        platform="Win32",
        sec_ch_ua="",
        sec_ch_ua_platform="",
        vendor="",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        webgl_vendor="Google Inc. (NVIDIA)",
        timezone="America/Chicago",
    ),
    DeviceProfile(
        name="mac_safari_17",
        ja4_prefix="t13d1615h2",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.4 Safari/605.1.15"
        ),
        platform="MacIntel",
        alpn=("h2", "http/1.1"),
        sec_ch_ua="",
        sec_ch_ua_platform="",
        vendor="Apple Computer, Inc.",
        webgl_renderer="Apple M2",
        webgl_vendor="Apple Inc.",
        screen_w=1680,
        screen_h=1050,
        timezone="America/Los_Angeles",
    ),
]


def get_profile(name: str | None = None) -> DeviceProfile:
    """Get a DeviceProfile by name, or return a random one."""
    if name:
        for p in DEVICE_PROFILES:
            if p.name == name:
                return p
    return random.choice(DEVICE_PROFILES)
