# 蓝绿部署:解决方案 + 操作手册(Argo Rollouts)

本文分两部分:
- **第一部分 · 解决方案**:蓝绿是什么、为什么、在本项目里怎么设计的。
- **第二部分 · 操作手册**:从零到跑通、日常发布、promote、回滚、排错。

相关文档:CD 总体流程见 [CD.md](./CD.md),从零落地的踩坑记录见 [CD-RUNBOOK.md](./CD-RUNBOOK.md)。

---

# 第一部分 · 解决方案

## 1. 蓝绿部署是什么

同时保留**两套全量环境**:
- **Blue(蓝)** = 当前对外提供服务的版本;
- **Green(绿)** = 新部署、等待验证的版本。

新版本(green)先在**预览入口**验证,确认无误后**一次性把流量从蓝切到绿**;旧版(蓝)保留一小段时间,出问题可秒级切回。

```
        ┌─────────────┐         切换前            ┌─────────────┐  切换后
 用户 ─▶│ active svc  │──▶ Blue(v1)              │ active svc  │──▶ Green(v2)
        └─────────────┘                           └─────────────┘
        ┌─────────────┐                           ┌─────────────┐
 验证 ─▶│ preview svc │──▶ Green(v2, 待验证)      │ preview svc │──▶ Green(v2)
        └─────────────┘                           └─────────────┘
                          ── promote ──▶                 Blue 保留 60s 后缩容
```

**对比滚动更新 / 金丝雀:**

| 策略 | 流量切换 | 中间态 | 资源 | 适合 |
|------|----------|--------|------|------|
| RollingUpdate | 逐个替换 Pod | 新旧混跑 | 低 | 一般发布 |
| **蓝绿** | **瞬时全量切** | **无(要么全蓝要么全绿)** | 翻倍 | 不接受新旧混跑、要一键切换/回退 |
| 金丝雀 | 按比例渐进 | 新旧按权重共存 | 低 | 大流量、要用真实流量+指标灰度 |

## 2. 为什么用 Argo Rollouts

原生 `Deployment` 只有滚动更新。**Argo Rollouts** 提供一个 `Rollout` CRD 替换 Deployment,原生支持 blueGreen / canary 策略,并被 Argo CD 直接识别健康状态——与现有 GitOps 流程无缝衔接。

> ⚠️ 前提:蓝绿只对**常驻服务**有意义(要有流量可切)。本项目为此把工作负载从批处理 CronJob 换成了常驻 HTTP 服务(`app.server`,监听 `:8000`,含 `/healthz`)。

## 3. 本项目的蓝绿架构

```
应用仓库 baor-demo                     配置仓库 baor-demo-config              k3s 集群
─────────────────                     ──────────────────────                ─────────
push dev/test/prod
  → CI 构建 HTTP 服务镜像 → GHCR
  → bump 回写 digest ───────────▶  overlays/<env> 的镜像 digest
                                         │  Argo CD 拉取同步
                                         ▼
                                   Rollout(blueGreen)+ active/preview 两个 Service
                                         │  Argo Rollouts 控制器接管
                                         ▼
                                   起 green → (dev/test 自动切 | prod 等人工 promote)
```

**关键对象(config 仓库 `manifests/`):**

| 对象 | 名称 | 作用 |
|------|------|------|
| Rollout | `cicd-demo` | 替换 Deployment,`strategy.blueGreen` |
| active Service | `cicd-demo-active` | 承接正式流量,指向 blue |
| preview Service | `cicd-demo-preview` | 指向 green,供验证 |
| 容器环境变量 | `APP_VERSION` | 在 `/healthz` 回显,**肉眼区分蓝/绿** |
| 命名空间 | `baor-demo-{dev,test,prod}` | 环境隔离 |

**Rollout 蓝绿策略核心字段(`base/rollout.yaml`):**
```yaml
strategy:
  blueGreen:
    activeService: cicd-demo-active      # 正式流量入口
    previewService: cicd-demo-preview    # 新版预览入口
    autoPromotionEnabled: false          # base 默认手动;dev/test 覆盖为 true
    scaleDownDelaySeconds: 60            # 切换后旧版保留 60s,便于秒级回滚
```

## 4. 环境分级策略(重要设计)

不是所有环境都需要"人工把关",按环境分级:

| 环境 | `autoPromotionEnabled` | 行为 |
|------|:----------------------:|------|
| `dev` | `true` | 新版就绪后**自动切**(等价快速滚动),快反馈 |
| `test` | `true` | 同上,配合集成测试 |
| `prod` | **`false`** | 新版起在 preview,**停下等人工 `promote`** —— 真正的蓝绿门 |

实现:`base` 默认 `false`;`overlays/dev`、`overlays/test` 用补丁改成 `true`;`overlays/prod` 保持 `false` 并把 `APP_VERSION` 覆盖为 `prod`。

## 5. 关键设计决策与坑

1. **镜像用不可变 digest**:CI 的 bump 写 `@sha256:...` 而非漂移的 `:dev` 标签 —— digest 变化才能可靠触发新 Rollout、精确回滚。
2. **去掉 overlay 的 `namePrefix`**:Rollout 的 `activeService/previewService` 是**按名字引用 Service**,kustomize 的 namePrefix 不会同步改 CRD 里这些引用,会错位。改用 namespace 隔离。
3. **prod 双重门**:GitHub Environment 审批(回写前)+ 集群内 `promote`(切流量前)—— 两道人工确认。
4. **prod 生产晋升的两种模式**:本方案用"CI 审批 + 集群 promote";更严格可改为 Argo 手动 sync + config 仓库 PR(见 [CD.md](./CD.md) §5)。

---

# 第二部分 · 操作手册

> 约定:`<env>` = dev/test/prod;命名空间 = `baor-demo-<env>`;Rollout 名 = `cicd-demo`。

## A. 一次性准备(集群侧)

### A1. 安装 Argo Rollouts 控制器
```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts \
  -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl -n argo-rollouts rollout status deploy/argo-rollouts
```
> 大清单在慢链路上易超时:可在集群本机 apply,或加 `--server-side=true --request-timeout=5m`。

### A2. 安装 kubectl 插件(观察 / promote / undo 用)
```bash
# Linux amd64(ARM 换 arm64):
curl -sSLo kubectl-argo-rollouts \
  https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts && sudo mv kubectl-argo-rollouts /usr/local/bin/
kubectl argo rollouts version
```
> 插件只是观察/操作工具,不影响部署本身。不装也可用原生 `kubectl get rollout/rs/pods`。

### A3. 确认镜像可拉取
GHCR 包设为 **Public**,或给每个命名空间配 `imagePullSecrets`(见 [CD-RUNBOOK.md](./CD-RUNBOOK.md) 坑 9)。

## B. 发布流程(按环境分场景)

三个环境走的是**同一条晋升链**,但**发布动作各不相同**:

```
feature/* ──PR──▶ dev ──PR──▶ test ──PR──▶ prod
   (开发)         自动切        自动切+集成      人工蓝绿门
```

| | dev | test | prod |
|---|-----|------|------|
| 使用场景 | 开发自测、快速反馈 | 集成/预发验证 | 生产发布,零停机、可控切换 |
| 触发 | push `dev` | 合并 `dev`→`test` 后 push | 合并 `test`→`prod` 后 push |
| 集成测试 | ❌ | ✅(`run-integration`) | ✅ |
| GitHub 审批 | ❌ | ❌ | ✅(production 环境) |
| `autoPromotionEnabled` | `true` | `true` | **`false`** |
| 蓝绿切换 | 就绪后**自动切** | 就绪后**自动切** | 起 green 后**暂停**,人工 `promote` 才切 |
| 你要做的 | 只 push | 只 push,看集成测试 | push→审批→验证 preview→promote |

> 三个环境**镜像不同标签**(`:dev` / `:test` / `:prod`),命名空间隔离(`baor-demo-<env>`),互不影响。

---

### 场景一:dev 环境(全自动,开发自测)

**目的**:改完代码想尽快在集群里看到效果。dev `autoPromotion=true`,蓝绿对你几乎无感——新版就绪后自动切。

```bash
cd baor-demo
git switch dev
# ...改代码...
git commit -am "feat: xxx"
git push                     # 触发 Dev Pipeline
```

**自动发生**:CI(lint/test/security)→ 构建 `:dev` 镜像 → bump 回写 dev overlay digest → Argo 同步 → Rollout 起 green →(探针就绪)**自动 promote** → `active` 指向新版。

**你只需验证**:
```bash
kubectl get rollout,pods -n baor-demo-dev              # Pod Running,新 ReplicaSet
kubectl logs -n baor-demo-dev -l app=cicd-demo --tail=3   # serving cicd-demo (version=dev) ...
```

**回滚**:直接改代码再 push 即可(dev 容忍快速试错);或 `kubectl argo rollouts undo cicd-demo -n baor-demo-dev`。

---

### 场景二:test 环境(全自动 + 集成测试,预发验证)

**目的**:把 dev 验过的改动晋升到 test,跑**集成测试**做预发把关。test 也是 `autoPromotion=true`,自动切;区别是 CI 会额外跑集成套件。

```bash
cd baor-demo
git switch test
git merge dev                # 从 dev 晋升
git push                     # 触发 Test Pipeline(含 run-integration)
```

**自动发生**:CI + **集成测试** → 构建 `:test` → bump 回写 test overlay → Argo 同步 → Rollout 起 green → 自动 promote。

**你需要关注**:
1. GitHub Actions 里 **集成测试 job 是否通过**(不过则不会构建/部署)。
2. 部署后验证:
   ```bash
   kubectl get rollout,pods -n baor-demo-test
   kubectl logs -n baor-demo-test -l app=cicd-demo --tail=3   # version=test
   ```
3. (可选)想在切换前先看新版,可临时把 test 也当手动门用:`kubectl argo rollouts get rollout cicd-demo -n baor-demo-test`。

**回滚**:`kubectl argo rollouts undo cicd-demo -n baor-demo-test`,或 config 仓库 `git revert`。

---

### 场景三:prod 环境(人工蓝绿门,生产发布)⭐

**目的**:生产零停机发布,且**切流量前必须人工确认**。prod `autoPromotion=false` —— 新版起在 preview 上**停住**,验证无误再手动 `promote`。这是唯一能看到完整蓝绿过程的环境。

**① 触发晋升**
```bash
cd baor-demo
git switch prod
git merge test               # 从 test 晋升
git push                     # 触发 Prod Pipeline
```

**② GitHub 审批(第一道门)**
GitHub Actions → Prod Pipeline 跑到 `approval` job 会**暂停** → 打开 → **Review deployments → Approve**。
批准后:构建 `:prod` → bump 回写 prod overlay digest → Argo 同步。

**③ 新版起在 preview 并暂停(第二道门 · 蓝绿核心)**
```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-prod --watch
# 状态 = Paused;stable=旧版(blue),preview=新版(green),active 仍指 blue
```

**④ 在 preview 验证 green,对比 active 上的 blue**
```bash
# green(待发布的新版)—— 另开终端
kubectl port-forward svc/cicd-demo-preview -n baor-demo-prod 9000:80
curl http://localhost:9000/healthz          # {"status":"ok","version":"<新版>"}

# blue(仍在承接生产流量的旧版)
kubectl port-forward svc/cicd-demo-active -n baor-demo-prod 9001:80
curl http://localhost:9001/healthz          # {"status":"ok","version":"<旧版>"}
```
> 这一步是蓝绿的价值所在:**新版已全量就绪、但还没接生产流量**,你可以在 preview 上充分验证(冒烟/回归/联调),生产流量完全不受影响。

**⑤ 确认无误 → 切流量(promote)**
```bash
kubectl argo rollouts promote cicd-demo -n baor-demo-prod
curl http://localhost:9001/healthz          # active 瞬间变成新版
```
`active` 秒级切到 green;旧版(blue)保留 **60s** 后自动缩容(回滚窗口)。

**⑥ 若验证发现问题 → 不 promote,直接中止**
```bash
kubectl argo rollouts abort cicd-demo -n baor-demo-prod   # 放弃 green,active 保持 blue
```
生产流量从未切过去,零影响。修好后重新走一遍。

**⑦ promote 后才发现问题 → 回滚(见 C)**
60s 内 blue 还在,`undo` 可秒回;超过后从上一个 digest 重建。

## C. 回滚

```bash
# 方式一:Rollouts 秒回上一版
kubectl argo rollouts undo cicd-demo -n baor-demo-prod

# 方式二:GitOps(可审计)—— 在 config 仓库 revert 那次 deploy 提交
cd baor-demo-config
git revert <deploy commit> && git push       # Argo 自动同步回旧 digest
```

## D. 验证 / 观察命令

```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-<env>    # 蓝绿状态全景
kubectl get rollout,rs,pods,svc -n baor-demo-<env>                # 原生视角
kubectl get application cicd-demo-<env> -n argocd \
  -o custom-columns=SYNC:.status.sync.status,HEALTH:.status.health.status
kubectl logs -n baor-demo-<env> -l app=cicd-demo --tail=5         # 应见 "serving cicd-demo ..."
```

## E. 排错速查

| 现象 | 原因 | 处理 |
|------|------|------|
| Pod `CrashLoopBackOff`,日志是 `2 + 3 = 5` | 镜像是旧**批处理版**(分支没含 `app.server`) | 把服务版代码合到该分支重新构建 |
| Pod `ImagePullBackOff` | GHCR 私有 / 早先失败进退避 | 设 Public 或配 pull secret;`kubectl delete pod -l app=cicd-demo` 触发重拉 |
| Rollout 一直 `Paused` | prod `autoPromotion=false`,**正常**,等你 promote | `kubectl argo rollouts promote ...` |
| Rollout `Degraded` | 新版 `/healthz` 探针不过(端口/启动失败) | `kubectl logs` 看容器 |
| `unknown command "argo"` | 没装 kubectl 插件 | 见 A2,或用原生 kubectl |
| Argo app `OutOfSync` 不动 | 自动同步没触发 | UI 点 Sync,或 `kubectl -n argocd patch application <app> --type merge -p '{"operation":{"sync":{"prune":true}}}'` |
| active/preview 指向不对 | overlay 加了 `namePrefix` 改错了引用 | 移除 namePrefix,靠 namespace 隔离 |

## F. 演示技巧:纯配置触发一次蓝绿

不重新构建镜像,只改 `overlays/prod` 的 `APP_VERSION`(`prod` → `prod-v2`)并 push config 仓库,即可触发一次 green 部署 —— 最干净地演示"暂停 → 预览 → promote",且 blue/green 版本号可肉眼区分。

---

## 附:整体链路一图

```
代码变更 → CI(测试+构建服务镜像)→ GHCR
   → bump 回写 config 仓库镜像 digest
   → Argo CD 同步 Rollout
   → Argo Rollouts:起 green
        ├─ dev/test:autoPromotion=true → 自动切 active
        └─ prod:autoPromotion=false → preview 暂停 → 人工验证 → promote → 切 active
   → 旧版保留 60s 后缩容(回滚窗口)
```
