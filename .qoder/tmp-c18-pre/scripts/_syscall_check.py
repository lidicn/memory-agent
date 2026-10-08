import os
# 读 PID 1 的 syscall 和 wchan
pid = 1
try:
    with open(f"/proc/{pid}/syscall") as f:
        print("syscall:", f.read().strip()[:200])
except Exception as e:
    print("syscall err:", e)
try:
    with open(f"/proc/{pid}/wchan") as f:
        print("wchan:", f.read().strip()[:200])
except Exception as e:
    print("wchan err:", e)
try:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if any(k in line for k in ("State:", "VmRSS:", "Threads:", "voluntary")):
                print(line.strip())
except Exception as e:
    print("status err:", e)
# 列线程
try:
    threads = os.listdir(f"/proc/{pid}/task")
    print(f"threads: {len(threads)}")
    for tid in threads[:10]:
        try:
            with open(f"/proc/{pid}/task/{tid}/comm") as f:
                print(f"  tid {tid}: {f.read().strip()}")
        except:
            pass
except Exception as e:
    print("task err:", e)
