"""Entry point: python -m parakeet_dictate"""

from . import config


def main():
    config.setup_logging()
    _lock = config.single_instance_lock()  # noqa: F841 - held for the life of the process
    if config.IS_MAC:
        from .mac_app import main as run
    elif config.IS_WIN:
        from .win_app import main as run
    else:
        raise SystemExit("Parakeet Dictate supports macOS and Windows only")
    run()


if __name__ == "__main__":
    main()
