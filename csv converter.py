#!/usr/bin/env python3
"""
Word -> CSV conversation parser
--------------------------------
Parses chat-like logs exported into a Word (.docx) file and converts them to
a clean CSV with four columns: date, time, sender, message.

This script is intentionally robust and tolerant of variations in dash
characters, optional role tags (e.g. "Rohan (Member)"), multi-line messages,
and date headers like: [15/08/2025] or [15/08/2025] (later in the day).

How it works (high level):
 - Read paragraphs from the .docx file (using python-docx). If python-docx
   isn't available, it tries docx2txt as a fallback.
 - Walk the text line-by-line, keeping track of the current date header.
 - For lines that start with a time (HH:MM), split into time, sender, message.
 - Lines without a leading time are appended to the previous message (continuation).
 - Output results to CSV with columns: date (YYYY-MM-DD), time (HH:MM), sender, message

Usage:
    python word_to_csv_converter.py "word file.docx" output.csv

Requirements (install if needed):
    pip install python-docx docx2txt

Notes & heuristics:
 - Date parsing assumes day-first format (DD/MM/YYYY) when a bracketed date
   like [15/08/2025] is found. If your file uses a different format, pass
   --date-format or edit the date parsing logic.
 - Supports dashes: hyphen (-), en-dash (–), em-dash (—) as separators.
 - Keeps the sender exactly as written (including role in parentheses).
 - Preserves message newlines by replacing internal newlines with "\\n" in the CSV
   (so that the CSV cell contains the full message). If you prefer raw newlines
   inside CSV cells, set --preserve-newlines.

This file is meant to be copied to a .py file and executed in your environment.
"""

from __future__ import annotations
import re
import sys
import csv
import argparse
from datetime import datetime
from typing import List, Dict, Optional

# Try python-docx first (recommended)
try:
    from docx import Document  # type: ignore
    _HAS_DOCX = True
except Exception:
    _HAS_DOCX = False

# Fallback: docx2txt (reads as plain text)
try:
    import docx2txt  # type: ignore
    _HAS_DOCX2TXT = True
except Exception:
    _HAS_DOCX2TXT = False


def read_docx_paragraphs(path: str) -> List[str]:
    """Read paragraphs from a .docx file and return as a list of text lines.

    Prioritises python-docx (retains paragraph boundaries). Falls back to
    docx2txt which returns a large blob; we splitlines() it.
    """
    if _HAS_DOCX:
        doc = Document(path)
        paras: List[str] = []
        for p in doc.paragraphs:
            text = p.text
            # Normalize weird non-breaking spaces
            text = text.replace('\u00a0', ' ').rstrip()
            if text:
                paras.append(text)
        return paras
    elif _HAS_DOCX2TXT:
        raw = docx2txt.process(path)
        # docx2txt sometimes returns None or str; guard
        if not raw:
            return []
        lines = [l.rstrip() for l in raw.splitlines() if l.strip()]
        return lines
    else:
        raise RuntimeError(
            "No library available to read .docx. Install python-docx or docx2txt."
        )


# Regex patterns (flexible)
# Date header like: [15/08/2025]  or [15/08/2025] (later in the day)
RE_DATE_HEADER = re.compile(r'^\s*\[(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\]')

# Message line like: 09:02 – Rohan (Member): Hi Ruby, ...
# Accepts different dashes and colon after sender.
RE_MESSAGE_LINE = re.compile(
    r'''^\s*(?P<time>\d{1,2}:\d{2})\s*[\u2013\u2014\-–—]?\s*(?P<sender>[^:]{1,120}?)\s*:\s*(?P<message>.*\S.*)$'''
)
# Explanation: allows en-dash/em-dash/hyphen between time and sender.


def normalize_date_str(date_str: str, date_format: Optional[str] = None) -> str:
    """Normalize date string like '15/08/2025' into ISO '2025-08-15'.

    If date_format is provided, datetime.strptime will use it. Otherwise try
    common day-first formats.
    """
    if date_format:
        dt = datetime.strptime(date_str, date_format)
    else:
        # Try common variants
        tried = ["%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"]
        dt = None
        for fmt in tried:
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except Exception:
                continue
        if dt is None:
            # As a last resort, return original
            return date_str
    return dt.strftime("%Y-%m-%d")


def parse_lines(lines: List[str], date_format: Optional[str] = None) -> List[Dict[str, str]]:
    """Parse the list of text lines into conversation rows.

    Returns a list of dicts with keys: date, time, sender, message
    """
    rows: List[Dict[str, str]] = []
    current_date: Optional[str] = None
    last_row: Optional[Dict[str, str]] = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Check for date header
        mdate = RE_DATE_HEADER.match(line)
        if mdate:
            date_text = mdate.group(1)
            iso = normalize_date_str(date_text, date_format)
            current_date = iso
            # Date header lines don't produce rows directly
            continue

        # Check for message line
        m = RE_MESSAGE_LINE.match(line)
        if m:
            time = m.group('time')
            sender = m.group('sender').strip()
            message = m.group('message').strip()

            # If there's no current_date, leave date blank or try to use the last row's date
            date_to_use = current_date if current_date else (rows[-1]['date'] if rows else '')

            row = {
                'date': date_to_use or '',
                'time': time,
                'sender': sender,
                'message': message,
            }
            rows.append(row)
            last_row = row
            continue

        # If the line doesn't match a new message, treat it as a continuation of the last message
        if last_row is not None:
            # Append with newline to preserve paragraphs inside a message
            last_row['message'] = last_row['message'] + '\n' + line
        else:
            # No previous message to append to: create a placeholder row with unknown time/sender
            date_to_use = current_date if current_date else ''
            placeholder = {
                'date': date_to_use,
                'time': '',
                'sender': '',
                'message': line,
            }
            rows.append(placeholder)
            last_row = placeholder

    return rows


def write_csv(rows: List[Dict[str, str]], out_path: str, preserve_newlines: bool = False) -> None:
    """Write parsed rows to CSV. Uses utf-8-sig to be Excel-friendly.

    If preserve_newlines is False, internal message newlines will be replaced
    with literal '\\n' so each CSV cell remains a single line. If True, the
    CSV will contain actual newlines inside quoted fields which is also valid.
    """
    fieldnames = ['date', 'time', 'sender', 'message']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            rowcopy = r.copy()
            if not preserve_newlines:
                rowcopy['message'] = rowcopy['message'].replace('\r', '').replace('\n', '\\n')
            writer.writerow(rowcopy)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert a Word (.docx) chat log into a CSV with columns date,time,sender,message.")
    parser.add_argument('input', help='Path to the input .docx file')
    parser.add_argument('output', help='Path to the output .csv file')
    parser.add_argument('--date-format', default=None, help='Optional date format for parsing bracketed dates (e.g. "%%d/%%m/%%Y")')
    parser.add_argument('--preserve-newlines', action='store_true', help='Store real newlines inside CSV message cells (quoted)')
    parser.add_argument('--show-sample', action='store_true', help='Print parsed sample rows to stdout (first 10)')
    args = parser.parse_args(argv)

    try:
        lines = read_docx_paragraphs(args.input)
    except Exception as e:
        print("Error reading .docx:", e, file=sys.stderr)
        sys.exit(2)

    # Robustness: sometimes the docx file contains long paragraphs with embedded newlines
    # so split any paragraphs that contain multiple logical lines
    expanded_lines: List[str] = []
    for p in lines:
        # split on explicit newlines inside paragraphs too
        for sub in re.split(r"\r?\n", p):
            s = sub.strip()
            if s:
                expanded_lines.append(s)

    rows = parse_lines(expanded_lines, date_format=args.date_format)

    if args.show_sample:
        print('\n--- Sample parsed rows (first 10) ---')
        for i, r in enumerate(rows[:10]):
            print(i + 1, r)
        print('------------------------------------\n')

    write_csv(rows, args.output, preserve_newlines=args.preserve_newlines)
    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == '__main__':
    main()
