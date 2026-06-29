AstrBot bilibili learning_bot插件

https://img.shields.io/badge/Python-100%25-blue?logo=python
https://img.shields.io/badge/AstrBot-插件-orange
https://img.shields.io/badge/license-MIT-green

📖 项目简介

本插件是 AstrBot 生态系统中的一个知识库管理工具，为机器人提供了完整的知识库操作能力。通过本插件，用户可以方便地对知识库中的条目进行添加、删除、修改、查询等管理操作，帮助机器人更高效地组织和检索知识信息。

AstrBot 本身支持多种知识库管理，在对话过程中可以自由指定使用哪个知识库。本插件在此基础上进一步封装了便捷的管理接口，让知识库的维护工作变得更加简单直观。

✨ 功能特性

· 📝 知识条目管理：支持对知识库条目进行增、删、改、查等完整 CRUD 操作
· ⚙️ 灵活配置：通过 _conf_schema.json 可自定义插件行为，适配不同使用场景
· 🔌 即插即用：基于 AstrBot 插件框架开发，安装后自动注册，无需额外配置
· 🧩 易于扩展：遵循 AstrBot 插件规范，可与其他插件协同工作，构建更强大的功能组合
· 🎨 美观标识：自带 logo.png 图标，在 WebUI 中展示更专业

📁 项目结构

```
astrbot_plugin_b-/
├── knowledge_mgr.py          # 知识库管理核心业务逻辑模块[reference:4]
├── _conf_schema.json         # 插件配置 Schema 定义文件[reference:5]
├── logo.png                  # 插件图标资源[reference:6]
└── README.md                 # 项目说明文档[reference:7]
```

文件 说明
knowledge_mgr.py 插件主程序，包含知识库管理的核心功能实现
_conf_schema.json 配置结构定义，用于 AstrBot WebUI 动态渲染配置表单
logo.png 插件标识图标，在插件列表中展示

🚀 安装与使用

1. 安装插件

方式一：通过 Git 克隆

```bash
git clone https://github.com/mjy1113451/astrbot_plugin_b-.git
```

方式二：手动下载

将本仓库下载后，把整个文件夹放置到 AstrBot 的 plugins 目录下。

2. 配置插件

插件安装后，可通过 AstrBot 的 WebUI 配置页面进行设置。_conf_schema.json 定义了配置项的格式和类型，WebUI 会根据该 Schema 自动生成配置表单。

如需手动调整配置，可直接编辑插件目录下的配置文件。

3. 加载与使用

重启 AstrBot 服务，插件将自动加载。加载成功后，即可通过机器人对话界面调用知识库管理功能。

💡 提示：AstrBot 支持同时上传最多 10 个文件，单文件最大 128 MB。本插件充分兼容这些能力，确保知识库管理的高效与稳定。

🛠️ 技术栈

· Python 3.8+：插件开发语言
· AstrBot Framework：插件运行依赖的机器人框架


🤝 参与贡献

欢迎通过以下方式参与本项目：

· 提交 Issue 报告 Bug 或提出建议
· 提交 Pull Request 贡献代码
· Fork 本仓库进行个性化定制
· 加入插件群提问 1079297679

📄 许可证

本项目采用 MIT License 开源协议，可自由使用、修改和分发。

---

📌 说明：以上内容基于仓库中的文件结构（knowledge_mgr.py、_conf_schema.json、logo.png）及提交记录生成。如需了解更详细的功能说明，建议查看 knowledge_mgr.py 源码中的函数注释与实现逻辑。