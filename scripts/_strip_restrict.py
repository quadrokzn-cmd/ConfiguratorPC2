import sys, re, pathlib

src = pathlib.Path('scripts/preprod_seed.sql')
dst = pathlib.Path('scripts/preprod_seed.clean.sql')
data = src.read_bytes()
pattern = re.compile(rb'^\\(?:restrict|unrestrict)[^\r\n]*\r?\n', re.MULTILINE)
out, n = pattern.subn(b'', data)
dst.write_bytes(out)
print(f'src_bytes={len(data)} dst_bytes={len(out)} removed_lines={n}')
