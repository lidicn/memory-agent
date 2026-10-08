import os, time
# 找 python 进程
pids = [d for d in os.listdir("/proc") if d.isdigit()]
for pid in pids:
    try:
        with open(f"/proc/{pid}/comm") as f:
            comm = f.read().strip()
        if "python" in comm or "uvicorn" in comm:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read().split()
            utime = int(stat[13])
            stime = int(stat[14])
            print(f"PID {pid} {comm} utime={utime} stime={stime} total={utime+stime}")
            # 读 cmdline
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode(errors="replace")[:120]
            print(f"  cmd: {cmd}")
    except Exception:
        pass
