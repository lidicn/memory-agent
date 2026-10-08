# vMA-1.3 MiMo 旧方案（已废弃）

## 废弃原因
DCD 裁定书：20260928-MA多模态统一-打回.md
- 方向错误：建新表+双写是重复建设（perception_events 已是统一感知总线）
- 正确方案：VIEW 方案（零双写、零存储、永远实时）
- 流程错误：绕过 PoC 门直接写代码

## 归档内容
- MiMo完整输出.md：MiMo 50,862 字符设计稿
- unified_store_旧方案.py：~510 行建表+双写代码（已 revert）
- test_unified_store_旧方案.py：29/32 通过的测试

## 复用价值
- query_events() 参数设计（person/room/start/end）可参考
- 时间戳归一化逻辑可参考
- 枚举校验逻辑可参考
