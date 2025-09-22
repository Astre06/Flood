import requests
import re
import threading
import asyncio
from playwright.async_api import async_playwright

BIN_CACHE = {}

BIN_LOOKUP_SERVICES = [
    {
        "name": "binlist",
        "url": "https://lookup.binlist.io/",
        "headers": {"Accept-Version": "3", "User-Agent": "Mozilla/5.0"},
        "params": {},
        "api_key": False,
        "post": False,
        "parse": lambda data: (
            data.get("scheme", "N/A").upper(),
            data.get("type", "N/A").upper(),
            data.get("brand", "STANDARD").upper(),
            data.get("bank", {}).get("name", "Unknown Bank"),
            data.get("country", {}).get("name", "Unknown Country"),
        ),
    },
    {
        "name": "antipublic_bins",
        "url": "https://bins.antipublic.cc/bins/",
        "headers": {},
        "params": {},
        "api_key": False,
        "post": False,
        "parse": lambda data: (
            data.get("brand", "Unknown").upper(),
            data.get("type", "Unknown").upper(),
            "STANDARD",
            data.get("bank", "Unknown Bank"),
            data.get("country_name", "Unknown Country"),
        ),
    },
    {
        "name": "pulse_pst_net",
        "url": "https://pulse.pst.net/api/bin/",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "params": {},
        "api_key": False,
        "post": False,
        "parse": lambda data: (
            data.get("brand", "Unknown").upper(),
            data.get("type", "Unknown").upper(),
            data.get("level", "STANDARD").upper(),
            data.get("bank", "Unknown Bank"),
            data.get("country", "Unknown Country"),
        ),
    },
]

_service_index_lock = threading.Lock()
_service_index = 0

async def fetch_bin_info_pulse(bin_number: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://pulse.pst.net", timeout=60000)
        await page.fill('input.block-check-bin__input[type="tel"]', bin_number)
        await page.click('button.block-check-bin__btn-submit')
        await page.wait_for_selector('div.flex.flex-col.min-h-screen')
        card_type = await page.text_content('div:has-text("Card Type") + div')
        issuer_bank = await page.text_content('div:has-text("Issuer / Bank Name") + div')
        country = await page.text_content('div:has-text("Country Name") + div')
        await browser.close()
        return {
            "bin": bin_number,
            "card_type": card_type.strip() if card_type else "Unknown",
            "issuer_bank": issuer_bank.strip() if issuer_bank else "Unknown",
            "country": country.strip() if country else "Unknown",
        }

def lookup_single_service(bin_number: str, service: dict, proxy=None, timeout_seconds=10):
    try:
        headers = service.get("headers", {}).copy()
        params = {}
        url = service["url"]
        auth = service.get("auth")

        if service.get("post", False):
            params = service.get("auth", {}).copy()
            params["bin"] = bin_number
            resp = requests.post(url, headers=headers, data=params, proxies=proxy, timeout=timeout_seconds)
        else:
            if not url.endswith("/"):
                url += "/"
            url += bin_number
            if auth:
                headers.update(auth)
            resp = requests.get(url, headers=headers, params=params, proxies=proxy, timeout=timeout_seconds)

        if resp.status_code == 200:
            data = resp.json()
            scheme, card_type, level, bank, country = service["parse"](data)
            country_clean = re.sub(r"\s*\(.*?\)", "", country).strip()
            return (f"{bin_number} - {level} - {card_type} - {scheme}", bank, country_clean)
        else:
            print(f"Error response from {service['name']}: HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"Exception during request to {service['name']}: {e}")
        return None

def round_robin_bin_lookup(card_number: str, proxy=None, timeout_seconds=10):
    bin_number = card_number[:6]
    if bin_number in BIN_CACHE:
        print(f"Cache hit for BIN {bin_number}: {BIN_CACHE[bin_number]}")
        return BIN_CACHE[bin_number]

    default_result = (f"{bin_number} - Unknown", "Unknown Bank", "Unknown Country")

    for service in BIN_LOOKUP_SERVICES:
        print(f"Trying BIN lookup using site: {service['name']} for BIN {bin_number}")
        result = lookup_single_service(bin_number, service, proxy, timeout_seconds)
        if result:
            print(f"Success from {service['name']}: {result}")
            BIN_CACHE[bin_number] = result
            return result
        else:
            print(f"Failed to get BIN info from {service['name']}")

    print(f"Falling back to Pulse PST playwrght automation for BIN {bin_number}")
    try:
        info = asyncio.run(fetch_bin_info_pulse(bin_number))
        result = (
            f"{bin_number} - {info['card_type']} - Unknown - Unknown",
            info["issuer_bank"],
            info["country"]
        )
        if info['card_type'] != "Unknown":
            BIN_CACHE[bin_number] = result
        print(f"Success from Pulse PST playwright: {result}")
        return result
    except Exception as e:
        print(f"Pulse PST playwright fallback failed: {e}")

    print(f"All BIN lookup services failed for BIN {bin_number}. Returning Unknown.")
    return default_result
