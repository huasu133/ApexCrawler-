"""Page interaction executor — runs JSON action sequences on pages."""
from __future__ import annotations
import asyncio
import ipaddress
import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Validate URL to prevent SSRF attacks. Raises ValueError if blocked."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(f"Blocked host: {host}")
    try:
        addr = ipaddress.ip_address(host)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise ValueError(f"Blocked IP: {host} (in {net})")
    except ValueError:
        if host:
            raise
    except Exception:
        pass


class InteractError(Exception):
    """Page interaction error."""
    pass


async def execute_actions(page, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Execute a sequence of page interactions.

    Args:
        page: Any Page protocol-compatible object.
        actions: List of action dicts, each with:
            - type: "navigate" | "click" | "fill" | "scroll" | "wait"
                   | "screenshot" | "evaluate" | "extract" | "hover" | "press"
            - Plus type-specific params.

    Returns:
        List of result dicts for each action that produces output.
    """
    results = []

    for i, action in enumerate(actions):
        action_type = action.get("type", "")
        try:
            result = await _execute_single(page, action)
            if result is not None:
                results.append({"step": i, "type": action_type, "result": result})
        except Exception as e:
            error_msg = f"Step {i} ({action_type}) failed: {e}"
            logger.warning(error_msg)
            if action.get("required", False):
                raise InteractError(error_msg)
            results.append({"step": i, "type": action_type, "error": str(e)})

    return results


async def _execute_single(page, action: Dict[str, Any]):
    """Execute a single interaction action."""
    action_type = action.get("type", "")

    if action_type == "navigate":
        url = action.get("url", "")
        if url:
            _validate_url(url)
            await page.goto(url)
            return {"url": url}

    elif action_type == "click":
        selector = action.get("selector", "")
        if selector:
            await page.click(selector)
            return {"selector": selector}

    elif action_type == "fill":
        selector = action.get("selector", "")
        value = action.get("value", "")
        if selector:
            await page.fill(selector, value)
            return {"selector": selector, "value": f"{value[:20]}..."}

    elif action_type == "scroll":
        x = action.get("x", 0)
        y = action.get("y", 500)
        await page.scroll(x, y)
        return {"x": x, "y": y}

    elif action_type == "wait":
        ms = action.get("ms", 1000)
        await asyncio.sleep(ms / 1000)
        return {"waited_ms": ms}

    elif action_type == "screenshot":
        data = await page.screenshot()
        path = action.get("path", "")
        if path and data:
            safe_path = os.path.normpath(os.path.abspath(path))
            allowed_base = os.path.abspath(os.path.join(os.getcwd(), "output"))
            if not safe_path.startswith(os.path.abspath(os.getcwd())) and not safe_path.startswith(allowed_base):
                return {"error": f"Path not allowed: {path}"}
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            try:
                with open(safe_path, "wb") as f:
                    f.write(data)
                return {"path": safe_path, "size": len(data)}
            except (OSError, PermissionError) as e:
                return {"error": f"Failed to write screenshot: {e}"}
        return {"size": len(data) if data else 0}

    elif action_type == "evaluate":
        script = action.get("script", "")
        if script:
            result = await page.evaluate(script)
            return {"result": str(result)[:500]}

    elif action_type == "extract":
        selector = action.get("selector", "")
        attr = action.get("attr", "")
        if selector:
            if attr:
                val = await page.get_attribute(selector, attr)
            else:
                val = await page.text_content(selector)
            return {"selector": selector, "value": str(val)[:500] if val else None}

    elif action_type == "hover":
        selector = action.get("selector", "")
        if selector:
            await page.hover(selector)
            return {"selector": selector}

    elif action_type == "press":
        key = action.get("key", "Enter")
        await page.press(key)
        return {"key": key}

    return None
