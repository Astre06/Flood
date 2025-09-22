import requests
import re
import threading

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
        "name": "freebinchecker",
        "url": "https://www.freebinchecker.com/api/bin/",
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

def round_robin_bin_lookup(card_number: str, proxy=None, timeout_seconds=10):
    global _service_index

    bin_number = card_number[:6]
    if bin_number in BIN_CACHE:
        print(f"Cache hit for BIN {bin_number}: {BIN_CACHE[bin_number]}")
        return BIN_CACHE[bin_number]

    num_services = len(BIN_LOOKUP_SERVICES)
    attempts = 0

    default_result = (f"{bin_number} - Unknown", "Unknown Bank", "Unknown Country")

    while attempts < num_services:
        with _service_index_lock:
            service = BIN_LOOKUP_SERVICES[_service_index]
            _service_index = (_service_index + 1) % num_services

        try:
            print(f"Trying BIN lookup using site: {service['name']} for BIN {bin_number}")
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
                try:
                    data = resp.json()
                    scheme, card_type, level, bank, country = service["parse"](data)
                    country_clean = re.sub(r"\s*\(.*?\)", "", country).strip()
                    result = (f"{bin_number} - {level} - {card_type} - {scheme}", bank, country_clean)
                    BIN_CACHE[bin_number] = result
                    print(f"Success from {service['name']}: {result}")
                    return result
                except Exception as e:
                    print(f"Parsing error from {service['name']}: {e}")
                    return default_result
            else:
                print(f"Error response from {service['name']}: HTTP {resp.status_code}")
                attempts += 1
                continue
        except Exception as e:
            print(f"Exception during request to {service['name']}: {e}")
            attempts += 1
            continue

    print(f"All BIN lookup services failed for BIN {bin_number}. Returning Unknown.")
    return default_result
