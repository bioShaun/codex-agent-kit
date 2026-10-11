# 配置维护

`bundle/` 是可编辑配置源。新增角色时添加对应 TOML，文件名应等于 `name`；安装器自动注册角色。删除仓库里的角色文件不会自动删除各机器旧角色，需逐机核对引用并手工移除，防止清理掉本机自定义配置。

`bundle/config.toml` 只放需要跨机器同步的公共设置，不要将整个用户级配置复制进来。主模型和账号配置由各机器自行维护。模型目录、provider URL、认证和本机项目路径均不属于此仓库。

本机资源策略由用户级及项目级 `AGENTS.md` 维护，包括调度工具（如 `slot`）、重任务的时间/内存阈值、日志要求和临时目录限制。`bundle/astra-planner.md` 与角色配置只规定遵守适用规则及在 TaskSpec 中传递规则的责任，不重复固化某台机器的策略。适用规则要求的工具缺失时，应注明规则来源并报告相关检查尚未执行。

协议入口和 `protocols/manifest.json` 所列专项文档里的 `@@CODEX_HOME@@` 表示实际安装路径，`@@CODEX_HOME_SHELL@@` 表示适合 shell 参数的引用形式；安装时替换。Bash launcher 的调用方式保持不变；`review-workflow.py` 的协议版本校验覆盖清单、入口和全部专项文件。调度协议中的历史验证记录仅是配置源的历史，迁移到新机器后须重新验证运行时行为。

当前设置依据本机已安装配置提取，未承诺适配所有 Codex 版本。项目级配置可以覆盖用户级配置；同名角色也可能被覆盖。核对实际运行时元数据，不能仅凭角色自述认定模型或权限已生效。

参考 OpenAI 官方文档：

- [Subagents：角色与配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Configuration Reference：用户级及项目级设置](https://learn.chatgpt.com/docs/config-file/config-reference)

## 专项协议与迁移

| 文件 | 读取时机 |
|---|---|
| `astra-planner.md` | Root 的短入口，按任务选择下列文件 |
| `protocols/environment.md` | 首次实施、验证、委派或审查；配置覆盖、资源和项目约束 |
| `protocols/delegation.md` | 派发子代理；角色、上下文及调度 |
| `protocols/tasks.md` | 准备 TaskSpec、处理常规结果和验证证据 |
| `protocols/execution.md` | 实施、验证、预算、并发及 strict launcher |
| `protocols/review.md` | 准备、派发、修正、接收独立审查 |

原协议条款按主题保留，入口的概要不代替所需专项约束。旧文档里按顺序阅读的跨章节指向改为专项链接。角色配置保持不变，子代理仍只依赖 Root 的自包含 TaskSpec。

从单文件版本升级时运行现有 `install.py --apply` 即可安装专项文件；所有文件仍有备份、回读验证和本机修改保护。`--skip-instructions` 也会安装完整协议集；原有“读取 astra-planner.md”入口继续有效。

`protocol_sha256` 改为整个协议集的组合哈希。安装目录内的占位符替换可能导致不同服务器的哈希不同；审查包应在目标服务器、目标项目准备和验收，不跨服务器复用已准备包。进行中的审查先完成再更新。长任务恢复模块的启用、升级、关闭和跨服务器部署参见 [部署说明](context-recovery.md)。

`protocols/context-recovery.md` 在已启用 compact 恢复模块的长程任务中按需读取。`context-state.py` 与模板随基础安装部署，`--with-context-recovery` 才注册运行 hook；后续安装保留该选择。

## 按使用情况调整调度

当前优化保留模型、effort、并发和无历史派发，重点是已有角色与审查流程的执行：

- 值得委派且条件明确的定位、确定性验证、机械修改优先路由到对应轻角色；简单任务仍由 Root 完成，不设轻角色调用配额。
- 审查前核对当前范围的材料和证据；第二轮仍未通过时先归因再派发。修复后的 fresh review、独立性和最终验收条件保持完整。
- 等待遵循宿主工具下限，避免会被自动抬高的短等待；剩余 hard 预算不足最小等待时进入截止处理。
- 在既有工作包记录中区分初审、修复/补证复审、范围变化及格式更正，记录实际模型、结果、耗时和 usage。字段未知就记未知，不把完成状态或审查次数当作通过率和返工率。

完整要求见 bundle/protocols/delegation.md、tasks.md 和 review.md；这些是协议约定，未新增宿主强制校验或遥测采集器。安装后新主会话加载角色变更；已有会话按入口的协议刷新规则处理。
