from __future__ import annotations

import asyncio
import signal

from .config_manager import build_arg_parser, load_settings
from .logging_utils import configure_logging, get_logger
from .tor_proxy_integrator import TorProxyIntegrator
from .utils import ensure_directory


async def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    settings = load_settings(args)
    ensure_directory(settings.tor_data_dir)
    configure_logging(settings)
    logger = get_logger("main")

    integrator = TorProxyIntegrator(settings)
    shutdown_event = asyncio.Event()

    def _shutdown(signum: int, frame) -> None:  # noqa: ARG001
        logger.info("Received signal %s, shutting down", signum)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    try:
        await integrator.start_pool()
        logger.info(
            "Rotating Tor proxy running on socks5://127.0.0.1:%s", settings.frontend_port
        )
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, stopping pool")
    finally:
        await integrator.stop_pool()


if __name__ == "__main__":
    asyncio.run(main())