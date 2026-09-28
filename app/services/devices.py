"""Describe the device behind a request, for the session list (Tài khoản › Bảo mật)."""

import re
from dataclasses import dataclass
from urllib.parse import unquote

from starlette.requests import Request


@dataclass(frozen=True)
class DeviceInfo:
    device_name: str
    device_type: str  # desktop | phone | tablet
    browser: str
    location: str
    user_agent: str


def _browser(ua: str) -> str:
    # order matters: Edge and Opera also say "Chrome", Chrome also says "Safari"
    for pattern, name in (
        (r"Edg(e|A|iOS)?/", "Edge"),
        (r"OPR/|Opera", "Opera"),
        (r"Firefox/|FxiOS/", "Firefox"),
        (r"CriOS/|Chrome/", "Chrome"),
        (r"Safari/", "Safari"),
    ):
        if re.search(pattern, ua):
            return name
    return "Trình duyệt"


def _device_type(ua: str) -> str:
    if re.search(r"iPad|Tablet|Android(?!.*Mobile)", ua):
        return "tablet"
    if re.search(r"Mobi|iPhone|Android", ua):
        return "phone"
    return "desktop"


def _device_name(ua: str, device_type: str) -> str:
    for pattern, name in (
        (r"iPhone", "iPhone"),
        (r"iPad", "iPad"),
        (r"Android", "Điện thoại Android" if device_type == "phone" else "Máy tính bảng Android"),
        (r"Macintosh|Mac OS X", "Máy Mac"),
        (r"Windows", "Máy tính Windows"),
        (r"CrOS", "Chromebook"),
        (r"Linux", "Máy tính Linux"),
    ):
        if re.search(pattern, ua):
            return name
    return {"phone": "Điện thoại", "tablet": "Máy tính bảng"}.get(device_type, "Máy tính")


def _location(request: Request) -> str:
    # Vercel adds these headers (URL-encoded) on deployed functions; empty locally.
    city = unquote(request.headers.get("x-vercel-ip-city", ""))
    country = request.headers.get("x-vercel-ip-country", "")
    return ", ".join(part for part in (city, country) if part)[:100]


def describe_request(request: Request) -> DeviceInfo:
    ua = request.headers.get("user-agent", "")
    device_type = _device_type(ua)
    return DeviceInfo(
        device_name=_device_name(ua, device_type)[:100],
        device_type=device_type,
        browser=_browser(ua)[:50],
        location=_location(request),
        user_agent=ua[:1000],
    )
