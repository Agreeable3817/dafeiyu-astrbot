# 云端验证记录

日期：2026-11-08 UTC。执行器 Linux x86_64，Python 3.12，Docker 29.9.2，Compose 2.40.3；不是用户的 Mac mini。未使用真实 API Key、QQ 凭据或私人聊天；没有启动 QQ 或调用模型。

## 已执行

| 检查 | 命令/入口 | 结果 |
|---|---|---|
| Compose 插值/结构 | `docker compose --env-file deploy/macos/.env.example -f deploy/macos/docker-compose.yml config --quiet` | 退出 0 |
| 实际部署 CLI | `bash deploy/macos/deploy.sh check`（本地离线占位配置） | 退出 0，不输出配置/Key |
| Shell 语法 | `bash -n deploy/macos/deploy.sh deploy/macos/backup.sh` | 退出 0 |
| Python 语法 | `python -m py_compile deploy/macos/ops.py deploy/macos/journal/*.py plugin/astrbot_plugin_bili_activity/*.py` | 退出 0 |
| 上游部署体检 | `python deploy-kit/preflight.py --json` | 退出 0，`ok=true` |
| 镜像架构 | 实际运行 `bash deploy/macos/deploy.sh check-arm64` | 退出 0，两固定 ARM64 digest 均存在 |
| Python ARM64 依赖 | pip 跨平台下载锁定直接依赖 | 15 个 ARM64/通用 wheel 下载成功；jieba 0.42.1 sdist 下载并检查纯 Python setup.py，详见 ARM64.md |
| 初始化真实入口 | `bash deploy/macos/deploy.sh init` | 退出 0，拉取三个固定提交、核对 schema 哈希、生成六个插件及新配置/人格；没有启动容器 |
| 禁止重复初始化 | 再次执行 `init` | 按预期退出 1，拒绝覆盖已有数据 |
| 上游真实配置验证器 | 将生成的 LivingMemory 配置传给其固定版本的 `validate_config`，仅替换 logger | 通过 Pydantic 验证，scope=session；没有加载模型 |
| 防 amd64 回退 | 在云端 x86_64 daemon 调用 `start` | 按预期退出 1：`Production start requires a native ARM64 Docker daemon; no amd64 emulation fallback`，未起容器 |
| 部署/归档专项 | `python -m unittest discover -s tests -p 'test_macos_*.py' -v` | 21 项，全部通过 |
| 最终全量回归 | `python -m unittest discover -s tests -p 'test_*.py' -q` | 退出 0：`Ran 910 tests in 44.174s`，`OK (skipped=2)` |
| Git 隔离 | `git check-ignore` 检查 `.env`、模拟数据路径和备份路径 | 均被忽略；不会进入提交 |

仓库测试包含真实本地 HTTP/MCP 服务，沙箱内 socket 权限不足会使部分异步测试阻塞。已诊断并在同一云端执行器的获准沙箱外运行回归，而非更改测试以跳过。上游体检的端口探测也使用该执行方式。

专项覆盖：正确配置及低资源默认；无效输入/令牌/时间/会话拒绝；初始化幂等保护；独立数据库、情绪文件、主动联系状态和配置恢复；含已提交 WAL 的快照；停止失败/数据库损坏/服务恢复失败；原先停止的服务保持停止；并发操作锁；归档去重和并发 SQLite 写入；访客/群/其他平台隔离；损坏数据包/镜像包/越界路径/符号链接拒绝；恢复目标已存在拒绝覆盖；升级失败后还原迁移前数据库和旧镜像 ID；成功升级保留配置和服务运行状态。

Docker 调用在上述操作单测中由状态化替身模拟；SQLite、文件归档、校验和、WAL、恢复和线程并发使用真实文件与数据库。因此可以证明管理流程的数据/状态行为，**不能证明真实 Docker 的停止信号、镜像 save/load、容器重建或 QQ 交付已成功**。

## 实际尝试但未完成

尝试拉取同版本 AstrBot 的 amd64 子镜像（`sha256:60de50b63ad84b4e6c0ca4ca43c46d0ff4a55c1edf3af74703a397bbd71a4dbd`）并构建独立 `dafeiyu-macos:cloud-amd64-test`，没有更改生产 ARM64 配置。

1. 第一轮在 VFS 复制镜像层时失败：`ResourceExhausted ... no space left on device`。
2. 将 requirements 从 COPY 层改为只读构建挂载，减少一层完整文件系统复制后重试；pip 已解析/获取依赖，但安装阶段仍失败：`OSError: [Errno 28] No space left on device: '/usr/local/lib/python3.12/site-packages/pytz'`。

执行器只有约 8GB 可写磁盘，Docker VFS 复制官方镜像层导致容量不足。只按精确缓存 ID 清理了本次构建产生的缓存；没有运行全局 Docker prune，没有操作用户的 Mac 或其他服务。没有生成可运行的测试镜像，没有执行到 Dockerfile 末尾的 `pip check` 与 import smoke；这些检查在 Mac 实际构建时仍必须通过。

未取得项目 Wiki 正文：Wiki Git 克隆失败，网页请求也未取得内容。没有编造其配置。使用了仓库内 README/QUICKSTART/docs/deploy/deploy-kit 和依赖仓库的实际固定版本源码/文档，来源在 README-macos.md 中列出。

## 未覆盖和实机风险

- Mac 原生 ARM64 镜像构建、native 包导入、容器启动/信号处理、NapCat 镜像内工具和实际账号配置优先级。
- QQ 扫码、快速登录、WS 断线重连、消息实际送达、跨夜免打扰及跨重启排期。
- DeepSeek/硅基流动真实 Key 的可用性、延迟、调用成本、记忆召回质量与长期情绪行为。
- Docker 镜像备份/加载，以及整套服务在新目录的真实在线恢复；数据恢复算法已测，容器行为未测。
- Mac 常驻约 2GB/峰值约 3GB 目标、Docker VM 开销及与现有 ChatGPT/SillyTavern/Stash 并行运行的响应。不能用配置上限代替实际测量。
- 原文归档只保存 AstrBot 收到的管理员私聊组件和出站钩子内容，不下载附件二进制，不证明送达；源平台未交给 AstrBot 的消息无法追溯。

## 逆向自审

“测试通过但语义仍可能错误”的重点路径及处理：

1. 面板健康不等于 QQ/API 健康：文档明确拆开，真实消息验收留待 Mac。
2. 上下文裁剪不等于完整聊天留存：增加独立 SQLite 原文归档，保留每个出站阶段的证据边界。
3. 仅回滚镜像可能读到新版数据库：保留完整迁移前数据，失败回退和独立新目录恢复均覆盖旧数据/旧镜像 ID。
4. 复制正在写的 SQLite、漏掉 WAL/FAISS/登录态：停本项目两容器后整棵数据复制，对副本校验，WAL 测试覆盖。
5. 备份失败意外启动本来停止的服务：记录运行集合，恢复只针对原集合，异常路径已测。
6. 健康检查假设不存在的命令：检查 NapCat 基础 Dockerfile 后使用已安装的 curl；镜像实际执行仍需实机验证。
7. `.env` 初始参数覆盖面板后续修改：仅 init 写配置，start 不重置；后续策略以持久化面板设置为准。
8. 备份含隐私或恢复越界：备份全部留在忽略目录并限制权限；拒绝外部链接/越界路径；外部备份需用户的加密存储。

本次只新增 `deploy/macos/`、`tests/test_macos_deploy.py`、`tests/test_macos_journal.py` 和带上游许可说明的 schema fixture；未修改现有服务配置、插件业务逻辑或部署文件。
