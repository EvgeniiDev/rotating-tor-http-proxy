import asyncio
import urllib.parse
import requests
import logging
import random
import concurrent.futures
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class TorRelayGrabber:
    def __init__(self, timeout=10.0, proxy=None):
        self.timeout = timeout
        self.proxy = {'https': proxy} if proxy else None
        self.cors_proxies = [
            "https://api.codetabs.com/v1/proxy/?quest={}",
            "https://corsproxy.io/?{}",
            "https://api.allorigins.win/get?url={}",
            "https://test.cors.workers.dev/?"
        ]

    def _grab(self, url):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        try:
            response = requests.get(url, timeout=int(self.timeout), 
                                  proxies=self.proxy, headers=headers)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"Failed to fetch from {url}: {e}")
        return None

    def grab(self, preferred_urls_list=None):
        base_url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=fingerprint,or_addresses,country"
        
        urls = []
        if preferred_urls_list:
            urls.extend(preferred_urls_list)
        
        urls.append(base_url)
        
        for cors_proxy in self.cors_proxies:
            if "{}" in cors_proxy:
                urls.append(cors_proxy.format(urllib.parse.quote(base_url)))
        
        urls.extend([
            "https://github.com/ValdikSS/tor-onionoo-mirror/raw/master/details-running-relays-fingerprint-address-only.json",
            "https://bitbucket.org/ValdikSS/tor-onionoo-mirror/raw/master/details-running-relays-fingerprint-address-only.json"
        ])

        for url in urls:
            try:
                data = self._grab(url)
                if data:
                    return data
            except Exception as e:
                logger.debug(f"Can't download from {url}: {e}")
        return None

    def grab_parse(self, preferred_urls_list=None):
        grabbed = self.grab(preferred_urls_list)
        if grabbed and "relays" in grabbed:
            return grabbed["relays"]
        return []


class TorRelay:
    def __init__(self, relayinfo):
        self.relayinfo = relayinfo
        self.fingerprint = relayinfo.get("fingerprint", "")
        self.country = relayinfo.get("country", "")
        self.iptuples = self._parse_or_addresses(relayinfo.get("or_addresses", []))
        self.reachable = []

    def _parse_or_addresses(self, or_addresses):
        ret = []
        for address in or_addresses:
            try:
                parsed = urllib.parse.urlparse("//" + address)
                if parsed.hostname and parsed.port:
                    ret.append((parsed.hostname, parsed.port))
            except Exception:
                continue
        return ret

    async def check_connectivity(self, timeout=5.0):
        self.reachable = []
        for host, port in self.iptuples:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout)
                writer.close()
                await writer.wait_closed()
                self.reachable.append((host, port))
            except Exception:
                continue
        return len(self.reachable) > 0

    def get_bridge_lines(self):
        lines = []
        for host, port in self.reachable:
            host_str = f"[{host}]" if ":" in host else host
            lines.append(f"obfs4 {host_str}:{port} {self.fingerprint} cert=AUTO iat-mode=0")
        return lines


class TorRelayParser:
    def __init__(self, timeout=10.0, proxy=None):
        self.grabber = TorRelayGrabber(timeout, proxy)
        
    def get_working_bridges(self, count=10, countries=None, ports=None):
        relays = self.grabber.grab_parse()
        if not relays:
            return []
        
        random.shuffle(relays)
        
        if countries:
            country_set = set(countries)
            relays = [r for r in relays if r.get("country", "").lower() in country_set]
        
        if ports:
            port_set = set(ports)
            filtered_relays = []
            for relay in relays:
                relay_obj = TorRelay(relay)
                if any(port in port_set for _, port in relay_obj.iptuples):
                    filtered_relays.append(relay)
            relays = filtered_relays
        
        return self._test_relays_sync(relays[:count * 3])
    
    def _test_relays_sync(self, relays):
        try:
            return asyncio.run(self._test_relays_async(relays))
        except Exception as e:
            logger.error(f"Error testing relays: {e}")
            return []
    
    async def _test_relays_async(self, relays):
        working_relays = []
        relay_objects = [TorRelay(r) for r in relays]
        
        tasks = []
        for relay in relay_objects:
            tasks.append(relay.check_connectivity())
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for relay, result in zip(relay_objects, results):
            if result is True and relay.reachable:
                working_relays.append(relay)
        
        return working_relays
