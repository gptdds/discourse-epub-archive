# Discourse 文集归档器

一个面向 Windows 的通用桌面工具，用 Python、PySide6 和 SQLite 将用户有权访问的
Discourse 公开主题整理为可离线阅读的 EPUB 3。站点地址由用户配置，不绑定任何特定
网站、分类或题材。

> **使用边界**
>
> 只归档你有权访问、保存和再分发的内容。使用前应检查目标站点的服务条款、robots.txt
> 和版权要求，并取得必要的授权。工具不会绕过登录、验证码、限流或访问控制，也不提供
> 代理池、多账号轮换或高并发绕过机制。内容权利归原作者，软件本身不授予任何内容许可。

## 功能

- 从可配置的 Discourse 站点读取公开分类树和分类主题列表。
- 分类分页、主题发现、帖子流分页补齐。
- 主题、帖子、作者、原始时间、来源链接持久化到 SQLite。
- 按楼层顺序保留主帖和回复，支持引用、列表、表格、代码块、图片和附件链接。
- 图片懒加载地址兼容：`src`、`data-src`、`data-orig-src`、`srcset`、`data-srcset`。
- 图片按 URL 和内容 SHA-256 去重，验证 MIME、实际图片内容、尺寸和文件大小。
- GIF 动画原样保留；静态 WebP 等格式可转换为 JPEG/PNG。
- 每个主题独立 XHTML；超长帖子按大小自动分章。
- EPUB 3 导航、封面、版权与来源说明、分类一级目录、主题二级目录。
- SQLite 断点续传、失败重试、增量更新、暂停/继续/取消。
- 全局串行调度、429/403/503/5xx/超时处理、持久化冷却、自适应间隔。
- 深色 Qt 工作台：分类、任务、电子书、设置、日志和服务器保护监控。
- 内置 Java 17 精简运行时与 EPUBCheck 5.2.1，可离线执行 EPUB 校验。

## 快速启动

最新修复版：
[DiscourseEpubArchive v1.0.2](https://github.com/gptdds/discourse-epub-archive/releases/tag/v1.0.2)

### 打包版

双击 `dist/DiscourseEpubArchive/DiscourseEpubArchive.exe`。该目录必须整体保留，
不能只复制 EXE；`tools/` 中的 EPUBCheck 和 Java 运行时已随目录打包。

### 源码

要求 Python 3.11+。双击 `start.bat`，脚本会在首次运行时创建 `.venv` 并安装依赖。
调试启动可使用 `start-debug.bat`。

也可以手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

源码模式默认使用项目目录下的 `data/`、`output/`、`logs/`。打包版默认使用
`%LOCALAPPDATA%\DiscourseEpubArchive`。设置环境变量 `EPUB_ARCHIVE_HOME` 可以指定
独立工作目录。

默认站点地址为空。升级或更换程序目录后，软件仍会读取上述工作目录中
`data/settings.json` 保存的站点地址；界面里已有的网址不代表发布包预设了站点。
如需取消已保存的站点，在「下载设置」中清空地址、取消授权勾选并保存。

## 首次使用

1. 打开「下载设置」，填写目标 Discourse 站点的 HTTPS 根地址，例如
   `https://forum.example.com`。
2. 阅读目标站点的规则，确认你有权归档公开内容；勾选授权确认并填写授权依据。
3. 使用「测试公开 API」做一次受控检查。请求仍遵守串行调度和请求间隔。
4. 前往「分类管理」刷新并勾选分类。任务名可以修改。
5. 点击「建立采集任务」。关闭程序时会先取消并保存进度。
6. 任务完成后前往「电子书管理」，选择分类生成 EPUB。

站点地址默认为空。没有填写站点地址或没有确认授权时，程序不会发送任何网络请求。
下载设置中的滚轮只用于上下滚动页面，不会修改数字或帖子范围；修改参数请直接输入、
使用数字框箭头或点击下拉选项。

## 服务器保护与自适应限流

### 单入口调度器

`core/scheduler.py` 是所有 HTTP 请求的唯一出口。Discourse JSON、robots.txt 和图片
下载都经过同一个队列：

- 只允许 1 个请求在途；
- 最小间隔 3 秒，默认 4 秒，并附加 ±1 秒随机抖动；
- 正文 API 优先于非必要图片；
- 相同图片 URL 只请求一次；
- 不安全协议、凭据 URL 和自动重定向会被拒绝。

### 状态码处理

- `429`：立即暂停全局请求，严格遵守数字或 HTTP 日期格式的 `Retry-After`；
- `403`：停止任务并锁定联网，必须人工检查后明确解除；
- `503`：进入退避等待并提高后续间隔；
- 其他 `5xx`：指数退避，受重试次数限制；
- 网络超时或连接失败：有限重试，连续失败会降低请求速度。

冷却时间和自适应间隔写入 SQLite，程序重启后不会立即重新请求。连续成功不会触发
激进的提速，恢复后保持保守节奏。

程序启动时读取系统配置的 HTTPS 代理（含 Windows 手动代理或代理环境变量），
在本次运行期间固定使用；没有配置时直连。不会轮换代理、自动切换出口或读取
`.netrc` 账号。系统代理变更后需重启软件；PAC 自动配置脚本暂不支持。
公开 API 测试成功后会保存分类列表；失败会明确显示测试失败。

## 增量采集

数据库为 `data/archive.db`，使用 WAL、外键和索引。主题成功时在一个事务中写入帖子、
更新采集版本（分类列表中的更新时间）和图片引用。

再次运行同一个分类时：

- 已有分类页游标会从上次位置继续；
- 已采集且更新时间未变的主题默认跳过正文请求；
- 只有分类列表报告更新时间变化时才重新获取帖子流；
- 启动时只恢复未完成任务，不扫描全部历史正文；
- 失败项保留在数据库中，可单独重试。

## EPUB 生成

分类是导航中的一级分组，主题是二级条目。正文按 `post_number` 排序，每个帖子保留
作者、原始时间和来源主题链接。

图片会下载到本地并嵌入 EPUB；缺失图片会替换为可见的来源链接，不会静默生成空白。
生成过程先写临时文件，EPUBCheck 返回 0 后才改名为正式文件并写入成功记录。

内置校验器使用项目或打包目录中的 `tools/epubcheck/epubcheck.jar` 和 Java 运行时。
也可以从设置中选择自己的 `epubcheck.jar`，但仍然必须通过校验。

## 监控面板

概览页显示：

- 当前请求速度；
- 最近 5 分钟请求数；
- HTTP 429 次数；
- HTTP 403 次数；
- 请求失败次数；
- 当前冷却剩余时间；
- 当前全局请求间隔；
- 预计剩余任务数量。

## 测试

测试全部使用 mock 响应和合成排版内容，不访问真实站点：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖内容包括：

- 分类树、分页参数、主题字段校验和帖子流补齐；
- 429 数字/HTTP 日期 `Retry-After`、403 全局锁定、503/5xx/超时退避；
- 持久化冷却跨重启、请求优先级、响应大小限制和危险 URL 拒绝；
- SQLite 每主题落库、增量跳过、失败重试、429 暂停任务；
- 图片地址提取、MIME/内容校验、去重、缩放和失败记录；
- 多分类 EPUB、导航、长文分章、图片本地化、缺失图片回退；
- 真实 EPUBCheck 校验；
- GUI 暂停、继续、取消状态。

## 打包

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean DiscourseEpubArchive.spec
```

产物在 `dist/DiscourseEpubArchive/`。规格文件会包含：

- `tools/epubcheck`：EPUBCheck 5.2.1 及依赖；
- `tools/jre`：Java 17 运行时；
- `resources/icons`：窗口图标。

打包目录必须整体分发。只复制 EXE 会缺少 EPUB 校验依赖。

## 目录结构

```text
discourse-epub-archive/
├── app.py
├── config/
├── core/
├── gui/
├── tests/
├── tools/
├── resources/
├── scripts/
├── README.md
├── LICENSE
├── requirements.txt
├── DiscourseEpubArchive.spec
├── start.bat
└── start-debug.bat
```

## 已知限制

- 只支持标准公开 Discourse JSON 结构；站点插件或定制接口可能需要适配。
- 论坛投票、可展开摘要、脚注浮层等插件交互只保留其静态 HTML 部分。
- 图片需要可公开访问；需要登录、签名过期或受访问控制的内容不会绕过限制。
- 超大书籍逐章构建、逐图校验，EPUBCheck 耗时可能较长。
- 不同 Discourse 版本的分页边界可能有差异，运行时会对关键字段做严格结构校验并停止该主题。
- 项目的真实站点覆盖度取决于用户配置的站点和授权范围，仓库不附带真实采集数据。

## 建议的后续工作

1. 用小规模公开测试数据校准目标站点的分类分页、置顶主题和已删除帖子边界。
2. 在明确授权下补充更多站点类型和内容格式适配。
3. 为大型书籍增加构建进度和断点恢复。

## 第三方组件

- EPUBCheck 5.2.1 及依赖遵循其随附许可证，详见 `tools/epubcheck/licenses/`。
- Java 17 精简运行时遵循其随附许可证，详见 `tools/jre/legal/`。
- Python 依赖分别遵循 `requirements.txt` 中各组件的上游许可证。

## 许可证

本项目源代码采用 MIT License，详见 `LICENSE`。第三方组件继续遵循各自许可证。
