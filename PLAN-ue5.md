# PLAN-ue5 — 第四条流水线：Unreal Engine 5

状态：**M0、M1、M2 完成（见文末结论）**；下一步 M3（render + perf）。
适用机器：Windows 11 / UE 5.8.2 Launcher 版（见 M0）。Mac 上没有 UE，本线只在 Windows 机器上跑。

## 0. 目标与范围

在 `multi-model` 里加一条 `ue5` 线，角色分工不变：**Claude 生产，Grok 4.6 对抗式审查，TypeSafe Jev 做类型化判断和门禁**。

四个子模式全要，验证方式统一为 **编译 + 自动化测试**（能跑的证据优先于任何模型的判断）：

| 子模式 | 典型请求 | 产物 | 硬验证 |
|---|---|---|---|
| `cpp` | 写/改 UCLASS、组件、Subsystem、GAS 能力、网络复制 | `.h/.cpp` diff | `Build.bat <Proj>Editor Win64 Development` + `Automation RunTests <filter>` |
| `blueprint` | 解释/审查/改一个蓝图；蓝图↔C++ 迁移 | 蓝图导出文本 + 修改说明（或 C++ 等价实现） | 导出后重新 `CompileAllBlueprints`（或 Python 内 `compile_blueprint`）+ Automation |
| `render` | 材质/着色器/RDG pass/后处理/Nanite-Lumen 设置 | `.usf/.ush` + C++ + 材质参数说明 | C++ 编译 + 着色器编译（材质统计）+ 截图/像素测试（M3 定） |
| `perf` | "为什么掉帧"、优化某系统、内存/加载时间 | 诊断报告 + 改动 diff | 改前/改后 CSV Profiler 采样对比 + 编译 + Automation |

不做：打包发布、多平台（只做 Win64 Editor 目标）、源码版引擎、UE4。

## 1. 架构

```
用户请求
  └─ judge.py --preset route            # route 增加 "ue5" 选项；ue5 时追加 ue_mode 选择题
       └─ skills/ue5/SKILL.md            # 入口：定位引擎/工程 → 选子模式 → 走通用 8 步
            ├─ scripts/ue_env.sh         # 从 .uproject 的 EngineAssociation 解析引擎路径、Build.bat、UnrealEditor-Cmd
            ├─ scripts/ue_build.sh       # Build.bat 包装：解析 UBT 输出 → build.json（结果、耗时、错误列表 file:line:code:msg）
            ├─ scripts/ue_test.sh        # UnrealEditor-Cmd 头less 跑 Automation → test.json（来自 ReportExportPath/index.json）
            ├─ scripts/ue_bp_export.py   # 头less 导出蓝图（T3D）→ 解析成可读伪代码 bp.md + 结构化 bp.json
            ├─ scripts/ue_t3d_parse.py   # T3D/剪贴板文本解析器（ue_bp_export.py 和手工粘贴共用）
            ├─ scripts/roles/ue_*.md     # Grok 角色：ue_reviewer（cpp/render/perf 共用）、ue_bp_reviewer、ue_perf_analyst
            └─ judge.py presets          # gate-ue5-cpp / gate-ue5-blueprint / gate-ue5-render / gate-ue5-perf
第三方底座（不改其文件，只在本仓库 wrapper 里定制）：
  • 引擎自带 ModelContextProtocol + ToolsetRegistry + AllToolsets（5.8 起）
  • EpicGames/unreal-engine-skills-for-claude-code-plugin（官方 Claude Code 插件，MIT）
```

设计原则：
- **头less 优先**。所有硬验证走 `UnrealEditor-Cmd` / `Build.bat`，不依赖 GUI 编辑器在跑；MCP 实时编辑器路径是 M5 的可选增强，不是主路径。
- **证据进状态，判断在代码**。`build.json` / `test.json` / `bp.json` 原样进 TypeSafe 的 `state`；阈值只在 `judge.py THRESHOLDS`。
- **Grok 只看文本**。给 Grok 的是 diff + 导出文本 + 构建/测试报告，从不给它二进制 `.uasset`。
- **工程零侵入**。不改用户 `.uproject`（插件用 `-EnablePlugins=` 命令行临时启用），不进 `Content/`，产物全放 `~/.multi-model/runs/ue5/<slug>/`。

## 2. 新增文件清单

| 文件 | 里程碑 | 说明 |
|---|---|---|
| `skills/ue5/SKILL.md` | M1 | 入口 + 通用流程；子模式差异写在同文件的分节里，避免四份重复 |
| `scripts/ue_env.sh` | M1 | `ue_env.sh <path/to/.uproject>` → 输出 `ENGINE_ROOT / BUILD_BAT / EDITOR_CMD / PROJECT_NAME / EDITOR_TARGET` 的 shell 赋值；读 `LauncherInstalled.dat` 解析 `EngineAssociation` |
| `scripts/ue_build.sh` | M1 | 包装 `Build.bat`，超时默认 600s（`UE_BUILD_TIMEOUT`），输出 `build.json` |
| `scripts/ue_test.sh` | M1 | 包装 `UnrealEditor-Cmd … -ExecCmds="Automation RunTests <filter>; Quit"`，输出 `test.json` |
| `scripts/ue_bp_export.py` | M2 | 在编辑器 Python 里跑（`-run=pythonscript`），T3D 导出 + 调 `ue_t3d_parse.py` |
| `scripts/ue_t3d_parse.py` | M2 | 纯 Python，无 `unreal` 依赖；解析 `Begin Object … CustomProperties Pin (…)` 为节点/引脚/连线/默认值/注释 |
| `scripts/roles/ue_reviewer.md` | M1 | cpp/render/perf 共用的 UE 特化审查角色（UPROPERTY/GC、TObjectPtr、Tick 开销、复制、模块依赖、`.Build.cs`） |
| `scripts/roles/ue_bp_reviewer.md` | M2 | 审查蓝图伪代码：执行流断裂、Tick 里做重活、Cast 链、硬引用、缺少 IsValid |
| `scripts/roles/ue_perf_analyst.md` | M3 | 读 CSV Profiler 摘要，给出瓶颈假设 + 反例 |
| `scripts/judge.py` | M1–M3 | `route` 增 `ue5`；新增 `ue_mode` 题；四个 gate preset；THRESHOLDS 增 `ue_build_pass`、`ue_test_pass` |
| `EVAL.md` | M4 | 追加 ue5 行 |
| `README.md` | M1 | Windows 前置条件补 UE 部分 |

## 3. 门禁问题表（TypeSafe）

所有 gate 共有（来自 `general`）：`findings_resolved`、`scope_respected`、`readiness`。
以下是 ue5 特有题；**阻断**列为 yes 的 `noul` 必须 ≥ `gate_pass`。

| preset | key | type | 看什么 | 阻断 |
|---|---|---|---|---|
| 所有 ue5 | `build_ok` | noul | `build.json.result == Succeeded` 且 `errors == []`（题目让模型核对报告，不让它猜） | yes |
| 所有 ue5 | `tests_ok` | noul | `test.json.failed == 0` 且请求要求的测试确实在 `tests[]` 里跑过 | yes |
| 所有 ue5 | `no_editor_only_leak` | noul | Runtime 模块里没有 `#if WITH_EDITOR` 之外的编辑器 API | yes |
| cpp | `reflection_correct` | noul | UCLASS/UPROPERTY/UFUNCTION 宏、`GENERATED_BODY`、`TObjectPtr`、GC 可达性 | yes |
| cpp | `lifecycle_correct` | noul | BeginPlay/EndPlay/Tick 用法、Timer/Delegate 解绑、`IsValid` | yes |
| cpp | `replication_consistent` | noul | 若涉及网络：`DOREPLIFETIME`、RPC 修饰符、权限判断一致 | yes（仅当 state.networked） |
| cpp | `test_adequacy` | score | 新增/修改的行为是否有对应 Automation 测试（0 无 / 1 冒烟 / 2 覆盖主要分支 / 3 覆盖边界） | no（≥ `readiness_pass`） |
| blueprint | `export_faithful` | noul | `bp.md` 中每个事件的执行链与 `bp.json` 连线一致（防导出器丢链，见 M0） | yes |
| blueprint | `explanation_matches_graph` | noul | Claude 的解释 / 迁移代码与伪代码逐事件对应 | yes |
| blueprint | `no_tick_heavy_work` | noul | Tick/Timer 高频路径里没有查找、Cast 链、Spawn | no |
| render | `shader_compiles` | noul | 材质/着色器编译报告无错误 | yes |
| render | `cost_reported` | noul | 给出了指令数/采样数/pass 数变化 | no |
| perf | `baseline_captured` | noul | 改前 CSV 采样存在且与改后采样条件相同（地图、时长、分辨率） | yes |
| perf | `improvement_real` | noul | 改后关键指标（Frame/GameThread/RenderThread/GPU ms）不劣于改前，且差异大于噪声 | yes |
| perf | `hypothesis_verified` | score | 瓶颈假设是否被采样数据直接支持 | no |

## 4. 里程碑

### M0 调研（本次；纯调研，只修一个已知 bug）
1. 引擎版本/路径/Launcher 或源码；`Build.bat`、`UnrealEditor-Cmd` 路径与可用 flag。
2. 手动跑通一次 Editor 目标编译 + 一次 Automation，记录耗时与输出格式。
3. 蓝图导出：T3D 复制粘贴 vs Editor Python，各试一次，记录可行性和信息丢失。
4. 第三方底座调研。
5. 修 `judge.py` `_api_key()` 只回退 `~/.zshrc` 的 bug，实测 `--preset route`。

### M1 骨架 + cpp 子模式端到端
- `judge.py`：`route` 加 `ue5`；`ue_mode` 题（cpp/blueprint/render/perf）；`gate-ue5-cpp`。
- `ue_env.sh` / `ue_build.sh` / `ue_test.sh`（解析规则按 M0 样例）。
- `skills/ue5/SKILL.md`：clarify → 定位工程 → 产出 → `ue_build.sh` → `ue_test.sh` → Grok（`ue_reviewer`）→ TypeSafe filter → 修 → gate → 交付（审计行同其它线）。
- 验收：在 Kurodemo 上完成"新增一个带 Automation 测试的 UActorComponent"，门禁通过；再种一个反射 bug（漏 `UPROPERTY` 导致 GC）被 Grok+gate 拦下。
- 顺手：SKILL 里所有 `python3` 改成通过 `PYTHON` 变量/`ue_env.sh` 解析出的解释器（Windows 上 `python3` 是 Store 空壳，见 M0）。

### M2 blueprint 子模式
- `ue_bp_export.py` + `ue_t3d_parse.py`：头less T3D 导出 → 伪代码；同一解析器接受用户从图里 Ctrl+C 粘贴的文本。
- `ue_bp_reviewer.md`、`gate-ue5-blueprint`（含 `export_faithful`）。
- 验收：ThirdPerson 模板角色蓝图的解释与迁移，事件链 8/8 保真（M0 中官方 DSL 只有 3/8）。

### M3 render + perf 子模式
- render：确定着色器编译证据的取法（候选：`-run=pythonscript` 下 `unreal.MaterialEditingLibrary.recompile_material` + `get_statistics`，需真实 RHI，不能 `-nullrhi`）；`gate-ue5-render`。
- perf：`UnrealEditor-Cmd -game` 头less CSV Profiler 采样（`-csvProfile`/`csvprofile start|stop`）→ 用 `CsvTools`/自写脚本聚合 → 改前/改后对比进 state；`ue_perf_analyst.md`；`gate-ue5-perf`。
- 两个子模式各有一个 spike 任务先验证取证方式，再写门禁。

### M4 eval
按 §5 跑；结果进 `EVAL.md`。

### M5 加固与可选路径
- 可选：接 Epic 官方插件的 `unreal-mcp`（实时编辑器：Live Coding、`AutomationTestToolset`、`read_graph_dsl` 作为第二导出源）。
- 门禁阈值按 M4 数据调 `THRESHOLDS`。
- README/SKILL 文档收口；`claude plugin validate`。

## 5. Eval 计划

每个子模式 ≥ 3 个 case，其中 ≥ 1 个负样本（无缺陷，检验不误拦）：

| 子模式 | 种植缺陷 | 期望 |
|---|---|---|
| cpp | 成员 `UObject*` 无 `UPROPERTY`（GC 悬垂） | Grok 提出 → filter 保留 → gate `reflection_correct` fail |
| cpp | Timer 在 EndPlay 未清 | Grok 提出；gate `lifecycle_correct` fail |
| cpp | 测试通过但复制变量漏 `DOREPLIFETIME` | gate `replication_consistent` fail |
| cpp | 无缺陷 + 测试全过 | gate pass，readiness ≥ .7 |
| blueprint | 解释里漏掉一个事件（模拟导出丢链） | `export_faithful` 或 `explanation_matches_graph` fail |
| blueprint | Tick 里 GetAllActorsOfClass | Grok 提出（非阻断，进 findings） |
| render | 材质改动使指令数翻倍但没报告 | `cost_reported` low |
| perf | 只有改后采样没有基线 | `baseline_captured` fail |
| perf | 改后 GameThread 更慢却声称优化 | `improvement_real` fail |

记录：Grok 条数/保留数/成本、gate 各题概率、编译与测试耗时；与 `EVAL.md` 现有表同格式。

## 6. 风险

| 风险 | 影响 | 对策 |
|---|---|---|
| 蓝图导出丢信息（本计划唯一可能整体失败的点） | blueprint 子模式不可信 | M0 已验证：T3D 头less 导出无丢失，官方 DSL 丢多出口事件链 → 主路径用 T3D + 自写解析器，DSL 只做辅助 |
| 编译时间不可控（全量重编 10–30 min） | 单轮审查太慢 | 只做增量；超时 600s 报错让人决定；建议 M1 前把 `UE_BUILD_TIMEOUT` 定死 |
| 机器内存紧张（M0 时 UBT 因可用内存 1.8 GB 把并行降到 1） | 编译慢 3–8 倍 | 跑之前关编辑器/浏览器；`ue_build.sh` 把"limiting max parallel actions"写进 build.json 提示 |
| MSVC 报错是 GBK 中文，UTF-8 日志变乱码 | 错误解析失败 | `ue_build.sh` 用 `chcp 65001` 或 Python 以 `cp936` 回退解码后再匹配 `path(line,col): error Cnnnn` |
| Automation 失败时 `UnrealEditor-Cmd` 退出码不可靠 | 误判通过 | 只信 `index.json` 的 `failed` 计数，不信退出码 |
| `-nullrhi` 下没有着色器/像素证据 | render 子模式取证难 | M3 spike；必要时 render 验证降级为"C++ 编译 + 材质统计"并在审计行标注 |
| 官方 MCP/Toolset 都是 Experimental，5.8 → 5.9 可能改 API | M5 可选路径脆 | 主路径不依赖它们；只在 M5 接 |
| `python3` 是 Store 空壳 | 现有三条线在本机静默失败 | M1 统一改为解释器探测（`grok_review.sh` 已改） |

---

## M0 结论（2026-09-21，Windows 机器）

### 1. 引擎
- 两个 **Launcher 版**（`Engine/Build/InstalledBuild.txt` 存在，非源码版）：
  - **UE 5.8.2**（CL 56702186）`E:\ProgramSoftware\UrealEngine\UE_5.8` ← **本线以此为准**（官方 MCP/Toolset/蓝图 DSL 只在 5.8 有）
  - UE 5.6.1（CL 44394996）`E:\ProgramSoftware\UrealEngine\UE_5.6`（无 ModelContextProtocol 插件）
- 路径（5.8）：`Engine\Build\BatchFiles\Build.bat`、`Engine\Build\BatchFiles\RunUAT.bat`、`Engine\Binaries\Win64\UnrealEditor-Cmd.exe`。
- 工具链：Visual Studio Community 2026 (18.9) `E:\Visual Studio\VS`，MSVC 14.50 / 14.51；另有 VS 2022 BuildTools；UBT 自带 .NET 10。
- 工程：`E:\ProgramSoftware\UE_Project\Kurodemo`（5.8，C++，Content 为空）、`Myfirst`（5.6，C++，ThirdPerson 模板）。M0 验证用 Kurodemo。
- 目标平台：Win64 Editor（Development）。

### 2. 编译与 Automation 实测（Kurodemo，UE 5.8.2）

**编译**（`Build.bat KurodemoEditor Win64 Development -Project=<uproject> -WaitMutex -NoHotReload`）
- 增量（touch 一个 .cpp）：**47.8 s**，7 个 action；可用内存 1.83 GB → UBT 把并行降到 1。
- 无改动：1.2 s，`Target is up to date`。
- 新增文件：UBT 自动 `Invalidating makefile … (source file added)`；同一秒内创建再编译有一次没被发现（目录 mtime 粒度），`ue_build.sh` 遇到"up to date 但 diff 有新文件"时用 `-NoUBTMakefiles` 重跑一次。
- 失败样例（故意 `undefined_symbol_xyz`）：**退出码 6**，
  ```
  E:\...\M0Probe.cpp(2,30): error C2065: ��undefined_symbol_xyz��: δ�����ı�ʶ��   ← GBK 中文被当 UTF-8
  Result: Failed (OtherCompilationError)
  ```
- 解析规则（`build.json`）：`Result: (Succeeded|Failed \((\w+)\))`、`Total execution time: ([\d.]+) seconds`、错误行 `^(.+?)\((\d+),(\d+)\): (error|warning) (C\d{4}|LNK\d{4}): (.*)$`、并行提示 `limiting max parallel actions to (\d+)`；日志按 cp936 回退解码。退出码：0 成功，6 编译错误。
- 日志：`~/.multi-model/runs/ue5/m0/build_incremental.log`、`build_fail.log`。

**Automation**（`UnrealEditor-Cmd <uproject> -ExecCmds="Automation RunTests System.Core.Math; Quit" -unattended -nopause -nosplash -nullrhi -NoSound -stdout -FullStdOutLogOutput -log=<f> -ReportExportPath=<dir> -TestExit="Automation Test Queue Empty"`）
- 总耗时 **28 s**（编辑器启动到"Ready to start automation"约 15 s；40 个测试 6.8 s）；6354 个测试可用；退出码 0。
- 逐条日志：`LogAutomationController: Display: Test Completed. Result={Success|Fail} Name={…} Path={…}`。
- 报告 `<dir>/index.json`：顶层 `succeeded / succeededWithWarnings / failed / notRun / totalDuration / devices[]`，`tests[]` 每项 `fullTestPath / state / duration / errors / warnings / entries[]`（entries 里是失败时的消息+文件+行号）。`ue_test.sh` 以 `index.json` 为准，不信退出码。
- 报告：`~/.multi-model/runs/ue5/m0/report/index.json`、`automation_stdout.log`。

### 3. 蓝图导出（关键结论：**可行，但官方 DSL 丢信息，主路径用 T3D**）

在一次性探针工程 `~/.multi-model/runs/ue5/m0/BpProbe`（ThirdPersonBP 模板 + 共享 Input/Characters/LevelPrototyping 内容）上，用 `UnrealEditor-Cmd BpProbe.uproject -run=pythonscript -script=bp_export_probe.py -nullrhi -unattended` 头less 跑三条路，总耗时 12 s：

| 路径 | 结果 | 信息丢失 |
|---|---|---|
| **A. 裸 `unreal` 模块** | `unreal.EdGraph`/`EdGraphNode` 反射 API 里没有节点枚举；5.8 新增 `unreal.BlueprintGraphEditor`（`get_graph_editor(_by_name)`、`list_all_nodes`、`list_nodes_of_class`、`list_comment_nodes`、`find_event_node` 等），节点上 `list_all_pins()` | 能枚举节点/引脚，但要自己拼连线和默认值；等于自写导出器 |
| **B. 官方 EditorToolset `BlueprintTools.read_graph_dsl`**（`-EnablePlugins=PythonScriptPlugin,EditorToolset,ToolsetRegistry` 临时启用，不改 .uproject） | 4 个图全部导出为 S 表达式，EventGraph 698 字符，1.8 s | **丢多出口事件链**：`Decompiler` 只沿 `then` 引脚走（`blueprint_dsl.py` ≈ L2519 `_follow_exec_named(ni, _PIN_THEN)`），`K2Node_EnhancedInputAction` 的 `Triggered/Started/Completed` 全部丢失 → EventGraph 8 条事件链只剩 3 条（IA_Move/IA_Look/IA_MouseLook/IA_Jump 变成空事件）；注释节点、Knot、节点位置也不在 DSL 里。这是 5.8.2 自带解码器的 bug，**不能单独作为审查输入** |
| **C. T3D 对象导出**（`unreal.AssetExportTask` + `ObjectExporterT3D`，`unreal.Exporter.run_asset_export_task`） | 0.03 s，1.4 MB `.t3d`（含 CDO/SCS，全蓝图） | **无丢失**：每个 `K2Node_*` 的 `CustomProperties Pin (PinName, LinkedTo=(Node Guid), DefaultValue, …)`、`NodePosX/Y`、`NodeComment`、`EdGraphNode_Comment` 文本、`InputAction` 引用全在。缺点是啰嗦、含 7 个孤儿 `BreakVector2D` 隐藏节点（split pin 残留），需要解析器过滤 |
| **D. 手工 T3D 复制粘贴**（图里 Ctrl+A/Ctrl+C） | 未在 GUI 里实操；源码确认 `FEdGraphUtilities::ExportNodesToText` 走同一个 `UExporter` T3D 通道，剪贴板文本 = C 路径中图节点那一段 | 与 C 相同；作为用户不便跑头less 时的备用输入，同一解析器 |

决定：**M2 主路径 = C（头less T3D 导出）+ 自写 `ue_t3d_parse.py` 生成伪代码**；D 共用解析器；B 只做辅助（作为第二视角给 Grok 对照，或等 Epic 修复）；A 不用。
产物：`~/.multi-model/runs/ue5/m0/bp_export/{*.dsl, BP_ThirdPersonCharacter.t3d, graph_dsl_docs.md, probe_result.json}`，脚本 `bp_export_probe.py`。

### 4. 第三方底座：**有，而且是官方的**
- **引擎内置（5.8）**：`Engine/Plugins/Experimental/ModelContextProtocol`（HTTP MCP server，`ModelContextProtocol.StartServer`，默认 `127.0.0.1:8000/mcp`，附 `Extras/Proxy/Bin/Win64/unreal_mcp_proxy.exe`）、`ToolsetRegistry`、`Toolsets/AllToolsets` 聚合 21 个 toolset，其中与本线相关：`EditorToolset`（蓝图/资产/材质读写，Python 实现）、`AutomationTestToolset`（返回 `total/passed/failed/skipped/tests[]`）、`LiveCodingToolset`（`CompileLiveCoding` 阻塞到编译完成并返回 MSVC 诊断）。全部 Experimental。
- **Claude Code 插件**：[EpicGames/unreal-engine-skills-for-claude-code-plugin](https://github.com/EpicGames/unreal-engine-skills-for-claude-code-plugin) v3.1.1，MIT，`/plugin install unreal-engine-skills-for-claude-code@claude-plugins-official`；skills：`unreal-mcp`（驱动实时编辑器；只暴露 `list_toolsets/describe_toolset/call_tool` 三个元工具）、`create-toolset`、`unreal-skill`。这就是 ue5 线对标 `algo-sensei` / `academic-research-skills` 的底座——**但它要求编辑器在跑**，与本线"头less 优先"互补，放 M5。
- 社区知识型 skill（纯 Markdown，无工具）：[quodsoler/unreal-engine-skills](https://github.com/quodsoler/unreal-engine-skills)（27 个 UE C++ skill，Agent Skills 规范）、[maystudios/claude-skills](https://github.com/maystudios/claude-skills)、[ibrews/ue5-mcp](https://github.com/ibrews/ue5-mcp)（第三方 MCP，5.8 官方出来后价值下降）。M1 写 `ue_reviewer.md` 时可参考 quodsoler 的检查清单，不引入为依赖。

### 5. `judge.py` bug 修复
- `_api_key()`：环境变量之后按序读 `~/.zshrc, ~/.bashrc, ~/.bash_profile, ~/.profile`，接受 `export X=`/`X=`、带引号、行尾注释；单元测试通过。
- 实测 `--preset route`：jev-1.13.0，1.1 s，629/107 tokens；UE5 请求当前路由到 `general`（0.99）——M1 加 `ue5` 选项后重测。
- 顺带发现并修复：本机 `python3` 是 Microsoft Store 重定向空壳（退出码 49、无输出），`grok_review.sh` 的 JSON 后处理因此**静默输出空**；已改为探测 `python3`→`python`（或 `PYTHON` 变量）。三条现有 skill 的 `python3 …` 命令同样中招，M1 统一处理。
- Grok 实测：`~/.grok/bin/grok.exe`（1.0.25）已登录，reviewer 角色 11 s / $0.016。

### 6. 缺的东西 / 需要你给的输入
- **`TYPESAFE_API_KEY` 用户环境变量未设置**（M0 是从 `E:\Code\Claude\typesafe key.txt` 临时注入的）。请执行一次：`setx TYPESAFE_API_KEY "<key>"`，或把 `export TYPESAFE_API_KEY=…` 写进 `~/.bashrc`。
- Tavily：`SKILL.md` 说未配置；本会话里有 `tavily` MCP server 在连，M1 时确认可用后接进 `general`/`ue5` 的 research 步。
- Epic 官方插件安装要走 `/plugin install …@claude-plugins-official`（M5 才需要）。
- 已按推荐值假定：引擎 5.8.2、验证工程 Kurodemo、`UE_BUILD_TIMEOUT=600s`、目标 Win64 Editor。不同意请在 M1 开始前改这里。

---

## M1 结论（2026-09-21）

交付：`skills/ue5/SKILL.md`、`scripts/ue_env.sh`、`scripts/ue_build.sh`、`scripts/ue_test.sh`、`scripts/roles/ue_reviewer.md`、`judge.py`（`route` 增 `ue5` + `ue_mode`，`gate-ue5-cpp`，硬证据 `evidence.build_ok/tests_ok` 在代码里判）、根 `SKILL.md` 路由、README、EVAL #7/#7b/#8。

验收（Kurodemo，`UHealthComponent` + 4 个 Automation 测试）：
- 正样本：v1 被 Grok 抓到两个**真实且未预埋**的问题（`IsNearlyEqual` 吞掉最后一击；测试没绑 `OnDeath`），修后门禁通过（readiness .95）。
- 负样本（预埋 `TObjectPtr<UObject>` 无 `UPROPERTY`）：编译/测试全绿，Grok critical（p .82 保留），门禁 `reflection_correct` .44 → 拦下。
- 路由：三条 UE 请求全部 `ue5`（置信 1.0），`ue_mode` 分别 cpp/blueprint/perf；非 UE 请求仍 `general`。

实现中确定的规则（已写进脚本/skill）：
- `ue_build.sh`：两个陈旧 makefile 守卫（"up to date 但 Source 更新" → `-NoUBTMakefiles` 重跑；C1083 指向已删除 .cpp → 重跑）；日志按 UTF-8→cp936 回退解码；退出码 0/1/124。
- `ue_test.sh`：结论只看 `report/index.json`；`found == 0` 记为 `NoTests`（失败）；默认 `-nullrhi`，`--rhi` 可关。
- 门禁问题改成"反问缺陷"（yes == 有缺陷），在 `1 - gate_pass` 处判失败；原先"规则都遵守了吗"的问法在正确代码上只有 .61，会误拦。§3 表中 cpp 行的题目语义按此理解。
- Windows：`python3` 空壳、stdout 代码页（`judge.py` 强制 UTF-8 输出，`@file` 按 UTF-8 读；`grok_review.sh` 设 `PYTHONIOENCODING`）。**MSYS 路径不要写进 `python -c` 字符串**，用参数传或 `cygpath -w`。
- 测试放 `Private/Tests/`，反射类型必须在头文件里（UHT 不处理 .cpp 里的 UCLASS）。

未做 / 留给后面：
- `replication_consistent` 题只在 `networked: true` 时出现，尚无联网样本验证（M4）。
- Kurodemo 里的 `HealthComponent` 夹具留在工程里未提交（`Source/Kurodemo/Public|Private`），副本在 `~/.multi-model/runs/ue5/Kurodemo/m1-health/`；要清可以直接删。
- `TYPESAFE_API_KEY` 用户环境变量在 M1 开始时仍未生效（User/Machine/注册表/所有 profile 都没有），本轮仍从 key 文件注入。

---

## M2 结论（2026-09-21）

交付：`scripts/ue_t3d_parse.py`（纯 Python，T3D/剪贴板 → `bp.json` + 伪代码）、`scripts/ue_bp_export.py`（编辑器内跑：T3D 导出、组件、`compile_blueprint`）、`scripts/ue_bp_export.sh`（头less 包装，`-EnablePlugins=PythonScriptPlugin`，不改 .uproject）、`scripts/roles/ue_bp_reviewer.md`、`judge.py` `gate-ue5-blueprint`（`bp_compiles`/`export_complete` 在代码里判）、`skills/ue5/SKILL.md` B 节、EVAL #9/#9b/#10。

验收：6 个模板蓝图导出全部编译 `BS_UP_TO_DATE`、exec 覆盖 100%；角色蓝图 8 条事件链 8/8（M0 官方 DSL 3/8）。解释任务：正样本 Grok `sound` + 门禁通过；负样本（丢 Started/Completed、编造 Tick、说错 Move 旋转）Grok 抓到 3 个预埋 + 2 个未预埋，门禁 `graph_misrepresented` .96 拦下。

实现中确定的规则：
- T3D 里图的成员以 EdGraph 的 `Nodes(i)` 数组为准；同 outer 下的孤儿对象（split-pin 残留、宏实例幽灵、编译中间图 `ExecuteUbergraph_*`/`*_MERGED`）一律忽略并计数。
- 伪代码规则：每个 exec 根一个块；沿**所有** exec 出口走，命名出口写成 `→ Pin:`（Branch 写 `true/false`，validated get 写 `Is Valid/Is Not Valid`）；Knot 透明；数据输入内联，纯节点递归展开（深度 12）；`Set Var = expr`、`Target.Var`、`Cast → AsX`；注释框按几何包含挂到入口块前。
- 组件不在 T3D 里，用 `SubobjectDataSubsystem` 另取。
- 阈值：`THRESHOLDS` 新增 `cpp_defect_max=.25`、`bp_fidelity_max=.5`（原来统一用 `1-gate_pass`）；`unrequested_scope`、`tick_heavy_work` 只进 `warnings`。
- Git Bash 会把参数和环境变量里形如 `/Game/...` 的值改写成 Windows 路径 → 资产路径 base64 后经环境变量传给 Python。
- `-run=pythonscript` 走 `UE_BP_EXPORT_CONFIG` 环境变量传配置；`compile_blueprint` 后状态从 `<BlueprintStatus.BS_UP_TO_DATE: 3>` 里正则取。

未做 / 留给后面：
- 只读：不改 `.uasset`。"改蓝图"交付的是节点级改动清单，或走 cpp 迁移。
- 真实剪贴板文本只用模拟样本验证过（格式来自 `FEdGraphUtilities::ExportNodesToText` 源码），M4 补一次编辑器里 Ctrl+C 的实测。
- 未覆盖的节点类型（Timeline 曲线值、Delay、MakeArray、Interface 消息、Sequencer/AnimBP/Widget 专用图）只会按类名兜底显示；AnimBP/UMG/Material 图未测。
- `migration_complete` 题没有真实迁移样本（M4）。
