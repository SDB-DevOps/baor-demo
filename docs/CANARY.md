# 金丝雀部署:解决方案 + 操作手册(Argo Rollouts)

本文分两部分:
- **第一部分 · 解决方案**:金丝雀是什么、为什么、在本项目里怎么设计的。
- **第二部分 · 操作手册**:从零到跑通、日常发布、渐进观察、promote/abort、回滚、排错。

相关文档:蓝绿见 [BLUE-GREEN.md](./BLUE-GREEN.md);两种策略如何选择/切换见 [RELEASE-STRATEGIES.md](./RELEASE-STRATEGIES.md);CD 总体流程见 [CD.md](./CD.md)。

> 本项目默认:`dev` / `test` 用**金丝雀**,`prod` 用**蓝绿**。任意环境改 overlay 的一行 `components:` 即可切换(见 RELEASE-STRATEGIES.md)。

---

# 第一部分 · 解决方案

## 1. 金丝雀部署是什么

新版本**按比例逐步接管流量**:先给一小部分(如 20%),观察无异常再加到 50%、100%。每一步之间可暂停/自动分析,发现问题立即回退,把风险限制在一小撮流量内。

```
        权重 20%                权重 50%                权重 100%
用户 ─┬─▶ stable(v1) 80%    ─┬─▶ stable 50%       ─┬─▶ canary(v2) 100%
     └─▶ canary(v2) 20%      └─▶ canary 50%          (stable 缩容)
       │pause/分析              │pause/分析
       ▼ 通过                   ▼ 通过
```

**对比蓝绿:**

| | 金丝雀 | 蓝绿 |
|---|--------|------|
| 流量切换 | 按比例渐进(20→50→100) | 瞬时全量切 |
| 新旧共存 | 是(同时承接真实流量) | 是,但只有一版接流量 |
| 风险暴露 | 逐步、可控(先影响小部分) | 切换瞬间全量 |
| 依赖 | **流量路由器**(ingress/网格) | 两个 Service 即可 |
| 适合 | 大流量、要用真实流量+指标验证 | 要一键切换/回退、不接受中间态 |

## 2. 为什么用 Argo Rollouts + 流量路由

原生 Deployment 无法按百分比切流量。**Argo Rollouts** 的 `Rollout` CRD 提供 `strategy.canary`,配合**流量路由器**(本项目用 ingress-nginx)按 `setWeight` 精确调配 stable / canary 的流量比例,并支持 `pause`(人工/定时)与 `analysis`(自动判定)。

> 前提:金丝雀只对**常驻服务**有意义(要有流量可切)。本项目为此把工作负载改成了常驻 HTTP 服务(`app.server`,`:8000`,`/healthz`)。

## 3. 本项目的金丝雀架构

```
应用仓库 baor-demo                     配置仓库 baor-demo-config              k3s 集群
push dev/test
  → CI 构建镜像 → GHCR
  → bump 回写 digest ───────────▶  overlays/<env> 引用 canary 组件
                                         │  Argo CD 同步
                                         ▼
                          Rollout(canary)+ canary/stable Service + nginx Ingress
                                         │  Argo Rollouts 控制器接管
                                         ▼
                          按 steps 调 Ingress canary-weight:20% → 50% → 100%
```

**关键对象(config 仓库 `manifests/components/canary/`):**

| 对象 | 名称 | 作用 |
|------|------|------|
| Rollout 策略 | `strategy.canary` | steps(权重步进)+ trafficRouting |
| stable Service | `cicd-demo-stable` | 稳定版流量 |
| canary Service | `cicd-demo-canary` | 金丝雀版流量 |
| Ingress | `cicd-demo` | nginx 稳定入口;Rollouts 克隆出 `-canary` Ingress 调权重 |
| 容器环境变量 | `APP_VERSION` | `/healthz` 回显,肉眼区分 stable/canary |

**canary 策略核心字段(`components/canary/strategy.yaml`):**
```yaml
strategy:
  canary:
    canaryService: cicd-demo-canary
    stableService: cicd-demo-stable
    trafficRouting:
      nginx:
        stableIngress: cicd-demo
    steps:
      - setWeight: 20
      - pause: { duration: 30s }   # 改成 pause: {} 则无限暂停,等人工 promote
      - setWeight: 50
      - pause: { duration: 30s }
      - setWeight: 100
```

## 4. 关键设计决策

1. **精确流量靠 ingress-nginx**:`trafficRouting.nginx.stableIngress` 让 Rollouts 自动维护一个 `-canary` Ingress,通过 `nginx.ingress.kubernetes.io/canary-weight` 精确切分。换 Traefik / Istio / Gateway API 只需替换 `trafficRouting` 块 + 对应路由资源。
2. **策略可插拔**:金丝雀是一个 Kustomize Component,overlay 引用即启用;不影响 base 和另一种策略(见 [RELEASE-STRATEGIES.md](./RELEASE-STRATEGIES.md))。
3. **步进 + 暂停策略**:`pause: {duration}` = 定时自动推进;`pause: {}` = 停下等人工 `promote`。可按环境把最后一步设成人工卡点。
4. **镜像不可变 digest**:CI bump 写 `@sha256:...`,digest 变化才触发新一轮金丝雀。
5. **可进阶自动分析**:给 step 加 `analysis`(`AnalysisTemplate` 查 Prometheus 成功率/延迟),达标自动推进、不达标自动回滚。

---

# 第二部分 · 操作手册

> 约定:`<env>` = dev/test/prod;命名空间 = `baor-demo-<env>`;Rollout 名 = `cicd-demo`。

## A. 一次性准备(集群侧)

### A1. Argo Rollouts 控制器 + kubectl 插件
```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts \
  -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl -n argo-rollouts rollout status deploy/argo-rollouts

# kubectl 插件(观察/promote/abort 用),Windows 换 windows-amd64.exe
curl -sSLo kubectl-argo-rollouts \
  https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts && sudo mv kubectl-argo-rollouts /usr/local/bin/
```

### A2. ingress-nginx 控制器(金丝雀精确流量必需)⚠️
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller
```
**k3s 注意**:k3s 自带 Traefik 占用 80/443。二选一:
- 禁用 Traefik(启动加 `--disable traefik`,或删掉 `traefik` HelmChart)再装 nginx;
- 或让 ingress-nginx 用 NodePort/其它端口。

> 不想装 nginx?把 `components/canary/strategy.yaml` 的 `trafficRouting.nginx` 换成 `traefik`(k3s 已有 Traefik)或 Gateway API,并相应替换 `ingress.yaml`。

### A3. 确认镜像可拉取
GHCR 包设 **Public**,或给命名空间配 `imagePullSecrets`(见 [CD-RUNBOOK.md](./CD-RUNBOOK.md) 坑 9)。

## ▶ 完整实操:单独执行一次金丝雀发布(step-by-step + 验证/观察)

本节是一次**从零到完成**的金丝雀发布全过程,每一步都给出**验证成功的方法**与**状态监控命令**。以 dev 为例,namespace `baor-demo-dev`、Rollout `cicd-demo`、Service `cicd-demo-canary`/`cicd-demo-stable`、Ingress `cicd-demo`。其它环境把 `-n baor-demo-<env>` 换掉即可。

### 步骤 0:环境自检(缺一不可)

```bash
# 每个新终端都要设(Windows: $env:KUBECONFIG="...\k3s.yaml")
export KUBECONFIG=/path/to/k3s.yaml
kubectl get nodes                                  # 节点 Ready
kubectl get pods -n argo-rollouts                  # Rollouts 控制器 Running
kubectl get pods -n ingress-nginx                  # 金丝雀精确流量必需
kubectl argo rollouts version                      # kubectl 插件已装
kubectl get rollout cicd-demo -n baor-demo-dev     # 当前 Rollout 存在
```
✅ **验证通过**:上面每条都有正常输出、无报错。任一缺失见 §A 或 §E 排错。

### 步骤 1:确认金丝雀策略(要发布成什么样)

查看当前生效的步进/权重(config 仓库):
```bash
cd baor-demo-config
kubectl kustomize manifests/overlays/dev | grep -A15 "canary:"
```
✅ **验证**:能看到 `canaryService/stableService`、`trafficRouting.nginx`、`steps`(如 20→50→100)。要调整就改 `manifests/components/canary/strategy.yaml` 后 `git commit && git push`(改组件会同时影响 dev 和 test)。

### 步骤 2:触发一轮金丝雀发布

二选一:

- **A. 走完整 CI(真实路径)**:改代码 → push `dev` → CI 构建新镜像 → bump 回写新 digest。
  ```bash
  cd baor-demo && git switch dev && git commit -am "feat: xxx" && git push
  ```
- **B. 纯配置改版本号(最快演示)**:改 `overlays/dev` 的 `APP_VERSION`(如 `dev`→`dev-v2`)→ push config 仓库。

✅ **验证已触发**:
```bash
# config 仓库出现新的 deploy 提交 / APP_VERSION 变更已 push
git log origin/main --oneline -1
# Argo 已拉到新版本(REV = 刚 push 的 commit)
kubectl get application cicd-demo-dev -n argocd \
  -o custom-columns=SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision
```
若 `OutOfSync` 迟迟不动:`kubectl -n argocd annotate application cicd-demo-dev argocd.argoproj.io/refresh=hard --overwrite`。

### 步骤 3:实时观察金丝雀渐进(核心监控)

**主监控窗口**(保持开着):
```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev --watch
```
盯这几项:
- `Status`:`◌ Progressing` → 每步 `॥ Paused`(定时/人工)→ 最终 `✔ Healthy`
- `Actual Weight` / `Setpoint`:随步骤从 20 → 50 → 100 爬升
- 两个 ReplicaSet:`revision:N (canary)` 与 `(stable)` 的副本数变化
- `Steps` 区高亮当前第几步

**脚本化单值状态**(适合等待/判断):
```bash
kubectl argo rollouts status cicd-demo -n baor-demo-dev
# 输出 Progressing / Paused / Healthy / Degraded,阻塞到稳定态
```

### 步骤 4:验证"权重真的按比例切了"

**① nginx 实际权重**(Rollouts 自动维护的 canary Ingress):
```bash
kubectl get ingress -n baor-demo-dev                       # 应见 cicd-demo 与 cicd-demo-canary 两个
kubectl get ingress cicd-demo-canary -n baor-demo-dev \
  -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/canary-weight}{"\n"}'
```
✅ 输出应等于当前步权重(如 `20`)。

**② 真实流量分布**(打多次看新旧版本比例):
```bash
INGRESS=<ingress入口地址>     # 如 nginx 的 NodePort/LoadBalancer 地址
for i in $(seq 1 20); do
  curl -s -H "Host: cicd-demo.local" http://$INGRESS/healthz | grep -o '"version":"[^"]*"'
done | sort | uniq -c
```
✅ 新旧版本出现次数比 ≈ 当前 canary 权重(如约 20% 命中新版)。

**③ Pod 层面**(另开窗口):
```bash
kubectl get pods -n baor-demo-dev -l app=cicd-demo -w      # canary/stable 两组 Pod 均 Running
```

### 步骤 5:遇到"暂停"步的放行(仅当用了 `pause: {}`)

```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev   # Status: ॥ Paused
kubectl argo rollouts promote cicd-demo -n baor-demo-dev        # 放行到下一步
# kubectl argo rollouts promote cicd-demo -n baor-demo-dev --full  # 直接跳到 100%
```
✅ **验证**:promote 后 `--watch` 里权重继续上升。

### 步骤 6:确认发布完成

```bash
kubectl argo rollouts status cicd-demo -n baor-demo-dev            # → Healthy
kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev       # 权重 100,canary 缩容,stable=新版
kubectl logs -n baor-demo-dev -l app=cicd-demo --tail=3            # version = 新版本
```
✅ **成功标志**:Status `Healthy`、权重 100%、`/healthz` 全部返回新 version。

### 步骤 7:异常处理(中止 / 回滚)

```bash
# 渐进中发现问题 → 中止,流量退回 stable(零影响)
kubectl argo rollouts abort cicd-demo -n baor-demo-dev

# 已完成后要回退 → 回滚上一版
kubectl argo rollouts undo cicd-demo -n baor-demo-dev
# 或 GitOps(可审计):config 仓库 git revert 那次 deploy 提交并 push
```
✅ **验证**:`--watch` 显示权重归 0 / 回到旧版本,`/healthz` version 恢复。

### 图形化观察(可选,比 CLI 直观)

```bash
kubectl argo rollouts dashboard          # 本地 3100 端口,浏览器看 Rollout 步骤/权重
# 或 Argo CD UI:kubectl port-forward svc/argocd-server -n argocd 8080:443
```

### 本节命令速查

| 目的 | 命令 |
|------|------|
| 全景监控(步骤/权重/RS/暂停) | `kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev --watch` |
| 单值状态(脚本用) | `kubectl argo rollouts status cicd-demo -n baor-demo-dev` |
| Argo 是否同步到新版本 | `kubectl get application cicd-demo-dev -n argocd` |
| nginx 实际权重 | `kubectl get ingress cicd-demo-canary -n baor-demo-dev -o jsonpath='{...canary-weight}'` |
| 真实流量比例 | `curl` 循环统计 version |
| 放行/中止/回滚 | `promote` / `abort` / `undo` |

---

## B. 发布流程(按环境分场景)

### 场景一:dev 环境(金丝雀自动渐进,开发自测)

**目的**:改完代码,想看新版按比例灰度上线的过程。dev 的 canary 每步 `pause: {duration: 30s}` 自动推进。

```bash
cd baor-demo
git switch dev
# ...改代码...
git commit -am "feat: xxx"
git push                     # 触发 Dev Pipeline
```

**自动发生**:CI → 构建 `:dev` → bump 回写 digest → Argo 同步 → Rollout 起 canary → **20% →(30s)→ 50% →(30s)→ 100%** 自动推进 → 全量后 stable 指向新版。

**观察渐进过程**:
```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev --watch
# 关注 Weight 从 20 → 50 → 100,canary/stable 各自的 Pod 数与权重
```

**验证**:
```bash
kubectl get rollout,pods,ingress -n baor-demo-dev
kubectl logs -n baor-demo-dev -l app=cicd-demo --tail=3   # version=dev
```

**回滚**:改代码再 push;或 `kubectl argo rollouts undo cicd-demo -n baor-demo-dev`;或 config 仓库 `git revert`。

---

### 场景二:test 环境(金丝雀 + 集成测试,预发验证)

**目的**:把 dev 验过的改动晋升到 test,CI 额外跑集成测试,再金丝雀渐进上线。

```bash
cd baor-demo
git switch test
git merge dev
git push                     # 触发 Test Pipeline(含 run-integration)
```

**关注点**:
1. GitHub Actions 里**集成测试 job 通过**(不过则不部署)。
2. 部署后同 dev 一样观察金丝雀渐进:
   ```bash
   kubectl argo rollouts get rollout cicd-demo -n baor-demo-test --watch
   ```
3. 想在某个权重停下人工确认:把 `components/canary/strategy.yaml` 对应 step 的 `pause` 去掉 duration(`pause: {}`),到点会停,`promote` 放行。

**回滚**:`kubectl argo rollouts undo cicd-demo -n baor-demo-test`,或 config `git revert`。

---

### 场景三:prod 用金丝雀(可选 —— 默认是蓝绿)

prod 默认走**蓝绿**(见 [BLUE-GREEN.md](./BLUE-GREEN.md))。若想让 prod 也用金丝雀 + 人工卡点:

**① 切换 prod 策略**(config 仓库)
```bash
cd baor-demo-config
# 编辑 manifests/overlays/prod/kustomization.yaml:
#   components: [../../components/blue-green]  →  [../../components/canary]
git commit -am "chore: prod use canary" && git push
```

**② 建议给 prod 加人工卡点**:把 `components/canary/strategy.yaml` 最后一步前的 `pause` 设为 `pause: {}`(无限暂停),这样金丝雀升到某比例后**停下等人工**:
```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-prod --watch   # Paused at weight N%
# 验证无误后放行到下一步 / 全量
kubectl argo rollouts promote cicd-demo -n baor-demo-prod
```

**③ 发现问题 → 中止(流量退回 stable)**
```bash
kubectl argo rollouts abort cicd-demo -n baor-demo-prod
```

> prod 若保留蓝绿,操作见 [BLUE-GREEN.md](./BLUE-GREEN.md) 场景三。

## C. 渐进控制命令(金丝雀常用)

```bash
kubectl argo rollouts get rollout cicd-demo -n baor-demo-<env> --watch   # 看权重/步骤
kubectl argo rollouts promote cicd-demo -n baor-demo-<env>               # 放行到下一步(遇到 pause:{} 时)
kubectl argo rollouts promote cicd-demo -n baor-demo-<env> --full        # 直接跳到 100%
kubectl argo rollouts abort   cicd-demo -n baor-demo-<env>               # 中止,流量退回 stable
kubectl argo rollouts undo    cicd-demo -n baor-demo-<env>               # 回滚到上一版
```

## D. 回滚

```bash
# 方式一:Rollouts 回退
kubectl argo rollouts undo cicd-demo -n baor-demo-<env>

# 方式二:GitOps(可审计)
cd baor-demo-config
git revert <deploy commit> && git push    # Argo 自动同步回旧 digest
```

## E. 排错速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 权重一直 0% / 不推进 | 没装 ingress-nginx 或 `stableIngress` 名不匹配 | 装 ingress-nginx;确认 Ingress 名 = `cicd-demo` |
| 报 `no matches for kind "Ingress"` / 类不识别 | 集群无 ingress 控制器 | 见 A2 |
| Pod `CrashLoopBackOff`,日志 `2 + 3 = 5` | 镜像是旧批处理版(分支缺 `app.server`) | 把服务版代码合到该分支重建 |
| Pod `ImagePullBackOff` | GHCR 私有 / 退避 | 设 Public 或配 pull secret;`kubectl delete pod -l app=cicd-demo` |
| Rollout `Paused` 不动 | 某步是 `pause: {}` 等人工 | `kubectl argo rollouts promote ...` |
| `unknown command "argo"` | 没装 kubectl 插件 | 见 A1 |
| 权重不精确/只按副本 | `trafficRouting` 没生效(缺路由器) | 确认 nginx 装好且 Rollout 有 `trafficRouting.nginx` |
| Argo app `OutOfSync` 不动 | 自动同步没触发 | UI 点 Sync,或 `kubectl -n argocd patch application cicd-demo-<env> --type merge -p '{"operation":{"sync":{"prune":true}}}'` |

## F. 演示技巧:纯配置触发一次金丝雀

不重建镜像,只改 `overlays/<env>` 的 `APP_VERSION`(如 `dev` → `dev-v2`)并 push config 仓库,即可触发一轮金丝雀 —— 观察权重从 20→50→100 推进,`/healthz` 的 version 可肉眼区分 stable/canary。

---

## 附:整体链路一图

```
代码变更 → CI(测试+构建服务镜像)→ GHCR
   → bump 回写 config 仓库镜像 digest
   → Argo CD 同步 Rollout(canary)
   → Argo Rollouts 调 ingress canary-weight:
        20% → (pause) → 50% → (pause) → 100%
        ├─ pause:{duration}  自动推进
        └─ pause:{}          停下等人工 promote(可 abort 退回 stable)
   → 全量后 stable 指向新版
```
