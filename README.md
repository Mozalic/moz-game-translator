# 日文游戏自动翻译器

Windows-first 的日文游戏文本本地化工具，目标是把游戏包解析、文案抽取、术语管理、大模型翻译和翻译回填串成一条可控工作流。

项目当前支持 RPG Maker MV/MZ、Unity 明文资源、TextAsset、managed ldstr、短 UI 序列化字符串与 Unity 显示别名层抽取/回填，并预留了适配器架构，后续可以优先接入成熟开源工具来支持 Ren'Py、Kirikiri、Wolf RPG、视觉小说私有包等格式。

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
- Unity 文案处理：支持常见 JSON/CSV/TSV/键值文本资源回填，并可抽取 UnityPy 可读取的 TextAsset、Mono.Cecil 可读取的 managed ldstr、场景/Prefab 序列化文件中的短 UTF-8 UI 字符串，以及不写回资源键的运行时显示别名

## 当前支持

### 已实现

- RPG Maker MV/MZ
  - 识别 `data/` 或 `www/data/`
  - 抽取 `*.json` 中的日文字符串
  - 保留 JSON path 上下文
  - 按 JSON path 回填译文
- Unity
  - 识别 `UnityPlayer.dll`、`GameAssembly.dll`、`*_Data/`、`globalgamemanagers` 等典型 Unity 游戏结构
  - 抽取 `StreamingAssets` 等目录中的 `*.json`、`*.csv`、`*.tsv`、`*.txt`、`*.lang`、`*.strings`、`*.ini` 等明文日文资源
  - 安装 `UnityPy` 后，可抽取 `.assets` / `.bundle` 中的 `TextAsset`（包括常见 UnityFS / Addressables bundle）
  - 可抽取 Unity Mono managed assembly 中的 `ldstr` 硬编码 UI 文案；该功能需要本机可用的 `csc.exe` 与 `Mono.Cecil.dll`，也可通过 `JGT_MONO_CECIL` 指定本地 Mono.Cecil
  - 可离线扫描 `level*`、`resources.assets`、`sharedassets*.assets` 等 Unity 序列化文件中的短 UTF-8 UI 字符串；回填时仅默认 patch 内置安全静态标签或手工标记 `raw_patch_policy=allow` 的条目，译文过长会跳过，短译文会补空格以保持对象偏移稳定
  - 对会被运行时代码复用的角色名/说话人 key，优先生成 `unity_display_alias` 条目；资源表中的 key 保持原文，回填时安装 `JgtUnityDisplayAliases` 运行时补丁，只在 UI Text/TMP_Text 的显示层做精确别名替换
  - 可生成离线 Unity 资源索引：扫描明文资源、UnityPy 可枚举对象，以及外部 AssetRipper 恢复项目中的 YAML/text 文件，用于检查抽取覆盖率
  - 对 JSON 保留 path；对 CSV/TSV 保留行列；对键值/脚本文本保留行号和字符串片段
  - 明文资源、UnityPy 可读取的 `TextAsset`、managed ldstr 与等长 raw UI 字符串可回填译文到复制后的游戏目录；Addressables bundle 写回后会同步修补 `catalog.bin` 中的 CRC，仍建议进游戏实测
  - Unity 回填默认启用 `auto` 字体修复：优先安装 JGT TMP 运行时动态字体 fallback，失败时再尝试 TMP FontAsset 静态 patch；如果存在显示别名，会额外安装独立的 `JgtUnityDisplayAliases` 运行时补丁
- Unreal Engine
  - 识别 Windows 打包目录、`Engine/`、`Content/Paks/`、`.pak`、`.ucas/.utoc`、Shipping exe 和打包 manifest
  - 当前用于检测和路线提示；IoStore/Pak 内部 `.uasset/.uexp` 文案抽取与补丁回填仍需接入外部解包与资产解析工作流

### 优先接入方向

项目设计上优先复用成熟开源项目，而不是为所有私有包格式重复造轮子：

- GARbro：视觉小说资源浏览和解包
- Kuriimu2：通用游戏文本格式插件工具箱
- UnityPy / AssetRipper：Unity 资源读取、分析和恢复；AssetRipper 作为外部离线恢复工具使用，仓库不内嵌其 GPL 代码
- Mono.Cecil：Unity managed assembly 字符串读取与 patch；作为外部 DLL 引用，项目不内嵌其源码
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

Unity 输出如果需要显式控制字体方案，可在 `apply` 时添加：

```powershell
python -m jp_game_translator apply "D:\Games\SomeGame" ".\work\SomeGame" "D:\Games\SomeGame_zh" --unity-font-strategy auto
```

`auto` 是默认值：先安装 `JgtTmpFontFallback` 运行时动态字体 fallback，再在运行时方案不可用时回退到 TMP FontAsset 静态 patch。`none` 只建议用于确认游戏自带字体已覆盖中文的情况。

Unity 的 `unity_display_alias` 条目用于处理“同一个字符串既是程序 key 又是 UI 显示名”的情况。典型例子是 `EVENT/Text*` 里的说话人列：回填资源时仍保留 `エリー`、`ミュージー` 等原始 key，避免破坏立绘、表情、事件或存档查找；应用阶段会从已翻译的 alias 条目或已批准术语表生成 `display_aliases.tsv`，并通过 `JgtUnityDisplayAliases` 在运行时把精确匹配的 UI Text/TMP_Text 显示为中文名。

Unity 离线资源索引用于检查“还有哪些资源没有被抽取器覆盖”。如果你先用 AssetRipper 导出了恢复项目，可以把导出目录一起传入：

```powershell
python -m jp_game_translator index-unity "D:\Games\PriTea_Ver1.1" --output ".\work\PriTea_Ver1.1\unity_resource_index.json" --assetripper-export-dir "D:\Recovered\PriTea"
```

`index-unity` 不修改游戏文件；它会输出 JSON 报告，包含明文资源、UnityPy 可读取的 bundle/object、AssetRipper 导出 YAML/text 中的日文样例与计数。AssetRipper 只作为外部工具运行，本项目不复制或链接其 GPL-3.0 代码。

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

## 开源依赖声明

本项目自身使用 MIT License，详见仓库根目录的 `LICENSE`。

- UnityPy：可选 Python 依赖，MIT License，用于 Unity `TextAsset` 抽取与回填。
- Mono.Cecil：可选 .NET 依赖，MIT License，用于 Unity managed `ldstr` 抽取、回填，以及注入 `JgtTmpFontFallback` / `JgtUnityDisplayAliases` 这类本项目生成的运行时辅助 DLL；本项目只在本地编译辅助工具时引用/复制本机已有 DLL，不内嵌其源码。
- AssetRipper：GPL-3.0 项目，本项目只支持扫描它在外部运行后生成的导出目录，不内嵌、不链接、不分发其代码。
- 其他候选工具列在 `src/jp_game_translator/resources/tool_catalog.json`，接入时优先通过外部进程或独立适配层保持许可证边界清晰。

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

- Unity MonoBehaviour / ScriptableObject 私有字段文案抽取与回填覆盖率继续提升
- Unreal Engine IoStore/Pak 解包、FText/DataTable/StringTable 抽取与 patch pak 回填
- Ren'Py 文案抽取与补丁生成
- Kirikiri/Xp3 外部工具接入
- 控制符、变量、换行、颜色码一致性校验
- 翻译质量报告与术语冲突检测
- token 用量可视化分析面板
- 更多 Provider 的速率限制与自动退避策略
