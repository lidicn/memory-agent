import hashlib
import sys

# run17 格 0 的"快照真身"核对：对 filelist 里每个文件按**字节**取 md5，再对整张表取聚合摘要。
# 本机与容器各跑一次（cwd 都是树根、filelist 同一份），聚合摘要必须逐字相同。
# 这一格替代了以前"几十条 grep 锚点两侧手抄对表"的做法：锚点只用来证形状，
# "容器里跑的就是我这棵树"由这里一票判掉。缺文件（MISSING）一律算整跑作废。
paths = [l.strip() for l in open(sys.argv[1], encoding='utf-8') if l.strip()]
lines, missing = [], []
for p in sorted(set(paths)):
    try:
        with open(p, 'rb') as fh:
            lines.append(hashlib.md5(fh.read()).hexdigest() + ' ' + p)
    except OSError:
        missing.append(p)
print('HASH_LISTED=%d HASH_FILES=%d HASH_MISSING=%d' % (len(set(paths)), len(lines), len(missing)))
for m in sorted(missing)[:12]:
    print('MISSING ' + m)
print('HASH_AGGREGATE=' + hashlib.sha256('\n'.join(lines).encode()).hexdigest())
