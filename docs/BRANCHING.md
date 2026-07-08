# 多分支 CI/CD 设计(模型 B:prod 与 main 分离)

## 分支模型与晋升流程

```
feature/* ──PR──> dev ──PR──> test ──PR──> prod
   开发            开发环境      测试环境      生产环境

main = 受保护的稳定基线,发布 tag(v*)的来源
```

| 分支 | 环境 | 谁往里合 | 说明 |
|------|------|----------|------|
| `feature/*` | 无 | 开发者自建 | 开发功能,PR 合入 `dev` |
| `dev` | development | feature PR | 开发集成环境 |
| `test` | test | dev PR 晋升 | 测试/预发,跑集成测试 |
| `prod` | production | test PR 晋升 | 生产环境,部署需人工审批 |
| `main` | —— | 定期同步 | 稳定基线;打 `v*` tag 触发发布 |

## 工作流文件

采用**可复用工作流(reusable workflow)**,检查逻辑只写一份,各分支入口只负责调用与传参。

| 文件 | 触发 | 作用 |
|------|------|------|
| `_reusable-ci.yml` | 被调用 | lint + test + security(+ 可选集成测试) |
| `_reusable-docker.yml` | 被调用 | 构建镜像 → 推 GHCR → Trivy 扫描(标签参数化,输出 digest) |
| `_reusable-bump.yml` | 被调用 | 把镜像 digest 回写 GitOps 配置仓库(Argo CD 随后同步 = 部署) |
| `pr.yml` | PR → 任意长期分支 | 只跑 CI(门禁) |
| `dev.yml` | push `dev` | CI → 构建 `:dev` → 回写 dev overlay |
| `test.yml` | push `test` | CI(含集成) → 构建 `:test` → 回写 test overlay |
| `prod.yml` | push `prod` | CI → 构建 `:prod`(CVE 阻断) → **审批** → 回写 prod overlay |
| `main.yml` | push `main` / tag `v*` | main:跑 CI;tag:构建 `:v1.2.3` + `:latest` |

> 部署走 **Argo CD 拉取式 GitOps**:回写配置仓库后由集群内 Argo CD 自动同步,详见 [CD.md](./CD.md)。

## 触发矩阵

| 触发 | CI 检查 | 集成测试 | 构建镜像 | 镜像标签 | 部署(Argo CD) |
|------|:------:|:--------:|:--------:|----------|------|
| PR → dev/test/prod/main | ✅ | ❌ | ❌ | —— | —— |
| push `dev` | ✅ | ❌ | ✅ | `dev`, `dev-<sha>` | 回写 → dev 自动同步 |
| push `test` | ✅ | ✅ | ✅ | `test`, `test-<sha>` | 回写 → test 自动同步 |
| push `prod` | ✅ | ✅ | ✅ | `prod`, `prod-<sha>` | **审批** → 回写 → prod 同步 |
| push `main` | ✅ | ❌ | ❌ | —— | —— |
| tag `v*` | ✅ | ❌ | ✅ | `v1.2.3`, `latest` | (发布) |

## 需要在 GitHub 上做的配置

### 1. Environments(Settings → Environments)

建 3 个环境,给它们各自的 secrets/变量:

| 环境 | 保护规则 |
|------|----------|
| `development` | 无 |
| `test` | 可选:限定 `test` 分支 |
| `production` | ✅ **Required reviewers**(人工审批)+ 限定 `prod` 分支 |

`prod.yml` 的 `deploy` job 指定了 `environment: production`,因此会**暂停等待审批**后才部署。

### 2. Branch protection(Settings → Branches)

越靠近生产越严:

| 分支 | 要求 PR | 必过 checks | Review |
|------|:-------:|-------------|:------:|
| `dev` | 建议 | lint, test, security | 1 |
| `test` | ✅ | lint, test, security | 1 |
| `prod` | ✅ | lint, test, security | 2 + Code Owner |
| `main` | ✅ | lint, test, security | 2 |

> 注:必过的 status check 名称来自 `_reusable-ci.yml` 里的 job(`Lint & Type Check`、`Test (...)`、`Security Scan`)。首次运行后可在分支保护的下拉里选到它们。

## 日常使用流程

```bash
# 1. 开发
git checkout -b feature/xxx dev
# ...改代码, commit...
git push -u origin feature/xxx
# 在 GitHub 发 PR: feature/xxx -> dev  (pr.yml 跑 CI)

# 2. 晋升到测试
# PR: dev -> test   (合并后 test.yml 自动构建 :test 并部署 test 环境)

# 3. 晋升到生产
# PR: test -> prod  (合并后 prod.yml 构建 :prod,deploy 暂停等审批)

# 4. 发布版本(基于 main)
git tag v1.2.3 && git push origin v1.2.3   # main.yml 构建 :v1.2.3 + :latest
```
