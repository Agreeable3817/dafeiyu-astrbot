# ARM64 核查（2026-11-08 UTC）

工具：`docker manifest inspect`，真实访问 Docker Hub。原始 index 分别保存在 `astrbot-manifest.json`、`napcat-manifest.json`。

| 生产镜像 | linux/arm64 子 manifest | 结果 |
|---|---|---|
| soulter/astrbot:v4.29.0 | `sha256:66ae67e93be2996f9aaef29d90f659c0b7f1d245a45cda8bea40ebdd0f34d943` | 存在，Compose 构建基础直接固定此 digest |
| mlikiowa/napcat-docker:v4.18.37 | `sha256:5973e8f1b16c0c42cb72bb9db99d93d8143012dcdc1dd6f91257b48054eb302f` | 存在，Compose 直接固定此 digest |
| dafeiyu-macos:20261108 | 由以上 AstrBot ARM64 基础构建 | 生产只允许 ARM64 daemon；实际构建及运行未完成 |

两个 index 同时提供 amd64，`unknown/unknown` 项为附属 manifest，不当作可运行架构。不会设置 `platform: linux/amd64`，也不会以模拟运行掩盖架构缺失。

复核：`bash deploy.sh check-arm64`。该命令重新读取标签的 index，确认仍包含固定 ARM64 digest；不修改运行数据。生产 `build`/`start` 检查 Docker daemon 架构，`start` 还检查实际本地镜像架构。

没有第三个模型镜像、外部数据库镜像、备份 sidecar 或 autoheal 镜像。Dockerfile 的唯一 FROM 是上述 AstrBot ARM64 digest；构建时使用 Dockerfile frontend，不是常驻服务。

## Python 直接依赖

实际执行 pip 跨平台下载，目标 CPython 3.12、`manylinux2014_aarch64` / `manylinux_2_28_aarch64`，仅下载不执行 ARM64 二进制：

| 依赖 | 固定版本 | 下载验证 |
|---|---|---|
| networkx | 3.7 | 通用 wheel |
| jieba | 0.42.1 | 无官方 wheel；已下载并检查纯 Python sdist setup.py，无 C 扩展；固定基础镜像已包含它 |
| pytz | 2026.6 | 通用 wheel |
| aiofiles | 25.1.0 | 通用 wheel |
| aiosqlite | 0.22.1 | 通用 wheel |
| numpy | 2.5.4 | aarch64 CPython 3.12 wheel |
| pydantic | 2.14.1 | 通用 wheel；其 native pydantic-core 属于基础镜像的依赖，未单独运行 ARM64 验证 |
| quart | 0.22.0 | 通用 wheel |
| sqlalchemy | 2.1.4 | aarch64 wheel；asyncio 的 greenlet 属于基础镜像依赖，未单独运行 ARM64 验证 |
| apscheduler | 3.11.3 | 通用 wheel |
| fastapi | 0.143.0 | 通用 wheel |
| uvicorn | 0.54.0 | 通用 wheel |
| dashscope | 1.27.7 | 通用 wheel；默认不配置、不调用百炼 |
| imageio-ffmpeg | 0.8.1 | aarch64 wheel；实际强制使用基础镜像 `/usr/bin/ffmpeg` |
| aiohttp | 3.14.5 | aarch64 wheel |
| faiss-cpu | 1.14.3 | aarch64 abi3 wheel；避开 LivingMemory 文档警告的 1.14.2，不是 Embedding 模型 |

本仓库六个插件均为 Python 源码，其中 journal/schedule/affective 核心持久化使用 Python SQLite。源代码无“只允许 amd64”配置。向量模型在远端 API 运行，不占本机模型内存。

**边界：** manifest 与 wheel 存在不等于 Mac 实际加载成功。云端为 x86_64，不能替代 M2 原生运行验收；尝试构建同版本 amd64 测试镜像两次均遇执行器磁盘耗尽。Dockerfile 中保留 `pip check`、FAISS/NumPy/插件依赖 import 与索引创建检查，需在 Mac 构建时真正通过。完整 ARM64 运行、QQ 与 API 链路均未验证。
