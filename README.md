# CI/CD Demo (Python + GitHub Actions + Docker)

一个演示**完整 CI/CD 流程**的最小 Python 项目:应用逻辑本身极简(一个计算模块),重点在于外围的**自动化质量门禁、多环境流水线与容器化发布**。

- 架构与设计原理详解见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 分支模型与多环境流水线见 [docs/BRANCHING.md](docs/BRANCHING.md)
- 首次接入 GitHub 的操作步骤见 [docs/SETUP.md](docs/SETUP.md)

## 项目结构

```
.
├── .github/workflows/         # CI/CD 流水线定义
│   ├── _reusable-ci.yml       #   可复用:lint + test + security (+ 可选集成测试)
│   ├── _reusable-docker.yml   #   可复用:构建镜像 → 推 GHCR → Trivy 扫描
│   ├── pr.yml                 #   PR → dev/test/prod/main(只做检查)
│   ├── dev.yml                #   push dev  → CI + 构建 :dev  + 部署 development
│   ├── test.yml               #   push test → CI(含集成)+ 构建 :test + 部署 test
│   ├── prod.yml               #   push prod → CI + 构建 :prod + 审批部署 production
│   └── main.yml               #   push main / tag v* → CI / 发布版本镜像
├── src/app/                   # 应用代码
│   ├── calculator.py          #   业务逻辑(加/减/除)
│   └── main.py                #   入口 main()
├── tests/                     # 单元测试
│   ├── test_calculator.py
│   └── test_main.py
├── docs/                      # 架构 / 分支 / 接入文档
├── Dockerfile                 # 多阶段构建镜像(非 root 运行)
├── .dockerignore
└── pyproject.toml             # 依赖 + 工具配置(ruff/mypy/pytest)
```

## 本地开发

```bash
# 安装依赖(含开发工具)
pip install -e ".[dev]"

# 静态检查
ruff check .          # Lint
ruff format --check . # 格式校验
mypy src              # 类型检查

# 测试(带覆盖率,阈值 80%)
pytest

# 运行程序
python -m app.main

# 构建并运行镜像
docker build -t cicd-demo .
docker run --rm cicd-demo
```

## CI/CD 架构

检查逻辑采用**可复用工作流**(`on: workflow_call`)只写一份,各分支入口只负责"何时调、传什么参数":

- **可复用工作流**(不被事件直接触发,只被入口调用):
  - `_reusable-ci.yml` —— `lint` / `test` / `security`(+ 可选 `integration`)
  - `_reusable-docker.yml` —— 构建镜像 → 推送 GHCR → Trivy 扫描(标签与扫描策略参数化)
- **入口工作流**(由 push / PR / tag 事件触发):`pr` / `dev` / `test` / `prod` / `main`

### CI 检查阶段(`_reusable-ci.yml`)

| Job | 内容 |
|------|------|
| `lint` | Ruff lint + Ruff format 校验 + Mypy 类型检查 |
| `test` | 在 Python 3.11 / 3.12 矩阵下跑 pytest + 覆盖率(阈值 80%) |
| `security` | pip-audit(依赖漏洞)+ Trivy 文件系统扫描 |
| `integration` | 起 PostgreSQL 服务跑集成测试(仅当调用方传 `run-integration: true`) |

`lint`、`test`、`security` 三个 job 并行执行以加快反馈。

### 各入口触发矩阵

| 触发 | CI | 集成测试 | 构建镜像 | 镜像标签 | CVE 阻断 | 部署 |
|------|:--:|:-------:|:--------:|----------|:-------:|------|
| PR → dev/test/prod/main | ✅ | ❌ | ❌ | —— | —— | —— |
| push `dev` | ✅ | ❌ | ✅ | `dev`, `dev-<sha>` | 否 | development(自动) |
| push `test` | ✅ | ✅ | ✅ | `test`, `test-<sha>` | 否 | test(自动) |
| push `prod` | ✅ | ✅ | ✅ | `prod`, `prod-<sha>` | **是** | production(**人工审批**) |
| push `main` | ✅ | ❌ | ❌ | —— | —— | —— |
| tag `v*` | ✅ | ❌ | ✅ | `v1.2.3`, `latest` | 否 | (发布) |

## 建议的分支保护规则

在仓库 **Settings → Branches → Branch protection rules** 中,越靠近生产越严。以 `main` 为例:

- ✅ Require status checks:`Lint & Type Check`、`Test (...)`、`Security Scan`
- ✅ Require pull request review(至少 1 人)
- ✅ Require branches to be up to date before merging

> 完整的分支保护与 Environments 配置见 [docs/BRANCHING.md](docs/BRANCHING.md)。
