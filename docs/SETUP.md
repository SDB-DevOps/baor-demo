# 操作步骤:从本地到 GitHub CI 跑通

本文档说明如何把本项目推送到 GitHub 并让 CI 流水线自动运行。

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
git commit -m "chore: bootstrap CI pipeline"
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

## 步骤 3:查看 CI 自动运行

推送后 CI 会自动触发(`ci.yml` 配置了 `on: push: branches: [main]`):

1. 打开仓库页面 → 顶部 **Actions** 标签
2. 找到名为 **CI** 的运行,包含 4 个 job:

   | Job | 说明 |
   |-----|------|
   | `lint` | Ruff lint + format 校验 + Mypy 类型检查 |
   | `test` | Python 3.11 / 3.12 矩阵下跑 pytest + 覆盖率 |
   | `security` | pip-audit(依赖漏洞)+ Trivy(文件系统扫描) |
   | `docker` | 仅 `main` 分支:构建镜像 → 推送 GHCR → 扫描镜像 |

3. 点进任一 job 查看日志,全绿即通过 ✅

## 步骤 4:配置分支保护(门禁)

**Settings → Branches → Add branch protection rule**:

- Branch name pattern:`main`
- ✅ Require a pull request before merging(至少 1 个 Review)
- ✅ Require status checks to pass before merging → 勾选 `lint`、`test`、`security`
- ✅ Require branches to be up to date before merging

配置后,未通过 CI 的代码将无法合入 `main`。

## 步骤 5:验证完整 PR 流程(可选)

模拟一次真实改动:

```bash
git checkout -b feature/test-ci
# 修改任意代码……
git add .
git commit -m "test: trigger CI"
git push -u origin feature/test-ci
```

在 GitHub 上对该分支发起 **Pull Request**,可观察到:

- CI 在 PR 页面自动运行(不含 `docker`,镜像仅在合并到 `main` 后推送)
- 全绿 + Review 通过后,Merge 按钮才可用

---

## 关于 Docker 镜像

- `docker` job 仅在合并进 `main` 后触发,镜像推送到 `ghcr.io/<用户名>/<仓库名>`。
- 使用内置的 `GITHUB_TOKEN` 登录 GHCR,**无需额外配置密钥**。
- 首次推送后,可在仓库 **Packages** 页面调整镜像可见性(public / private)。

## 常见问题

| 现象 | 排查方向 |
|------|----------|
| Actions 没触发 | 确认推送到了 `main` 或 `develop` 分支;检查 `.github/workflows/ci.yml` 是否已提交 |
| `docker` job 被跳过 | 正常行为 —— 它只在 push 到 `main` 时运行,PR 上不跑 |
| 推送镜像 403 | 确认 workflow 里 `permissions: packages: write` 存在(已默认配置) |
| 覆盖率不达标失败 | 阈值 80%,在 `pyproject.toml` 的 `--cov-fail-under` 调整,或补测试 |
