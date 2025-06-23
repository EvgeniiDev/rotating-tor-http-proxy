import asyncio
import random
import urllib.parse
import requests
import logging

logger = logging.getLogger(__name__)


class TCPSocketConnectChecker:
    def __init__(self, host, port, timeout=10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connection_status = None

    def __repr__(self):
        return "{}:{}".format(
            self.host if self.host.find(":") == -1 else "[" + self.host + "]",
            self.port)

    async def connect(self):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
            writer.close()
            await writer.wait_closed()
            self.connection_status = True
            return (True, None)
        except (OSError, asyncio.TimeoutError) as e:
            self.connection_status = False
            return (False, e)


class TorRelayGrabber:
    def __init__(self, timeout=10.0):
        self.timeout = timeout

    def _grab(self, url):
        with requests.get(url, timeout=int(self.timeout)) as r:
            return r.json()

    def grab(self):
        BASEURL = "https://onionoo.torproject.org/details?type=relay&running=true&fields=fingerprint,or_addresses,country"
        CORS_URLS = [
            f"https://api.codetabs.com/v1/proxy/?quest={BASEURL}",
            f"https://api.allorigins.win/get?url={urllib.parse.quote(BASEURL)}",
            f"https://test.cors.workers.dev/?{BASEURL}",
            f"https://corsproxy.io/{urllib.parse.quote(BASEURL)}",
            BASEURL
        ]

        for url in CORS_URLS:
            try:
                response = self._grab(url)
                if 'api.allorigins.win' in url:
                    import json
                    response = json.loads(response['contents'])
                return response
            except Exception as e:
                logger.debug(f"Failed to grab from {url}: {e}")
                
        return None

    def grab_parse(self):
        grabbed = self.grab()
        if grabbed and "relays" in grabbed:
            return grabbed["relays"]
        return []


class TorRelay:
    def __init__(self, relayinfo):
        self.relayinfo = relayinfo
        self.fingerprint = relayinfo["fingerprint"]
        self.iptuples = self._parse_or_addresses(relayinfo["or_addresses"])
        self.reachable = []

    def _parse_or_addresses(self, or_addresses):
        ret = []
        for address in or_addresses:
            parsed = urllib.parse.urlparse("//" + address)
            ret.append((parsed.hostname, parsed.port))
        return ret

    async def check(self, timeout=10.0):
        for i in self.iptuples:
            s = TCPSocketConnectChecker(i[0], i[1], timeout=timeout)
            sc = await s.connect()
            if sc[0]:
                self.reachable.append(i)
        return bool(self.reachable)

    def get_bridge_lines(self):
        lines = []
        for ip, port in self.reachable:
            ip_str = ip if ip.find(":") == -1 else "[" + ip + "]"
            lines.append(f"obfs4 {ip_str}:{port} {self.fingerprint} cert=AUTO iat-mode=0")
        return lines


class BridgeParser:
    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self.grabber = TorRelayGrabber(timeout)

    async def get_working_bridges(self, max_relays=50, target_bridges=10):
        logger.info("Downloading Tor relay information")
        relays_data = self.grabber.grab_parse()
        
        if not relays_data:
            logger.error("Failed to download relay information")
            return []

        random.shuffle(relays_data)
        relays_data = relays_data[:max_relays]

        logger.info(f"Testing {len(relays_data)} relays")
        test_relays = [TorRelay(r) for r in relays_data]

        tasks = []
        for relay in test_relays:
            tasks.append(asyncio.create_task(relay.check(self.timeout)))
        
        await asyncio.gather(*tasks)

        working_bridges = []
        for relay in test_relays:
            if relay.reachable:
                bridge_lines = relay.get_bridge_lines()
                working_bridges.extend(bridge_lines)
                if len(working_bridges) >= target_bridges:
                    break

        logger.info(f"Found {len(working_bridges)} working bridges")
        return working_bridges[:target_bridges]

    def get_working_bridges_sync(self, max_relays=50, target_bridges=10):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.get_working_bridges(max_relays, target_bridges)
        )
