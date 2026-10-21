# PatchForge 稳定真实用例回归汇总 v2

## 当前结论

截至 2026-07-22，PatchForge 已在 pytest 历史真实缺陷上完成 5 个稳定成功用例。成功标准是：Patch 通过预检并应用、目标测试通过、验证测试通过、最终 Diff 只包含允许修改范围。

| 用例 | 最终修改文件 | 结果 | 迭代 | 耗时 |
| --- | --- | --- | ---: | ---: |
| pytest#7432 | `src/_pytest/skipping.py` | 成功 | 1 | 213.49 秒 |
| pytest#7569 | `src/_pytest/logging.py` | 成功 | 1 | 359.07 秒 |
| pytest#10081 | `src/_pytest/unittest.py` | 成功 | 1 | 135.37 秒 |
| pytest#10356 | `src/_pytest/mark/structures.py` | 成功 | 4 | 403.28 秒 |
| pytest#6197 | `src/_pytest/python.py` | 成功 | 2 | 1132.84 秒 |

## 汇总指标

- Task Success Rate：5/5，100%
- Patch Apply Rate：5/5，100%
- Test Pass Rate：5/5，100%
- Average Iterations：1.8
- Average Runtime：448.81 秒
- 目标文件越界修改：0 个最终成功用例
- 全量工程回归：143 passed，1 条弃用警告

## pytest#6197 过程记录

1. 首轮 Patch 方向正确，但 `tox.ini` 声明的 `testing` extra 没有被安装，`xmlschema` 和 `hypothesis` 缺失。
2. 环境管理器增加了对 tox `extras` 的识别，并安装 `.[testing]`。
3. Windows/Docker 命令规范化暴露了 extra 命令引号问题，已修正。
4. 裸 `pytest` 被收敛到 `testing/test_collection.py`，避免无界运行整个历史测试集。
5. 第一版 Patch 通过新回归测试但破坏既有显式包文件收集测试；Agent 第二轮保留了显式路径场景的兼容行为。
6. 最终主测试通过 `70 passed, 1 xfailed`，验证 Patch 中的两个目标测试也通过。

## 相关完整报告

- `pytest-dev__pytest-7432-optimized-regression-v1`
- `pytest-dev__pytest-7571-optimized-regression-v3`
- `pytest-dev__pytest-10081-optimized-regression-v1`
- `pytest-dev__pytest-10356-optimized-regression-v5`
- `pytest-dev__pytest-6197`

## 评测边界

这些结果证明 PatchForge 在当前验收标准下能够完成真实仓库缺陷修复闭环，不等同于上游维护者已经接受或合并 Patch。下一阶段应继续增加不同仓库和不同失败模式的用例，并把总耗时拆分为环境准备、模型调用和测试执行三类指标。
