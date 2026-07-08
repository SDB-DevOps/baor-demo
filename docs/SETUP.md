# 操作步骤:从本地到 GitHub CI/CD 跑通

本文档说明如何把本项目推送到 GitHub,并让多环境 CI/CD 流水线自动运行。

- 分支模型与触发矩阵见 [BRANCHING.md](./BRANCHING.md)
- 架构与设计原理见 [ARCHITECTURE.md](./ARCHITECTURE.md)

## 前置条件

- 已安装 [Git](https://git-scm.com/)
- 拥有一个 GitHub 账号
- (可选)已安装 [GitHub CLI](https://cli.github.com/) `gh`
- 本地已能跑通检查(见 [README](../README.md) 的「本地开发」一节)

---

## 步骤 1:初始化 Git 仓库

在项目根目录执行:

```bash
cd /c/CICD_Demo_repo
git init
git add .
git commit -m "chore: bootstrap CI/CD pipeline"
```

## 步骤 2:创建远程仓库并推送

先在 GitHub 网页新建一个**空仓库**(不要勾选 README / .gitignore / license),然后:

```bash
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

> 使用 GitHub CLI 可一步到位:
>
> ```bash
> gh repo create <仓库名> --private --source=. --push
> ```

## 步骤 3:创建长期分支

本项目采用多分支晋升模型(`feature/* → dev → test → prod`,`main` 为稳定基线)。除 `main` 外,再建出各环境分支:

```bash
git switch -c dev  && git push -u origin dev
git switch -c test && git push -u origin test
git switch -c prod && git push -u origin prod
git switch main
```

> 分支与环境的对应关系、谁往里合,详见 [BRANCHING.md](./BRANCHING.md)。

## 步骤 4:查看流水线自动运行

不同事件触发不同的入口工作流(而非单一的 CI):

| 事件 | 触发的工作流 | 运行内容 |
|------|--------------|----------|
| push `main` | **Main / Release** (`main.yml`) | 只跑 CI 保持基线常绿 |
| push `dev` | **Dev Pipeline** (`dev.yml`) | CI → 构建 `:dev` → 部署 development |
| push `test` | **Test Pipeline** (`test.yml`) | CI(含集成)→ 构建 `:test` → 部署 test |
| push `prod` | **Prod Pipeline** (`prod.yml`) | CI(含集成)→ 构建 `:prod`(CVE 阻断)→ **审批**部署 |
| PR → dev/test/prod/main | **PR Checks** (`pr.yml`) | 只跑 CI(门禁) |
| tag `v*` | **Main / Release** (`main.yml`) | CI → 构建 `:v1.2.3` + `:latest` |

查看方式:

1. 打开仓库页面 → 顶部 **Actions** 标签
2. 按上表找到对应的工作流运行。CI 部分(来自可复用工作流 `_reusable-ci.yml`)包含并行的:

   | Job | 说明 |
   |-----|------|
   | `Lint & Type Check` | Ruff lint + format 校验 + Mypy 类型检查 |
   | `Test (Python 3.11 / 3.12)` | 矩阵下跑 pytest + 覆盖率(阈值 80%) |
   | `Security Scan` | pip-audit(依赖漏洞)+ Trivy(文件系统扫描) |
   | `Integration Tests` | 仅 test/prod:起 PostgreSQL 跑集成测试 |

   镜像构建/推送/扫描来自可复用工作流 `_reusable-docker.yml`(仅在 push dev/test/prod 或 tag v* 时运行,PR 上不跑)。

3. 点进任一 job 查看日志,全绿即通过 ✅

## 步骤 5:配置 Environments(部署与审批)

**Settings → Environments** 建 3 个环境:

| 环境 | 保护规则 |
|------|----------|
| `development` | 无 |
| `test` | 可选:限定 `test` 分支 |
| `production` | ✅ **Required reviewers**(人工审批)+ 限定 `prod` 分支 |

`prod.yml` 的 `deploy` job 指定了 `environment: production`,配置 Required reviewers 后会**暂停等待审批**才部署。

## 步骤 6:配置分支保护(门禁)

**Settings → Branches → Add branch protection rule**,越靠近生产越严(完整表见 [BRANCHING.md](./BRANCHING.md))。以 `main` 为例:

- Branch name pattern:`main`
- ✅ Require a pull request before merging(至少 1 个 Review)
- ✅ Require status checks to pass before merging → 勾选 `Lint & Type Check`、`Test (...)`、`Security Scan`
- ✅ Require branches to be up to date before merging

> status check 的名称来自 `_reusable-ci.yml` 里的 job 名,**首次运行过一次后**才会出现在下拉列表中。

## 步骤 7:验证完整 PR 流程(可选)

模拟一次真实改动:

```bash
git switch dev
git switch -c feature/test-ci
# 修改任意代码……
git add .
git commit -m "test: trigger CI"
git push -u origin feature/test-ci
```

在 GitHub 上对该分支发起 **Pull Request → `dev`**,可观察到:

- **PR Checks**(`pr.yml`)在 PR 页面自动运行(只做检查,不构建镜像)
- 全绿 + Review 通过后,Merge 按钮才可用
- 合并进 `dev` 后,**Dev Pipeline**(`dev.yml`)自动构建 `:dev` 镜像并部署

---

## 关于 Docker 镜像

- 镜像构建由 `_reusable-docker.yml` 完成,仅在 push `dev`/`test`/`prod` 或 tag `v*` 时触发,PR 上不构建。
- 镜像推送到 `ghcr.io/<用户名>/<仓库名>`,标签按环境区分(`:dev` / `:test` / `:prod`,发布时 `:v1.2.3` + `:latest`)。
- 使用内置的 `GITHUB_TOKEN` 登录 GHCR,**无需额外配置密钥**。
- 首次推送后,可在仓库 **Packages** 页面调整镜像可见性(public / private)。

## 常见问题

| 现象 | 排查方向 |
|------|----------|
| Actions 没触发 | 确认推送到了 `dev`/`test`/`prod`/`main`,或发起了 PR;检查 `.github/workflows/` 下对应工作流是否已提交 |
| 看不到镜像构建 | 正常行为 —— 镜像只在 push 环境分支 / tag 时构建,PR 与 push `main`(非 tag)不构建 |
| 生产部署一直卡住 | 正常行为 —— `production` 环境配置了 Required reviewers,`deploy` job 在等人工审批 |
| 推送镜像 403 | 确认 `_reusable-docker.yml` 里 `permissions: packages: write` 存在(已默认配置) |
| prod 因 CVE 失败 | `prod.yml` 传了 `scan-exit-code: "1"` 阻断高危漏洞;修复依赖/基础镜像后重跑 |
| 覆盖率不达标失败 | 阈值 80%,在 `pyproject.toml` 的 `--cov-fail-under` 调整,或补测试 |
