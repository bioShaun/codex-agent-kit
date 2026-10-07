# codex-agent-kit

将当前 Codex subagent 角色、调度协议和审查工具放在 Git 中维护，让多台机器使用同一套配置，也可以直接在 GitHub 网页或云端开发环境修改后同步。

包含 7 个 Astra 角色、4 个 LazyCodex 角色、调度协议、5 个辅助脚本，以及一条把子代理 `fork_turns` 改写为 `none` 的 PreToolUse hook。配置源是 `bundle/`；请在这里修改，提交后在各机器拉取并安装。

## 安装

需要 Python **3.11+**、Git，以及支持本仓库配置项的 Codex。审查入口还需要 Bash。安装脚本仅使用 Python 标准库，不下载或升级 Codex，也不调用模型。

```sh
git clone git@github.com:bioShaun/codex-agent-kit.git
cd codex-agent-kit
python3 install.py             # 预览，不写入
python3 install.py --apply     # 备份、安装、回读验证
python3 install.py --check     # 0=已同步，1=待更新，2=配置/环境错误
```

目标目录按 `--target` > 当前 `CODEX_HOME` > `~/.codex` 选择，不修改环境变量。例如：

```sh
python3 install.py --target /path/to/codex-home --apply
```

安装后新开 Codex 主会话，确认角色列表和实际子会话的模型、effort，并在 `/hooks` 里信任一次 `force_fork_turns_none.py`。信任绑定当前 hook 定义的哈希；定义变化后要重新信任。安装检查只证明文件及配置同步，不能证明某台机器的模型账号有权限，也不能证明实际运行时只读隔离，也不能代替这次信任。

若全局 `AGENTS.md` 是多家 agent 共用的符号链接，安装器默认拒绝修改链接。已经在共享指令中配置了 `astra-planner.md` 入口的机器（包括本次配置来源机器），安装、预览和检查时均追加 `--skip-instructions`。没有入口时，应在共享指令来源中手工加入“Codex Root 委派前读取实际安装目录下的 astra-planner.md，使用 astra_* 角色；子 agent 按 TaskSpec 执行”，再使用该选项。

## 更新与云端编辑

1. 在 GitHub 网页、Codespaces 或本地编辑 `bundle/agents/*.toml`、`bundle/config.toml`、`bundle/hooks/` 或 `bundle/astra-planner.md`，提交到仓库。
2. 各机器在克隆目录运行：

   ```sh
   git pull --ff-only
   python3 install.py
   python3 install.py --apply
   python3 install.py --check
   ```

3. 新开主会话。进行中的审查绑定了协议和辅助脚本哈希，应先完成再更新。

本仓库不会后台自动拉取或替你提交本机配置。云端仓库是配置源；每台机器在明确执行安装时更新。首次安装会备份并替换同名角色/脚本；后续安装发现这些文件被本机修改时会拒绝覆盖。优先把修改同步回仓库；确需采用仓库版本时，用 `--overwrite-local --apply`，旧文件仍会备份。

## 安装内容和边界

| 内容 | 安装行为 |
|---|---|
| `bundle/agents/*.toml` | 安装全部 11 个角色并显式注册；保留其他角色 |
| `bundle/config.toml` | 合并其中的审查模型、multi-agent 开关、默认子模型、并发参数 |
| `bundle/astra-planner.md` | 按实际安装目录替换路径占位符 |
| 5 个辅助脚本 | 安装到目标目录根部；脚本通过相邻文件定位依赖 |
| `bundle/hooks/force_fork_turns_none.py` | 安装到 `hooks/`，并把 matcher `^collaborationspawn_agent$` 合并进 `hooks.json` 或已有的内联 `[hooks]` |
| 全局指令 | 更新带标记的简短入口；非空 `AGENTS.override.md` 优先，否则使用 `AGENTS.md` |
| 本机记录 | `.codex-agent-kit.json` 保存受管文件哈希和这条 hook 的分组哈希，`backups/` 保存每次修改前的版本 |

已有主模型、provider、登录、API、MCP、插件、权限和项目 trust 配置保持原值。首次合并需要修改 `config.toml` 时，会重新序列化 TOML：**值保留，注释与格式不保留**，原文在备份中；配置已经一致时不重写文件。输出只列文件名，不打印配置或密钥。

仓库不包含完整的本机 `config.toml`、认证、会话记录、模型目录或个人全局指令。新机器请单独配置登录/provider 和主模型。模型标识沿用当前部署，不自动降级或替换；具体支持由目标机器的 Codex/provider 决定。

| 角色 | 模型 | effort |
|---|---|---|
| `astra_locator`、`astra_validator` | `gpt-6-luna` | low |
| `astra_worker_fast` | `gpt-6-luna` | medium |
| `astra_explorer`、`astra_worker`、`astra_validator_complex` | `gpt-6.1-sol` | medium |
| `astra_reviewer`、三个 `lazycodex-*-reviewer` | `gpt-6.1-sol` | high |
| `lazycodex-qa-executor` | `gpt-5.6-luna` | high |

默认子模型为 `gpt-6.1-sol/medium`，审查模型为 `gpt-6.1-sol`。LazyCodex 角色仅用于相应工作流；其技能/`omo` 等外部工具不随此仓库安装。

资源调度、重任务阈值和临时目录规则由各机器的用户级及项目级 `AGENTS.md` 维护。调度协议和角色遵循当前环境的适用规则，由 Root 在 TaskSpec 中显式传给子代理；本仓库不统一要求安装特定调度工具。测试只使用本仓库 `.work/` 下的小型隔离目录。

## 子代理 fork_turns 护栏

`bundle/hooks/force_fork_turns_none.py` 是一条 PreToolUse command hook。Codex 在省略 `fork_turns` 时默认继承父会话全部历史。参数还不是 `"none"` 时，hook 复制完整的原始 `tool_input`，只把 `fork_turns` 改成 `"none"`，并同时返回 `permissionDecision: "allow"` 和 `updatedInput`。已经是 `"none"` 时不输出、直接放行。输入无法解析，或事件明显不是这次验证过的调用时，失败开：退出码 0、没有 stdout，也不打印回溯。失败的改写不会改变原来的派发；非零退出或拒绝派发会把解析错误变成一次失败的委派。

这是护栏，不是隔离边界。安装后必须在 `/hooks` 中按当前定义信任一次。Codex 0.160.1 的 `multi_agent_v2` 上，实际 `tool_name` 是 `collaborationspawn_agent`，matcher `^collaborationspawn_agent$` 能完成改写。同一次测试里 `^Agent$` 和 `^spawn_agent$` 都没有触发，因此安装器不写入这两个 matcher，脚本也不把 `Agent` / `spawn_agent` 当成别名。顶层 `agent_type` 表示调用者（Root 上没有该字段，子代理里是角色名），hook 不读取它。子代理再派生子代理的路径没有在这次测试里验证。详见 [issue #4](https://github.com/bioShaun/codex-agent-kit/issues/4)。那次信任是通过 app-server 写入的，没有在 TUI 里操作 `/hooks`。

没有 hook 配置时，安装器新建目标目录下的 [`hooks.json`](https://developers.openai.com/codex/hooks)。文件里已有其他 hook 时，只追加或更新这一条，保留其余条目。`hooks.json` 整文件不计入受管文件哈希：本地改掉这一条会被拒绝，另加一条用户 hook 不会。若还没有 `hooks.json`，但 `config.toml` 里已经有 `[hooks]`，就合并进现有内联表，避免同一配置层出现第二份 hook 来源（Codex 会合并两份并在启动时警告）。两份都已经存在时，只改 `hooks.json` 里的受管条目，不搬移、不删除另一侧的用户 hook。受管命令若和用户 handler 写在同一个 matcher 组里，安装器拒绝合并，包括 `--overwrite-local`，因为替换整组会丢掉用户 handler。内联合并若改写 `config.toml`，和现有受管设置一样会重排 TOML，注释不保留，原文在备份中。

## 备份与恢复

安装输出会显示 `目标目录/backups/codex-agent-kit-时间戳/`。目录权限为 `0700`，备份文件为 `0600`，其中可能包含目标机器原来的私有配置，不能提交到仓库。

修改失败时脚本尝试恢复本轮已经写入的文件；若发现并发修改则保留并报告。不要同时运行多个安装器或在安装期间编辑目标配置。机器断电或进程被强杀时不能保证整批原子性，应依据备份检查后恢复。

手工恢复时先停止配置编辑：按备份 `manifest.json` 的文件清单，把备份里的原文件复制回目标位置；`before` 为 `null` 表示此前不存在，确认没有后续修改后移除本次新增文件。安装器不提供会覆盖后续修改的一键回滚命令。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
bash -n bundle/review-readonly.sh
```

GitHub Actions 会执行相同的离线检查，不读取账号凭据或调用模型。请参见 [配置说明](docs/configuration.md)。
