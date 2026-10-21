# Task Manager 示例仓库

这是 PatchForge 用来做“更真实一点”的评测目标仓库。它模拟了一个小型任务管理库，包含：

- 任务模型和优先级校验
- 任务过滤逻辑
- JSON 文件保存
- 简单配置文件解析

仓库中故意保留了几类 bug，方便 PatchForge 从 Issue 描述和测试失败中定位并生成 patch。推荐通过下面的命令验证：

```powershell
patchforge eval --dataset evals/task_manager_tasks.jsonl
```
