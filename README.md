# 日文游戏自动翻译器

Windows-first 的日文游戏文本本地化工具，目标是把游戏包解析、文案抽取、术语管理、大模型翻译和翻译回填串成一条可控工作流。

项目当前重点支持 RPG Maker MV/MZ，并预留了适配器架构，后续可以优先接入成熟开源工具来支持 Unity、Ren'Py、Kirikiri、Wolf RPG、视觉小说私有包等格式。

## 功能概览

- PySide6/Qt 桌面 GUI，面向 Windows 使用
- 自动识别游戏引擎并抽取日文文案
- 工作区管理：自动发现 `work/` 下已有工作区，并匹配源游戏目录
- 文案列表查看、搜索、手工编辑译文与状态
- 专属名词候选提取、批量处理、人工批准/拒绝
- OpenAI-compatible Provider 配置，支持模型读取、测试 API、模型选择
- 内置正文翻译 Prompt 与术语翻译 Prompt，支持在 GUI 中编辑
- DeepSeek/OpenAI 兼容 Chat Completions 翻译
- 批量翻译、全部翻译、停止翻译、并发控制
- 文案去重翻译：相同原文只请求一次，自动回填到所有重复条目
- 动态批处理：默认 60 条唯一文案一批，并用字符预算防止超长请求
- 按批筛选术语表：只把当前批次命中的已批准术语放进 Prompt
- token 用量统计：输出 `logs/token_usage.jsonl`，记录请求耗时、token、cache hit/miss、批次规模
- RPG Maker MV/MZ 回填：把翻译写回复制后的游戏目录

## 当前支持

### 已实现

- RPG Maker MV/MZ
  - 识别 `data/` 或 `www/data/`
  - 抽取 `*.json` 中的日文字符串
  - 保留 JSON path 上下文
  - 按 JSON path 回填译文

### 优先接入方向

项目设计上优先复用成熟开源项目，而不是为所有私有包格式重复造轮子：

- GARbro：视觉小说资源浏览和解包
- Kuriimu2：通用游戏文本格式插件工具箱
- UnityPy / AssetRipper：Unity 资源读取、分析和恢复
- rpatool / unrpyc：Ren'Py RPA 与 RPYC 处理
- RPGMaker Trans / RPGMTranslate：RPG Maker XP/VX/VX Ace
- wolftrans：Wolf RPG Editor 文案处理
- KirikiriTools / VNTranslationTools：Kirikiri 与 VN 文本补丁参考

## 安装

推荐 Python 3.8+。GUI 依赖使用 PySide6：

```powershell
python -m pip install -r requirements-gui.txt
```

也可以以可编辑模式安装：

```powershell
python -m pip install -e .[gui]
```

## 启动 GUI

在仓库根目录运行：

```powershell
.\scripts\launch_gui.cmd
```

或：

```powershell
$env:PYTHONPATH="src"
python -m jp_game_translator.qt_gui
```

旧版 WinForms/PowerShell GUI 仍保留为备用入口：

```powershell
.\scripts\launch_winforms_gui.cmd
```

## 翻译工作流

GUI 顶部主流程围绕四步展开：

1. 读取游戏内置文案，生成文案列表和术语候选
2. 一键翻译专属名词，并由用户检查、批准、批量处理
3. 翻译正式文案，支持翻译一部分或自动全部翻译
4. 将翻译后的文案回填到游戏副本

设置中心包含：

- 全局配置
- 名词提取配置
- 正文翻译 Prompt
- 术语翻译 Prompt
- Provider/API 配置入口

## API 配置

示例配置位于：

```text
configs/providers.example.json
```

推荐把 API Key 放进环境变量，不要写入配置文件：

```powershell
setx DEEPSEEK_API_KEY "你的 DeepSeek API Key"
```

配置示例：

```json
{
  "providers": {
    "deepseek": {
      "type": "openai_compatible",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-v4-pro",
      "temperature": 0.2,
      "json_mode": false
    }
  }
}
```

`setx` 后需要重新打开终端或重启 GUI。

## CLI 用法

开发时无需安装包，设置 `PYTHONPATH` 即可：

```powershell
$env:PYTHONPATH="src"
python -m jp_game_translator --help
```

常用命令：

```powershell
python -m jp_game_translator detect "D:\Games\SomeGame"
python -m jp_game_translator extract "D:\Games\SomeGame" --workspace ".\work\SomeGame"
python -m jp_game_translator terms ".\work\SomeGame" --limit 200
python -m jp_game_translator review-terms ".\work\SomeGame"
python -m jp_game_translator translate ".\work\SomeGame" --config ".\configs\providers.example.json" --provider deepseek --target zh-Hans --limit 60
python -m jp_game_translator apply "D:\Games\SomeGame" ".\work\SomeGame" "D:\Games\SomeGame_zh"
```

## 工作区结构

抽取后会生成一个工作区：

```text
work/SomeGame/
  manifest.json
  entries.jsonl
  glossary.tsv
  logs/
    token_usage.jsonl
```

文件说明：

- `manifest.json`：游戏目录、适配器、引擎名称、条目数量
- `entries.jsonl`：统一文本中间格式，每行一条文案
- `glossary.tsv`：术语候选、译名、状态、出现次数
- `logs/token_usage.jsonl`：每次请求的 token、耗时、cache hit/miss、批次统计

`work/` 默认不应提交到 GitHub，因为它可能包含游戏文本、路径和翻译数据。

## 翻译优化

当前翻译管线包含几项成本控制：

- 只发送当前批次命中的术语，避免每批重复携带完整术语表
- 使用紧凑 JSON，省去缩进和空字段
- 相同原文去重翻译，回包后自动扇出到重复文案
- 默认 60 条唯一文案一批，上限可在 GUI 中调到 32 并发
- 使用 `6000` 源字符预算自动拆分长批次
- 记录 DeepSeek `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`

Prompt 日志可通过环境变量控制：

```powershell
$env:JGT_PROMPT_LOG="summary" # 默认，输出摘要
$env:JGT_PROMPT_LOG="full"    # 输出完整 prompt
$env:JGT_PROMPT_LOG="off"     # 关闭 prompt 日志
```

## 开发与测试

运行测试：

```powershell
python -m unittest discover -s tests
```

编译检查：

```powershell
python -m compileall src tests
```

项目结构：

```text
src/jp_game_translator/
  adapters/       游戏引擎适配器
  core/           数据模型、工作区读写
  terminology/    术语提取与术语表
  translation/    Provider、Prompt、批处理翻译
  resources/      GUI 文案与开源工具目录
  qt_gui.py       PySide6 GUI
  cli.py          CLI 入口
```

## 安全提示

- 不要提交真实 API Key
- 不要提交 `work/` 工作区和游戏文本
- 不要提交本地 GUI 设置文件
- 推荐使用环境变量管理 Provider Key

## Roadmap

- Unity 文案抽取与回填
- Ren'Py 文案抽取与补丁生成
- Kirikiri/Xp3 外部工具接入
- 控制符、变量、换行、颜色码一致性校验
- 翻译质量报告与术语冲突检测
- token 用量可视化分析面板
- 更多 Provider 的速率限制与自动退避策略
