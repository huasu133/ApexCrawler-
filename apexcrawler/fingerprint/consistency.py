"""Fingerprint consistency: single source of truth for all 6 layers."""

from dataclasses import dataclass


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
        if "Win64" in self.user_agent and "NVIDIA" not in self.webgl_renderer:
            errors.append("Win64 GPU mismatch")
        return errors


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
]
