# -*- coding: utf-8 -*-
"""读取 TBQuant .tbf 日志（SQLite）：修补页大小字段后导出为文本。

用法（在仓库根目录执行）:
  python tbquant/tools/read_tbf.py <日志.tbf> <导出.txt>

说明：TB 生成的 .tbf 页大小字段非标，直接打开会报错；脚本把偏移 16 处的
      页大小修补为 0x1000（4096）后只读导出。输出格式与 .tbf 表一致
      （'### TABLE' 头 + 'id | ts | msg' 行），可直接作为 check_slice.py 的输入。
      修补副本留在源文件旁（.patched），源 .tbf 不被修改。
"""
import sqlite3
import sys
import io

src, out_path = sys.argv[1], sys.argv[2]
b = bytearray(open(src, 'rb').read())
if b[16:18] != b'\x10\x00':
    b[16:18] = b'\x10\x00'
    db = src + '.patched'
    open(db, 'wb').write(bytes(b))
else:
    db = src

def _decode(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', errors='replace')

con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
con.text_factory = _decode
cur = con.cursor()
out = io.open(out_path, 'w', encoding='utf-8')

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print('tables:', tables)
for t in tables:
    cols = [d[1] for d in cur.execute('PRAGMA table_info("%s")' % t)]
    n = cur.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    print('table=%s cols=%r rows=%d' % (t, cols, n))
    out.write('### TABLE %s (cols=%r, rows=%d)\n' % (t, cols, n))
    for row in cur.execute('SELECT * FROM "%s"' % t):
        out.write(' | '.join('' if v is None else str(v) for v in tuple(row)) + '\n')

out.close()
con.close()
print('done ->', out_path)
