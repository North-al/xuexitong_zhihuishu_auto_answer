import sys

from auto_answer.homework import main

if __name__ == "__main__":
    main(["gui"] if len(sys.argv) == 1 else None)
