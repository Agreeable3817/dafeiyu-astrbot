# Mac mini M2 · 8GB 隔离部署

这是待在 Mac 执行的部署包。云端已验证范围见 [VALIDATION.md](VALIDATION.md)，原生架构证据见 [ARM64.md](ARM64.md)。没有在你的 Mac 上部署，也没有登录 QQ 或调用付费 API。

## 1. 实际架构与固定版本

```text
QQ ⇄ NapCat 4.18.37 ⇄ ws://astrbot:6199/ws ⇄ AstrBot 4.29.0
                                                ├─ DeepSeek 官方 API / deepseek-chat
                                                ├─ 硅基流动 / BAAI/bge-m3 / 1024维
                                                └─ 本地 SQLite、FAISS 索引、配置与聊天归档
```

只运行两个容器。插件装在 AstrBot 内，不各起容器。

| 部件 | 选择与理由 |
|---|---|
| AstrBot / NapCat | 沿用项目 `deploy/` 固定版本；使用 ARM64 子 manifest 的 digest，避免标签漂移 |
| 大肥鱼 | 本仓库 face_convert、schedule_awareness、affective_core 与人格 |
| LivingMemory | 稳定版 **2.9.1**，固定提交，文档/关键词/向量长期记忆 |
| proactive_chat | 稳定版 **v1.2.6**，固定提交，不采用 v1.3 测试版 |
| 情绪 | 本项目 v3.1.0 中的 affective_core v0.1.0；不用旧 EmotionAI，避免双注入 |
| 聊天原文 | 附带轻量 `dafeiyu_journal` SQLite 插件，独立于模型上下文裁剪 |

版本及配置 schema 哈希见 `sources.lock.json`。Python 直接依赖见 `requirements-extra.lock.txt`，同时保留上游 requirements 约束，冲突会使构建失败。底层 Python 依赖由固定基础镜像提供。所有配置字段来自已阅读的上游源码/schema；不运行仓库的 `apply_sf_embedding.py`，它是针对维护者现有配置的迁移器，不适合新安装。

**没有 Ollama、torch 模型服务、Embedding 下载或本地推理。** FAISS 是内存中的向量索引库，NumPy 是数值库，均不运行模型。DeepSeek 负责聊天、记忆总结、情绪评估、主动消息生成；硅基流动只负责向量生成。对话和记忆相关文本会分别发送到这些 API。

## 2. 资源预算与隔离边界

| 容器 | 内存软预留 | 内存硬上限 | CPU 上限 |
|---|---:|---:|---:|
| AstrBot（含全部插件） | 768MiB | 1536MiB | 1 核 |
| NapCat（含 QQ/Xvfb） | 512MiB | 1280MiB | 1 核 |
| 合计 | 1280MiB | **2816MiB，约 2.95GB** | 2 核 |

常驻约 2GB、短时不超过 3GB 是**容器负载目标，未经 M2 实测**。2816MiB 硬上限不包含 Docker 虚拟机、磁盘缓存、宿主系统和其他应用；不能把它当成整台 Mac 的内存承诺。软预留不代表预先分配内存，也不保证常驻用量。关闭容器额外 swap，避免持续换页拖慢 Mac；这不修改 macOS swap 设置。

已限制 BLAS/OpenMP 为单线程、媒体并发为 1、模型上下文为 30 轮、记忆会话为 4、单次总结为 1 段、向量批次为 2。默认文本交流，不启用视觉/ASR/TTS、浏览器、模拟器、B站服务、观测台、联网搜索、图谱/记忆原子、自动记忆巩固和 Agentic Recall Beta。`face_convert` 仍做 QQ 表情转换；没有百炼 Key 时不能理解图片/语音。

**不要为了逼近 2GB 目标继续压低限制。** 若有 OOMKilled 或反复重启，先停本项目，检查活动监视器、`docker stats` 和日志；可分别把 `.env` 的内存上限增加 256MiB，再 `bash deploy.sh start` 重建生效，但须重新评估 3GB 目标。脚本拒绝低于每容器 1024MiB 的配置。优先限制输入与后台功能，内存不足时停止本项目。

项目固定名为 `dafeiyu-macos-m2`，独立 bridge 网络，不使用 host 网络、固定容器名、外部卷或 Docker socket。所有宿主挂载均为本目录 `.runtime/data` 下的新目录。端口为 `127.0.0.1:16185` 和 `127.0.0.1:16099`；6199 不映射到宿主。脚本会检查同名项目的 working_dir，发现属于其他目录就拒绝操作。

不会修改 ChatGPT、SillyTavern、Stash 的数据、容器、端口、Compose 项目或全局 Docker 设置。不得执行 `docker system prune`、`docker volume prune` 或清空已有 Docker 磁盘。共享同一 Docker 虚拟机的服务仍会竞争资源；本部署包不会自动更改 Docker Desktop 的全局内存配额。

## 3. 回家后首次启动

需要：macOS Apple Silicon、已可用的 ARM64 Docker Desktop（或你现有的 ARM64 Docker 环境）、Compose v2.24+、Git、Python 3.10+。Python 只用于管理脚本，不在宿主安装模型或插件依赖。没有 Python 时自行从 python.org 或现有包管理器安装；脚本不改你的 Python 环境。

先检查当前 Docker 上下文和端口，不切换或重建既有 Docker 环境：

```bash
docker context show
docker info --format '{{.Architecture}}'
docker compose version
python3 --version
lsof -nP -iTCP:16185 -sTCP:LISTEN
lsof -nP -iTCP:16099 -sTCP:LISTEN
```

`Architecture` 必须为 `aarch64` / `arm64`。端口无输出表示没有监听进程；占用时在新项目 `.env` 中换端口，不停止已有服务。建议为镜像、首次构建、image vault、数据快照和恢复演练预留 **20GB 以上**磁盘空间；长期聊天与备份增长后需要更多。

```bash
git clone --branch feat/macos-m2-deployment https://github.com/whyever77/dafeiyu-astrbot.git
cd dafeiyu-astrbot/deploy/macos
cp .env.example .env
chmod 600 .env
```

若分支尚未推送，先用交付的完整 checkout / Git bundle 获取本分支，见交付说明；不能直接从 GitHub 克隆不存在的分支。

在本机编辑 `.env`，填入 DeepSeek Key、硅基流动 Key、主人 QQ、AstrBot 密码，以及两个**不同**的随机 token。随机 token 可分别用 `openssl rand -hex 24` 生成。不要把值发到聊天、终端共享记录或 Git。值按字面读取，不执行 Shell；不要使用 `$`、反斜杠或多行值。`OWNER_UMO` 可以先留空，`BOT_QQ` 可在首次扫码后填写小号。

```bash
bash deploy.sh check
bash deploy.sh check-arm64
bash deploy.sh init
bash deploy.sh build
bash deploy.sh start
bash deploy.sh status
```

`init` 只接受不存在的数据目录，重复运行拒绝覆盖。它拉取固定提交、组装六个插件、写入新配置，并生成 `.runtime/persona.txt`。`prepare` 只更新构建输入，不改已安装插件/数据。`build` 只构建本项目镜像和拉取固定 NapCat；`start` 不自动构建，不允许 amd64 回退，并等待健康检查。镜像构建失败时不启动服务。

### 本机面板接线

1. 打开 AstrBot `http://127.0.0.1:16185/`，用户名 `astrbot`，密码为 `.env` 中所设值。密码采用上游 PBKDF2 格式保存，并保留该版本要求的兼容 MD5 字段；不记录明文到控制台。
2. 检查默认模型为 `deepseek-chat`、API Base 为 `https://api.deepseek.com/v1`。检查嵌入提供商为 `sf-bge-m3`，模型 `BAAI/bge-m3`、1024 维、Base `https://api.siliconflow.cn/v1`；分别用面板测试。Key 只保存在本机 `.env` 和忽略的持久化配置中。
3. 新建人格，粘贴 `.runtime/persona.txt`，设为默认人格（agent_runner 的 persona）。不要把其中依赖百炼/模拟器的能力描述当成已启用功能；可在人格末尾注明“本实例为文本陪伴模式，视觉、语音、手机及B站活动未启用”。
4. 打开 NapCat `http://127.0.0.1:16099/webui`，输入 `.env` 中的 NapCat token，在本机扫码登录机器人小号。登录凭据会保存在 `.runtime/data/napcat/qq`；不能从其他系统迁移现有 QQ 登录态。不要让同一机器人小号在两个实例同时登录。
5. NapCat 的 OneBot WebSocket **客户端**已预置 `ws://astrbot:6199/ws`、array 消息格式和共享 OneBot token。AstrBot 机器人 ID 为 `qq`，监听 `0.0.0.0:6199`。在 NapCat WebUI 确认实际加载的账号配置也使用这条连接；若它生成了账号专属配置，在 WebUI 中修正该账号配置，不覆盖其他实例配置。
6. 主人私聊 `/sid`，把得到的精确 UMO 填入 AstrBot 访问白名单、LivingMemory 的会话设置（scope 已为 session）、affective_core 的 `owner_scope`，以及 proactive_chat 私聊 `session_list`。通常为 `qq:FriendMessage:<主人QQ>`，但以实际 `/sid` 为准。初始允许管理员私聊、拒绝群聊；**UMO 列表为空时不会主动发送**。
7. 重载相应插件或 `bash deploy.sh stop` 后 `bash deploy.sh start`。首次正常交流后执行一次 `bash backup.sh`，再做下文的恢复演练。

`BOT_QQ` 写入小号后，Compose 会给 NapCat 提供上游确实使用的 `ACCOUNT` 与 `NAPCAT_QUICK_ACCOUNT`，便于重启快速登录；固定 hostname 减少设备指纹变化。需要 `start` 重建一次生效，仍可能因 QQ 风控要求重新扫码，不能保证永久免扫码。

## 4. 持久化、聊天原文与可配置策略

```text
.env                              # 本机密钥和首次初始化参数，600 权限
.runtime/
  installation.json               # 本机安装版本与固定源码提交
  persona.txt                     # 可导入人格正文
  data/
    astrbot/                       # 整个 /AstrBot/data
      data_v4.db                   # 对话、人格、定时任务等
      cmd_config.json             # 模型/平台/访问控制/管理配置
      config/                      # 各插件配置
      plugins/                     # 插件代码与 face_convert/config.json
      plugin_data/
        astrbot_plugin_livingmemory/     # SQLite、FAISS索引、来源与运行状态
        astrbot_plugin_affective_core/   # affective.db、情绪和牵挂账本
        astrbot_plugin_proactive_chat/   # session_data.json、覆盖设置、调度状态
        astrbot_plugin_dafeiyu_journal/   # chat.db，原文归档
    napcat/config|qq|plugins/       # 连接配置、登录态、插件目录
  backups/                         # 数据包、校验文件、去重镜像 images/
```

挂载整个 data 目录，不只挂载一个 SQLite 文件；WAL、SHM、FAISS 和插件辅助状态一起保存。容器删除/重建不会删除这些目录。不要手工删除它们，也不要在应用运行时直接复制单个 `.db` 作为备份。

本地聊天归档只记录 aiocqhttp 的管理员私聊，不归档群或访客。记录入站组件原文、准备发送的组件，以及发送后钩子事件；**钩子运行不证明 QQ 接收成功**。相同平台消息 ID/阶段去重，记录不会自动删除。归档独立于 30 轮模型上下文和 LivingMemory 的短期缓冲。它不额外下载图片/语音/文件的二进制附件，临时 URL 可能失效；文本原文和平台提供的组件元数据会落盘。未进入 AstrBot、被协议端丢弃的消息无法补录。若以后要求永久保存附件，应单独设计有磁盘配额的附件归档，不能把默认临时缓存当档案。

`.env` 的策略参数仅用于**首次 init**；之后以 AstrBot 插件面板中的持久化配置为准，脚本不会在启动时重置你的修改。`.env` 的端口/资源/镜像标签/`BOT_QQ` 属于 Compose 参数，重新 `start` 才生效。轮换 API Key 时修改面板中相应 provider，并同步本机 `.env`；不要重新 init。

| 策略 | 默认 | 后续修改位置 |
|---|---|---|
| 时区 | Asia/Shanghai | AstrBot 系统时区；Compose TZ 与情绪作息按上海时间 |
| 夜间免打扰 | `23-8` | proactive_chat → friend_settings.schedule_settings.quiet_hours |
| 主动间隔 | 120–240 分钟 | 同处 min_interval_minutes / max_interval_minutes |
| 连续未回复上限 | 2 次 | 同处 max_unanswered_times；不要设成 0（无限） |
| 启动自动排期 | 等待 30 分钟 | friend_settings.auto_trigger_settings；真正发信仍受间隔/DND 限制 |
| 群主动联系 | 关闭 | group_settings.enable；初期保持关闭 |
| 长期记忆 scope | session | LivingMemory filtering_settings.memory_scope_mode |
| 总结触发 | 10 轮 | reflection_engine.summary_trigger_rounds |
| 自动删除低重要性记忆 | **关闭** | forgetting_agent.auto_cleanup_enabled |
| 可选清理条件 | 90 天、重要性低于 0.3 | cleanup_days_threshold / cleanup_importance_threshold |
| 原文聊天归档 | 不自动删除 | chat.db；与模型记忆清理无关 |

长期记忆默认保留，重要性仍按插件机制衰减；“不常召回”不代表记录已删除。若明确需要控制记忆体量，先备份，再在面板启用自动清理并设置保留条件。它是低重要性记忆的条件清理，**不是严格删除所有超过 N 天记录**，也不会清理独立聊天档案。LivingMemory 内部备份默认保留 7 天，仅作额外保护；不能替代整套备份。

情绪插件采用 `sync_pre`、DeepSeek 提供商、2.5 秒评估超时与确定性快筛回退，情绪/牵挂存 SQLite；它是本仓库当前版本中的新模块，真实长期效果仍需验收。可在面板切 `math_only` 降低额外 API 调用（纯数学状态更新，不是本地模型）。禁止同时安装 EmotionAI。

### 实验性功能如何启用

先完成基础验收和整套备份，再在插件面板逐个开启并观察一天：

- 图谱：LivingMemory `graph_memory.enabled=true`；需要额外内存/API开销。记忆原子 `atom_enabled` 与 Agentic Recall Beta 保持关闭，除非另行验证。
- Agentic Recall Beta：`agent_tools.enable_agentic_recall_beta=true`，按插件文档设置调用次数和预算；默认关闭。
- 记忆巩固：`memory_consolidation.enabled=true`；会改变记忆结构，应先完成恢复演练。
- B站/浏览器/模拟器/生活节律/自主活动：本包不安装其插件和服务。参照仓库对应文档另行评估资源和账号授权，不要直接复制维护者的运维配置。
- 不安装 `deploy/patches/proactive_chat` 的增量补丁：其 README 明确要求另一特定源码基线，不能直接覆盖稳定版 v1.2.6。本部署的标准低频主动聊天不需要它。

## 5. 健康、自动重启、日志

```bash
bash deploy.sh status
docker compose -p dafeiyu-macos-m2 --env-file .env -f docker-compose.yml logs --tail 100
```

AstrBot 健康检查请求容器内 6185 HTTP；NapCat 使用其基础镜像确实安装的 curl 检查 6099 WebUI，不假定存在独立 node 命令。启动宽限 180 秒、每 60 秒一次、连续 5 次失败才 unhealthy。**这些检查不代表 QQ 已登录、WebSocket 已接通或 API 可用**。QQ 掉线需要本机面板和收发消息验收；NapCat WS 设置 10 秒重连，但上游已记录过不能自动恢复的情况，必要时在 WebUI 重开该连接。

`restart: unless-stopped` 负责进程退出和 Docker 重启后的恢复；Docker 不会仅因 unhealthy 自动重启容器。没有启用会导致重启风暴的 autoheal。手动 `stop` 后服务保持停止；开机恢复还要求你已有的 Docker 环境本身正常自启，macOS 睡眠也会中断陪伴。

Docker 控制台日志各 10MB × 3 个轮转文件。AstrBot 文件日志/trace 默认关闭，聊天档案不依赖这些诊断日志。NapCat 可能写额外内部日志到持久化目录，定期用 `du -sh .runtime/data/napcat/*` 检查体积，不批量删除登录目录。日志可能含 QQ ID、QR 登录信息和聊天内容，不提交或公开上传。

## 6. 一致性备份与自动计划

```bash
bash backup.sh
# 使用脚本输出的实际文件名：
bash deploy.sh verify /绝对路径/备份.tar.gz
```

备份先按当前容器的**精确镜像 ID**执行 `docker image save`，放在 `.runtime/backups/images/` 去重保存并计算 SHA256。然后记录原先正在运行的服务，停本项目两个容器，拒绝 OOM/强杀/仍运行的状态；在停机期间复制全部数据、登录态、配置、密钥和管理脚本到暂存目录，对复制的全部 SQLite 执行 `PRAGMA integrity_check`，保留 WAL/SHM 与非数据库索引文件，生成文件级 SHA256 清单并压缩。仅忽略 QQ 目录下临时的 SingletonLock/SingletonCookie/SingletonSocket 符号链接；其余链接或特殊文件会使备份失败，不跟随外部路径。

最后只启动备份前在运行的服务，并等待健康检查；恢复启动失败、复制失败、压缩失败或校验失败均返回非零，不发布正式备份包。临时停机包括数据复制和压缩时间，大数据可能需要数分钟；镜像导出在停机前完成。Ctrl-C/SIGTERM 会尝试相同的服务恢复，SIGKILL、宿主断电和磁盘硬故障无法保证自动恢复。

`.operation.lock` 阻止脚本间并发。不要同时在面板改配置、手工执行 Compose 或运行第二个管理员脚本。异常断电遗留锁时先确认没有该脚本运行，再仅移除本项目的锁目录。

**备份集合 = `.tar.gz` + 对应 `.sha256` + 同级 `images/` 下引用的 `.tar` 及 `.sha256`。** 一起复制到本地加密外置磁盘或加密备份系统，不能只拿走数据包。备份包含 API Key、QQ 登录态和私聊原文，脚本权限为 600/700，但它没有额外加密；依赖 FileVault/加密目标保护静态数据。校验和防损坏，不证明来源可信，只恢复你自己保存的备份。

默认不自动删除任何历史备份/镜像。建议每日一份，保留最近 7 份和每次升级前一份；确认外部副本可恢复后人工清理旧备份。共享的 `images/` 不能随意清空，否则旧数据包无法恢复原镜像。

自动备份可在**你的用户 crontab 中追加一行**（先 `crontab -l` 查看，不用管道覆盖原任务）：

```cron
15 4 * * * cd /绝对路径/dafeiyu-astrbot/deploy/macos && /bin/bash backup.sh >> .runtime/backup.log 2>&1
```

macOS 的 cron PATH 很小，需要在该用户 crontab 顶部按现有安装实际路径补 `PATH`（Apple Silicon Homebrew 常为 `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`）。不要更改其他现有行。Mac 睡眠时 cron 不保证补跑，Docker 必须已运行；每天检查 backup.log 和最新备份时间。此操作仅由你在本机添加，本包不会安装或覆盖系统任务。backup.log 本身需要人工按大小归档。

## 7. 经自动化验证的恢复流程

云端自动化已验证 SQLite/WAL 数据、情绪/主动状态的文件完整恢复、损坏包拒绝恢复、目标已存在拒绝覆盖；镜像加载和真实 QQ 恢复仍需本机验收。

```bash
# 在原部署目录执行，替换为一个明确选择的历史备份；不要使用不确定的“最新”匹配。
bash deploy.sh verify /绝对路径/backups/所选备份.tar.gz
bash deploy.sh restore /绝对路径/backups/所选备份.tar.gz "$HOME/dafeiyu-restored-20261108"
bash deploy.sh load-images /绝对路径/backups/所选备份.tar.gz

# 确认新目录完整后，移除原项目的容器/网络以释放项目名和端口；原数据仍保留。
bash deploy.sh retire

cd "$HOME/dafeiyu-restored-20261108"
bash deploy.sh check
bash deploy.sh start
bash deploy.sh status
```

恢复目标必须**不存在**，恢复先在新暂存目录校验路径、文件/镜像 SHA256 和 SQLite 完整性，再原子改名。它不覆盖旧目录，也不会自动启动恢复的数据。`load-images` 读取镜像 vault；恢复生成的 `.runtime/image-lock.json` 使用原镜像 ID 与 `pull_policy: never`，`start --no-build` 不会悄悄拉新版。`retire` 仅对经过目录归属检查的本项目执行 `compose down`，没有 `-v` 或全局清理。

恢复后的自包含目录可直接启动；如要继续升级，需要回到完整 Git checkout 的部署目录并按已验收的数据迁移流程操作，不在孤立的恢复目录运行 init。若新实例验收失败，先在新目录 `retire`，再回原目录 `start`；原数据和容器镜像仍保留。不要让同一小号双实例同时上线。

## 8. 日常启停、升级和回滚命令

```bash
bash deploy.sh start            # 启动/按 Compose 资源或端口变更重建；不重置插件配置
bash deploy.sh stop             # 只停止本项目，保留所有数据
bash deploy.sh status
bash backup.sh
```

升级只在 Mac 执行。先备份旧状态，再拉取**已经审查过的明确提交**，不要无差别跟随 latest，也不要在面板独立升级这几个已固定版本的插件：

```bash
bash backup.sh
# 回仓库根目录：
cd ../..
git fetch origin
git switch --detach <已经审查的新提交SHA>
cd deploy/macos
bash deploy.sh check
bash deploy.sh prepare
bash deploy.sh build
bash deploy.sh upgrade
bash deploy.sh status
```

`build` 在旧容器运行期间准备新镜像，旧容器仍使用其旧镜像 ID。`upgrade` 再备份一次精确旧镜像与数据，停服务，复制整棵数据到暂存目录，替换本包管理的插件代码并保留 face_convert 的 `config.json`，其他插件配置和 plugin_data 原样保留；原数据目录保留为 `.runtime/pre-upgrade-*`。不删除你另装的插件。所管理插件目录中的自定义源码/额外运行文件只在旧数据目录和备份中保留，不自动合入新插件；不要在插件代码目录存私人附件或独立数据库。

启动新版本并等待健康检查失败时，脚本停止新容器，把失败数据留在 `.runtime/failed-upgrade-*`，恢复迁移前数据及旧镜像 ID，再尝试恢复之前在运行的服务，并返回非零。停机状态的服务不会被顺便启动。故障可能阻止自动回退，仍需查看状态并使用整套历史备份恢复。

**人工回滚使用第 7 节 restore 流程**，选择升级前的明确备份。不要仅把镜像标签改旧然后读取新数据库；不要把 `.runtime/pre-upgrade-*` 直接覆盖到正在运行的服务。Git 源码回滚可在备份后 `git switch --detach <旧提交SHA>`；运行数据回滚仍使用完整历史备份，而不是 `git reset --hard`。

## 9. Mac 实机验收清单

- [ ] 两镜像及构建后镜像为 arm64；无 amd64 模拟运行。`start` 的架构检查通过。
- [ ] 本机只有 16185/16099 的回环监听，局域网其他设备连不上；既有服务端口/数据不变。
- [ ] 两容器 healthy；NapCat 完成扫码且 WS 连通；主人私聊“在吗”能收发，访客/群被访问控制拒绝。
- [ ] DeepSeek 与硅基流动面板测试通过；没有 Ollama/本地模型进程。
- [ ] 人格导入正确；`/心情` 可查询，重启后情绪状态仍在；没有 EmotionAI 双注入。
- [ ] 私聊十轮后 `/lmem status`、`/lmem summarize`、`/lmem search 关键词`；重启后召回能找回事实。
- [ ] 模型上下文超过 30 轮后，`chat.db` 旧文本仍在；它的出站状态没有冒充送达。
- [ ] `/sid` 已配置，2–4 小时观察主动消息；未回复达到上限停止；23:00–08:00 不发送；重启后任务/未回复状态恢复。
- [ ] `bash backup.sh`、`verify` 和独立新目录 `restore` 演练通过；镜像加载、重启、记忆/情绪/联系状态均恢复。必要时重新扫码。
- [ ] 连续一天空闲、普通私聊与总结/备份时采样 `docker stats` 和活动监视器；记录常驻/峰值，检查 OOMKilled/RestartCount 以及 ChatGPT、SillyTavern、Stash 的响应。约 2GB/3GB 的目标只有这一步能确认。
- [ ] Docker 重启后按 restart 策略恢复；手动 stop 不自启；自动备份次日实际生成，外部加密副本可读。

## 10. 文档依据

- 本项目 [README](../../README.md)、[deploy](../README.md)、[compose](../compose.yaml)、[Dockerfile](../Dockerfile)、[部署指南](../../docs/部署指南.md)、[deploy-kit](../../deploy-kit/README.md)、[主动补丁限制](../patches/proactive_chat/README.md)。调查基线 `be39b3a076e3051ed0da8930e6606b082482b914`。
- [AstrBot 4.29.0 源码](https://github.com/AstrBotDevs/AstrBot/tree/db4ce0c4b0ae94f443c641f0f78cc9228ddabbeb)：配置默认值、agent_runner、provider manager、OpenAI Embedding、密码格式、平台适配器与消息钩子。
- [LivingMemory 2.9.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/tree/1c6a471c9af9ae45a4ccb3a8cec5695c917de50b)：README、requirements、schema、configuration、memory_scope、初始化/FAISS 文档。需要独立的远端 Embedding Provider。
- [proactive_chat v1.2.6](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/tree/635fc18eeb84e3c7b58661425359244552286835)：README、requirements、schema、终止保存和消息发送钩子。
- [NapCat Docker](https://github.com/NapNeko/NapCat-Docker)：README、base/Dockerfile、入口脚本、模板与 AstrBot Compose；源码调查提交 `dc3ca1abc2bcb3b5f1e1087db12c1b8e337d243b`，镜像使用项目指定版本的固定 digest。仓库没有对应 v4.18.37 Git 标签，不能宣称现有入口脚本与该镜像逐字相同；账号配置加载/健康检查需实机核对。
- 项目 Wiki 克隆请求失败（GitHub 返回需要认证/不可获取），网页请求也未取得正文；本包没有声称已经读到 Wiki。现有仓库内文档已覆盖本方案的部署依据，Wiki 内容如之后可用仍需补查。
