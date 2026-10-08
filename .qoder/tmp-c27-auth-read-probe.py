"""A3 §八「未卸载的同步 I/O」的两格慢操作到底多慢——只测时长，不打印任何内容。

成对读数用的探针（不写任何东西）：同一份探针在 `/app/src`（部署码=改前）与
工作区快照 `src`（改后）各跑一遍，量鉴权链上那两次同步调用**本身**的耗时：

- `AuthManager.has_users()` → `_load_users()` → `open()`+`json.load()` 账号文件：
  改前每个非公开请求都要在事件循环上走一遍（JWT 分支的 `verify_token` 里是同一条读盘）；
- `bcrypt.hashpw` / `bcrypt.checkpw`：改前在协程里直调（Basic 分支每请求一次）。

对照档 `login_allowed()` 是纯内存字典查——它把"延迟来自读盘而不是函数调用本身"
和主读数放进同一次运行，免得只给一个孤零零的分位数。

输出只有时长分位数、账号文件字节数与账号条数；不打印路径、用户名、哈希或凭据。
"""
import os
import statistics
import time

from memory_agent.auth import AuthManager
from memory_agent.config import Config

N = 40


def _pct(xs, p):
    """小样本分位数：按排序位置取、不插值（读数口径要说得出是怎么算的）。"""
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _times(fn, n=N):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000.0)
    return xs


cfg = Config()
am = AuthManager(cfg)
print("ACCOUNT_ROWS=%d USERS_FILE_BYTES=%d" % (
    len(am._load_users()), os.path.getsize(str(am.users_file))))

disk = _times(am.has_users)
print("DISK_READ_MS p50=%.3f p95=%.3f max=%.3f mean=%.3f  (账号文件同步读，改前每请求一次)" % (
    _pct(disk, 50), _pct(disk, 95), max(disk), statistics.fmean(disk)))

mem = _times(lambda: am.login_allowed("10.0.0.1", "probe"))
print("IN_MEMORY_MS p50=%.3f p95=%.3f max=%.3f  (对照档：不读盘的鉴权步骤)" % (
    _pct(mem, 50), _pct(mem, 95), max(mem)))

try:
    import bcrypt
except ImportError as exc:
    print("BCRYPT_IMPORT=FAIL %s" % type(exc).__name__)
    raise SystemExit(0)

PW = b"probe-password-not-a-real-one"
salt = bcrypt.gensalt()
digest = bcrypt.hashpw(PW, salt)
h = _times(lambda: bcrypt.hashpw(PW, salt), n=5)
c = _times(lambda: bcrypt.checkpw(PW, digest), n=10)
print("BCRYPT_HASH_MS p50=%.1f max=%.1f  BCRYPT_CHECK_MS p50=%.1f p95=%.1f max=%.1f  cost=%s" % (
    _pct(h, 50), max(h), _pct(c, 50), _pct(c, 95), max(c),
    salt.decode("ascii", "ignore").split("$")[2]))
print("PROBE_DONE=1")
