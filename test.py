"""교과서 PDF 구조화 파서 명령행 진입점.

사용 예:
    python test.py parse "원고.pdf" --output output/pdf
    python test.py parse "." --output output/pdf --recursive
"""

from textbook_parser import main


if __name__ == "__main__":
    raise SystemExit(main())
