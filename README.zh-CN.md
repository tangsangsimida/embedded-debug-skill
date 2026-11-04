# EmbeddedDebug-Skill

使用标准 Skills CLI 将调试 Skill 安装到 Codex 或 Claude Code：

```sh
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a codex -g -y
npx skills add tangsangsimida/embedded-debug-skill --skill embedded-debug -a claude-code -g -y
```

CLI 需要 Python 3.11+。硬件调试还需要用户自行安装的 OpenOCD、匹配目标架构的 GDB、调试器及现有工程中的调试配置。安装 Skill 不会安装或修改系统工具链。CLI 与 Skill 版本匹配后，可在用户授权下由内置启动器从 Skill 启动器固定的不可变 Git 提交安装；当前仓库提交不是稳定 Release。日常 PR CI 使用 Python 3.11，在 Linux、Windows、macOS 各一个 runner 上执行 Python、Mock GDB/MI、会话、构建和代码质量检查；Codex 与 Claude Code 的快速安装 smoke 在 Ubuntu 执行。完整 9 平台 × 2 Agent 安装矩阵每天夜间及正式 Release 时运行。CI 不代表任何平台已完成实体 USB 调试器或 MCU 实测。即使缺少 OpenOCD/GDB，也可运行 `edbg --help`、`edbg --version` 和 `edbg doctor`。

| 平台 | Python 3.11 完整夜间/Release runner | USB 调试器 / 实体 MCU |
|---|---|---|
| Linux x64 | Ubuntu 24.04、26.04 | 未做硬件验证 |
| Linux arm64 | Ubuntu 24.04 | 未做硬件验证 |
| Windows x64 | Server 2022、Server 2025 | 未做硬件验证 |
| Windows arm64 | Windows 11 arm64 | 未做硬件验证 |
| macOS Intel | macOS 15 Intel | 未做硬件验证 |
| macOS arm64 | macOS 15、26 | 未做硬件验证 |

## 能力范围

项目按 OpenOCD、GDB 架构和实际探测响应确定可用能力，不维护芯片厂商白名单。用户直接提供 interface/target/board 配置、完整 OpenOCD 配置或自定义初始化命令；切换目标或调试器不要求修改 Python 源码。未知能力保持 `unknown`。

v0.1.0 最小范围为工程/工具发现、经 GDB 验证的 OpenOCD 连接、持久化 GDB/MI 检查与受保护的运行控制、System Toolchain First 环境诊断，以及已验证的 Codex/Claude Skills 安装。HardFault、RTOS/ThreadX、SVD 和遥测列入路线图，不作为当前能力声明。本候选版本新增可执行的 opt-in HIL runner，但尚未执行源码级实体硬件闭环测试。

候选代码已实现工具链 PATH 诊断、现有项目配置发现、OpenOCD 服务复用与子进程管理、跨 CLI 持久化且串行化的 GDB/MI2 会话、基本 GDB 操作、Codex/Claude Skills CLI 安装验证和 Mock 测试。平台目录、进程身份/回收、锁和认证 IPC 由平台适配层封装。使用 `--session <name>` 可隔离多个目标会话；相同回环 GDB 端口不能被多个会话同时认领。尚无稳定 Release；`release-readiness.toml` 会在缺少验收项时阻止发布。HardFault/ThreadX/SVD/遥测和实体硬件链路尚未验证。

`edbg environment inspect --json` 独立检查当前工具，`edbg environment plan --json` 只提供安装来源类别和 PATH 配置建议，不修改系统工具链或 Shell 文件。需要持久添加用户 PATH 时，先确认实际工具目录，再显式运行 `edbg environment path-add --directory /实际/toolchain/bin --shell fish --authorize`（Shell 可选 `bash`、`zsh` 或 `fish`）；它只写入对应用户配置文件中的受管理区块。用 `edbg environment path-remove --shell fish --authorize` 回滚。授权的 `edbg environment install-cli --source <pip-source> --authorize` 只将 Python CLI 安装到版本隔离的用户缓存。

## 安装 CLI 与快速检查

```sh
python <installed-skill-directory>/scripts/run_edbg.py --install-python-cli doctor --json
edbg --help
edbg doctor --json
edbg environment inspect --json
edbg environment plan --json
```

Skills CLI 只负责安装 Skill 文件，不会安装 Python CLI、GDB、OpenOCD 或驱动。项目遵循 System Toolchain First：按 CLI 覆盖、可选项目配置、PATH 的顺序发现工具，不自动改 PATH 或 Shell 配置，也不自动安装系统依赖。只有用户明确审查目录并传入 `--authorize` 时，PATH 修复命令才会更改用户 Shell 文件。项目无需创建 `.edbg.toml`；它只用于覆盖发现到的设置。

## 连接示例

先在已有工程目录发现并复用当前配置：

```sh
edbg discover --json
edbg doctor --json
edbg --session bench-a session start
edbg --session bench-a registers --json
edbg registers --json
edbg backtrace --json
edbg session stop
```

发现逻辑会检查 VS Code launch/tasks、OpenOCD 命令参数、CMake/Make/PlatformIO、ELF 和常见本机 GDB Server 端口。若现有配置已明确，直接复用；只有发现结果不明确时才补充参数。无需创建 `.edbg.toml`，也不要求独立 `target.cfg`：IDE 启动配置、board/full config 或命令行参数都可提供目标配置。`connect` 会自动复用已运行的本机 GDB Server，并通过 GDB 验证连接和目标响应；持久 GDB/MI 会话供后续独立 CLI 命令复用。若某个 GDB 命令卡住，可在另一个终端运行 `edbg session cancel --authorize-run`；该操作可能暂停目标。
需要时可直接引用完整 OpenOCD 配置或已有 interface/target 脚本：

```sh
edbg --json connect \
  --interface interface/cmsis-dap.cfg \
  --target target/your-target.cfg \
  --elf build/firmware.elf
```

完整配置文件与自定义 GDB：

```sh
edbg --json --gdb /path/to/gdb --openocd /path/to/openocd connect \
  --openocd-config debug/openocd.cfg --elf build/firmware.elf
```

默认只绑定 loopback，避免意外暴露调试端口。使用 `edbg doctor --json` 检查工具及版本；缺失的可选工具只影响依赖它的具体操作。

## 目标适配

STM32F407 示例仅展示如何引用用户本机 OpenOCD 脚本，不构成硬件支持承诺。GD32E5 模板要求用户提供并验证适用的 target 配置，不暗示其与 STM32 兼容。CMSIS-DAP v1/v2 或其他适配器能否工作取决于用户的 OpenOCD 构建、配置和实际响应。

## 安全

只读检查为默认策略。暂停/继续/单步和断点增删需分别传入 `--authorize-run` 或 `--authorize`；烧录/擦除、复位、内存或寄存器写入、运行可能控制执行机构的固件、批量硬件测试，也需用户对具体操作明确授权。目标操作会写入审计日志。不得关闭急停、联锁和保护逻辑。停机读取不等于实时无扰动遥测。

参见 [安装说明](docs/installation.md)、[配置](docs/configuration.md)、[CLI 参考](docs/cli-reference.md)、[架构](docs/architecture.md) 和 [故障排查](docs/troubleshooting.md)。
