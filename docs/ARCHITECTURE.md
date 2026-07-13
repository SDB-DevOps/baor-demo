# 项目架构与 CI/CD 流程说明

本文档描述 `baor-demo`(CI/CD Demo)工程的**完整架构、流水线流程与设计原理**。

- 应用侧文档见 [README.md](../README.md)
- 分支模型与多环境流水线见 [BRANCHING.md](./BRANCHING.md)
- CD 流程(Argo CD + GitOps)见 [CD.md](./CD.md)
- 首次接入 GitHub 的操作步骤见 [SETUP.md](./SETUP.md)

---

## 1. 项目概览

一个演示**完整 CI/CD 流程**的最小 Python 项目:应用逻辑本身极简(一个计算模块),重点在于外围的**自动化质量门禁、多环境流水线与容器化发布**。

| 维度 | 选型 |
|------|------|
| 语言 | Python 3.11 / 3.12 |
| 包管理 / 构建 | `pyproject.toml` + setuptools |
| 代码质量 | Ruff(lint + format)、Mypy(类型) |
| 测试 | pytest + coverage(阈值 80%) |
| 安全 | pip-audit(依赖)、Trivy(文件系统 + 镜像) |
| 容器 | Docker 多阶段构建,非 root 运行 |
| CI 平台 | GitHub Actions(可复用工作流) |
| 镜像仓库 | GitHub Container Registry(GHCR) |
| CD / 部署 | Argo CD(GitOps 拉取式)+ Kustomize |

---

## 2. 目录结构

```
CICD_Demo_repo/
├── .github/workflows/          # CI/CD 流水线定义
│   ├── _reusable-ci.yml        #   可复用:lint + test + security (+ 集成测试)
│   ├── _reusable-docker.yml    #   可复用:构建镜像 → 推 GHCR → Trivy 扫描
│   ├── _reusable-bump.yml      #   可复用:把镜像 digest 回写 GitOps 配置仓库
│   ├── pr.yml                  #   入口:PR → 任意长期分支(只做检查)
│   ├── dev.yml                 #   入口:push dev  → CI + 构建 :dev  + 部署
│   ├── test.yml                #   入口:push test → CI(含集成)+ 构建 :test + 部署
│   ├── prod.yml                #   入口:push prod → CI + 构建 :prod + 审批部署
│   └── main.yml                #   入口:push main / tag v* → CI / 发布版本镜像
├── src/app/                    # 应用源码
│   ├── __init__.py
│   ├── calculator.py           #   业务逻辑(加/减/除)
│   ├── server.py               #   常驻 HTTP 服务(容器入口,蓝绿/金丝雀用)
│   └── main.py                 #   批处理示例(保留作测试)
├── tests/                      # 单元测试
│   ├── test_calculator.py
│   └── test_main.py
├── docs/                       # 文档
│   ├── ARCHITECTURE.md         #   本文件
│   ├── BRANCHING.md            #   分支模型与多环境流水线
│   ├── CD.md                   #   CD 流程(Argo CD + GitOps)
│   └── SETUP.md                #   接入 GitHub 的操作步骤
├── Dockerfile                  # 多阶段镜像构建
├── .dockerignore
├── .gitignore
├── pyproject.toml              # 依赖 + 工具配置(ruff/mypy/pytest)
└── README.md
```

> **CI / CD 仓库分离**:上面是**应用代码仓库**(CI 侧)。CD 的 K8s 清单(Kustomize base + overlays + Argo CD Application)放在**独立的配置仓库 `baor-demo-config`**,由 Argo CD 监听。CI 只在构建镜像后把 digest 回写该配置仓库,详见 [CD.md](./CD.md)。

---

## 3. 应用架构

结构极简,分层清晰:

```
server.py ──调用──> calculator.py <──调用── main.py
 (HTTP 服务)         (纯函数业务逻辑)      (批处理示例)
```

- [calculator.py](../src/app/calculator.py):纯函数 `add / subtract / divide`,无副作用、易测试。
- [server.py](../src/app/server.py):**常驻 HTTP 服务**(标准库 `http.server`,零依赖),监听 `:8000`,提供 `/healthz` 探针与 `/add`、`/subtract`、`/divide` 端点。**容器入口** `python -m app.server`。渐进式发布(蓝绿/金丝雀)需要有流量可切,故用常驻服务而非批处理。
- [main.py](../src/app/main.py):早期的批处理入口(跑完即退),保留作示例与测试;不再是容器入口。

---

## 4. 容器化架构(Dockerfile)

采用**多阶段构建(multi-stage build)**,把"构建环境"与"运行环境"分离:

```
┌─────────────── Build stage (builder) ───────────────┐
│ FROM python:3.11-slim                                │
│ 拷贝 pyproject.toml + src → pip install 安装依赖      │
└──────────────────────────┬───────────────────────────┘
                           │ 只拷贝装好的产物
                           ▼
┌─────────────── Runtime stage (runtime) ─────────────┐
│ FROM python:3.11-slim(全新干净基础)                 │
│ 创建非 root 用户 appuser                              │
│ COPY --from=builder  site-packages + src             │
│ 删除 pip/setuptools/wheel(消除 CVE、瘦身)            │
│ USER appuser                                          │
│ EXPOSE 8000 / CMD ["python","-m","app.server"]       │
└──────────────────────────────────────────────────────┘
```

**设计原理:**

| 设计 | 目的 |
|------|------|
| 多阶段构建 | 最终镜像不含编译/构建中间产物,体积小、攻击面小 |
| 先拷 `pyproject.toml` 再拷源码 | 利用 Docker 层缓存,源码变动时不重装依赖 |
| 非 root 用户运行 | 容器被攻破时限制权限(安全最佳实践) |
| 删除 pip/setuptools/wheel | 运行时用不到这些构建工具,移除后消除其已知 CVE |
| `PYTHONUNBUFFERED=1` | 日志实时输出,不被缓冲 |

---

## 5. CI/CD 整体架构

### 5.1 分支模型(模型 B:prod 与 main 分离)

```
feature/* ──PR──> dev ──PR──> test ──PR──> prod
   开发            开发环境      测试环境      生产环境

main = 受保护的稳定基线,打 v* tag 触发版本发布
```

| 分支 | 环境 | 严格程度 |
|------|------|----------|
| `feature/*` | 无 | 轻量 |
| `dev` | development | 标准 CI |
| `test` | test | 标准 CI + 集成测试 |
| `prod` | production | 完整 CI + CVE 阻断 + **人工审批** |
| `main` | —— | CI;tag 触发发布 |

### 5.2 可复用工作流架构(避免重复)

核心思想:**检查逻辑只写一份,各分支入口只负责"何时调、传什么参数"。**

```
                 ┌──────────────────────┐
   pr.yml ──────>│  _reusable-ci.yml    │   lint / test / security
   dev.yml ─────>│  (workflow_call)     │   (+ 可选集成测试)
   test.yml ────>└──────────────────────┘
   prod.yml ───┐
   main.yml ───┤  ┌──────────────────────┐
   dev.yml ────┼─>│ _reusable-docker.yml │   build → push GHCR → Trivy scan
   test.yml ───┤  │  (workflow_call)     │   (镜像标签/扫描策略参数化,输出 digest)
   prod.yml ───┘  └──────────┬───────────┘
                             │ image digest
   dev.yml ────┐             ▼
   test.yml ───┼─> ┌──────────────────────┐
   prod.yml ───┘   │ _reusable-bump.yml   │   kustomize set image → 提交 config 仓库
                   │  (workflow_call)     │   (Argo CD 随后拉取同步 = 部署)
                   └──────────────────────┘
```

- **入口工作流**(`pr/dev/test/prod/main.yml`):由 push / PR / tag 事件触发,决定调用哪些可复用工作流、传什么参数(镜像标签、是否跑集成测试、扫描是否阻断)。
- **可复用工作流**(`_reusable-*.yml`):`on: workflow_call`,自身不会被事件触发,只被入口调用,承载真正的执行逻辑。

---

## 6. 流水线阶段详解

### 6.1 CI 检查(`_reusable-ci.yml`)

| Job | 步骤 | 失败即阻断 |
|-----|------|:----------:|
| `lint` | Ruff lint + Ruff format 校验 + Mypy 类型检查 | ✅ |
| `test` | Python 3.11 / 3.12 **矩阵**跑 pytest + 覆盖率(≥80%),上传报告 | ✅ |
| `security` | pip-audit(依赖漏洞)+ Trivy 文件系统扫描(`--ignore-unfixed`) | ✅ |
| `integration` | 起 PostgreSQL 服务跑集成测试(仅当 `run-integration: true`) | ✅ |

`lint / test / security` **并行执行**,尽早、快速地暴露问题。

### 6.2 镜像构建与发布(`_reusable-docker.yml`)

```
checkout → setup-buildx → 登录 GHCR → 生成标签(metadata-action)
       → build-push(带 GHA 缓存)→ 安装 Trivy → 扫描镜像
```

- 镜像名:`ghcr.io/<owner>/<repo>`(自动转小写)。
- 标签(参数化):`<env>`、`<env>-<shortsha>`、发布时额外 `latest`。
- 镜像扫描:`--ignore-unfixed`,`--exit-code` 由调用方决定(prod 用 `1` 阻断,其他默认 `0` 只报告)。

### 6.3 各入口触发矩阵

| 触发 | CI | 集成测试 | 构建镜像 | 镜像标签 | CVE 阻断 | 部署 |
|------|:--:|:-------:|:--------:|----------|:-------:|------|
| PR → 任意长期分支 | ✅ | ❌ | ❌ | —— | —— | —— |
| push `dev` | ✅ | ❌ | ✅ | `dev`, `dev-<sha>` | 否 | dev(自动) |
| push `test` | ✅ | ✅ | ✅ | `test`, `test-<sha>` | 否 | test(自动) |
| push `prod` | ✅ | ✅ | ✅ | `prod`, `prod-<sha>` | **是** | 生产(**审批**) |
| push `main` | ✅ | ❌ | ❌ | —— | —— | —— |
| tag `v*` | ✅ | ❌ | ✅ | `v1.2.3`, `latest` | 否 | (发布) |

---

## 7. 端到端流程(数据流)

```
开发者提交
   │
   ├─ 发 PR → 目标分支 ───────────> pr.yml ──> _reusable-ci ──> 结果回报 PR(门禁)
   │
   └─ PR 合并(push 到环境分支)
          │
          ├─ dev  ──> dev.yml  ──> CI ──> 构建 :dev  ──> 回写 config 仓库 ──┐
          ├─ test ──> test.yml ──> CI(含集成) ──> 构建 :test ──> 回写 ────┤
          └─ prod ──> prod.yml ──> CI ──> 构建 :prod ──┐                     │
                                                        │ CVE 通过            │
                                                        ▼                     │
                                             等待人工审批(production)         │
                                                        │ 批准                │
                                                        ▼                     │
                                                   回写 config 仓库 ──────────┤
                                                                              ▼
                                                              Argo CD 检测 Git 变更 → 自动同步
                                                              到 baor-demo-{dev,test,prod} 命名空间

  main 打 tag v* ──> main.yml ──> CI ──> 构建 :v1.2.3 + :latest(发布)
```

> CD 的完整原理、App-of-Apps 结构、审批与回滚见 [CD.md](./CD.md)。

---

## 8. 核心设计原理

### 8.1 CI 三原则
- **快**:静态检查在最前、job 并行、pip 缓存、`concurrency` 取消过时运行。
- **稳**:多 Python 版本矩阵、固定依赖、可复现构建。
- **门禁**:未通过检查不能合入;越靠近生产越严(prod 需 CVE 通过 + 人工审批)。

### 8.2 "PR 只检查,push 才构建"
PR 阶段只跑质量门禁(轻、快),避免每个 PR 都产镜像;只有合入环境分支后才构建对应镜像,减少资源浪费与镜像噪声。

### 8.3 环境逐级晋升
同一份代码从 dev → test → prod 逐级推进,每级对应独立环境、独立配置(GitHub Environments 管理各环境 secrets 与保护规则),生产部署强制人工审批。

### 8.4 安全扫描策略:`--ignore-unfixed`
只对**有可用补丁**的漏洞阻断。上游未发布补丁的 OS 级 CVE 无法修复,若硬卡会导致生产永远无法发布;有补丁却未打时才真正拦截,使门禁"可执行"。配合定期重建镜像,补丁一旦发布即被重新标记。

### 8.5 最小运行镜像
运行镜像不携带构建工具(pip/setuptools/wheel)、以非 root 用户运行,既减小体积也降低攻击面。

---

## 9. 需要在 GitHub 上完成的配置

| 配置 | 位置 | 时机 | 说明 |
|------|------|------|------|
| Environments | Settings → Environments | **运行前** | 建 `development`/`test`/`production`;给 `production` 配 Required reviewers(否则审批不生效) |
| Branch protection | Settings → Branches | **运行后** | 把 `Lint & Type Check`、`Test (3.11/3.12)`、`Security Scan` 设为必过 check(check 名需先跑过一次才出现在列表) |

详细配置表见 [BRANCHING.md](./BRANCHING.md)。

---

## 10. 可演进方向

- **部署实现**:已由 **Argo CD(GitOps 拉取式)**实现 —— CI 把镜像 digest 回写配置仓库,Argo CD 自动同步到集群(见 [CD.md](./CD.md))。可进一步接入 Argo Rollouts 做金丝雀/蓝绿发布。
- **更小攻击面**:基础镜像可换 `distroless`,进一步减少 OS 层 CVE。
- **本地防线**:接入 pre-commit 钩子在提交前自动跑 ruff format,减少因格式问题导致的 CI 失败。
- **发布增强**:tag 发布时自动生成 GitHub Release 与 changelog。
