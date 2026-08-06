# mjy1113451/astrbot_plugin_b- 项目概述

## 1. 项目简介

**bilibili_learning_bot / BiliLearn** 是一个面向 B 站的 AI 学习互动机器人，用于自动浏览视频、提取知识、管理知识库、评论/私信互动，并提供 Web 管理面板、桌面版与 Docker 部署能力。

## 2. 技术栈

- **主要语言**
  - Python：核心业务逻辑、AI 调用、B 站 API、知识库、CLI、桌面启动器
  - JavaScript / HTML：Web 管理面板、前端交互、可视化学习产物
  - Shell / Batchfile：安装、构建、跨平台启动脚本
  - Dockerfile / docker-compose：容器化部署

- **后端与运行环境**
  - Python 应用架构
  - Flask 风格 Web 管理面板
  - CLI 命令行交互入口
  - Windows 桌面启动器与 PyInstaller 打包
  - Docker / docker-compose 部署
  - Termux Android 环境支持

- **AI 与自动化能力**
  - 大语言模型调用与降级机制
  - 视频内容理解、总结、问答、知识辅导
  - 评论、私信、@通知自动回复
  - ASR 语音识别，可接入 FunASR / Whisper
  - Agent 式任务规划与自主学习闭环

- **平台集成**
  - Bilibili API 客户端
  - 字幕、评论、私信、推荐流等能力封装
  - 开放平台桥接 `ob_bridge/`
  - 本地通知、备份恢复、用户数据管理

## 3. 项目结构

### 根目录

- `main.py`  
  项目主入口，负责 CLI 菜单、机器人启动与主流程调度。

- `desktop_app.py`  
  Windows 桌面版启动器，支持托盘图标、自动打开 Web 面板等能力。

- `BiliLearn.spec`、`build_windows_exe.bat`  
  PyInstaller 打包配置与 Windows 一键构建脚本。

- `Dockerfile`、`docker-compose.yml`  
  容器化部署配置。

- `config.example.json`  
  示例配置文件，用于指导用户初始化运行参数。

- `README.md`、`CHANGELOG.md`、`SECURITY.md`、`REFACTOR_PLAN.md`  
  项目说明、版本记录、安全说明与重构规划文档。

- `metadata.yaml`  
  项目元信息，可能用于插件系统或部署识别。

### `api/`

B 站 API 访问层，负责平台交互的底层封装。

- `client.py`：API 客户端
- `auth.py`：登录、鉴权、Cookie 相关逻辑
- `subtitles.py`：字幕获取与处理
- `throttle.py`：请求节流与风控控制
- `compat.py`：兼容性适配

### `brain/`

机器人“大脑”核心模块，采用多个 Mixin/子模块拆分复杂逻辑。

- `_brain_loop.py`：主循环
- `_brain_ai.py`：AI 调用与生成
- `_brain_learn.py`：学习与知识吸收
- `_brain_interact.py`：评论、私信等互动逻辑
- `_brain_history.py`：历史记录与上下文
- `_brain_journal.py`：日记与自我反思
- `_brain_curiosity.py`：兴趣探索
- `_brain_auto.py`：自动化行为
- `_brain_init.py`：初始化流程

### `cli/`

命令行应用模块。

- `app.py`：CLI 菜单、交互命令、启动流程组织

### `core/`

基础设施层，集中管理配置、全局状态、用户数据目录和平台行为。

- `config.py`：配置读取与校验
- `globals.py`：全局变量与运行状态
- `user_data.py`：用户数据路径与持久化
- `factory_reset.py`：恢复出厂设置
- `platform_actions.py`：跨平台操作封装
- `time_policy.py`：时间策略与运行节奏控制

### `knowledge/`

知识库与学习资料管理模块。

- `browse.py`：知识浏览
- `classifier.py`：内容分类
- `custom.py`：自定义知识
- `organize.py`：知识整理
- `revisit.py`：复习回顾
- `web_search.py`：Web 搜索辅助

### `ob_bridge/`

开放平台桥接模块，负责外部平台集成相关能力。

- `client.py`：开放平台客户端
- `config_bridge.py`：配置桥接
- `audit.py`：审计
- `ab_test.py`：A/B 测试
- `health.py`：健康检查
- `types.py`：类型定义

### `dev_refs/`

开发参考文档目录，包含配置系统、LLM 调用、Bilibili API、Web 面板 API、Web 搜索、文件 IO、服务模板和架构流程等说明。

### `assets/`、`app-icons/`

静态资源与应用图标目录，用于 Web 面板、桌面应用和打包产物。

## 4. 开发约定

- **分层清晰**  
  项目按 API 层、核心配置层、大脑逻辑层、知识库层、CLI 层、平台桥接层拆分，避免所有逻辑集中在入口文件中。

- **模块职责单一**  
  例如 `api/` 只处理 B 站接口，`core/` 只处理配置和基础能力，`brain/` 负责任务决策与 AI 行为，便于维护和替换。

- **Mixin/拆分式大脑设计**  
  `brain/` 中使用多个 `_brain_xxx.py` 文件拆分复杂行为，适合持续扩展学习、互动、记忆、反思等能力。

- **配置与用户数据分离**  
  提供 `config.example.json`，并通过 `core/user_data.py` 管理用户数据路径，降低升级、打包和隐私泄露风险。

- **兼容多种运行方式**  
  同时支持源码运行、CLI、Windows EXE、Docker、Termux，说明开发时需要注意跨平台路径、环境变量与依赖兼容。

- **重视稳定性与风控**  
  存在请求节流、API 降级、鉴权兼容、审计、健康检查等模块，开发新功能时应考虑失败重试、冷却、限流和异常兜底。

- **文档驱动开发倾向明显**  
  `dev_refs/`、`REFACTOR_PLAN.md`、`CHANGELOG.md` 等文件较完整，新增模块应同步补充开发说明和变更记录。

- **安全意识较强**  
  项目包含 `SECURITY.md`、审计模块、提示词注入防护和关键词过滤等设计，开发涉及外部输入、AI Prompt、评论回复时应进行安全校验。

- **前后端相对独立**  
  Python 负责后端与自动化逻辑，HTML/JavaScript 负责 Web 面板展示和交互，新增管理功能通常应同时补充后端接口与前端页面。

- **面向插件化和可扩展**  
  `metadata.yaml`、开放平台桥接、服务化模块和开发参考文档表明项目预期长期扩展，新增能力应优先设计为独立模块，而非硬编码进主流程。