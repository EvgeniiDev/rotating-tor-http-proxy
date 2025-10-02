import asyncio
import logging
import time
from pathlib import Path
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

# Fixed import - use absolute import instead of relative
from .mitmproxy_balancer import MitmproxyBalancerAddon

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_mitmproxy")

# Create a dummy config file
proxy_urls = [
    "socks5://127.0.0.1:9050",
    "socks5://127.0.0.1:9051",
    "socks5://127.0.0.1:9052",
]

config_path = Path("temp_proxies.json")
logger.info(f"Created dummy proxy list with {len(proxy_urls)} proxies")


def run_master():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def start_master():
        logger.info("Creating options")
        options = Options(
            listen_host="127.0.0.1",
            listen_port=8080,
        )
        logger.info("Creating master")
        master = DumpMaster(options)
        logger.info("Master created successfully")

        # Add our balancer addon directly
        balancer_addon = MitmproxyBalancerAddon(
            proxy_urls, retry_limit=10, failure_threshold=2, cooldown_seconds=15.0
        )
        master.addons.add(balancer_addon)

        logger.info("Balancer addon loaded")
        logger.info("Setup complete, master ready to run")
        # await master.run()  # commented for test

    try:
        loop.run_until_complete(start_master())
    except Exception as e:
        logger.error("Error in setup: %s", e, exc_info=True)
    finally:
        logger.info("Test setup completed inside finally")
        loop.close()


run_master()

# Keep the script running for testing
time.sleep(30)  # Run for 30 seconds to check if it starts without errors

logger.info("Test setup completed")
