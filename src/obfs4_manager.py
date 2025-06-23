import logging
from typing import List
from tor_relay_parser import TorRelayParser

logger = logging.getLogger(__name__)


class OBFS4Manager:
    def __init__(self):
        self.relay_parser = TorRelayParser()
        
    def get_builtin_bridges(self) -> List[str]:
        builtin_bridges = [
            "obfs4 154.35.22.10:80 8FB9F4319E89E5C6223052AA525A192AFBC85D55 cert=GGGS1TX4R81m3r0HBl79wKy1OtPPNR2CZUIrHjkRg65Vc2VR8fOyo64f9kmT1UAFG7j0HQ iat-mode=0",
            "obfs4 154.35.22.11:80 204368A7AD7F7F49B8B05D4E3B7DBC9ED8A8AD91 cert=uklD5tmUzjZLmJF8XDOJCnuZrFZGR7UdUhm5WYWg9QV6dJEJ0n1qISRjkQTXlWYSFXF3wA iat-mode=0",
            "obfs4 154.35.22.12:80 00DC6C4FA49A65BD1472993CF6730D54F11E0DBB cert=N86E9hKXXXVz6G7w2z8wFfhIDztDAzZ/3poxVePHEYjbKDWzjkRDccFMAnhK75fc65pYSg iat-mode=0",
            "obfs4 154.35.22.13:80 A832D176ECD5C7C6B58825AE22FC4C90FA249637 cert=YPbQqXaqaXjJpsM4YSAHVzQ3Dh4ynYjdPUGRjxLoB2cRZeKjGZKRo6Ag8gQSMVQq3SfoPQ iat-mode=0",
            "obfs4 199.195.251.84:443 FF7A25ED2C05A37B16E2A58A0E6C7FA4F3C85E6F cert=ZQXKQFVG7E2bEyQ4MN8T6oLhZqD3zTJdXg+Ut8TFnGDXhPRFdwCJrKwpPFiNUxXKAzrr6A iat-mode=0",
            "obfs4 192.95.36.142:443 CDF2E852BF539B82BD10E27E9115A31734E378C2 cert=qUVQ0srL1JI/vO6V6m/24anYXiJD3QP2HgzUKQtQ7GRqqUvs7P7ILK2YOUy6XRDBLAa0pQ iat-mode=0",
            "obfs4 85.31.186.26:443 91A6354697E6B02A386312F68D82CF86824D3606 cert=PBwr+S8JTVZo6MPdHnkTwXJPILWADLqfMGoVvhZClMq/Urndyd42BwX9YFJHZnBB3H0XCw iat-mode=0",
            "obfs4 85.31.186.98:443 011F2599C0E9B27A22B8F6BC96DB8B8F0EC144D0 cert=MH12GFaKp7oO5wTEOKrKU+dGMLEJjWKbwGfC9E5dBKg4I4E4Lp3Fs0NkfTb6LRXKnPHykg iat-mode=0",
            "obfs4 193.11.166.194:27015 2D82C2E354CE8E5938A1D6A98F39132CF4D5C3F3 cert=6U4sWXJJh+XAWMu2s6p1Y+4gS5Rp3L4hn4qcN4O7m0YKmz8MzNxY9m4K2L3X7qK9z8v1a iat-mode=0",
            "obfs4 193.11.166.194:27020 F8B9E3BBE5B42CDC1ABE3B491F5A6D3BF2F6F5C8 cert=iMGGEhUF7lDWZVqGq2yGZs+EQDh5tZhLNvhV3h+GJf4hNb6MzFkLhDxS3xQtZpfqJMvHq iat-mode=0"
        ]
        return builtin_bridges
        

        
    def get_fresh_bridges(self, count: int = 10) -> List[str]:
        try:
            working_relays = self.relay_parser.get_working_bridges(
                count=max(count // 2, 3),
                ports=[80, 443]
            )
            
            if len(working_relays) < count // 3:
                logger.info("Not enough bridges found, trying extended ports")
                working_relays = self.relay_parser.get_working_bridges(
                    count=count,
                    ports=[80, 443, 8080, 8443, 9000, 9001]
                )
            
            fresh_bridges = []
            for relay in working_relays:
                bridge_lines = relay.get_bridge_lines()
                fresh_bridges.extend(bridge_lines)
            
            if not fresh_bridges:
                logger.warning("No fresh bridges found, using builtin bridges")
                return self.get_builtin_bridges()[:count]
            
            logger.info(f"Retrieved {len(fresh_bridges)} fresh working bridges")
            return fresh_bridges[:count]
        except Exception as e:
            logger.error(f"Failed to get fresh bridges: {e}")
            return self.get_builtin_bridges()[:count]
