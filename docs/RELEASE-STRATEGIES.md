# 发布策略方案:蓝绿 + 金丝雀可选(Kustomize Components)

本文档给出让项目**同时具备蓝绿(blue-green)和金丝雀(canary)两种发布方式、且每个环境可任选其一**的设计方案与实施步骤。**应用代码、CI、bump 回写机制完全不变**,只调整 config 仓库(`baor-demo-config`)的清单结构。

- 蓝绿操作手册见 [BLUE-GREEN.md](./BLUE-GREEN.md)
- 金丝雀操作手册见 [CANARY.md](./CANARY.md)
- CD 总体流程见 [CD.md](./CD.md)

---

## 1. 目标与思路

**现状**:`base/rollout.yaml` 把 `strategy.blueGreen` 和 active/preview 两个 Service 硬编码在 base,三个环境只能蓝绿。

**目标**:两种策略都在、每个环境**改一行**即可切换。

**思路**:用 **Kustomize Components** 把"发布策略"抽成两个可插拔组件。每个组件自带该策略需要的 Service / 流量路由资源 + Rollout 策略补丁。环境 overlay 引用哪个组件,就用哪种策略。

**默认映射**:`dev` / `test` → 金丝雀;`prod` → 蓝绿。
**金丝雀流量**:精确百分比,用 **nginx ingress** 流量路由。

---

## 2. 目标目录结构(config 仓库 `manifests/`)

```
manifests/
├── base/
│   ├── rollout.yaml            # Rollout:pod 模板/replicas/selector,不含 strategy(由组件注入)
│   └── kustomization.yaml      # resources: [rollout.yaml]
├── components/
│   ├── blue-green/
│   │   ├── kustomization.yaml  # kind: Component
│   │   ├── services.yaml       # cicd-demo-active + cicd-demo-preview
│   │   └── strategy.yaml       # 注入 spec.strategy.blueGreen
│   └── canary/
│       ├── kustomization.yaml  # kind: Component
│       ├── services.yaml       # cicd-demo-canary + cicd-demo-stable
│       ├── ingress.yaml        # nginx Ingress(stable),Rollouts 据此克隆 canary Ingress 调权重
│       └── strategy.yaml       # 注入 spec.strategy.canary(steps + trafficRouting.nginx)
└── overlays/
    ├── dev/    → components: [../../components/canary]      # 金丝雀
    ├── test/   → components: [../../components/canary]      # 金丝雀
    └── prod/   → components: [../../components/blue-green]   # 蓝绿
```

---

## 3. 关键设计点

1. **base 的 Rollout 不含 strategy**:由组件用 strategic-merge 补丁注入。单独 `kustomize build base` 会得到无 strategy 的 Rollout(不直接 apply,无妨);每个 overlay 必引用一个组件,渲染结果总是完整合法。
2. **Service 随策略走**:蓝绿要 active/preview,金丝雀要 canary/stable —— 各自放进对应组件,不再放 base。
3. **overlay 只管三件事**:选组件(`components:`)、设镜像(`images:`,CI 回写)、设 `APP_VERSION`(路径 `/spec/template/spec/containers/0/env/0/value`,两种策略都存在,安全)。**移除**原先 blueGreen 专属的 `autoPromotionEnabled` 补丁(挪进蓝绿组件),否则金丝雀环境会打到不存在的路径而报错。
4. **切换策略 = 改一行**:把 overlay 的 `components:` 在 `canary` / `blue-green` 之间换,push → Argo 同步。这就是"可选其一"。

---

## 4. 组件内容(样例)

### 4.1 `components/blue-green/`

`kustomization.yaml`
```yaml
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
resources:
  - services.yaml
patches:
  - path: strategy.yaml
    target: { kind: Rollout, name: cicd-demo }
```

`strategy.yaml`(strategic-merge)
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata: { name: cicd-demo }
spec:
  strategy:
    blueGreen:
      activeService: cicd-demo-active
      previewService: cicd-demo-preview
      autoPromotionEnabled: false     # prod 用蓝绿 = 手动 promote 门
      scaleDownDelaySeconds: 60
```

`services.yaml`:`cicd-demo-active` + `cicd-demo-preview`(80→8000,selector `app: cicd-demo`)。

### 4.2 `components/canary/`

`strategy.yaml`
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata: { name: cicd-demo }
spec:
  strategy:
    canary:
      canaryService: cicd-demo-canary
      stableService: cicd-demo-stable
      trafficRouting:
        nginx:
          stableIngress: cicd-demo
      steps:
        - setWeight: 20
        - pause: { duration: 30s }
        - setWeight: 50
        - pause: { duration: 30s }
        - setWeight: 100
```

`services.yaml`:`cicd-demo-canary` + `cicd-demo-stable`。
`ingress.yaml`:一个 nginx Ingress `cicd-demo` 指向 `cicd-demo-stable`(Rollouts 会自动克隆出 canary Ingress 并调 `canary-weight`)。

> prod 若也想金丝雀 + 人工卡点:把某个 `pause` 去掉 `duration`(无限暂停),用 `kubectl argo rollouts promote` 手动放行。

### 4.3 overlay(以 dev 为例)
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: baor-demo-dev
resources:
  - ../../base
components:
  - ../../components/canary        # ← 换成 ../../components/blue-green 即切蓝绿
images:
  - name: cicd-demo-app
    newName: ghcr.io/sdb-devops/baor-demo
    newTag: dev
patches:
  - target: { kind: Rollout, name: cicd-demo }
    patch: |
      - op: replace
        path: /spec/template/spec/containers/0/env/0/value
        value: dev
```

---

## 5. 集群前置

金丝雀精确流量需要 **ingress-nginx 控制器**:
- 安装 ingress-nginx(helm 或官方 manifest)。
- **k3s 注意**:k3s 自带 Traefik 占 80/443。二选一:①禁用 Traefik(`--disable traefik` 或删 `traefik` HelmChart)再装 nginx;②让 nginx 用 NodePort/其它端口。
- Argo Rollouts 控制器已装(蓝绿也需要),标准安装含操作 Ingress 的 RBAC。

> 换路由器很容易:把 `canary/strategy.yaml` 的 `trafficRouting.nginx` 换成 `traefik` / `istio` / Gateway API 对应块 + 相应路由资源即可,其余不变。

---

## 6. 影响面

| 部分 | 是否改动 |
|------|:--------:|
| 应用源码 `src/app/*` | ❌ 不动 |
| Dockerfile | ❌ 不动 |
| CI 工作流(ci/docker/bump) | ❌ 不动(镜像名仍 `cicd-demo-app`,images transformer 对 Rollout 生效) |
| config 仓库 base / overlays | ✅ 重构 |
| config 仓库 components | ✅ 新增 |

---

## 7. 验证

无集群时静态校验:
```bash
cd baor-demo-config
kubectl kustomize manifests/overlays/dev    # 应含 strategy.canary + canary/stable Service + Ingress,镜像=:dev
kubectl kustomize manifests/overlays/prod   # 应含 strategy.blueGreen + active/preview Service
# 把 dev 的 components 临时换成 blue-green 再 build,确认能干净切换
```
有集群 + ingress-nginx:push dev 看金丝雀按 20→50→100 渐进(`kubectl argo rollouts get rollout cicd-demo -n baor-demo-dev --watch`);push prod 看蓝绿 preview→promote。

---

## 8. 每个环境的操作差异(速览)

| 环境 | 策略 | 发布时行为 | 你要做的 |
|------|------|-----------|----------|
| dev | 金丝雀 | 20%→50%→100% 自动渐进(每步暂停 30s) | push,观察权重推进 |
| test | 金丝雀 | 同上 + 集成测试 | push,看集成测试 + 权重 |
| prod | 蓝绿 | 起 green 停在 preview 等人工 | push→审批→验证 preview→`promote` |

> 任意环境切换策略:改该 overlay 的 `components:` 一行(`canary` ↔ `blue-green`)并 push。
