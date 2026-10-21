# PatchForge 第一组稳定真实用例回归汇总

## 运行结论

本组包含 3 个 pytest 历史真实缺陷用例，均完成了：源码定位、Patch 生成、Patch 预检、Docker 测试和 validation 测试。

| 用例 | 修改文件 | 结果 | 迭代 | 总耗时 |
| --- | --- | --- | ---: | ---: |
| pytest#7432 | `src/_pytest/skipping.py` | 成功 | 1 | 213.49 秒 |
| pytest#7569 | `src/_pytest/logging.py` | 成功 | 1 | 359.07 秒 |
| pytest#10081 | `src/_pytest/unittest.py` | 成功 | 1 | 135.37 秒 |

## 汇总指标

- Task Success Rate：3/3，100%
- Patch Apply Rate：3/3，100%
- Test Pass Rate：3/3，100%
- Average Iterations：1.0
- Average Runtime：235.98 秒
- 目标修改范围：3 个用例均只修改预期源文件

## 评测说明

7571 和 10081 的评测数据包含 `validation_patch`，该补丁会删除原测试文件中的旧测试。Harness 已自动调整主测试命令并在报告中标记该情况，因此本汇总代表 PatchForge 当前验收标准下的成功，不等同于上游维护者已经接受该 Patch。

## 单项报告

- `pytest-dev__pytest-7432-optimized-regression-v1`
- `pytest-dev__pytest-7571-optimized-regression-v3`
- `pytest-dev__pytest-10081-optimized-regression-v1`

## 下一阶段

下一阶段运行高难度用例 `pytest#10356`，并将失败原因按仓库获取、源码定位、Patch 预检、测试回归、模型输出五类统计，形成第一版 Failure Pattern 基线。
