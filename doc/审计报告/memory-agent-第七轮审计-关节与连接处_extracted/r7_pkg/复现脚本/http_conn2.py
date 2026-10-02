import time, threading, http.server, httpx, statistics
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length','7'); self.end_headers(); self.wfile.write(b'{"ok":1}')
    def log_message(self,*a): pass
srv=http.server.HTTPServer(('127.0.0.1',18123),H)
threading.Thread(target=srv.serve_forever,daemon=True).start(); time.sleep(0.3)
URL="http://127.0.0.1:18123/api/states"

def bench(fn, n=100, rep=3):
    out=[]
    for _ in range(rep):
        t0=time.time()
        for _ in range(n): fn()
        out.append((time.time()-t0)*1000/n)
    return statistics.median(out), min(out), max(out)

print("="*78); print("拆解：单次 HA 调用的耗时构成（中位数 ms/次，3 轮取中位）"); print("="*78)
def new_each():
    with httpx.Client() as c: c.get(URL, timeout=5)
c=httpx.Client()
def reuse(): c.get(URL, timeout=5)
def create_only():
    cc=httpx.Client(); cc.close()

m1=bench(new_each); m2=bench(reuse); m3=bench(create_only)
print(f"\n   A) 每次新建 client + 请求   {m1[0]:>7.2f} ms/次  (min {m1[1]:.2f} / max {m1[2]:.2f})")
print(f"   B) 复用 client + 请求       {m2[0]:>7.2f} ms/次  (min {m2[1]:.2f} / max {m2[2]:.2f})")
print(f"   C) 仅创建+关闭 client（无请求）{m3[0]:>7.2f} ms/次")
print(f"\n   → 建连固定成本约 {m3[0]:.2f} ms/次，占 A 的 {m3[0]/m1[0]*100:.0f}%")
print(f"   → 复用可省 {m1[0]/max(m2[0],0.001):.0f}×")
c.close(); srv.shutdown()
print()
print("="*78)
print("注意：本地回环测试。真实 HA 在局域网/NAS 上，建连成本更高（DNS+TCP+TLS）")
print("="*78)
