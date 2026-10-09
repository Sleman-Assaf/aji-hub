# assist/markdown_blocks.py
import re

_SEP_RE = re.compile(r'^\s*\|[-:\s|]+\|\s*$')


def parse_markdown_blocks(md_text):
    """Split markdown text into an ordered list of blocks:
    {'type': 'text', 'content': str} or {'type': 'table', 'rows': [[str, ...], ...]}
    Table detection mirrors the client-side markdownToHtml() table parsing in base.html:
    contiguous lines starting with '|', with the separator row (---|---) dropped.
    """
    lines = (md_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks = []
    buf = []
    i = 0

    def flush_text():
        content = '\n'.join(buf).strip()
        if content:
            blocks.append({'type': 'text', 'content': content})
        buf.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('|') and '|' in line:
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|') and '|' in lines[i]:
                table_lines.append(lines[i])
                i += 1
            data_rows = [r for r in table_lines if not _SEP_RE.match(r)]
            if data_rows:
                flush_text()
                rows = [
                    [c.strip() for c in r.strip().strip('|').split('|')]
                    for r in data_rows
                ]
                blocks.append({'type': 'table', 'rows': rows})
                continue
            else:
                buf.extend(table_lines)
                continue
        buf.append(line)
        i += 1

    flush_text()
    return blocks


def extract_tables(md_text):
    """Convenience: just the tables (list of row-lists) found in md_text."""
    return [b['rows'] for b in parse_markdown_blocks(md_text) if b['type'] == 'table']
