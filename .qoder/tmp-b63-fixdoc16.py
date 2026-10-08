"""把 §十六 整段挪到「通用还开着的格」清单之后（该清单归 §十五），并给 §十六 配一份**增量**清单。

事故记录（本脚本要修的两个错，都是我自己造成的）：
1. 上一步 Edit 把 §十五 的标题 `### 还开着的格（…#40/#58）` 直接顶成了 `…#63`，
   于是通用清单被挂到 §十六 名下、§十五 丢了标题；
2. 第一版搬运脚本用 `lines.index('---')` 找页脚——**这份文档里有 13 条 `---` 分隔线**，
   index 命中的是第 11 行那条，切片 `lines[798:10]` 直接成了空表，写出来的文件从第 11 行起整段重复
   （813 → 1611 行）。已用 `.qoder/bak-progress-before-move16.md` 复原。
   规矩：**搬运类脚本必须先算完再断言、断言过了才落盘**，且页脚要用"最后一个 `---`"而不是"第一个"。
"""
import io

P = 'doc/审计报告/进度与交接/Qoder接手进度_20261004.md'
H15 = '### 还开着的格（与本批无关，别混进 #40/#58）'
H16 = '### 还开着的格（与本批无关，别混进 #63）'
H16NEW = '### 本批留下的增量格（别和上面那份通用清单混读）'
SEC = '## 十六、2026-10-06 追加（任务表 #63：`behavior_routes` 输入边界 + 事件循环收口，容器 run14 → run14c）'

raw = io.open(P, encoding='utf-8', newline='').read()
lines = raw.split('\n')
i_sec = lines.index(SEC)
i_h16 = lines.index(H16)
hr_after = [i for i, l in enumerate(lines) if l == '---' and i > i_h16]
assert len(hr_after) == 1, ('页脚 --- 不唯一', hr_after)
i_ftr = hr_after[0]
assert i_h16 > i_sec, '标题顺序不对'

body = lines[i_sec + 1:i_h16]
openlist = lines[i_h16 + 1:i_ftr]
assert 20 < len(body) < 60, len(body)
assert 5 <= len(openlist) <= 12, len(openlist)

delta = ['', H16NEW, '',
         '- **69% 不是 100%**：`behavior_routes` 还剩 **207 条未执行语句**（本批只清了输入边界与事件循环两族），'
         '下一档继续量。引用覆盖率必须带口径——**本机 Python313 + coverage 7.16.1 全量单轮**是一档，'
         '容器 SUITE（1544 passed / 10 skipped）是另一档；两档各自的 passed+skipped 都是 1554 才对得上，'
         '数字对得上不等于可以混引。',
         '- **族 2 的四处卸载只量到"心跳没停"**：`PROBE` 的 `tick=44` + 线程记录 `asyncio_0` 是这一族的运行时证据，'
         '**不是压测**。A7 §5.1 那三项（`llm_ask(CC=45)` 重端点压测、72h 长跑、`voice_util`/`behavior_predictor` 功能覆盖）'
         '仍然整格开着——通用清单里那条别当成本批"顺手做了一点"。',
         '- **`.coverage` 不入库**：它没被 `.gitignore` 覆盖（`git check-ignore` 非零），跑完就删，'
         '永远不进显式 stage 清单。',
         '- **搬运类脚本的两个新坑**已记在台账 §四十一.六之外，本脚本文档头也留了一份：Edit 覆盖标题会静默改变归属、'
         '`index(sep)` 在有分隔线的 markdown 里必然命中第一条。',
         '']

out = (lines[:i_sec] + [H15] + openlist + [SEC] + body + delta + lines[i_ftr:])
assert out.count(SEC) == 1 and out.count(H15) == 1 and out.count(H16) == 0, '标题数量不对'
assert len(out) == len(lines) + len(delta), (len(lines), len(out), len(delta))
assert '\n'.join(out).count(chr(13)) == 0, 'CR 混入'
io.open(P, 'w', encoding='utf-8', newline='').write('\n'.join(out))
print('OK sec@', i_sec + 1, 'h16@', i_h16 + 1, 'ftr@', i_ftr + 1,
      'body', len(body), 'openlist', len(openlist), 'delta', len(delta),
      'total', len(lines), '->', len(out))
