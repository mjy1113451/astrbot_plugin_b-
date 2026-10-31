# bilibili_learning_bot

> **B站 AI 学习互动机器人** — AI 自动刷视频、学知识、评论互动、私信回复、自我进化
>
> 版本: **3.1.6** | License: MIT
>
> 本插件改写已经过原作者 xiaoyaya 同意：[bilibili_learning_bot](https://github.com/xiaoyaya191/bilibili_learning_bot)

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📺 **智能视频浏览** | AI 驱动 B站推荐流浏览，自动判断内容价值（评分 / 收藏 / 投币 / 点赞） |
| 📚 **知识库系统** | 自动归档高质量视频，多层分类 + 检索 + 复习回顾 |
| 💬 **评论互动** | 真实/模拟评论模式，AI 深度回复，支持图片分析 |
| 📩 **私信处理** | 自动回复粉丝私信，持久上下文 + 长期记忆，支持节奏控制 |
| 📡 **实时监听** | 独立监听引擎，只盯私信 + 评论实时 AI 回复，不刷视频不耗精力 |
| 🔔 **@通知响应** | 视频下评论 "@bot 总结这个视频"，自动识别并总结回复 |
| 🧬 **日记与自我进化** | 行为日志 + AI 自我反思 + 人格动态进化 |
| 🎙️ **ASR 语音识别** | 视频语音转文字（FunASR / Whisper，可选安装） |
| 🤖 **Agent 技能系统** | 自主规划目标 → 搜索 B站 → 看视频 → 总结知识，全自动闭环 |
| 🎓 **知识辅导** | AI 讲解 / 问答 / 二次创作 / 生成 HTML 学习卡片 |
| 🎨 **视频→网页** | 视频生成 PPT 风格 HTML（`services/video_to_ppt.py` 内置多套主题，含 Claude 主题） |
| 📊 **思维导图 & Word 导出** | 视频导出 `.mindmap.html` 与 `.docx` 文档 |
| 🔍 **深度研习** | 长视频多章节深研，证据链式总结（`services/deep_dive.py`） |
| 🎯 **智能兴趣引擎** | 多维评分 + 同义词 + 排除词 + 灵光一闪探索 + PsychoProfile 同步 |
| 😊 **AI 心情系统** | 动态心情影响互动风格，支持自定义 |
| 🏆 **干货点赞回顾** | 定期回顾收藏的干货视频，AI 复习（`services/like_review.py`） |
| 🔔 **本地提醒** | 桌面通知 + 待办提醒（`services/reminders.py`） |
| 🛡️ **安全审查** | 关键词过滤 + 政治敏感拦截 + 提示词注入防护 + 操作风控 |
| 🔄 **备用 API 降级** | 主 API 连续失败自动切换备用提供商 / 备用模型 |

---

## 🎮 指令速查

以下指令在 AstrBot 中直接发送（前缀可被 AstrBot 的唤醒词配置影响）。

| 指令 | 说明 |
|------|------|
| `/bili_start` | 启动后台学习机器人（每次启动前会重新校验核心依赖） |
| `/bili_stop` | 停止后台机器人（最多等待 10 秒收尾） |
| `/bili_status` | 查看后台机器人主循环状态 |
| `/bili_config` | 配置入口（提示使用 `/bili_config_set`，该子指令尚未实现，见下方说明） |
| `/bili_login` | B站扫码/验证码登录（**仅交互式终端可用**） |
| `/bili_kb_list` | 列出知识库分类与条目数量 |
| `/bili_kb_revisit` | 重温知识库 |
| `/bili_kb_organize` | 整理知识库 |
| `/bili_kb_custom` | 自定义知识管理（**仅交互式终端可用**） |
| `/bili_analyze <BV号/链接>` | 分析单个视频 |
| `/bili_up_learn <UID或名字>` | 学习 UP 主主页（默认取 10 个、跳过无字幕、评分 ≥ 7.0） |
| `/bili_video2html <BV号/链接>` | 把视频转为 HTML 学习页面 |
| `/bili_asr` | 切换 ASR 语音识别开关 |
| `/bili_quiet` | 切换安静模式 |
| `/bili_vision` | 切换封面分析开关 |
| `/bili_fast` | 切换快速模式（跳过模拟真人延迟） |
| `/bili_interest` | 兴趣设置（**仅交互式终端可用**） |
| `/bili_comment` | 评论设置（**仅交互式终端可用**） |
| `/bili_export` / `/bili_import` | 导出 / 导入配置（**仅交互式终端可用**） |
| `/bili_reset` | 恢复出厂设置（**仅交互式终端可用**） |

> 「仅交互式终端可用」的指令在 AstrBot 里发送只会得到提示，需要在服务器终端运行 `python main.py` 后进入对应菜单。
>
> ⚠️ 已知问题：`/bili_config` 会提示使用 `/bili_config_set <key> <value>`，但当前 `main.py` 并未注册该子指令。请改用 AstrBot 插件配置面板或终端 CLI 修改配置。

---

## 🧩 环境要求

- **AstrBot 核心版本**：`>=4.29.1,<5`（见 `metadata.yaml` 的 `astrbot_version`），低于该版本会在安装期被核心拒绝。
- **由核心提供的依赖**：`openai`、`pydantic`、`httpx[socks]`、`pillow`、`qrcode`、`python-docx`、`reportlab`。
  插件不在 `requirements.txt` 中声明这些包（避免触发核心依赖保护机制，见 issues #18 / #20），
  因此它们必须由 AstrBot 核心提供。每次执行 `/bili_start` 都会校验必需包的**导入与最低版本**，
  版本约束统一维护在 `main.py` 的 `_CORE_PROVIDED_DEPENDENCIES`，按 PEP 440 比较
  （包括 epoch、预发布、开发及本地版本）。缺失、版本过低或无法解析时会拒绝启动并列出具体原因；
  补齐依赖后可直接重新发送 `/bili_start`，无需重启 AstrBot。
- **SOCKS 代理（可选）**：缺少 `socksio` 时仅输出警告，不阻止直连或 HTTP 代理用户启动。
  如需使用 SOCKS 代理，请先在 AstrBot 运行环境中安装 `httpx[socks]`。
- **插件专属依赖**：见 `requirements.txt`，均使用兼容区间而非精确锁定。
- **可选依赖**：ASR（`funasr` / `torch` / `torchaudio`）与向量检索（`sentence-transformers` / `numpy`）
  在 `requirements.txt` 中以注释形式提供，按需自行安装。

---

## 🛠️ 安装依赖冲突排查（issue #25）

若 AstrBot v4.29.3 报 `openai==2.33.0` 与核心保护的 `openai==2.46.0` 冲突，
**不要降级核心 OpenAI 包或关闭依赖保护**。旧版 3.1.2 曾锁定该版本，当前清单已移除该声明；
修复版本标识为 **3.1.6**（详见 [更新日志](CHANGELOG.md)）。冲突日志本身不能证明下载来源或缓存状态。

1. 从本仓库 `https://github.com/mjy1113451/astrbot_plugin_b-` 的 `main` 更新或重新下载安装包，
   核对包内 `metadata.yaml` 的 `version` 与 `VERSION` 都是 `3.1.6`。若商店仍显示旧版本，刷新来源并使用强制更新；
   商店/自定义源若配置了固定 `download_url`，维护者还需更新该地址，单改版本号不会替换固定旧包。
   自行制作标签/发行包时应从包含此修复的提交构建，不能复用旧源码包。
2. 在日志给出的**实际安装目录**核对 `requirements.txt`，不要只看 GitHub 页面。
   该文件的有效依赖行不应直接声明上述核心包。若仍有 `openai==2.33.0`，说明实际使用的清单不是修复后的清单；
   保留日志和现有配置/数据，确认安装来源后再重新获取源码，不要直接删除运行目录。
3. 在 AstrBot **同一 Python 环境**中执行以下命令，只解析不安装，检查完整依赖链是否仍引入冲突：

   ```bash
   python -m pip install --dry-run --report dependency-report.json -r requirements.txt
   ```

   此命令只验证基础依赖链，不等同于 AstrBot 带动态核心约束的安装验证。
   若实际清单正确但仍失败，请提供下载地址或提交 SHA、包内版本、有效依赖行及完整解析日志，
   并核对日志中的 `Pip package manager argv`、实际 constraints 文件和额外 pip 参数，
   以便区分旧源码、安装镜像/缓存或传递依赖冲突。`/bili_start` 的预检在插件加载后才执行，不能修复安装阶段的旧依赖声明。

---

## ⚙️ AstrBot 插件配置

- `use_astrbot_config` 默认为 `false`，默认继续使用业务配置文件（`config.json`）。升级前建议备份 AstrBot 插件面板配置：AstrBot 加载新 schema 时会移除面板中不再声明的旧字段；独立业务配置文件不受此清理影响。
- 启用 `use_astrbot_config` 后，还须在 `apply_sections` 中列出需要应用的配置组名称（如 `api`、`vision`）；默认空列表不会修改旧配置。发送 `/bili_start` 时，**所选组内所有面板字段，包括默认值，都会覆盖业务配置**，请先检查这些值；未选中的组和未暴露的字段会保留。已运行的机器人需先停止再启动。关闭开关不会撤销已写入的设置。
- 面板中的密钥留空或填写脱敏值时，保留原有密钥；填写的新密钥会写入业务配置文件，沿用其现有的明文存储方式，请保护该文件。
- `automation` 中的 `dry_run` 和 `allow` 不是全局操作保护，不能当作安全保障；使用者仍需自行控制和审核操作。
- 插件模式**不提供 Web 管理面板**，登录 / 兴趣 / 评论 / 导入导出等交互式设置需在服务器终端运行 `python main.py`。
  `services/video_to_ppt.py` 在生成 HTML 后会按需启动一个本地 Flask 预览服务，仅用于本地预览，不是管理后台。

---

## 🗂️ 数据存放位置

| 场景 | 私有数据（`Data/`、`qr_codes/`） | 生成产物（知识库 / 思维导图 / Word / HTML） |
|------|--------------------------------|------------------------------------------|
| 源码运行 | `%LOCALAPPDATA%\BiliLearn\` | 项目根目录 |
| 打包 / EXE | `%LOCALAPPDATA%\BiliLearn\` | `%LOCALAPPDATA%\BiliLearn\` |

- 可用环境变量 `BILI_USER_DATA_DIR` 覆盖私有数据目录。
- 非 Windows 环境下 `LOCALAPPDATA` 未设置时回退到 `~/AppData/Local/BiliLearn`，建议显式设置 `BILI_USER_DATA_DIR`。
- Cookie、API Key、二维码、知识库全部只存在本地；打包产物不含任何隐私数据。

---

## 🧱 项目结构

```
├── main.py               # 🚀 插件入口（BilibiliLearningBot(Star) + CLI 主菜单）
├── metadata.yaml         # 🏷️ AstrBot 插件元数据
├── _conf_schema.json     # ⚙️ AstrBot 插件配置 schema
├── _upgrade_v2.py        # 🔄 v2 → v3 数据迁移脚本
├── editdistance.py       # 🔤 轻量编辑距离实现
├── api/                  # 🔌 B站 API 层（客户端 / 登录 / 字幕 / 节流）
├── brain/                # 🧠 核心大脑（Mixin 组合：主循环 / 视频理解 / AI 调用 / 会话）
├── cli/                  # 💻 命令行菜单（app.py 为交互式主菜单与后台任务）
├── core/                 # ⚙️ 配置 / 全局变量 / 用户数据路径 / 恢复出厂
├── knowledge/            # 📚 知识库（分类 / 搜索 / 浏览 / 复习 / 整理 / 自定义）
├── persona/              # 🎭 人格 + 心理画像引擎
├── security/             # 🛡️ 内容安全审查
├── services/             # 🔧 服务模块（深研 / 测验 / 思维导图 / Word / 兴趣引擎 / RAG / 提醒…）
├── ob_bridge/            # 🌉 开放平台桥接（鉴权 / AB 测试 / 审计）
├── xingye_bot/           # 🤖 扩展组件（LLM / 状态 / 记忆 / 进化 / ASR / 定时任务）
└── tests/                # 🧪 pytest 用例（5 个测试模块）
```

当前仓库共 **109 个 Python 文件**。

---

## ❓ FAQ

**Q: 数据存在哪里？**
见上方「数据存放位置」。默认在 `%LOCALAPPDATA%\BiliLearn`，可用 `BILI_USER_DATA_DIR` 覆盖。

**Q: 机器人刚启动就退出，日志里有 `ImportError`？**
检查是否用了干净的 Python 环境。若 `PYTHONPATH` 指向另一个 Python 的 site-packages（例如装了多个 Python），`import PIL` 可能加载到不匹配的 Pillow。运行前执行 `echo %PYTHONPATH%`，为空最稳妥。

**Q: AI 调用报 `'ascii' codec can't encode...`？**
检查 `config.json` 的 `api.vision_api_key` / `unified_api_key` 是否被写成了 `"[hidden]"` 这类占位符（不要把脱敏导出的配置原样导回）。清空该字段会回退到 `unified_api_key`。

**Q: 导出的配置里为什么没有 Cookie 和 API Key？**
脱敏导出会把 API Key 与 Cookie 替换为 `[hidden]`。完整导出包含真实凭证，请妥善保管，不要公开分享。

**Q: 导入别人的配置备份后 AI 全坏了，报 `'ascii' codec can't encode`？**
导入时会自动过滤 `[hidden]`：已存在则保留原值，否则删除该字段需重新填写。已受影响的用户请手动编辑 `%LOCALAPPDATA%\BiliLearn\Data\config.json`，把 `unified_api_key` / `vision_api_key` 里的 `[hidden]` 换成真实 Key。

---

## ⚠️ 免责声明

本项目仅供**学习与个人研究**使用。请遵守 B站用户协议与相关法律法规，合理控制互动频率。本项目与 B站官方无任何关系，不保证接口长期可用，任何账号封禁、法律纠纷等后果由使用者自行承担。`SESSDATA` 是账号最高权限凭证，本插件只将其保存在本地用户目录，**切勿公开分享**扫码登录截图、`auth.json` 或 Cookie 内容。

---

## 📄 License

[MIT](LICENSE) © xiaoyaya191

[作者插件群](https://qun.qq.com/universal-share/share?ac=1&authKey=g11r7YZ32NvsrmF6gs1p3jz%2FwPFaZvDqNTo2%2Bd1qJ0oi9QtKEUj48IuK%2BEYpJeVR&busi_data=eyJncm91cENvZGUiOiIxMDc1OTIwMzIzIiwidG9rZW4iOiJKbzdIbHRxVTV1OEovOG1MaVk2RWZMWkJvMjd3UVhNNktYOEViZVMrMDJ5bFEwa1lwRFlmdVBUUU4wTFkzRWsxIiwidWluIjoiMTczMTUzODMzNCJ9&data=Ee7Fzotcpdxp4NMkkSOCUWLBvNfkgreKTW5hcIg2_w337UccGjJgPcL-obkS2n6iraxtTqgDgZ1LxZ6UCfJY3Q&svctype=4&tempid=h5_group_info)

tip: 群仅供插件功能答疑；bug 或新功能请先提 issue，也可以在群里问 issue 进度。

---

# English

# bilibili_learning_bot

> **Bilibili AI Learning Bot** — An AI that auto-watches videos, learns knowledge, interacts via comments, replies to DMs, and evolves itself.
>
> Version: **3.1.6** | License: MIT
>
> This plugin is a fork of [bilibili_learning_bot](https://github.com/xiaoyaya191/bilibili_learning_bot), with permission from the original author.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📺 **Smart Video Browsing** | AI-driven browsing of Bilibili's recommendation feed, automatically judging content value (rating / favoriting / coin / like) |
| 📚 **Knowledge Base System** | Auto-archives high-quality videos, with multi-tier categorization + search + review & recap |
| 💬 **Comment Interaction** | Real/simulated comment modes, AI in-depth replies, with image analysis support |
| 📩 **Direct Message Handling** | Auto-replies to fan DMs, with persistent context + long-term memory, and pacing control |
| 📡 **Real-time Listening** | Standalone listening engine that only watches DMs + comments for real-time AI replies — no video watching, low resource use |
| 🔔 **@ Mention Response** | Comments like "@bot summarize this video" are auto-detected and summarized/replied |
| 🧬 **Diary & Self-Evolution** | Behavior logs + AI self-reflection + dynamic persona evolution |
| 🎙️ **ASR Speech Recognition** | Video speech-to-text (FunASR / Whisper, optional install) |
| 🤖 **Agent Skill System** | Autonomously plans goals → searches Bilibili → watches videos → summarizes knowledge, a fully automated loop |
| 🎓 **Knowledge Tutoring** | AI explanations / Q&A / secondary creation / generates HTML study cards |
| 🎨 **Video → Webpage** | Generates PPT-style HTML from videos with multiple built-in themes (including Claude theme) |
| 📊 **Mind Map & Word Export** | Export videos to `.mindmap.html` and `.docx` documents |
| 🔍 **Deep Study** | Multi-chapter deep study of long videos, with evidence-chain summaries (`services/deep_dive.py`) |
| 🎯 **Smart Interest Engine** | Multi-dimensional scoring + synonyms + exclusion words + "eureka" exploration + PsychoProfile sync |
| 😊 **AI Mood System** | Dynamic mood affects interaction style, customizable |
| 🏆 **High-Value Like Review** | Periodically reviews favorited high-value videos, AI recap (`services/like_review.py`) |
| 🔔 **Local Reminders** | Desktop notifications + to-do reminders (`services/reminders.py`) |
| 🛡️ **Safety Review** | Keyword filtering + political-sensitivity blocking + prompt-injection protection + operation risk control |
| 🔄 **Fallback API Degradation** | Auto-switches to backup provider / backup model after consecutive primary API failures |

---

## 🎮 Commands

| Command | Description |
|---------|-------------|
| `/bili_start` | Start the background bot (re-checks core dependencies on every start) |
| `/bili_stop` | Stop the background bot (wait up to 10 seconds for shutdown) |
| `/bili_status` | Show the background bot status |
| `/bili_config` | Config entry (prompts `/bili_config_set`, which is not implemented yet — see note below) |
| `/bili_login` | Bilibili QR / verification-code login (**interactive terminal only**) |
| `/bili_kb_list` | List knowledge base categories and item counts |
| `/bili_kb_revisit` | Review the knowledge base |
| `/bili_kb_organize` | Reorganize the knowledge base |
| `/bili_kb_custom` | Custom knowledge management (**interactive terminal only**) |
| `/bili_analyze <BV id / URL>` | Analyze a single video |
| `/bili_up_learn <UID or name>` | Learn an uploader's homepage (defaults: 10 videos, skip ones without subtitles, score ≥ 7.0) |
| `/bili_video2html <BV id / URL>` | Convert a video into an HTML study page |
| `/bili_asr` | Toggle ASR speech recognition |
| `/bili_quiet` | Toggle quiet mode |
| `/bili_vision` | Toggle cover-image analysis |
| `/bili_fast` | Toggle fast mode (skip human-like delays) |
| `/bili_interest` | Interest settings (**interactive terminal only**) |
| `/bili_comment` | Comment settings (**interactive terminal only**) |
| `/bili_export` / `/bili_import` | Export / import configuration (**interactive terminal only**) |
| `/bili_reset` | Factory reset (**interactive terminal only**) |

> Commands marked "interactive terminal only" just reply with a hint inside AstrBot; run `python main.py` on the server instead.
>
> ⚠️ Known issue: `/bili_config` tells users to use `/bili_config_set <key> <value>`, but that sub-command is not registered in `main.py`. Use the AstrBot plugin config panel or the terminal CLI instead.

---

## 🧩 Requirements

- **AstrBot core**: `>=4.29.1,<5` (see `astrbot_version` in `metadata.yaml`). Older cores reject the install.
- **Core-provided packages**: `openai`, `pydantic`, `httpx[socks]`, `pillow`, `qrcode`, `python-docx`, `reportlab`.
  The plugin does not declare them in `requirements.txt` to avoid the core dependency guard (issues #18 / #20).
  `/bili_start` validates their import and minimum versions (constraints live in `_CORE_PROVIDED_DEPENDENCIES` in `main.py`,
  compared per PEP 440). After installing missing packages, just send `/bili_start` again — no restart required.
- **SOCKS proxy (optional)**: missing `socksio` only produces a warning; install `httpx[socks]` to use SOCKS proxies.
- **Plugin-specific packages**: see `requirements.txt`, all use compatible ranges rather than exact pins.
- **Optional packages**: ASR (`funasr` / `torch` / `torchaudio`) and vector search (`sentence-transformers` / `numpy`)
  are listed as comments in `requirements.txt`.

---

## ⚙️ Plugin Configuration

- `use_astrbot_config` defaults to `false`, so the business configuration file (`config.json`) stays authoritative. Back up the AstrBot panel configuration before upgrading: AstrBot removes fields no longer present in the schema when it loads the plugin. This cleanup does not affect the separate business configuration file.
- After enabling `use_astrbot_config`, list the groups to apply in `apply_sections` (for example, `api` and `vision`); the default empty list leaves existing settings unchanged. On `/bili_start`, **all panel fields in the selected groups, including defaults, overwrite the business settings**, so check their values first. Unselected groups and unexposed fields are preserved. Stop and restart a running bot to apply changes. Disabling the switch does not undo previously saved values.
- Leaving a secret blank or entering a redacted value preserves the existing secret. A new secret is written to the business configuration file using its existing plaintext storage; protect that file.
- The `automation` settings `dry_run` and `allow` are not global protections for operations and must not be treated as security guarantees. Users must still control and review operations themselves.
- The plugin does **not** provide a Web admin panel. Interactive settings (login / interest / comments / import & export) require running `python main.py` on the server.
  `services/video_to_ppt.py` starts a local Flask preview server after generating HTML — for local preview only, not an admin backend.

---

## 🗂️ Where Data Lives

| Scenario | Private data (`Data/`, `qr_codes/`) | Generated artifacts (knowledge base / mind maps / Word / HTML) |
|----------|------------------------------------|----------------------------------------------------------------|
| Source checkout | `%LOCALAPPDATA%\BiliLearn\` | Project root |
| Frozen / EXE | `%LOCALAPPDATA%\BiliLearn\` | `%LOCALAPPDATA%\BiliLearn\` |

- Override the private data directory with the `BILI_USER_DATA_DIR` environment variable.
- On non-Windows systems without `LOCALAPPDATA`, the fallback is `~/AppData/Local/BiliLearn`; prefer setting `BILI_USER_DATA_DIR` explicitly.
- Cookies, API keys, QR codes and the knowledge base stay local; packaged artifacts contain no private data.

---

## 🧱 Project Structure

See the Chinese section above — the layout is identical.

Current repository size: **109 Python files**.

---

## 🛡️ Disclaimer

This project is for **personal learning purposes only**. It has no relationship with the official Bilibili service and provides no warranty of long-term availability; account bans or legal disputes arising from its use are borne solely by the user. `SESSDATA` is the highest-privilege credential of an account — this project stores it only in your local user directory. **Never** share QR-login screenshots, `auth.json`, or Cookie contents publicly.

---

## 📄 License

[MIT](LICENSE) © xiaoyaya191

[my plugin group](https://qun.qq.com/universal-share/share?ac=1&authKey=g11r7YZ32NvsrmF6gs1p3jz%2FwPFaZvDqNTo2%2Bd1qJ0oi9QtKEUj48IuK%2BEYpJeVR&busi_data=eyJncm91cENvZGUiOiIxMDc1OTIwMzIzIiwidG9rZW4iOiJKbzdIbHRxVTV1OEovOG1MaVk2RWZMWkJvMjd3UVhNNktYOEViZVMrMDJ5bFEwa1lwRFlmdVBUUU4wTFkzRWsxIiwidWluIjoiMTczMTUzODMzNCJ9&data=Ee7Fzotcpdxp4NMkkSOCUWLBvNfkgreKTW5hcIg2_w337UccGjJgPcL-obkS2n6iraxtTqgDgZ1LxZ6UCfJY3Q&svctype=4&tempid=h5_group_info)

tip: the group is for plugin usage questions only; for bugs or feature requests please open an issue first.
