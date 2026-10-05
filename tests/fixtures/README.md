# 测试夹具

## 从 dialux-compiler 搬来的真实样例（P1 搬运附带的原件）

- `sample_room.dxf` / `sample_lighting.dxf` / `sample_parse_config.json`
  —— `test_parser_dxf` / `test_chain_geometry` / `test_validator` 用。
- `mini_ir_2rooms.json` —— `test_exporter_stf` 用。

解析器测试以 fixtures 下的真实文件为准；当前 pipeline 测试使用内存构造的 DXF。

## STF 冻结基准（判卷标准，BLOCKED.md B-1 的产物）

| 文件 | 说明 |
|---|---|
| `mvp3_ir.json` | 真实 IR（1 房间 + 22 家具伪 space + 28 盏灯），源仓 `build/mvp3_ir.json` 原件 |
| `mvp3_lums_baseline.stf` | **冻结基准**：源仓导出器在该 IR 上的输出，逐字节固化 |

### 这份基准的来历

任务书原本指定的「黄金文件」`D:\dev\dialux-compiler\build\mvp3_lums.stf` 是 **MVP3 之前的过期产物**：
灯具段是旧的一行占位格式 `LumN=x y z symbol`（Z 写死 0），而现版导出器已改成三行格式并写真实挂高。
源仓自己也生不出那份文件——所以它不能当判卷标准，已降级为历史档（BLOCKED.md B-1）。

判卷标准改为「迁移等价性」，参照物必须落在本仓，否则会随源仓漂移而失去意义。本基准产出时的源仓出处：

```
HEAD            : 2ee320eeacdec5904e333fdda20bc41863fc83ec
stf.py blob hash: 9857ee16fba786974fb7b2f5a8a43c4ac269574e
stf.py 最后改动 : e06d9dc feat(MVP3): 放灯链路产品化 + 执行器内核 + exe 交付
生成参数        : project_name=mvp3_lums  date=2026-09-13
生成命令        : scripts/run_source_exporter.py（源仓 src/exporter/stf.py，只读）
```

### 谁来守护它

`tests/test_stf_golden_baseline.py`：搬过来的导出器必须**逐字节**复现这份基准，
并自带两条反向验证（扰动房间坐标 / 扰动灯具挂高都必须被发现）。
跑测试时**不读冻结仓**——基准是自包含的。

### 什么时候可以换基准

只有在**刻意**变更 STF 格式时，且变更必须重新做真机导入验证。换基准的命令：

```
python scripts/freeze_stf_baseline.py            # dry-run，只核对
python scripts/freeze_stf_baseline.py --write    # 真的重写基准（刻意动作）
```

（2026-10-05 起：dry-run 的临时文件落系统临时目录，不再写本目录，只读检出也可安全核对。）
换完必须把上面「源仓出处」的 hash 同步更新（脚本会打印新的）。
