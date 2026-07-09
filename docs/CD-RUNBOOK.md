# CD 实操记录(Runbook):从零把 Argo CD GitOps 跑通

本文是一次**真实落地**的操作记录 —— 把 [CD.md](./CD.md) 描述的流程,在一套具体环境里从零跑通,并记录**每个踩过的坑与解决办法**。适合照着复现,或给团队做交接。

- 设计原理见 [CD.md](./CD.md)
- 本文重点是"实际操作 + 排错"

## 本次环境

| 组件 | 本次用的 |
|------|----------|
| 应用代码仓库(CI) | `SDB-DevOps/baor-demo` |
| GitOps 配置仓库(CD) | `SDB-DevOps/baor-demo-config`(Public) |
| 镜像仓库 | GHCR `ghcr.io/sdb-devops/baor-demo` |
| 集群 | AWS EC2 上的 **k3s** |
| 本地 | Windows + PowerShell / Git Bash,`kubectl` |

---

## 总体链路

```
push dev → CI(lint/test/security)→ 构建镜像推 GHCR
        → bump 回写 config 仓库(digest)→ Argo CD 拉取 → 部署到 k3s
```

---

## 操作步骤(按序)

### A. 准备两个仓库

1. `baor-demo`(已有 CI + 本套 CD workflow)提交推送。
2. 新建**独立** config 仓库 `baor-demo-config`(建空仓库,不勾 README/.gitignore/license,选 Public 省去 Argo 读凭证),把 `gitops/` 内容推上去。
3. 把 config 仓库 `apps/*.yaml` 里 4 处 `repoURL` 改成真实地址 `https://github.com/SDB-DevOps/baor-demo-config.git`,提交推送。

### B. 在 baor-demo 配置 CI→CD 衔接

`baor-demo` → Settings → Secrets and variables → **Actions**:

| 类型 | 名称 | 值 |
|------|------|-----|
| **Variables** 页 | `CONFIG_REPO` | `SDB-DevOps/baor-demo-config` |
| **Secrets** 页 | `CONFIG_REPO_TOKEN` | Fine-grained PAT,对 config 仓库 `Contents: Read and write` |

(生产才需)Settings → Environments → 建 `production` → Required reviewers。

> Token 入口:头像 → **Settings**(账号级)→ 左侧栏**最底部** Developer settings → Personal access tokens → Fine-grained tokens。直达 https://github.com/settings/personal-access-tokens 。

### C. 触发 CI

```bash
cd baor-demo
git switch -c dev
git push -u origin dev        # 触发 Dev Pipeline
```

到 GitHub Actions 看:`ci` → `build` → `deploy`(bump)。bump 成功后,**config 仓库会自动多出一条** `deploy(dev): sha256:...` 提交,`overlays/dev` 的镜像被改成带 digest 的真实地址。

### D. 集群侧:连上 k3s + 装 Argo CD

```powershell
# 让本地 kubectl 连上 k3s(见下方"坑 3/4")
$env:KUBECONFIG = "C:\...\k3s.yaml"
kubectl get nodes

# 装 Argo CD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
```

### E. Bootstrap App-of-Apps

```bash
cd baor-demo-config
kubectl apply -n argocd -f apps/root-app.yaml   # 见"坑 6"若超时加 --validate=false
kubectl get applications -n argocd
```

期望:
```
cicd-demo-dev    Synced   Healthy
cicd-demo-test   Synced   Healthy
cicd-demo-prod   Synced   Healthy
cicd-demo-root   Synced   Healthy
```

### F. 验证部署

```bash
kubectl get cronjob -n baor-demo-dev
# 镜像应为 ghcr.io/sdb-devops/baor-demo@sha256:...
```

---

## 踩过的坑与解决 ⚠️

### 坑 1:bump 步骤 `kustomize exists. Remove it first.`
**现象**:Actions 的 deploy job 里安装 kustomize 报错退出。
**原因**:GitHub 托管 runner 已预装 kustomize 在 `/usr/local/bin`,安装脚本拒绝覆盖。
**解决**:改成"有就用、没有才装"(已合入 `_reusable-bump.yml`):
```bash
if command -v kustomize >/dev/null 2>&1; then echo "already present"; else <install>; fi
```

### 坑 2:看的是旧的运行 / 旧 Re-run
**现象**:改了 workflow 后,Actions 里还是报旧错误。
**原因**:在**旧的 run** 上点了 Re-run(会重放当时那个旧提交的 workflow)。
**解决**:去看**由修复提交触发的那次新 run**,不要在旧 run 上 Re-run。

### 坑 3:PowerShell 里 `export` 报错
**现象**:`export KUBECONFIG=...` → `export : 无法识别`。
**原因**:`export` 是 bash 语法,PowerShell 不认。
**解决**:
```powershell
$env:KUBECONFIG = "C:\...\k3s.yaml"        # 当前窗口
[Environment]::SetEnvironmentVariable("KUBECONFIG","C:\...\k3s.yaml","User")  # 永久(新窗口生效)
```

### 坑 4:kubectl 连到 `localhost:8080` 被拒
**现象**:`dial tcp [::1]:8080 ... refused`。
**原因**:当前窗口没设 `KUBECONFIG`,kubectl 回退到默认地址。`KUBECONFIG` 是**每个终端窗口独立**的,新开窗口要重设。
**解决**:在该窗口设 `$env:KUBECONFIG`,或用 `kubectl --kubeconfig <file> ...`。

### 坑 5:k3s 证书不含公网 IP(x509)
**现象**:`tls: certificate is valid for 127.0.0.1, 172.31.x.x, ... not <公网IP>`。
**原因**:k3s 默认证书只签了内网/回环地址。
**解决(二选一)**:
- 快(练习):本地 kubeconfig 里删掉 `certificate-authority-data`,加 `insecure-skip-tls-verify: true`。
- 正规:k3s 服务器 `/etc/rancher/k3s/config.yaml` 加 `tls-san: [<公网IP>]`,`sudo systemctl restart k3s`。
- 另外别忘了云安全组放行 `6443/TCP`(来源限本地公网 IP)。

### 坑 6:`kubectl apply` 报 openapi 下载超时
**现象**:`failed to download openapi ... context deadline exceeded`。
**原因**:apply 前要从远程 API server 下载完整 OpenAPI schema 做客户端校验,走公网太慢超时。
**解决**:
```bash
kubectl apply -f apps/root-app.yaml --validate=false     # 跳过客户端校验(服务端仍校验)
# 或直接在 k3s 服务器本机 apply,无公网延迟
```

### 坑 7:CI 明明成功,本地看不到回写提交
**现象**:Actions deploy job 绿色、`CONFIG_REPO` 也配了,但本地 `git log` 看不到 `deploy(dev)` 提交。
**原因**:CI 是往**远程** config 仓库 push 的,**本地克隆没 `git pull`**,看的是旧副本。
**解决**:`git fetch && git log origin/main`(或 `git pull`)看远程真实状态。

### 坑 8:`cicd-demo-root` 显示 `OutOfSync`
**现象**:三个环境应用 Synced/Healthy,但 root 是 OutOfSync。
**原因**:子应用是手动 `kubectl apply` 创建的,缺 root app 打的归属追踪标签,root 认为有差异。**不影响功能**。
**解决**:Argo UI 点 `cicd-demo-root` → Sync;或等 automated 策略自行 reconcile。

### (预防)坑 9:GHCR 镜像 private 导致 ImagePullBackOff
**现象**:Pod `ImagePullBackOff`。
**原因**:`ghcr.io/sdb-devops/baor-demo` 是 private,k3s 无凭证拉不到。
**解决**:把 `baor-demo` 仓库 **Packages** 里该镜像设为 public;或给命名空间配 GHCR pull secret:
```bash
kubectl create secret docker-registry ghcr-pull -n baor-demo-dev \
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<PAT>
# 并在 CronJob 的 spec 加 imagePullSecrets: [{name: ghcr-pull}]
```

### (安全)坑 10:kubeconfig 误提交
**提醒**:本地保存的 `k3s.yaml` 含集群管理员凭证,**切勿 commit**。加入 `.gitignore`,`git add` 时避免 `git add .` 把它带上。

---

## 日常使用速查

| 操作 | 命令 |
|------|------|
| 部署 dev | `git switch dev && git push` |
| 晋升 test | `git switch test && git merge dev && git push` |
| 晋升 prod | `git switch prod && git merge test && git push` → GitHub Actions 点 Approve |
| 发布版本 | `git tag v1.2.3 && git push origin v1.2.3` |
| 回滚 | 在 config 仓库 `git revert <deploy commit> && git push` |
| 看部署状态 | `kubectl get applications -n argocd` |
