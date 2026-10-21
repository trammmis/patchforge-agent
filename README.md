# PatchForge

PatchForge 是一个面向本地 Python 仓库的代码修复 Agent 原型。它的目标不是做一个简单的“代码聊天助手”，而是搭建一套可以被审计、复现、评测和逐步进化的 SWE Agent 工程闭环。

当前版本是一个可运行的 MVP：Agent 会读取 Issue 描述，探索目标仓库，生成最小 patch，运行测试，根据结果生成报告和 PR 草稿，并把每次运行的轨迹沉淀成后续可用的经验数据。

## 实现边界与评测证据

当前主运行链路由程序化 Orchestrator 编排；模型用于生成候选补丁，工具权限、补丁审批、回滚和最终验收由 Harness 控制。代码中的 LangGraph 预留入口不等于主链路已采用模型自主规划。

历史回归中，5 个 pytest 真实缺陷完成了目标测试、验证测试及修改范围验收，平均外层迭代 1.8 轮。结果仅覆盖现有选定样本，不代表任意 Python 仓库的修复成功率，也不代表上游已接受补丁。详见 [回归汇总](reports/stable_real_regression_summary_v2.md)。

源码中的 demo 和规则修复路径可离线运行；模型修复需要自行配置凭据。发布副本不包含真实凭据、目标仓库下载目录或历史运行日志。

## 当前已经实现了什么

- 本地代码修复闭环：`Issue -> 仓库探索 -> patch 生成 -> 测试执行 -> diff/report/PR 草稿`
- Execution Harness：每个任务会复制独立 workspace，不直接修改原始仓库
- 受控工具运行时：文件读取、代码搜索、patch 应用、测试运行、diff 获取都经过 Harness
- 纯 Python unified diff 应用与 baseline 对比：当前机器没有 Git 也能跑
- 安全边界：命令白名单、危险命令拦截、超时控制、可选 Docker sandbox
- Patch 审批 gate：修改依赖、Docker、CI、`.github` 或文件数超限时，Agent 会停在 `needs_approval`，生成审批请求；人工批准后可应用待审 diff 并继续运行测试
- JSONL 轨迹记录：每次工具调用、Agent 阶段、最终结果都会写入 `trace.jsonl`
- 标准化运行时间线：把 trace 转换成环境准备、Agent 阶段、工具调用、测试和结束状态，便于 UI/API 展示
- 完整验收报告：每个任务生成 Markdown 和 JSON 报告，包含测试对象、执行环境、阶段摘要、LLM 记录、patch 应用、测试执行、产物路径和最终 diff
- SQLite 任务中心：任务 payload、运行状态、结果 payload、报告路径和 trace 路径会持久化，服务重启后仍可查看历史记录
- Evaluation Harness：内置 20 条 demo bugfix case，可批量评测成功率
- Evolution Harness 初版：自动写入 reflection、skill、failure pattern 数据
- 中文 Streamlit UI：支持单任务运行、评测运行、历史记录查看和项目说明
- FastAPI 入口：支持后台任务启动、状态轮询、事件、timeline、diff、评测等 API
- GitHub Issue / 远程仓库导入：可从 Issue URL 解析所属仓库，并通过 GitHub archive 自动准备 workspace
- 结构化失败结果：仓库不存在、远程下载失败、配置缺失等 setup 阶段问题会返回 `failure_type` 和 `error_message`，不会直接抛出 traceback
- 任务级依赖准备：可选创建 `.patchforge_venv`，按 `requirements.txt`、`pyproject.toml`、`setup.py`、`setup.cfg` 准备 Python 环境，并把命令结果写入 trace / report / UI
- LLM patch 失败反馈：如果模型生成的 diff 因 context mismatch 无法应用，Harness 会把失败原因和最新文件上下文反馈给下一轮 LLM 重写 patch；如果模型返回空内容或非法 diff，会自动用 compact prompt 重试一次
- 聚焦上下文构建：Repo Explorer 会按搜索命中、实现文件、相关测试文件排序，减少无关测试污染 prompt
- 安全 fuzz patch：当 unified diff 的 hunk 行号轻微漂移、但上下文/删除行能唯一匹配当前文件时，Harness 可以安全应用；上下文不唯一时会拒绝
- Evolution 经验复用：新任务会用本地语义向量检索历史 reflection、skill、failure pattern，并注入 Planner 计划与最终报告

## 快速开始

建议先在项目根目录运行：

```powershell
cd patchforge-agent
pip install -e ".[dev]"
```

如果安装时遇到 pydantic 相关错误，可以执行：

```powershell
pip install --force-reinstall "pydantic>=2.12,<3"
pip install -e ".[dev]"
```

## 用命令行验证 Agent

运行单个 demo 修复任务：

```powershell
patchforge run `
  --task-id demo_discount `
  --repo examples/demo_python_repo `
  --issue-title "Fix zero price discount bug" `
  --issue-body "calculate_discount returns None when price is 0." `
  --test-command "python -m unittest tests.test_pricing"
```

也可以直接从 GitHub Issue URL 读取标题和正文，再对本地仓库副本执行修复：

```powershell
patchforge run `
  --task-id gh_issue_demo `
  --repo examples/task_manager_repo `
  --issue-url "https://github.com/owner/repo/issues/123" `
  --test-command "python -m unittest tests.test_config"
```

说明：

- `--repo` 仍然指向本地待修复仓库；如果省略 `--repo`，PatchForge 会尝试从 `--issue-url` 推导远程仓库并自动下载 workspace。
- `--issue-url` 会读取 GitHub Issue 的 title/body，并在报告和 PR 草稿中保留关联 Issue。
- 私有仓库或更高 API 限额可以设置 `PATCHFORGE_GITHUB_TOKEN` 或 `GITHUB_TOKEN`。
- PatchForge 仍然只生成本地 diff 和 PR 草稿，不会自动 push、创建 PR 或 merge。

如果你希望 PatchForge 根据 Issue URL 自动准备远程仓库 workspace，可以省略 `--repo`：

```powershell
patchforge run `
  --task-id gh_issue_remote `
  --issue-url "https://github.com/owner/repo/issues/123" `
  --test-command "pytest"
```

也可以显式传远程仓库 URL：

```powershell
patchforge run `
  --task-id remote_repo_demo `
  --repo-url "https://github.com/owner/repo" `
  --issue-title "Fix a bug" `
  --issue-body "Bug details..." `
  --test-command "pytest"
```

当前远程仓库准备策略：

- 本机没有 `git` 也能跑，PatchForge 会通过 GitHub zip archive 下载公开仓库。
- 自动下载暂只支持 `github.com/owner/repo` 形式的仓库 URL。
- 可用 `--base-commit` 指定分支名、tag 或 commit SHA；未指定时使用仓库默认分支。
- 下载后的仓库只存在于 `.patchforge_runs\<task_id>\repo`，不会修改远端。

成功时你会看到：

- `status` 为 `succeeded`
- `tests_passed` 为 `true`
- `final_diff` 中出现 `return None` 被改为 `return 0.0`
- 报告文件写入 `.patchforge_runs\demo_discount\reports\`
- 轨迹文件写入 `.patchforge_runs\demo_discount\trace.jsonl`

任务报告现在是固定的验收产物，建议每次真实仓库、Docker sandbox、GitHub Issue 或 SWE-bench 测试后都归档对应报告。报告中会包含：

- 测试对象：仓库 URL、本地仓库、base commit/ref、Issue、测试命令
- 执行环境：是否启用 Docker、是否允许网络、是否准备依赖、Python/venv 信息
- 执行过程：Agent 阶段、候选文件、LLM patch 尝试、patch 应用记录
- 测试结果：实际测试命令、状态、失败类型、失败测试、摘要
- 产物路径：Markdown 报告、JSON 结果、`trace.jsonl`

如果仓库路径不存在、GitHub 限流或远程仓库下载失败，你会看到结构化失败结果：

- `status` 为 `failed`
- `failure_type` 类似 `repo_not_found` 或 `repository_fetch_failed`
- `error_message` 给出可读失败原因
- 报告和 trace 仍会写入对应任务目录，便于排查

## 运行前准备 Python 依赖

默认情况下，PatchForge 不会安装目标仓库依赖，这样 demo 和 eval 会更快、更稳定。遇到真实 Python 仓库时，可以显式开启依赖准备：

```powershell
patchforge run `
  --task-id real_repo_with_deps `
  --repo-url "https://github.com/owner/repo" `
  --issue-title "Fix a failing test" `
  --issue-body "Bug details..." `
  --test-command "pytest" `
  --install-dependencies `
  --allow-network
```

当前策略：
- 开启 `--install-dependencies` 后，会在任务 workspace 里创建 `.patchforge_venv`，原始仓库不会被修改。
- 后续 `python`、`pytest`、`ruff`、`mypy` 会优先走这个任务级 venv。
- 如果存在 `requirements.txt`，只有同时开启 `--allow-network` 才会执行 `python -m pip install -r requirements.txt`。
- 如果存在 `pyproject.toml`、`setup.py` 或 `setup.cfg`，未允许网络时会尝试 `pip install -e . --no-deps --no-build-isolation`，允许网络时会执行普通 editable install。
- `.patchforge_venv` 会被文件树、搜索和最终 diff 忽略，不会污染补丁。
- Docker 任务默认启用依赖环境缓存：相同 Docker 镜像和依赖清单的后续任务会复用已准备好的 venv，跳过虚拟环境创建和依赖安装；可通过 `PATCHFORGE_ENV_CACHE_ENABLED=false` 关闭。
- 默认跳过 `pip upgrade`，并对无 `.git` 元数据的 GitHub archive 预配置版本号，减少网络等待和 editable install 重试；可通过 `PATCHFORGE_SKIP_PIP_UPGRADE=false` 或 `PATCHFORGE_PRECONFIGURE_SCM_VERSION=false` 恢复旧行为。
- 依赖准备结果会出现在任务报告的「环境准备」区、`trace.jsonl` 和 Streamlit 结果页。

运行项目自身测试：

```powershell
python -m unittest discover -s tests
```

运行 20 条 eval case：

```powershell
patchforge eval --dataset evals/tasks.jsonl
```

期望指标：

- `Task Success Rate = 1.0`
- `Patch Apply Rate = 1.0`
- `Test Pass Rate = 1.0`
- `Total Cases = 20`

评测报告会生成在：

```text
.patchforge_data\evaluation_report.md
```

## 更真实的任务管理示例仓库

除了最小 demo，项目现在还包含一个更接近真实 Python 库的小型目标仓库：

```text
examples\task_manager_repo
```

它包含任务模型、过滤逻辑、JSON 保存和配置解析四类代码，并故意保留了几类常见 bug：

- 优先级校验不接受 `HIGH` 这类大小写输入
- 默认过滤任务时错误地返回了已完成任务
- 保存到嵌套路径时没有创建父目录
- 配置解析遇到坏行时直接崩溃

可以单独运行其中一个修复任务：

```powershell
patchforge run `
  --task-id taskmgr_priority `
  --repo examples/task_manager_repo `
  --issue-title "Validate priority should be case-insensitive" `
  --issue-body "validate_priority rejects HIGH; it should normalize accepted priority values to lowercase and return high." `
  --test-command "python -m unittest tests.test_priority"
```

也可以运行专门的任务管理评测集：

```powershell
patchforge eval --dataset evals/task_manager_tasks.jsonl
```

真实开源项目回归集放在：

```powershell
patchforge eval --dataset evals/real_python_tasks.jsonl
```

首个真实用例是 SWE-bench Verified 的 `pytest-dev__pytest-7432`，固定到 `pytest-dev/pytest` 的 base commit `e6e300e729dd33956e5448d8be9a0b1540b4e53a`，并使用 `python:3.8-slim` Docker 镜像复现旧版 pytest 的运行环境。

也可以从 SWE-bench Verified 元数据导入新的真实 eval case：

```powershell
patchforge swebench-import `
  --instance-id pytest-dev__pytest-7432 `
  --output evals/real_python_tasks.jsonl `
  --test-command "python -m pytest testing/test_skipping.py" `
  --docker `
  --docker-image python:3.8-slim `
  --install-dependencies `
  --allow-network `
  --append
```

导入器会自动读取官方 `repo`、`base_commit`、`problem_statement` 和参考 patch 中的修改文件；测试命令和 Docker 镜像仍由 PatchForge 显式指定，避免真实旧项目因为 Python 版本不匹配而误判。

说明：`swebench-import` 读取 HuggingFace parquet 元数据，需要本机有 `pandas` 和 `pyarrow`；普通 `patchforge run/eval` 不依赖它们。

这套评测主要用于验证 PatchForge 能否在 toy demo 之外，完成更贴近日常业务代码的定位、patch 生成、测试执行和报告沉淀。

## 启用 DeepSeek LLM Patch 生成

默认模式是 `offline`，使用规则型 demo editor，适合稳定演示和跑测试。要启用 DeepSeek，请在项目根目录创建本地 `.env` 文件：

```dotenv
PATCHFORGE_LLM_PROVIDER=deepseek
PATCHFORGE_LLM_RULE_FALLBACK=true
DEEPSEEK_API_KEY=你的 DeepSeek API key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TEMPERATURE=0.1
DEEPSEEK_MAX_TOKENS=8192
```

注意：

- 不要把真实 key 写进代码、README 或 `.env.example`。
- `.env` 已在 `.gitignore` 中，不会被提交。
- `PATCHFORGE_LLM_RULE_FALLBACK=true` 表示 DeepSeek 调用失败、返回 diff 不合法或网络不可用时，会回退到离线规则 editor。
- DeepSeek 模式仍然由 Harness 负责 patch check、apply、测试和报告，模型不会直接修改原始仓库。
- 如果 DeepSeek 返回的 diff 无法应用，下一轮会携带 `PREVIOUS_PATCH_APPLY_FAILURES`，要求模型基于当前文件内容重新生成 diff。

## Evolution 经验检索策略

默认经验检索策略是：

```dotenv
PATCHFORGE_EVOLUTION_RETRIEVAL=semantic
```

它使用本地轻量向量索引，不依赖外部 embedding 服务。索引会综合任务 ID、修改文件路径、reflection、skill 触发条件、failure type 等文本，并做同义词扩展，例如：

- `settings / configuration / config`
- `bad / invalid / malformed`
- `done / completed / closed`
- `save / persist / storage`

如果想和旧版关键词检索对比，可以临时设置：

```powershell
$env:PATCHFORGE_EVOLUTION_RETRIEVAL="keyword"
```

## 启动中文 UI

启动 Streamlit：

```powershell
streamlit run frontend/streamlit_app.py
```

打开：

```text
http://127.0.0.1:8501
```

页面里可以做四件事：

1. **单任务修复**：输入 repo、Issue、测试命令，点击运行，查看状态、diff、PR 草稿和 trace。
2. **批量评测**：运行 `evals/tasks.jsonl`、`evals/task_manager_tasks.jsonl` 或 `evals/real_python_tasks.jsonl`，查看核心指标和结果表。
3. **运行记录**：浏览 `.patchforge_runs` 下的历史任务、报告和 trace。
4. **项目说明**：查看当前架构、经验复用、能力边界和后续路线。

单任务结果页里有 **经验复用** 标签页，会展示本次检索到的：

- Reflection：过去类似任务的结果总结
- Skill：已验证成功的修复步骤
- Failure Pattern：应避免的失败模式和恢复建议

## 启动 API

```powershell
uvicorn app.main:app --reload
```

主要接口：

- `POST /tasks`
- `GET /tasks`
- `POST /tasks/{task_id}/run`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/events`
- `GET /tasks/{task_id}/timeline`
- `GET /tasks/{task_id}/diff`
- `POST /tasks/{task_id}/approve-patch`
- `POST /tasks/{task_id}/approve-pr`
- `POST /evals/run`

`POST /tasks/{task_id}/run` 使用后台任务模式：接口会尽快返回 `queued` / `running` 状态，不再阻塞到修复结束。外部系统可以轮询：

- `GET /tasks`：查看最近任务列表，来自 SQLite 持久化任务中心。
- `GET /tasks/{task_id}`：查看任务生命周期、当前阶段、失败类型、报告路径和最终结果。
- `GET /tasks/{task_id}/events`：读取原始 JSONL trace 事件。
- `GET /tasks/{task_id}/timeline`：读取整理后的人类可读时间线，包括环境准备、Agent 阶段、工具调用、测试、结束状态。
- `GET /tasks/{task_id}/diff`：任务完成后读取最终 diff。
- `POST /tasks/{task_id}/approve-patch`：批准或拒绝 `needs_approval` 状态里的待审 patch；批准后会应用 diff 并继续跑测试。`approve-pr` 目前保留为兼容别名。

`POST /tasks` 支持传入 `issue_url`。提供后，API 会自动读取 GitHub Issue 的标题和正文：

```json
{
  "repo_path": "examples/task_manager_repo",
  "issue_url": "https://github.com/owner/repo/issues/123",
  "test_command": "python -m unittest tests.test_config"
}
```

如果不提供 `repo_path`，但 `issue_url` 指向 GitHub Issue，API 会使用 Issue 所属的 `repo_url` 自动下载远程仓库：

```json
{
  "issue_url": "https://github.com/owner/repo/issues/123",
  "test_command": "pytest"
}
```

## 目录结构

```text
app/
  agents/       Agent 决策层：Issue 解析、规划、patch 生成、审查、PR 草稿
  harness/      Harness 层：workspace、sandbox、工具运行时、评测、报告、进化资产
  tools/        受控工具：文件、搜索、patch、shell/test
  schemas/      任务、Issue、patch、test、eval、evolution 等结构化模型
  api/          FastAPI 路由
  graph/        LangGraph 兼容的状态机占位
  evolution/    reflection、skill、failure pattern 相关模块
frontend/
  streamlit_app.py
evals/
  tasks.jsonl                 20 条 demo eval case
  task_manager_tasks.jsonl    更真实的任务管理 eval case
  real_python_tasks.jsonl     真实开源项目/SWE-bench 风格回归 case
examples/
  demo_python_repo/
  task_manager_repo/
tests/
```

## 性能优化配置

Docker 任务建议在 `.env` 中启用以下配置：

```env
PATCHFORGE_ENV_CACHE_ENABLED=true
PATCHFORGE_ENV_CACHE_DIR=.patchforge_data/env_cache
PATCHFORGE_SKIP_PIP_UPGRADE=true
PATCHFORGE_PRECONFIGURE_SCM_VERSION=true
DEEPSEEK_TIMEOUT_SECONDS=90
DEEPSEEK_REQUEST_RETRIES=1
DEEPSEEK_RETRY_BACKOFF_SECONDS=0.5
PATCHFORGE_LLM_FAST_REPAIR=true
```

这些配置会复用相同依赖环境，避免重复升级 pip，并让后续修复轮次减少模型请求。环境缓存按依赖清单和 Docker 镜像计算，依赖文件或镜像变化后会自动生成新的缓存。

每次 LLM 请求的耗时和网络重试次数会写入 `trace.jsonl`，任务报告的“Patch 生成记录”中也会显示，便于定位模型、网络或测试本身的耗时。

## 当前设计边界

- `CodeEditor` 已支持 DeepSeek LLM-first 模式，但默认仍是离线规则模式，保证 demo/eval 稳定。
- DeepSeek 生成的 diff 会先经过 Harness check/apply/test；失败时可回退规则 editor。
- 高风险 patch 会在 check 阶段被拦截并返回 `needs_approval`；通过 API 或 Streamlit 批准后，会应用待审 diff、运行测试、重新生成报告和最终结果。
- GitHub Issue 读取已接入，支持从 `github.com/owner/repo/issues/123` 导入 title/body；远程 GitHub repo 可自动下载到 workspace，但不会 push 或创建真实 PR。
- API 已支持后台任务启动、状态轮询、原始事件和标准化 timeline，并把任务状态持久化到 SQLite，方便接入 UI、CI 或外部调度系统。
- PR 创建是审批流程占位，不会自动 push、merge 或改远程仓库。
- Docker sandbox 已有 runner；依赖准备第一版已支持任务级 venv，但默认仍使用本地快速模式，便于演示和测试。
- Evolution Harness 已能把历史经验通过本地语义向量检索注入 Planner，但还没有接入外部 embedding 模型或自动策略改写。

## 下一步路线

1. 将本地语义检索升级为可选外部 embedding 模型，并记录召回质量指标。
2. 继续打磨 Docker 镜像准备和依赖安装策略，补充更多真实 Python repo 的兼容性测试。
3. 完善 approval flow：保存更完整的审批审计记录，支持 PR 草稿发布审批。
4. 继续扩充 eval dataset：更多错误类型、更复杂仓库、更真实的测试失败反馈。
5. 继续强化 UI：自动轮询实时事件流、diff viewer、失败归因图表。
6. 将任务管理示例升级为多文件协同修复场景，验证跨模块定位和补测试能力。

## 发布验证

本次测试结果、跳过项与已知限制见 [发布验证记录](docs/RELEASE_CHECKS.md)。

## 公开展示安全边界

本仓库适用于项目学习与评测展示，尚未完成公网服务部署加固。启动方式、凭据要求与已知风险见 [安全说明](SECURITY.md)。Compose 默认仅监听本机回环地址。
