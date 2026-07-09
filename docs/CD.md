# CD 流程:Argo CD + GitOps(拉取式)

本文档描述 `baor-demo` 的 **CD(持续部署)**部分:如何用 **Argo CD** 以 **GitOps 拉取式**模型,把 CI 产出的镜像自动、可审计地部署到 dev / test / prod 环境。

- CI 部分见 [README.md](../README.md) 与 [ARCHITECTURE.md](./ARCHITECTURE.md)
- 分支模型见 [BRANCHING.md](./BRANCHING.md)
- **从零跑通的实操记录 + 踩坑排错见 [CD-RUNBOOK.md](./CD-RUNBOOK.md)**
- K8s 清单在**独立的配置仓库** `baor-demo-config`(与本仓库分离),见其 [`README.md`](../../baor-demo-config/README.md)

---

## 1. 核心理念:Push 式 vs Pull 式

| | 传统 Push 式部署 | **GitOps 拉取式(本方案)** |
|---|---|---|
| 谁发起 | CI 从外部 `kubectl apply` 推给集群 | 集群内的 **Argo CD** 主动拉取并同步 |
| 集群凭证 | 需暴露给 CI(攻击面大) | 不出集群(CI 无需集群凭证) |
| 真相来源 | 命令式、易漂移 | **Git = 唯一真相**,声明式 |
| 部署记录 | 散落在 CI 日志 | 每次部署 = 一次 Git commit,可审计、可回滚 |
| 漂移修正 | 无 | Argo CD `selfHeal` 自动纠正手改 |

> 一句话:**CI 不再"部署",只把新镜像 digest 写回 Git;Argo CD 看到 Git 变了,就把集群同步成 Git 声明的样子。**

---

## 2. 端到端数据流

```
 应用代码仓库 (baor-demo)                 配置仓库 (baor-demo-config)        集群
 ────────────────────────                ──────────────────────────       ──────────
 push dev/test/prod
      │
      ▼
   CI: lint/test/security
      │
      ▼
   构建镜像 → 推送 GHCR ──(digest)──┐
      │                            │
      ▼                            ▼
   _reusable-bump.yml:      overlays/<env>/kustomization.yaml
   kustomize edit set image ───► git commit + push ──┐
                                                      │  Argo CD 监听
                                                      ▼
                                              检测到 Git 变更 → Sync ──► kubectl apply
                                                                          到 baor-demo-<env>
```

- 镜像用**不可变 digest**(`@sha256:...`)固定,而非会漂移的 `:dev` 标签 —— Argo CD 才能可靠检测变更、精确回滚。
- prod 在"构建后、回写前"插入 **GitHub Environment 人工审批**(见 §5)。

---

## 3. App-of-Apps 结构

```
                       ┌─────────────────────────────┐
   kubectl apply ────► │ cicd-demo-root (Application) │  监听 config 仓库 apps/
   (一次性 bootstrap)  └──────────────┬──────────────┘
                                      │ 声明式派生
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
     cicd-demo-dev          cicd-demo-test          cicd-demo-prod
     overlays/dev           overlays/test           overlays/prod
     ns: baor-demo-dev      ns: baor-demo-test      ns: baor-demo-prod
     auto-sync              auto-sync               auto-sync(审批在 CI 侧)
```

新增一个环境 = 在 `apps/` 提交一个新的 `Application` YAML,root app 自动纳管,无需手工 `kubectl`。

---

## 4. 前置配置(一次性)

### 4.1 GitHub 侧(应用代码仓库)

| 类型 | 名称 | 值 / 说明 |
|------|------|-----------|
| Variable | `CONFIG_REPO` | GitOps 配置仓库,如 `my-org/baor-demo-config` |
| Secret | `CONFIG_REPO_TOKEN` | 对配置仓库有 **写**权限的 PAT / fine-grained token(内置 `GITHUB_TOKEN` 无法跨仓库推送) |
| Environment | `production` | 配置 **Required reviewers**,使 prod 部署暂停等审批 |

> 未设置 `CONFIG_REPO` 时,`_reusable-bump.yml` 会打印 warning 并**跳过**(不失败),方便先跑通 CI 再逐步接入 CD。

### 4.2 配置仓库

K8s 清单在**独立仓库** [`baor-demo-config`](../../baor-demo-config/README.md)(已从本仓库分离)。
把它推到 GitHub 后,将其 `apps/*.yaml` 里的
`repoURL: https://github.com/OWNER/baor-demo-config.git` 改成实际地址即可。

### 4.3 集群侧(Argo CD)

```bash
# 1. 安装 Argo CD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# 2. 给 Argo CD 读配置仓库的权限(私有仓库需 deploy key / repo credentials)
#    argocd repo add https://github.com/my-org/baor-demo-config.git --ssh-private-key-path <key>

# 3. Bootstrap App-of-Apps 根 —— 之后一切靠 Git
kubectl apply -n argocd -f apps/root-app.yaml
```

---

## 5. 生产晋升与审批(两种模式)

**模式 A(本方案默认):CI 侧审批 + Argo 自动同步。**
`prod.yml` 里 `approval` job 携带 `environment: production`,构建完成后**暂停等待人工审批**;审批通过才回写 prod overlay,Argo CD 生产 app 随即自动同步。审批只发生一次,流程顺滑。

**模式 B(更严格):Argo 手动同步 + 配置仓库 PR。**
把配置仓库 [`apps/prod.yaml`](../../baor-demo-config/apps/prod.yaml) 的 `syncPolicy.automated` 去掉,改为手动 Sync;prod 晋升改为向配置仓库发 PR,评审合并后在 Argo CD UI 点击 Sync。适合合规要求高、需要"部署即评审"的团队。

---

## 6. 回滚

GitOps 的回滚就是**让 Git 回到上一个好状态**:

- **Git 回滚**:`git revert` 配置仓库里那次 digest 变更并 push → Argo CD 自动同步回旧镜像。
- **Argo CD History**:`argocd app rollback cicd-demo-prod <历史版本号>`,或在 UI 的 History 里选择上一个 Synced 版本。

因为镜像是 digest 固定的,回滚精确到具体镜像内容,不会有标签漂移的歧义。

---

## 7. 工作负载说明:为什么是 CronJob

本 demo 应用是**批处理型**(`python -m app.main` 打印后即退出)。用 Deployment 会
`CrashLoopBackOff`,因此配置仓库的 [`manifests/base/cronjob.yaml`](../../baor-demo-config/manifests/base/cronjob.yaml) 用
**CronJob** 定时运行,Argo CD 健康状态正常。

若要部署**常驻服务**,把 `base/cronjob.yaml` 换成 `Deployment` + `Service` 即可,overlay
的 `namespace` / `images` / 镜像回写流程完全不变。

---

## 8. 触发矩阵(含 CD)

| 触发 | 构建镜像 | 回写 overlay | Argo CD 同步 | 审批 |
|------|:--------:|--------------|--------------|------|
| PR → dev/test/prod/main | ❌ | —— | —— | —— |
| push `dev` | ✅ | `overlays/dev`(自动) | dev app 自动 | 否 |
| push `test` | ✅ | `overlays/test`(自动) | test app 自动 | 否 |
| push `prod` | ✅ | `overlays/prod`(**审批后**) | prod app 自动 | **是** |
| push `main` | ❌ | —— | —— | —— |
| tag `v*` | ✅(`:v* + :latest`) | ——(仅发布镜像,生产晋升仍走 prod 分支) | —— | —— |
