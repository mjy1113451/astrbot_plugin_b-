# bilibili_learning_bot

> **B站 AI 学习互动机器人** — AI 自动刷视频、学知识、评论互动、私信回复、自我进化
>
> 版本: **3.1.2** | License: MIT 
>
>此插件改写经过原作者xiaoyaya
[bilibili_learning_bot](https://github.com/xiaoyaya191/bilibili_learning_bot)同意

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📺 **智能视频浏览** | AI 驱动 B站推荐流浏览，自动判断内容价值（评分 / 收藏 / 投币 / 点赞） |
| 📚 **知识库系统** | 自动归档高质量视频，3 层分类 + 语义检索 + 复习回顾 |
| 💬 **评论互动** | 真实/模拟评论模式，AI 深度回复，支持图片分析 |
| 📩 **私信处理** | 自动回复粉丝私信，持久上下文 + 长期记忆，支持节奏控制 |
| 📡 **实时监听** | 独立监听引擎，只盯私信 + 评论实时 AI 回复，不刷视频不耗精力 |
| 🔔 **@通知响应** | 视频下评论 "@bot 总结这个视频"，自动识别并总结回复 |
| 🧬 **日记与自我进化** | 行为日志 + AI 自我反思 + 人格动态进化 |
| 🎙️ **ASR 语音识别** | 视频语音转文字（FunASR / Whisper，可选安装） |
| 🤖 **Agent 技能系统** | 自主规划目标 → 搜索 B站 → 看视频 → 总结知识，全自动闭环 |
| 🎓 **知识辅导** | AI 讲解 / 问答 / 二次创作 / 生成 HTML 学习卡片 |
| 🎨 **视频→网页** | 视频生成 PPT 风格 HTML，19 种视觉风格，支持 Claude 主题 |
| 📊 **思维导图 & Word 导出** | 视频一键导出 `.mindmap.html` 与 `.docx` 文档 |
| 🔍 **深度研习** | 长视频多章节深研，证据链式总结（`services/deep_dive.py`） |
| 🎯 **智能兴趣引擎** | 多维度评分 + 同义词 + 排除词 + 灵光一闪探索 + PsychoProfile 同步 |
| 😊 **AI 心情系统** | 动态心情影响互动风格，支持自定义 |
| 🏆 **干货点赞回顾** | 定期回顾收藏的干货视频，AI 复习（`services/like_review.py`） |
| 🔔 **本地提醒** | 桌面通知 + 待办提醒（`services/reminders.py`） |
| 🛡️ **安全审查** | 关键词过滤 + 政治敏感拦截 + 提示词注入防护 + 操作风控 |
| 🔄 **备用 API 降级** | 主 API 连续失败自动切换备用提供商 / 备用模型 |

---

## 📊 v3.0.2 → v3.1.x 版本对比

| 维度 | v3.0.2 | v3.1.2+（当前 3.1.2） |
|------|--------|----------------------|
| **代码规模** | 77 个 Python 文件 / ~34k 行 | 113 个 Python 文件 / ~54k 行（+47%） |
| **数据目录** | 项目内 `Data/`（打包/升级易丢） | ✅ `%LOCALAPPDATA%\BiliLearn`（打包产物零隐私数据，升级不丢） |
| **人格管理** | 简单 prompt 配置 | ✅ Web 可视化多人格（创建 / 编辑 / 激活 / 删除），key 与显示名双匹配 |
| **HTML 渲染** | 各模块各自维护模板 | ✅ `services/html_renderer.py` 统一渲染（阅读页 / 幻灯片 / 导出） |
| **服务模块** | 12 个 | ✅ 32 个（新增深度研习、测验生成、思维导图、Word 导出、本地收藏、点赞回顾、提醒、RAG 问答、平台适配、代理配置、版本历史…） |
| **评论回复** | 基础回复 | ✅ 顶层/子回复路由修复、12006 失效处理、AI 选择失效 ID 跳过 |
| **监听引擎** | 基础轮询 | ✅ 上下文合并、超时跳过、`-509` 退避、网页日志可视化 |
| **开放平台桥接** | ❌ | ✅ `ob_bridge/`（开放平台鉴权、AB 测试、审计） |
| **备份与恢复** | 手动导出 | ✅ 分组备份（设置 / 记忆 / 知识 / 产物）+ 恢复 |
| **测试** | 43 个 pytest | ✅ 181 个 pytest（`319 passed` 全量发布验证） |
| **稳定性修复** | — | 人格持久化、Cookie 校验、风控、多实例锁、AI 降级冷却、上下文截断保护 |

> 详细演进见 [CHANGELOG.md](CHANGELOG.md)。

---

## 🧱 项目结构

```
├── main.py               # 🚀 主入口（CLI 交互菜单 + 自动化启动）
├── BiliLearn.spec        # 📦 PyInstaller 打包配置
├── api/                  # 🔌 B站 API 层（客户端 / 登录 / 字幕 / 节流）
├── brain/                # 🧠 核心大脑（Mixin 组合：主循环 / 视频理解 / AI 调用 / 会话）
├── cli/                  # 💻 命令行菜单
├── core/                 # ⚙️ 配置 / 全局变量 / 用户数据路径 / 恢复出厂
├── knowledge/            # 📚 知识库（分类 / 搜索 / 浏览 / 复习 / 自定义）
├── persona/              # 🎭 人格 + 心理画像引擎
├── security/             # 🛡️ 内容安全审查
├── services/             # 🔧 32 个服务（深研 / 测验 / 思维导图 / Word / 兴趣引擎 / RAG…）
├── ob_bridge/            # 🌉 开放平台桥接（鉴权 / AB 测试 / 审计）
├── xingye_bot/           # 🤖 扩展组件（LLM / 状态 / 记忆 / 进化 / ASR / 网格帧）
```
---

## ⚠️ 免责声明

本项目仅供**学习与个人研究**使用。请遵守 B站用户协议与相关法律法规，合理控制互动频率，任何使用后果由使用者自行承担。

---

## 📄 License

[MIT](LICENSE) © xiaoyaya191


#英文版
# bilibili_learning_bot

> **Bilibili AI Learning Bot** — An AI that auto-watches videos, learns knowledge, interacts via comments, replies to DMs, evolves itself, with a built-in Web admin panel, and one-click Windows EXE packaging.
>
> Version: **3.1.2** | License: MIT

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📺 **Smart Video Browsing** | AI-driven browsing of Bilibili's recommendation feed, automatically judging content value (rating / favoriting / coin / like) |
| 📚 **Knowledge Base System** | Auto-archives high-quality videos, with 3-tier categorization + semantic search + review & recap |
| 💬 **Comment Interaction** | Real/simulated comment modes, AI in-depth replies, with image analysis support |
| 📩 **Direct Message Handling** | Auto-replies to fan DMs, with persistent context + long-term memory, and pacing control |
| 📡 **Real-time Listening** | Standalone listening engine that only watches DMs + comments for real-time AI replies — no video watching, low resource use |
| 🔔 **@ Mention Response** | Comments like "@bot summarize this video" are auto-detected and summarized/replied |
| 🧬 **Diary & Self-Evolution** | Behavior logs + AI self-reflection + dynamic persona evolution |
| 🎙️ **ASR Speech Recognition** | Video speech-to-text (FunASR / Whisper, optional install) |
| 🤖 **Agent Skill System** | Autonomously plans goals → searches Bilibili → watches videos → summarizes knowledge, a fully automated loop |
| 🎓 **Knowledge Tutoring** | AI explanations / Q&A / secondary creation / generates HTML study cards |
| 🎨 **Video → Webpage** | Generates PPT-style HTML from videos, 19 visual styles, with Claude theme support |
| 📊 **Mind Map & Word Export** | One-click export of videos to `.mindmap.html` and `.docx` documents |
| 🔍 **Deep Study** | Multi-chapter deep study of long videos, with evidence-chain summaries (`services/deep_dive.py`) |
| 🎯 **Smart Interest Engine** | Multi-dimensional scoring + synonyms + exclusion words + "eureka" exploration + PsychoProfile sync |
| 😊 **AI Mood System** | Dynamic mood affects interaction style, customizable |
| 🏆 **High-Value Like Review** | Periodically reviews favorited high-value videos, AI recap (`services/like_review.py`) |
| 🔔 **Local Reminders** | Desktop notifications + to-do reminders (`services/reminders.py`) |
| 🛡️ **Safety Review** | Keyword filtering + political-sensitivity blocking + prompt-injection protection + operation risk control |
| 🔄 **Fallback API Degradation** | Auto-switches to backup provider / backup model after consecutive primary API failures |

---

## 📊 Version Comparison: v3.0.2 → v3.1.x

| Dimension | v3.0.2 | v3.1.2+ (current 3.1.2) |
|-----------|--------|--------------------------|
| **Code Size** | 77 Python files / ~34k lines | 113 Python files / ~54k lines (+47%) |
| **Data Directory** | In-project `Data/` (lost on packaging/upgrade) | ✅ `%LOCALAPPDATA%\BiliLearn` (packaged artifact contains zero private data, survives upgrades) |
 ✅ Dashboard / Bot Control / Real-time Listening / Persona Management / Knowledge Tutoring / Deep Study / Backup Restore |
| **Persona Management** | Simple prompt config | ✅ Web visual multi-persona (create / edit / activate / delete), key + display-name dual matching |
| **HTML Rendering** | Each module maintained its own templates | ✅ `services/html_renderer.py` unified rendering (reading page / slides / export) |
| **Service Modules** | 12 | ✅ 32 (added deep study, quiz generation, mind map, Word export, local favorites, like review, reminders, RAG Q&A, platform adaptation, proxy config, version history…) |
| **Comment Replies** | Basic reply | ✅ Top-level/child reply routing fix, 12006 failure handling, AI skips invalid selected IDs |
| **Listening Engine** | Basic polling | ✅ Context merging, timeout skip, `-509` backoff, web log visualization |
| **Open Platform Bridge** | ❌ | ✅ `ob_bridge/` (open-platform auth, A/B testing, audit) |
| **Backup & Restore** | Manual export | ✅ Grouped backup (settings / memory / knowledge / artifacts) + restore |
| **Tests** | 43 pytest | ✅ 181 pytest (`319 passed` full release verification) |
| **Stability Fixes** | — | Persona persistence, Cookie validation, risk control, multi-instance lock, AI degradation cooldown, context truncation protection |

> See [CHANGELOG.md](CHANGELOG.md) for detailed evolution.

---

## 🧱 Project Structure

```
├── main.py               # 🚀 Main entry (CLI interactive menu + automated startup)
├── api/                  # 🔌 Bilibili API layer (client / login / subtitles / throttling)
├── brain/                # 🧠 Core brain (Mixin composition: main loop / video understanding / AI calls / session)
├── cli/                  # 💻 Command-line menu
├── core/                 # ⚙️ Config / globals / user data path / factory reset
├── knowledge/            # 📚 Knowledge base (categorization / search / browsing / review / custom)
├── persona/              # 🎭 Persona + psychological profile engine
├── security/             # 🛡️ Content safety review
├── services/             # 🔧 32 services (deep study / quiz / mind map / Word / interest engine / RAG…)
├── ob_bridge/            # 🌉 Open-platform bridge (auth / A/B testing / audit)
├── xingye_bot/           # 🤖 Extension components (LLM / state / memory / evolution / ASR / grid frames)
└── dev_refs/             # 📖 Secondary-development reference docs
```

---


## ❓ FAQ

**Q: Where is the data stored?**
Source version: project root `Data/`; Web/EXE version: `%LOCALAPPDATA%\BiliLearn` (Cookies, API Keys, knowledge base, and QR codes are all local only; the packaged artifact contains no private data).

**Q: The bot exits immediately after startup, log shows `ImportError`?**
Check whether you're using a clean Python environment. If `PYTHONPATH` points to another Python's site-packages (e.g., multiple Pythons installed), `import PIL` may load a mismatched Pillow. Run `echo %PYTHONPATH%` before running — empty is safest.

**Q: AI call reports `'ascii' codec can't encode...`?**
Check whether `config.json`'s `api.vision_api_key` / `unified_api_key` was written as a placeholder like `"[hidden]"` (don't write back a desensitized config export). Clearing that field falls back to `unified_api_key`.

**Q: Persona save says "does not exist"?**
Caused by old-version data where the persona storage key didn't match the display name. 3.1.2+ already supports key/display-name dual matching; if it still happens, restart the panel to load new code, or delete `Data/web_personas.json` to re-migrate from `personas.json`.

**Q: Why doesn't the exported config include Cookie and API Key?**
Export has two modes: **desensitized export** (default, API Key / Cookie replaced with `[hidden]`, safe to share with others) and **full export** (includes real Key and login Cookie, for your own migration backup only, filename carries `_full` suffix). The web panel asks which to choose on export; the CLI menu enters `f` for full export. A full export imported to a new machine has login state and AI config working immediately.

**Q: After importing someone else's config backup, AI is all broken, reports `'ascii' codec can't encode`?**
On backup export, API Key / Cookie is desensitized to `[hidden]`; old versions would import and overwrite real config with the placeholder. 3.1.2 stable has fixed this: on import it auto-filters `[hidden]` (keeps existing value if present, otherwise deletes the field and you must refill). Already-affected users please manually edit `%LOCALAPPDATA%\BiliLearn\Data\config.json` and replace the `[hidden]` in `unified_api_key` / `vision_api_key` with the real Key.

**Q: Port already in use?**
Default 18083; auto-increments if occupied. Or `set WEB_PORT=xxxx && python web_panel.py`.

---

## 🛡️ Disclaimer (please read carefully)

> The project author knows well that "the tool is innocent, but misuse is culpable." The following disclaimer **stacks as many layers as needed** — please read each item:

1. **Unofficial**: This project has **no relationship** with the official bilibili (B站), is not an official release, and is not endorsed or responsible for by Bilibili. All trademarks and names belong to their respective owners.
2. **Personal learning & exchange only**: This project is for **personal learning purposes** only, to study technologies such as HTTP / MCP / data processing. Any form of commercial use, profit-making, large-scale batch scraping, attacks, or abuse of Bilibili services is **prohibited**.
3. **Legal risk at your own expense**: Bilibili's interfaces and terms of service may change at any time, and Bilibili has taken legal action against similar reverse-engineering projects (e.g., bilibili-api). This project is built on public interfaces and **does not guarantee long-term availability**; any disputes, account bans, or legal liabilities arising from using this project are borne solely by the user.
4. **Account security**: `SESSDATA` is the **highest-privilege credential** of a Bilibili account; this project stores it only in your local user directory. **Never** publicly share QR-login screenshots, auth.json, or Cookie contents — leaking them is handing your account to someone else.
5. **Content copyright**: Extracted subtitles, danmaku, comments, etc. are copyrighted by the original authors and Bilibili, for personal reading/learning only — **do not** repost, redistribute, or use commercially.
6. **Stability & availability**: This project is provided "as is", without any express or implied warranty. Bilibili redesigns, risk control, network conditions, and other factors may all cause it to fail; when interfaces fail, follow the README to re-scan or fix it yourself — **the author does not promise a fix time**.
7. **Not investment advice**: Any content produced by this project does not constitute investment, financial, legal, or other professional advice; quoting others' content does not mean agreeing with their views.
8. **Risk-assumption clause**: Use constitutes agreement to all the above terms. If your region or use case does not allow such tools, please **stop using and delete this project immediately**.

---

## 📄 License

[MIT](LICENSE) © xiaoyaya191
