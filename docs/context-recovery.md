# 长任务 compact 恢复部署

模块已实现，使用 Python 3.11+ 标准库和 POSIX 文件锁，面向 Linux/macOS。它保存显式绑定的任务状态，在 Codex 的 SessionStart startup/resume/compact 事件中恢复；不调整原生 compact 阈值，不创建新主会话，不调用额外模型。

## 在各服务器安装

在各服务器克隆或同步本仓库后执行：

```sh
python3 install.py --with-context-recovery
python3 install.py --with-context-recovery --apply
python3 install.py --check
```

目标目录优先级仍是 `--target`、`CODEX_HOME`、`~/.codex`。共享 AGENTS.md 为符号链接时加 `--skip-instructions`；启动 hook 自带简短使用指引，已有 astra-planner 入口也会引导读取长任务协议。首次启用时，恢复 hook 的命令与 fork_turns 护栏相同：`python3`、安装目录下的 `context-state.py`、以及 `hook` 参数。`python3` 留到 Codex 运行 hook 时再解析，安装器不把当时的解释器绝对路径写进定义。因此换任何一个 Python 3.11+ 再执行 `--check` 或 `--apply`，这条命令都不变；虚拟环境被删除，或 pyenv、Homebrew 升级掉安装时的解释器，hook 也不会因此静默失效。路径中的空格和单引号按 shell 规则引用。

已经启用的安装以 `.codex-agent-kit.json` 里的 registration 为准。磁盘上的受管 handler 与该记录一致时，命令原样保留，包括旧版安装器写入的绝对解释器路径。普通升级不改写这条定义，已经在 `/hooks` 信任过的恢复 hook 不必重新信任。受管 handler 被本地改动时仍然拒绝；`--overwrite-local` 先备份，再恢复为记录中的 registration，不会改成另一个解释器。两处重复注册时仍直接拒绝。若要让旧的绝对解释器路径改为上面的 `python3` 命令，先执行 `python3 install.py --without-context-recovery --apply`，再执行 `python3 install.py --with-context-recovery --apply`，然后在 `/hooks` 重新信任。安装器不会在普通升级里自动替换。

安装后新开 Codex，进入 `/hooks` 检查并信任 `codex-agent-kit: context recovery`。新建或改变的 hook 必须由用户按 Codex 的信任流程启用；安装器不伪造信任或绕过审核。原生 hooks 需支持 SessionStart 的 compact source 和 additionalContext；`--check` 仅验证文件与配置，不证明运行事件或信任已生效。

后续 `git pull --ff-only` 后运行普通 `install.py --apply` 会保留模块启用状态。关闭使用 `python3 install.py --without-context-recovery --apply`；只移除本模块的 hook，保留脚本和项目状态，也不会关闭其他 hook 使用的 hooks 功能。

## 与其他 hook 共存

默认合并用户级 `hooks.json`，保留 Herdr、其他事件和同事件的其他处理器。若本机只使用 config.toml 内联 `[hooks]`，就在该表合并；两处均有第三方设置时保留两处内容，只在已有受管位置（首次优先 hooks.json）注册本模块；若两处重复注册了本模块则拒绝并提示核对。config.toml 中单独的 hook 信任设置不被误判为事件处理器。模块用独有 statusMessage 标记和安装记录跟踪自己的处理器，修改或丢失受管处理器时要求显式 `--overwrite-local`，仍先备份。第三方 hook 的正常新增或修改不会导致整个文件被当成本模块漂移。

通用 helper、模板和协议始终随安装包更新；是否注册运行 hook 由可选模块开关控制。认证、provider、主模型、资源策略和 hook 信任不跨服务器同步。无外网服务器可传输固定提交的仓库包后运行同一安装器。

## 任务状态用法

在项目根准备 checkpoint.json，可复制安装目录 `templates/task-state.json` 后填写所有字段：goal、constraints、next_step、progress、decisions、evidence、jobs。目标和下一步不能为空，所有字段是字符串。

```sh
python3 ~/.codex/context-state.py init --task project-fix --from-file checkpoint.json
python3 ~/.codex/context-state.py save --from-file checkpoint.json
python3 ~/.codex/context-state.py status
python3 ~/.codex/context-state.py finish
```

从当前 Codex 会话中运行会自动使用 CODEX_THREAD_ID；终端外部操作需加明确的 `--session SESSION_ID`。自定义安装根相应替换 helper 路径。`--from-file -` 可从 stdin 读取 JSON。`--project` 可指定非 Git 项目根；默认从 cwd 查找已有状态目录或 Git 根。

Root 按 [任务状态协议](../bundle/protocols/context-recovery.md) 在关键阶段保存语义状态；自动化的是压缩后的读取与注入，不是从聊天中自动推断并保存所有进度。建议将项目 `.codex-task-state/` 和含私有信息的 checkpoint 文件加入该项目的忽略规则。

状态为 `.codex-task-state/tasks/<task-id>/state.json`，内容和身份元数据在同一 JSON 中原子更新。绑定按宿主及 session ID 的哈希隔离；禁止用“最新任务”推断会话身份。文件锁防止 helper 并发写入，状态及绑定拒绝符号链接，输入有大小限制。

## 恢复和迁移边界

有效且不足 72 小时的活动 checkpoint 会恢复目标、约束、下一步、进度、决策、证据和作业摘要，最多 4000 字符。超长字段指向完整 checkpoint。过期/损坏/归属变化/项目身份不符时只报告诊断，不注入旧正文；Root 核实后再保存新 checkpoint。已完成的任务不自动重启。

换会话显式运行 `bind --task TASK_ID --takeover`；复制状态到另一台服务器或移动项目还需 `--adopt-project`。使用这些参数前必须确认旧任务和写入者停止，并核对目标仓库、路径及作业。安装器不会自动搬运任务状态；不同服务器即使用户名或目录相同，也不会默认共享会话绑定。

hook 对状态只读，不运行 Git 扫描、测试或作业，不读取完整聊天。无法恢复时返回可见诊断并允许 Codex 正常继续。遵守目标机器资源与临时目录规则，所有临时写入均在对应状态目录内，不回退到 /tmp。

## 验证

离线检查：`python3 -m unittest discover -s tests -v`。覆盖事件格式、更新恢复、多会话隔离、显式接管、项目迁移、过期/损坏状态、输出限额、只读 hook、符号链接拒绝、文件锁、安装幂等、路径引用、第三方 hook 保留、内联配置、漂移保护、失败回滚、换解释器后 `--check` 仍通过，以及旧绝对路径 registration 原样保留。

真实 Codex 验收需在 `/hooks` 信任后进行：在一次性项目中保存带唯一标记的 checkpoint，执行 `/compact`，核对恢复消息和后续行为；再仅对该测试会话设置较低自动压缩阈值，验证自动 compact 中途恢复。离线模拟 hook 输入通过不等于真实宿主事件已验证，不要把文件同步检查当作事件执行证据。超过本机重任务阈值时按当地规则调度。

## 官方接口

- [SessionStart、compact 和 hook 信任](https://learn.chatgpt.com/docs/hooks)
- [原生 compact 配置](https://learn.chatgpt.com/docs/config-file/config-reference)
