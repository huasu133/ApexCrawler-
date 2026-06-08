"""AI Agent for autonomous web research — search, crawl, extract, summarize."""
from __future__ import annotations
import json
import logging
import os
import re
import socket
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Tool definitions ──

TOOLS = [
    {
        "name": "search_web",
        "description": "搜索网络，返回搜索结果列表（标题、链接、摘要）",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "num": {"type": "integer", "description": "返回结果数", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "crawl_page",
        "description": "爬取指定 URL 的页面内容，返回 Markdown 格式文本",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要爬取的网页 URL"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "extract_data",
        "description": "从文本内容中提取结构化数据",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要提取的文本内容"},
                "schema": {"type": "string", "description": "可选的 JSON Schema 描述输出格式"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "summarize",
        "description": "对文本进行 AI 总结",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要总结的文本"},
                "instruction": {"type": "string", "description": "总结要求"},
            },
            "required": ["text"],
        },
    },
]

SYSTEM_PROMPT = """你是一个网页研究助手。你的任务是帮助用户从网络获取信息。

你有以下工具可用：
1. search_web — 搜索网络
2. crawl_page — 爬取页面内容
3. extract_data — 提取结构化数据
4. summarize — 总结文本

请按需使用工具。每次只返回一个工具调用。完成任务后返回最终答案。
"""


async def run_agent(query: str, llm_provider: str = "openai/gpt-4o", 
                    api_token: str = "", max_steps: int = 10) -> Dict[str, Any]:
    """Run the agent with a user query.
    
    Args:
        query: User's natural language request.
        llm_provider: LLM provider string (e.g. "openai/gpt-4o").
        api_token: API key for the LLM provider.
        max_steps: Maximum number of agent iterations.
    
    Returns:
        Dict with "answer" (final answer) and "steps" (execution trace).
    """
    if not api_token:
        api_token = os.environ.get("OPENAI_API_KEY", "")
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    
    steps = []
    
    for step in range(max_steps):
        llm_response = await _call_llm(messages, llm_provider, api_token)
        content = llm_response.get("content", "")
        tool_calls = llm_response.get("tool_calls", [])
        
        if not tool_calls:
            steps.append({"step": step, "type": "answer", "content": content})
            return {"answer": content, "steps": steps}
        
        for tc in tool_calls:
            func_name = tc.get("function", {}).get("name", "")
            func_args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                func_args = json.loads(func_args_str)
            except json.JSONDecodeError:
                func_args = {}
            
            logger.info(f"Step {step}: Agent calls {func_name}")
            
            result = await _execute_tool(func_name, func_args, api_token)
            result_str = json.dumps(result, ensure_ascii=False)[:3000]
            
            messages.append({"role": "assistant", "content": f"Calling {func_name}..."})
            messages.append({"role": "tool", "content": result_str, "name": func_name})
            steps.append({"step": step, "type": func_name, "args": func_args, "result": result})
    
    return {"answer": "Agent reached max steps without final answer.", "steps": steps}


async def _call_llm(messages: List[Dict], provider: str, api_token: str) -> Dict:
    """Call LLM with function calling support."""
    if not api_token:
        return {"content": "Error: No API token configured. Set OPENAI_API_KEY environment variable.", "tool_calls": []}
    
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_token)
        
        # Determine model from provider string
        model = "gpt-4o"
        if "/" in provider:
            model = provider.split("/")[-1]
        
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[{
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
            } for t in TOOLS],
            tool_choice="auto",
            temperature=0.3,
        )
        
        choice = resp.choices[0]
        content = choice.message.content or ""
        tool_calls = []
        
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                })
        
        return {"content": content, "tool_calls": tool_calls}
    
    except ImportError:
        return {"content": "Error: openai package not installed. Run: pip install openai", "tool_calls": []}
    except Exception as e:
        return {"content": f"Error calling LLM: {e}", "tool_calls": []}


def _is_safe_url(url: str) -> bool:
    """检查 URL 是否安全（防止 SSRF 攻击内网）。"""
    try:
        parsed = urlparse(url)
        import ipaddress
        host = parsed.hostname or ""
        # 过滤 localhost 和内网地址
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return False
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            # 域名 — 解析到 IP 后检查是否内网
            try:
                resolved = socket.gethostbyname(host)
                addr = ipaddress.ip_address(resolved)
                if addr.is_private or addr.is_loopback or addr.is_link_local:
                    return False
            except (socket.gaierror, ValueError):
                pass  # 无法解析，放行（后续请求会失败）
        return True
    except Exception:
        return False


async def _execute_tool(name: str, args: Dict, api_token: str = "") -> Any:
    """Execute a tool and return its result."""
    if name == "search_web":
        try:
            from apexcrawler.search import search_web
            results = await search_web(
                query=args.get("query", ""),
                num=args.get("num", 5),
            )
            return [r.to_dict() for r in results]
        except ImportError:
            return [{"error": "Search module not available"}]
    
    elif name == "crawl_page":
        try:
            url = args.get("url", "")
            if not _is_safe_url(url):
                return {"url": url, "error": "Blocked: URL targets internal/private network"}
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                html = resp.text
            if html:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text(separator=" ", strip=True)
                except ImportError:
                    # Fallback to regex
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'\s+', ' ', text).strip()
                return {"url": url, "content": text[:5000]}
            return {"url": url, "error": "No content"}
        except Exception as e:
            return {"error": str(e)}
    
    elif name == "extract_data":
        try:
            from apexcrawler.extraction.llm_extract import LLMConfig, extract_with_llm
            config = LLMConfig(
                provider="openai/gpt-4o",
                api_token=api_token or os.environ.get("OPENAI_API_KEY", ""),
                instruction=args.get("schema", "提取关键信息"),
            )
            result = extract_with_llm(args.get("text", ""), config)
            return result.get("data", {})
        except Exception as e:
            return {"error": str(e)}
    
    elif name == "summarize":
        try:
            from apexcrawler.extraction.llm_extract import LLMConfig, extract_with_llm
            config = LLMConfig(
                provider="openai/gpt-4o",
                api_token=api_token or os.environ.get("OPENAI_API_KEY", ""),
                instruction=args.get("instruction", "总结要点"),
            )
            result = extract_with_llm(args.get("text", ""), config)
            return result.get("data", "")
        except Exception as e:
            return {"error": str(e)}
    
    return {"error": f"Unknown tool: {name}"}
