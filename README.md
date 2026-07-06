# CI/CD Demo (Python + GitHub Actions + Docker)

一个演示**完整 CI 流程**的最小 Python 项目。

## 项目结构

```
.
├── .github/workflows/ci.yml   # CI 流水线定义
├── src/app/                   # 应用代码
│   ├── calculator.py
│   └── main.py
├── tests/                     # 单元测试
│   └── test_calculator.py
├── Dockerfile                 # 多阶段构建镜像
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

## CI 流水线阶段

流水线在 push / PR 到 `main`、`develop` 时触发:

| 阶段 | Job | 内容 |
|------|-----|------|
| 1. 静态检查 | `lint` | Ruff lint + format 校验 + Mypy 类型检查 |
| 2. 构建测试 | `test` | 在 Python 3.11 / 3.12 矩阵下跑 pytest + 覆盖率 |
| 3. 安全扫描 | `security` | pip-audit(依赖漏洞)+ Trivy(文件系统扫描) |
| 4. 镜像发布 | `docker` | 仅 `main` 合并后:构建镜像 → 推送 GHCR → 扫描镜像 |

`lint`、`test`、`security` 三个 job 并行执行以加快反馈;`docker` 依赖三者全部通过。

## 建议的分支保护规则

在仓库 **Settings → Branches → Branch protection rules** 中,对 `main`:

- ✅ Require status checks:`lint`、`test`、`security`
- ✅ Require pull request review(至少 1 人)
- ✅ Require branches to be up to date before merging
