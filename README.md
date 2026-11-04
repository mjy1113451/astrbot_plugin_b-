 #B站学习助手

> AstrBot 插件：让 AI 在后台刷 B 站推荐流、理解视频内容、整理知识库、回复评论与私信，并支持独立 CLI 运行。

- 版本：**3.1.6**
- 许可证：MIT
- 兼容：AstrBot `>=4.29.1,<5`（见 `metadata.yaml`）
- 上游项目：[bilibili_learning_bot](https://github.com/xiaoyaya191/bilibili_learning_bot)（改写已获原作者同意）

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 后台主循环 | `/bili_start` 启动后台学习循环，`/bili_stop` 停止并释放单实例锁 |
| 推荐流浏览 | 按兴趣评分筛选推荐视频，决定点赞 / 收藏 / 投币 / 跳过 |
| 视频理解 | 字幕、封面、关键帧、评论与弹幕综合理解，输出结构化笔记 |
| 知识库 | 自动归档、分类、检索、复习与整理，支持自定义知识条目 |
| 评论与私信 | 评论回复、私信自动回复，带持久上下文与长期记忆 |
| UP 主学习 | `/bili_up_learn` 批量学习某个 UP 主的主页视频 |
| 视频转 HTML | 将视频内容生成 PPT 风格网页，内置 20 种主题（含 Claude 样式） |
| 思维导图 / Word | 一键导出 `.mindmap.html` 与 `.docx` 文档 |
| 深度研习 | 长视频分章节深研，输出带证据链的记录（`services/deep_dive.py`） |
| RAG 问答 / 测验 / 辅导 | 知识库检索问答、测验生成与知识讲解（`rag_qa.py` / `quiz_generator.py` / `knowledge_tutor.py`） |
| ASR 语音识别 | FunASR（默认）或 Whisper 转写视频语音，可用 `/bili_asr` 开关 |
| 兴趣引擎 | 多维度评分 + 同义词 + 排除词 + 探索模式（`services/interest_engine.py`） |
| 点赞回顾 | 定期回顾收藏的干货视频（`services/like_review.py`） |
| 本地提醒 | `Data/reminders.json` 中的提醒记录，供 Agent 与启动器使用 |
| 安全审查 | 关键词 / 政治敏感拦截、提示词注入防护、回复安全（`security/guard.py`） |
| 审批复核 | 点赞、关注、弹幕、私信、投币、收藏可逐项要求人工确认 |
| API 降级 | 主 API 连续失败后切换备用模型 / 备用服务商 |
| 开放平台桥接 | `ob_bridge/`（鉴权、A/B 测试、审计） |

---

## 环境要求

- **AstrBot**：`>=4.29.1,<5`，低于该版本会在安装期被核心拒绝。
- **由核心提供的依赖**：`openai`、`pydantic`、`httpx[socks]`、`pillow`、`qrcode`、`python-docx`、`reportlab`。
  这些包已从 `requirements.txt` 移除（避免触发核心依赖保护，见 issues #18 / #20），
  最低版本统一维护在 `main.py` 的 `_CORE_PROVIDED_DEPENDENCIES`。
  每次执行 `/bili_start` 都会重新校验导入与版本，缺失时会拒绝启动并列出原因；补齐后可直接重试，无需重启 AstrBot。
- **插件专属依赖**：见 `requirements.txt`（`bilibili-api-python`、`Flask`、`yt-dlp`、`pystray` 等），均使用兼容区间。
- **可选依赖**：
  - SOCKS 代理：需要 `httpx[socks]`；缺失时仅告警，不影响直连或 HTTP 代理。
  - ASR：`funasr` / `torch` / `torchaudio`（或 Whisper 相关包），按需手动安装。
  - 向量检索：`sentence-transformers` / `numpy`。

---

## 安装

### 方式一：插件市场

在 AstrBot WebUI 的插件市场搜索 `B站学习助手` 或 `astrbot_plugin_bilibili_learning_bot`，安装后重载插件。

### 方式二：手动安装

```bash
git clone https://github.com/mjy1113451/astrbot_plugin_b-.git
# 将仓库内容放入 AstrBot 插件目录（通常为 AstrBot/data/plugins/）
```

安装后在 AstrBot 插件管理页启用，并向机器人发送 `/bili_start`。

> 也可以直接 `python main.py` 以独立 CLI 模式运行（扫码登录、兴趣 / 评论设置、导出导入、恢复出厂等交互菜单只在终端可用）。

---

## 指令一览

| 指令 | 说明 |
|------|------|
| `/bili_start` | 后台启动学习循环（不阻塞对话） |
| `/bili_stop` | 停止后台任务并释放单实例锁 |
| `/bili_status` | 查看主循环运行状态与后台任务 |
| `/bili_config` | 显示配置子指令提示（当前版本未提供实际子指令，请改面板或业务配置文件） |
| `/bili_login` | B 站登录（需在服务器终端执行，扫码 / 验证码） |
| `/bili_kb_list` | 列出知识库分类与条目数量 |
| `/bili_kb_revisit` | 重温知识库 |
| `/bili_kb_organize` | 整理知识库分类 |
| `/bili_kb_custom` | 自定义知识管理（交互式，需终端） |
| `/bili_analyze <BV号/链接>` | 分析单个视频并归档 |
| `/bili_up_learn <UID或名字>` | 学习 UP 主主页视频（默认取 10 个、跳过无字幕、评分 ≥7） |
| `/bili_video2html <BV号/链接>` | 将视频转换为 HTML 学习页面 |
| `/bili_asr` | 切换 ASR 语音识别 |
| `/bili_quiet` | 切换安静模式 |
| `/bili_vision` | 切换封面分析 |
| `/bili_fast` | 切换快速模式（跳过拟人延迟） |
| `/bili_interest` | 兴趣设置（交互式，需终端） |
| `/bili_comment` | 评论设置（交互式，需终端） |
| `/bili_export` | 导出配置（交互式，需终端；导出内容中的密钥会被脱敏） |
| `/bili_import` | 导入配置（交互式，需终端；自动过滤 `[hidden]` 占位值） |
| `/bili_reset` | 恢复出厂设置（交互式，需终端，逐项确认） |

---

## 配置

插件有两条配置来源：

1. **AstrBot 插件面板**（`_conf_schema.json`）：默认不生效。
2. **业务配置文件** `Data/config.json`：插件实际读取的配置。

### 面板配置桥接

- `use_astrbot_config` 默认为 `false`，即继续使用 `Data/config.json`。
- 启用后，还需要在 `apply_sections` 中列出要应用的配置组名称；默认空列表不会修改任何旧配置。
- 发送 `/bili_start` 时，**选中的组内所有字段（含默认值）都会覆盖业务配置**；未选中的组保持不变。已运行的任务需先停止再启动。
- 关闭开关不会撤销已写入的值。
- 密钥留空或填写脱敏值时保留原值；新密钥会以明文写入业务配置文件，请自行保护。

可应用的配置组：`api`、`fallback_models`、`fallback_provider`、`video`、`prompt_injection`、`vision`、`approval_review`、`reply_safety`、`agent`、`learning_workflow`。

> 升级前请备份面板配置：AstrBot 加载新 schema 时会移除面板中已不再声明的旧字段；业务配置文件不受影响。

### 数据存放位置

| 内容 | 位置 |
|------|------|
| 私有数据（`config.json`、`mood_state.json`、`reminders.json`、`qr_codes/`） | `%LOCALAPPDATA%\BiliLearn\Data`（非 Windows 默认 `~/AppData/Local/BiliLearn`，可用环境变量 `BILI_USER_DATA_DIR` 覆盖） |
| 生成产物（`KnowledgeBase/`、`html_exports/`、`MindMaps/`、`Word/`、`highlights/`） | 源码运行：项目根目录；打包运行：`%LOCALAPPDATA%\BiliLearn` |

打包产物不包含任何隐私数据，升级不会覆盖用户数据。

> 插件不提供 Web 管理后台。仅在生成 HTML 预览时，会在 `127.0.0.1` 上临时启动本地预览服务。

---

## 依赖与安装冲突排查

若 AstrBot v4.29.3 报 `openai==2.33.0` 与核心保护的 `openai==2.46.0` 冲突：

1. 确认使用的是 **3.1.6** 源码包：核对包内 `metadata.yaml` 的 `version` 与 `VERSION` 均为 `3.1.6`。若插件市场仍显示旧版本，请刷新来源并强制更新；自定义源若配置了固定 `download_url`，需要同步更新地址。
2. 在日志给出的**实际安装目录**检查 `requirements.txt`，不应出现 `openai` 等核心包的直接声明。
3. 在 AstrBot 同一 Python 环境中只解析不安装，验证依赖链：

   ```bash
   python -m pip install --dry-run --report dependency-report.json -r requirements.txt
   ```

4. 仍失败时，请提供下载地址或提交 SHA、包内版本、有效依赖行和完整解析日志（含 `Pip package manager argv` 与 constraints 参数），以便区分旧源码、镜像缓存或传递依赖冲突。

---

## 项目结构

```
astrbot_plugin_b--main/
├── main.py                 # 插件入口：指令注册、依赖预检、CLI 入口
├── _conf_schema.json       # AstrBot 面板配置 schema
├── metadata.yaml           # 插件元信息（版本 / 兼容范围 / 平台）
├── requirements.txt        # 插件专属依赖
├── api/                    # B站接口层（client / auth / 字幕 / 节流）
├── brain/                  # 主循环、视频理解、决策、评论、私信、监听
├── knowledge/              # 知识库浏览 / 分类 / 整理 / 复习 / 自定义
├── persona/                # 人格、心情、心理画像
├── security/               # 内容安全审查
├── services/               # 深研 / 测验 / 思维导图 / Word / RAG / 兴趣 / 提醒等
├── ob_bridge/              # 开放平台桥接（鉴权 / A/B / 审计）
├── xingye_bot/             # 扩展组件（ASR / 日记 / 进化 / B站操作）
├── cli/                    # 终端交互菜单与配置读写
├── core/                   # 配置、全局变量、用户数据路径、面板配置桥接
├── tests/                  # 单元测试（unittest）
└── CHANGELOG.md            # 版本更新记录
```

测试运行：

```bash
python -m unittest discover -s tests -v
```

---

## FAQ

**Q：数据存放在哪里？**
私有数据固定在 `%LOCALAPPDATA%\BiliLearn\Data`（可用 `BILI_USER_DATA_DIR` 覆盖）；知识库与导出产物在源码运行时位于项目根目录，打包运行时同样位于 `BiliLearn` 目录。

**Q：启动后立刻退出，日志报 `ImportError`？**
检查是否使用了干净的 Python 环境。若 `PYTHONPATH` 指向其他 Python 的 site-packages（例如机器上装了多个 Python），可能加载到版本不匹配的 Pillow 等包。运行前执行 `echo $PYTHONPATH`，为空最稳妥。

**Q：AI 调用报 `'ascii' codec can't encode ...`？**
检查 `Data/config.json` 中的 `unified_api_key` / `vision_api_key` 是否被写成了 `[hidden]` 之类的脱敏占位值，替换为真实密钥即可。

---

## 免责声明

本项目仅供学习与个人研究使用。请遵守哔哩哔哩用户协议与相关法律法规，合理控制互动频率。
本项目与哔哩哔哩官方无任何关系，接口可能随时变更；账号风控、封禁或法律风险由使用者自行承担。
API Key、Cookie 等凭据仅保存在本地，请勿公开分享。

---

## License

[MIT](LICENSE) © xiaoyaya191

[作者插件群](https://qun.qq.com/universal-share/share?ac=1&authKey=g11r7YZ32NvsrmF6gs1p3jz%2FwPFaZvDqNTo2%2Bd1qJ0oi9QtKEUj48IuK%2BEYpJeVR&busi_data=eyJncm91cENvZGUiOiIxMDc1OTIwMzIzIiwidG9rZW4iOiJKbzdIbHRxVTV1OEovOG1MaVk2RWZMWkJvMjd3UVhNNktYOEViZVMrMDJ5bFEwa1lwRFlmdlBUUU4wTFkzRWsxIiwidWluIjoiMTczMTUzODMzNCJ9&data=Ee7Fzotcpdxp4NMkkSOCUWLBvNfkgreKTW5hcIg2_w337UccGjJgPcL-obkS2n6iraxtTqgDgZ1LxZ6UCfJY3Q&svctype=4&tempid=h5_group_info)

> 群里只讨论插件功能问题；bug 或新功能请先提 issue。

---

## English

**Bilibili Learning Bot for AstrBot** — an AI that browses Bilibili's recommendation feed, understands videos, organizes a knowledge base, replies to comments and DMs, and can also run standalone from the CLI.

- Version **3.1.6**, MIT license, requires AstrBot `>=4.29.1,<5`.
- Commands: `/bili_start`, `/bili_stop`, `/bili_status`, `/bili_analyze`, `/bili_up_learn`, `/bili_video2html`, `/bili_kb_*`, `/bili_asr`, `/bili_quiet`, `/bili_vision`, `/bili_fast`, `/bili_interest`, `/bili_comment`, `/bili_export`, `/bili_import`, `/bili_reset`, `/bili_login`.
- Private data lives in `%LOCALAPPDATA%\BiliLearn\Data` (override with `BILI_USER_DATA_DIR`).
- Plugin panel settings take effect only when `use_astrbot_config` is enabled and the relevant groups are listed in `apply_sections`.
- For installation dependency conflicts, verify that the installed package is version 3.1.6 and that `requirements.txt` does not redeclare core-provided packages.
