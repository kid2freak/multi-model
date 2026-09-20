#!/usr/bin/env python3
"""Extract text from a PDF (or pass a .md/.txt through) for review. Usage: pdf_text.py input.pdf > paper.txt"""
import sys
src = sys.argv[1]
if src.lower().endswith(".pdf"):
    from pypdf import PdfReader
    for i, page in enumerate(PdfReader(src).pages, 1):
        print(f"\n<<page {i}>>\n" + (page.extract_text() or ""))
else:
    sys.stdout.write(open(src, encoding="utf-8", errors="replace").read())
