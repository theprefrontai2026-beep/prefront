import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "oobingest.api:app",
        host=os.environ.get("OOB_HOST", "0.0.0.0"),
        port=int(os.environ.get("OOB_PORT", "8110")),
        log_level=os.environ.get("OOB_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
