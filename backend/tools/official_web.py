"""Catalog-only public queries and bounded, DNS-pinned official page retrieval."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
import jwt
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.config import get_settings


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    domains: list[str] = Field(min_length=1, max_length=10)
    topics: dict[str, str] = Field(min_length=1, max_length=30)
    versions: list[str] = Field(default_factory=list, max_length=50)


def product_profile(name: str) -> Product:
    settings = get_settings()
    if not settings.official_web_enabled:
        raise HTTPException(503, "官方网页检索尚未启用")
    value = settings.official_products.get(name)
    if not value:
        raise HTTPException(422, "product_unmapped：产品尚未登记官方资料目录")
    return Product.model_validate(value)


def policy_hash(profile: Product) -> str:
    return hashlib.sha256(json.dumps(profile.model_dump(), sort_keys=True).encode()).hexdigest()


def checked_url(url: str, domains: list[str]) -> httpx.URL:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode().lower()
        allowed = {d.encode("idna").decode().lower().rstrip(".") for d in domains}
        if parts.scheme != "https" or parts.port not in {None, 443} or parts.username or parts.password or not host or host.endswith("."):
            raise ValueError("Unsupported official URL")
        # Domains are exact: subdomains must be registered explicitly.
        if host not in allowed or len(url) > 4096:
            raise ValueError("URL outside official domain policy")
    except (ValueError, UnicodeError):
        raise HTTPException(422, "网址不符合官方来源策略") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(422, "不允许使用 IP 地址作为官方来源")
    return httpx.URL(url).copy_with(fragment=None)


async def public_addresses(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise HTTPException(503, "官方域名解析失败") from None
    values = list(dict.fromkeys(str(item[4][0]) for item in infos))
    if not values:
        raise HTTPException(503, "官方域名解析失败")
    for value in values:
        address = ipaddress.ip_address(value)
        if (not address.is_global or address.is_multicast or address.is_unspecified
                or address.is_reserved or getattr(address, "ipv4_mapped", None)):
            raise HTTPException(422, "官方来源解析到了禁止访问的地址")
    return values


async def fetch_bytes(url: str, domains: list[str], *, headers=None, redirects=3) -> tuple[bytes, str, str]:
    async with asyncio.timeout(get_settings().official_web_timeout):
        return await _fetch_bytes(url, domains, headers=headers, redirects=redirects)


async def _fetch_bytes(url: str, domains: list[str], *, headers=None, redirects=3) -> tuple[bytes, str, str]:
    settings = get_settings()
    for attempt in range(redirects + 1):
        target = checked_url(url, domains)
        addresses = await public_addresses(target.host)
        pinned = target.copy_with(host=addresses[0])
        # Pin the TCP destination; keep certificate verification and SNI for the original host.
        async with httpx.AsyncClient(timeout=settings.official_web_timeout, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", pinned, headers={**(headers or {}), "Host": target.host},
                                     extensions={"sni_hostname": target.host}) as response:
                if response.is_redirect:
                    if attempt == redirects or not response.headers.get("location"):
                        raise HTTPException(422, "官方网页重定向超过限制")
                    url = urljoin(str(target), response.headers["location"])
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in {"text/html", "text/plain", "application/json"}:
                    raise HTTPException(422, "官方页面内容类型暂不支持")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.official_web_max_bytes:
                        raise HTTPException(422, "官方页面超过大小限制")
                return bytes(body), str(target), content_type
    raise HTTPException(422, "官方页面读取失败")


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text: list[str] = []
        self.login = False

    def handle_starttag(self, tag, attrs):
        if tag == "input" and dict(attrs).get("type", "").lower() == "password":
            self.login = True
        if tag in {"script", "style", "noscript", "svg", "iframe"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "iframe"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.text.append(data.strip())


async def search(arguments, scope: str) -> dict:
    settings = get_settings()
    profile = product_profile(arguments.product)
    if arguments.topic not in profile.topics or arguments.version and arguments.version not in profile.versions:
        raise HTTPException(422, "查询主题或版本未登记在公开资料目录")
    if not settings.official_search_api_key:
        raise HTTPException(503, "官方搜索 Provider 尚未配置")
    query = " ".join([profile.name, profile.topics[arguments.topic], arguments.version,
                      "(" + " OR ".join("site:" + d for d in profile.domains) + ")"])
    target = httpx.URL("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 3})
    body, _, _ = await fetch_bytes(str(target), ["api.search.brave.com"],
                                  headers={"X-Subscription-Token": settings.official_search_api_key,
                                           "Accept": "application/json"}, redirects=0)
    results = []
    try:
        payload = json.loads(body)
        candidates = payload.get("web", {}).get("results", [])
        if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
            raise ValueError("Invalid Provider response")
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(503, "官方搜索 Provider 返回了无法解析的结果") from None
    seen = set()
    for item in candidates[:3]:
        if not isinstance(item.get("url"), str):
            continue
        try:
            url = str(checked_url(item.get("url", ""), profile.domains))
        except HTTPException:
            continue
        if url in seen:
            continue
        seen.add(url)
        claims = {"url": url, "product": arguments.product, "version": arguments.version,
                  "title": str(item.get("title", ""))[:200], "scope": scope,
                  "policy": policy_hash(profile), "exp": datetime.now(UTC) + timedelta(hours=1)}
        reference = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
        results.append({"title": claims["title"], "url": url, "reference": reference,
                        "version": arguments.version, "published_at": None})
    return {"results": results, "product": arguments.product,
            "status": "found" if results else "no_results", "provider": "brave"}


async def read(arguments, scope: str) -> dict:
    try:
        claims = jwt.decode(arguments.reference, get_settings().jwt_secret, algorithms=["HS256"],
                            options={"require": ["exp", "scope", "product", "policy", "url"]})
    except jwt.InvalidTokenError:
        raise HTTPException(422, "官方网页引用无效或已过期") from None
    if claims["scope"] != scope:
        raise HTTPException(403, "官方网页引用不属于当前调用范围")
    profile = product_profile(claims["product"])
    if claims["policy"] != policy_hash(profile):
        raise HTTPException(409, "官方来源策略已变化，请重新搜索")
    body, url, kind = await fetch_bytes(claims["url"], profile.domains)
    raw = body.decode("utf-8", errors="replace")
    if kind == "text/html":
        parser = TextOnly()
        parser.feed(raw)
        if parser.login:
            return {"status": "unavailable", "sources": [], "reason": "页面需要登录，无法核验公开正文"}
        raw = "\n".join(parser.text)
    if not raw.strip():
        return {"status": "unavailable", "sources": [], "reason": "页面没有可读取的静态正文；动态页面暂不支持"}
    digest = hashlib.sha256(body).hexdigest()
    source = {"id": "official-" + digest[:24], "title": claims.get("title") or profile.name,
              "kind": "official_web", "source": url, "url": url,
              "product": claims["product"], "version": claims.get("version", ""),
              "version_verified": False, "published_at": None,
              "retrieved_at": datetime.now(UTC).isoformat(), "content_hash": digest,
              "text": raw[:8000]}
    return {"status": "retrieved", "sources": [source],
            "warnings": ["产品版本为查询限定，页面版本与发布时间需人工核实；获取时间不等于发布时间"]}
