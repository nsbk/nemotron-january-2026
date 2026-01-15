"""
Custom Pipecat runner with health endpoints.

This module wraps pipecat's runner to add /health and /ready endpoints
to the FastAPI app.

Usage:
    # In your bot file, replace:
    #   from pipecat.runner.run import main
    #   main()
    # With:
    #   from runner import main
    #   main(bot)
"""

import argparse
import os
import sys

import uvicorn
from loguru import logger

from health import add_health_routes


def main(bot_func=None):
    """
    Run the bot with health endpoints.

    This wraps pipecat's runner to add /health and /ready endpoints.

    Args:
        bot_func: Optional bot function (ignored, pipecat discovers it automatically).
    """
    # Import pipecat's app creator
    from pipecat.runner.run import _create_server_app

    # Parse arguments (same as pipecat's runner)
    parser = argparse.ArgumentParser(description="Nemotron Voice Bot")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("BOT_PORT", "7860")),
        help="Port number"
    )
    parser.add_argument(
        "-t", "--transport",
        type=str,
        choices=["daily", "webrtc", "twilio", "telnyx", "plivo", "exotel"],
        default="webrtc",
        help="Transport type",
    )
    parser.add_argument("--proxy", "-x", help="Public proxy host name")
    parser.add_argument(
        "--esp32",
        action="store_true",
        default=False,
        help="Enable SDP munging for ESP32 compatibility",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity"
    )

    args = parser.parse_args()

    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level="TRACE" if args.verbose else "DEBUG")

    # Print startup info
    print()
    print(f"🚀 Bot ready with health endpoints!")
    print(f"   → Client UI: http://{args.host}:{args.port}/client")
    print(f"   → Health check: http://{args.host}:{args.port}/health")
    print(f"   → Readiness check: http://{args.host}:{args.port}/ready")
    print()

    # Create the pipecat app
    app = _create_server_app(
        transport_type=args.transport,
        host=args.host,
        proxy=args.proxy,
        esp32_mode=args.esp32,
        whatsapp_enabled=False,
        folder=None,
        dialin_enabled=False,
    )

    # Add health endpoints to the app
    add_health_routes(app)

    # Run the server
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
