import uvicorn

from . import config


def main() -> None:
    uvicorn.run(
        "evalengine.api:app",
        host=config.EVAL_HOST,
        port=config.EVAL_PORT,
        log_level=config.EVAL_LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
