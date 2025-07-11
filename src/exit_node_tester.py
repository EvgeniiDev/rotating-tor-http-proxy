import requests
from typing import List

class ExitNodeChecker:
    """
    Отвечает только за проверку пригодности exit-ноды для парсинга Steam.
    """
    def __init__(self, test_url: str = "https://steamcommunity.com/market/search?appid=730", test_requests_count: int = 6, required_success_count: int = 3, timeout: int = 20):
        self.test_url = test_url
        self.test_requests_count = test_requests_count
        self.required_success_count = required_success_count
        self.timeout = timeout

    def test_node(self, proxy: dict) -> bool:
        success_count = 0
        for _ in range(self.test_requests_count):
            try:
                response = requests.get(self.test_url, proxies=proxy, timeout=self.timeout)
                if response.status_code == 200:
                    success_count += 1
                    if success_count >= self.required_success_count:
                        return True
            except Exception:
                continue
        return success_count >= self.required_success_count

    def test_nodes(self, proxies: List[dict]) -> List[dict]:
        return [proxy for proxy in proxies if self.test_node(proxy)]
