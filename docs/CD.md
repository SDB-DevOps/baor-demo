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

## 7. 渐进式发布:蓝绿部署(Argo Rollouts)

> 工作负载已从早期的批处理 CronJob 改为**常驻 HTTP 服务**(`python -m app.server`,监听 `:8000`,
> 带 `/healthz`)—— 蓝绿/金丝雀需要有流量可切,批处理没有流量。

蓝绿用 **Argo Rollouts** 实现:配置仓库里用 `Rollout` CRD 替换 Deployment,原生支持 blueGreen。

### 7.1 原理

新旧两版**全量并存**,先在 preview 入口验证新版(green),确认后**一次性把流量从蓝切到绿**,旧版保留一小段时间便于秒级回滚。

```
active Service  ──►  blue(旧版,v1)     ← 生产流量
preview Service ──►  green(新版,v2)    ← 验证入口
        │  promote(人工/自动)
        ▼
active Service  ──►  green(新版,v2)     blue 保留 60s 后缩容
```

### 7.2 关键对象与字段

- [`base/rollout.yaml`](../../baor-demo-config/manifests/base/rollout.yaml):`kind: Rollout`,`strategy.blueGreen`:
  - `activeService: cicd-demo-active`(正式流量)、`previewService: cicd-demo-preview`(预览新版)
  - `autoPromotionEnabled`:是否自动切换
  - `scaleDownDelaySeconds: 60`:旧版保留时间(回滚窗口)
- [`base/services.yaml`](../../baor-demo-config/manifests/base/services.yaml):`active` / `preview` 两个 Service(Rollouts 自动注入 pod-hash 到 selector)

### 7.3 环境分级(overlay 差异)

| 环境 | `autoPromotionEnabled` | 行为 |
|------|:---:|------|
| `dev` / `test` | `true` | 新版就绪后**自动切换**(等价快速滚动) |
| `prod` | `false` | 新版停在 preview,**人工 promote** 才切流量(真正的蓝绿门) |

### 7.4 晋升与回滚(prod)

```bash
# 看蓝绿状态(哪个是 active、哪个在 preview 等待)
kubectl argo rollouts get rollout cicd-demo -n baor-demo-prod --watch

# 在 preview 上验证新版后,晋升(active 切到 green)
kubectl argo rollouts promote cicd-demo -n baor-demo-prod

# 回滚到上一版本
kubectl argo rollouts undo cicd-demo -n baor-demo-prod
# 或 GitOps 方式:配置仓库 git revert 那次 digest 变更并 push
```

### 7.5 进阶:自动晋升

给 `strategy.blueGreen` 加 `prePromotionAnalysis`,用 `AnalysisTemplate`(查 Prometheus 的成功率/延迟)在切换前自动判定,达标才 promote、不达标自动放弃。金丝雀(按权重渐进 + 自动分析)是下一步演进方向。

> 集群需先安装 Argo Rollouts 控制器,见 [CD-RUNBOOK.md](./CD-RUNBOOK.md)。

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
