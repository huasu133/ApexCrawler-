"""Fingerprint consistency: single source of truth for all injection layers.

Provides DeviceProfile definitions and CDP-level injection for:
  1. TLS (JA4)
  2. HTTP Headers (UA, Sec-CH-UA, Accept-Language)
  3. JS navigator (userAgent, platform, hardwareConcurrency, deviceMemory)
  4. Canvas (randomised hash seed)
  5. WebGL (renderer, vendor)
  6. AudioContext (oscillator fingerprint seed)
  7. Permissions API normalization (always return 'granted')
  8. mediaDevices enumeration spoofing
  9. Keyboard API normalization
  10. performance.memory normalization
  11. $cdc_ / cdc_ / _cdc_ variable cleanup from window object

Fixes applied (2025-06-07):
  - Removed console.debug log that leaked ApexCrawler marker (WAF probe detection)
  - Fixed navigator.plugins to return proper PluginArray with real Chrome plugin objects
    (PDF Viewer, Chrome PDF Plugin, Native Client) instead of numeric array [1,2,3,4,5]
  - Added navigator.mimeTypes coverage with corresponding MIME types linked to plugins
  - Added $cdc_ variable cleanup to remove Playwright automation markers from document
"""

from __future__ import annotations

import hashlib
import random
import re
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
    sec_ch_ua_mobile: str = "?0"

    @staticmethod
    def _parse_sec_ch_ua_to_brands(sec_ch_ua: str) -> str:
        """Parse Sec-CH-UA header into JavaScript brands array literal.

        Input:  '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"'
        Output: [{brand:'Google Chrome',version:'131'},{brand:'Chromium',version:'131'},{brand:'Not=A?Brand',version:'24'}]
        """
        if not sec_ch_ua:
            return "[]"
        brands = []
        for m in re.finditer(r'"(.*?)";v="(\d+)"', sec_ch_ua):
            brands.append("{brand:'%s',version:'%s'}" % (m.group(1), m.group(2)))
        return "[" + ",".join(brands) + "]"

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
        """Generate CDP addInitScript JavaScript for comprehensive fingerprint injection (15+ layers).

        Injects navigator overrides, WebGL renderer/vendor spoofing,
        Canvas hash seed, AudioContext fingerprint seed, permissions normalization,
        mediaDevices enumeration, keyboard API, performance.memory, and
        additional automation marker cleanup.
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
Object.defineProperty(navigator, 'languages', {{
    get: () => ['{self.language}', '{self.language.split("-")[0]}'],
    configurable: false,
}});

// ── Layer 1.1: Client Hints (userAgentData) ──
Object.defineProperty(navigator, 'userAgentData', {{
    get: () => ({{
        brands: {self._parse_sec_ch_ua_to_brands(self.sec_ch_ua)},
        mobile: {'true' if '?1' in self.sec_ch_ua_mobile else 'false'},
        platform: '{self.sec_ch_ua_platform.strip(chr(34))}',
        getHighEntropyValues: function(hints) {{
            return Promise.resolve({{
                brands: {self._parse_sec_ch_ua_to_brands(self.sec_ch_ua)},
                mobile: {'true' if '?1' in self.sec_ch_ua_mobile else 'false'},
                platform: '{self.sec_ch_ua_platform.strip(chr(34))}',
                architecture: 'x86',
                bitness: '64',
                model: '',
                platformVersion: '10.0',
                uaFullVersion: '{self.user_agent.split("/")[-1].split(" ")[0] if "Chrome" in self.user_agent else "131.0.0.0"}',
            }}));
        }},
        toJSON: function() {{
            return {{
                brands: {self._parse_sec_ch_ua_to_brands(self.sec_ch_ua)},
                mobile: {'true' if '?1' in self.sec_ch_ua_mobile else 'false'},
                platform: '{self.sec_ch_ua_platform.strip(chr(34))}',
            }};
        }},
    }}),
    configurable: false,
}});

// ── Layer 1.2: Network Information (connection) ──
if (!navigator.connection) {{
    Object.defineProperty(navigator, 'connection', {{
        get: () => ({{
            effectiveType: '4g',
            rtt: Math.floor(Math.random() * 50) + 50,
            downlink: Math.random() * 10 + 5,
            saveData: false,
        }}),
    }});
}}

// ── Layer 1.5: plugins (real PluginArray with Chrome plugins) ──
// Helper constructors for Plugin/MimeType-like objects
function _MimeType(type, suffixes, desc) {{
    this.type = type;
    this.suffixes = suffixes;
    this.description = desc;
}}
function _Plugin(name, filename, desc) {{
    this.name = name;
    this.filename = filename;
    this.description = desc;
    this.length = 0;
}}
// Native Client plugin
var _pn = new _Plugin('Native Client', 'internal-nacl-plugin', 'Native Client Executable');
var _mn = [
    new _MimeType('application/x-nacl', '', 'Native Client Executable'),
    new _MimeType('application/x-pnacl', '', 'Portable Native Client Executable'),
];
_pn.length = _mn.length;
for (var _ai = 0; _ai < _mn.length; _ai++) {{ _pn[_ai] = _mn[_ai]; _mn[_ai].enabledPlugin = _pn; }}
// PDF Viewer plugin
var _pp = new _Plugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format');
var _mp = [
    new _MimeType('application/pdf', 'pdf', 'Portable Document Format'),
    new _MimeType('text/pdf', 'pdf', 'Portable Document Format'),
];
_pp.length = _mp.length;
for (var _bi = 0; _bi < _mp.length; _bi++) {{ _pp[_bi] = _mp[_bi]; _mp[_bi].enabledPlugin = _pp; }}
// Chrome PDF Plugin
var _pc = new _Plugin('Chrome PDF Plugin', 'internal-pdf-plugin', 'Portable Document Format');
var _mc = [
    new _MimeType('application/x-google-chrome-pdf', 'pdf', 'Portable Document Format'),
];
_pc.length = _mc.length;
for (var _ci = 0; _ci < _mc.length; _ci++) {{ _pc[_ci] = _mc[_ci]; _mc[_ci].enabledPlugin = _pc; }}
// Build PluginArray
var _pluginsArr = [_pp, _pc, _pn];
_pluginsArr.length = 3;
_pluginsArr.item = function(i) {{ return this[i] || null; }};
_pluginsArr.namedItem = function(n) {{
    for (var i = 0; i < this.length; i++) {{
        if (this[i].name === n) return this[i];
    }}
    return null;
}};
_pluginsArr.refresh = function() {{}};
Object.defineProperty(navigator, 'plugins', {{
    get: () => _pluginsArr,
    configurable: false,
}});

// ── Layer 1.6: mimeTypes (real MimeTypeArray matching plugins) ──
var _mimeArr = [];
for (var _di = 0; _di < _pluginsArr.length; _di++) {{
    var _p = _pluginsArr[_di];
    for (var _ei = 0; _ei < _p.length; _ei++) {{
        _mimeArr.push(_p[_ei]);
    }}
}}
_mimeArr.length = _mimeArr.length;
_mimeArr.item = function(i) {{ return this[i] || null; }};
_mimeArr.namedItem = function(n) {{
    for (var i = 0; i < this.length; i++) {{
        if (this[i].type === n) return this[i];
    }}
    return null;
}};
Object.defineProperty(navigator, 'mimeTypes', {{
    get: () => _mimeArr,
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

// ── Layer 8: Permissions API normalization ──
const originalQuery2 = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    Promise.resolve({{state: 'granted'}})
);

// ── Layer 9: mediaDevices enumeration ──
if (navigator.mediaDevices) {{
    navigator.mediaDevices.enumerateDevices = () => Promise.resolve([
        {{deviceId: '', kind: 'audioinput', label: '', groupId: ''}},
        {{deviceId: '', kind: 'audiooutput', label: '', groupId: ''}},
        {{deviceId: '', kind: 'videoinput', label: '', groupId: ''}},
    ]);
}}

// ── Layer 10: keyboard API ──
if (navigator.keyboard) {{
    navigator.keyboard.getLayoutMap = () => Promise.resolve(new Map());
}}

// ── Layer 11: clipboard API normalization ──
if (navigator.clipboard) {{
    // Just ensure it exists without overriding core methods that need user gesture
}}

// ── Layer 12: performance.memory normalization ──
if (!performance.memory) {{
    Object.defineProperty(performance, 'memory', {{
        get: () => ({{
            jsHeapSizeLimit: 2172649472,
            totalJSHeapSize: 10000000,
            usedJSHeapSize: 8000000,
        }}),
        configurable: false,
    }});
}}

// ── Layer 13: document.hidden / visibilityState ──
// Keep default values (already correct in headed mode)

// ── Layer 14: Additional cdc_ variable cleanup ──
try {{
    var _allProps = Object.getOwnPropertyNames(window);
    for (var _gi = 0; _gi < _allProps.length; _gi++) {{
        if (_allProps[_gi].indexOf('cdc_') === 0 || 
            _allProps[_gi].indexOf('_cdc_') === 0 ||
            _allProps[_gi].indexOf('$cdc_') === 0) {{
            delete window[_allProps[_gi]];
        }}
    }}
}} catch(_e) {{}}

// ── Layer 15: screen.availWidth/availHeight consistency ──
// (keep existing overrides, they're fine)
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
        name="cn_win_chrome_120",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        platform="Win32",
        sec_ch_ua=(
            '"Google Chrome";v="120", "Chromium";v="120", "Not=A?Brand";v="24"'
        ),
        sec_ch_ua_platform='"Windows"',
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer=(
            "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)"
        ),
        timezone="Asia/Shanghai",
        language="zh-CN",
        accept_language="zh-CN,zh;q=0.9",
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
