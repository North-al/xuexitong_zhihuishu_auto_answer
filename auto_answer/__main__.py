import sys

from auto_answer.homework import main as _homework_main


def main(argv: list[str] | None = None) -> None:
    """CLI / console_script 入口：无参数时打开 GUI。"""
    if argv is None:
        argv = ["gui"] if len(sys.argv) <= 1 else None
    _homework_main(argv)


if __name__ == "__main__":
    main()
