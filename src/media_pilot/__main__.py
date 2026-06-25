import logging
import sys

import uvicorn

from media_pilot.app import create_runtime_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    uvicorn.run(create_runtime_app(), host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
