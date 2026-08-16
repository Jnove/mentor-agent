#!/usr/bin/env python3
"""Dump staged kb markdown files body (after frontmatter) for audit review.
Usage: python _dump_batch.py <dir> <start_idx> <count>
Prints: index | filename (repr) | publish_date | body (title/date lines stripped)
"""
import sys, os, glob, re

target_dir, start, count = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
files = sorted(glob.glob(os.path.join(target_dir, '*.md')))
end = min(start + count, len(files))

for i in range(start, end):
    p = files[i]
    with open(p, encoding='utf-8') as f:
        text = f.read()
    parts = text.split('---', 2)
    fm = parts[1] if len(parts) >= 3 else ''
    body = parts[2] if len(parts) >= 3 else text
    m = re.search(r'^publish_date:\s*[\'\"](.*?)[\'\"]', fm, re.M)
    pub = m.group(1) if m else '?'
    lines = body.split('\n')
    # strip leading # title and > date lines
    while lines and (lines[0].strip().startswith('#') or lines[0].strip().startswith('>')):
        lines.pop(0)
    clean = '\n'.join(l for l in lines if l.strip()).strip()
    print(f'===== [{i}] {os.path.basename(p)!r} | pub={pub} =====')
    print(clean[:1800])
    print()
