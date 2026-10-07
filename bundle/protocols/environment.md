# 配置加载与项目约束

## 全局使用与项目约束

Codex Root 在每个 Root 会话首次执行原生 Codex 实现、验证或独立审查时读取 astra-planner.md 入口，并按入口读取当前任务需要的专项协议；当前上下文已有时复用。仅在协议发生变化或上下文压缩导致关键规则丢失时由 Root 补读相关章节。获知规则更新后，旧 Root 在下一次派发/接受前运行 status 并重新读取 TaskSpec、结果解析和状态记录章节，记录新协议哈希；新工作优先在新主会话开始，不假定已有会话热加载。astra_* 子角色不读本协议，以 Root 的自包含 TaskSpec 和项目约束为准；缺少完成任务所需的信息时向 Root 返回缺口，不自行加载协议。Root 必须在 TaskSpec 中提供子角色所需的资源约束、ownership、预算、证据及验收要求。普通问答不触发完整委派流程。

七个角色安装于 `@@CODEX_HOME@@/agents/`，不固定项目路径和测试命令。每次通过自包含 TaskSpec 提供目标项目的约束、ownership、验收和验证命令。模型和 effort 是默认路由策略，不是所有项目成本最优的保证；模型不可用时报告阻碍，不擅自更换。

保留现有七个角色及模型分配，不新增通用 default。复杂调用链或高风险审查是否升级模型/effort，由 Root 根据任务证据和同类任务表现决定；固定角色参数不能假定被 spawn 参数覆盖。另有 `lazycodex-*` 角色时，只在对应工作流调用，不默认与 Astra 的验证、审查重复叠加；项目明确要求的门禁不得省略。

全局审查脚本 `review-readonly.sh`、`run-bounded.py`、`review-contract.py`、`review-workflow.py` 必须保持在 `@@CODEX_HOME@@/` 中。入口以当前项目目录保存临时请求和末条消息并在退出时清理。始终从被审查项目 cwd 调用审查入口，由该 cwd 决定加载的项目配置。

项目配置可能覆盖用户级配置，同名角色的不同版本不能假设会合并。配置层级、provider 或宿主版本变化后，重新确认实际加载的角色、模型、effort 和权限；以实际元数据和工具结果为证据，不采信模型自述。不批量提升项目的信任级别。区分静态检查、实际运行和未验证能力；安装成功不等于严格只读隔离已经验证。

资源调度工具、重任务阈值、启动前检查、日志要求和临时目录限制由当前环境的用户级及项目级 AGENTS.md 定义，本协议不固定机器专属工具、阈值或路径。Root 遵循适用规则，并将具体约束显式纳入相关子任务的 Context；子角色不应依赖隐式继承。本地规则要求的工具不可用时，报告具体规则来源及尚未执行的检查，不把未运行记为检查失败。不擅自终止其他任务或扩大资源额度。

## 参考

- [OpenAI Subagents 文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

源码核对基线：Codex `rust-v0.160.1`。以下链接用于区分版本实现与通用文档说明；源码核对不能替代目标宿主的运行时权限验证。

- [角色可应用的覆盖字段](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/src/agent/role.rs)
- [子代理权限快照继承](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/src/agent/child_config.rs)
- [子代理登记与 Root 计数](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/src/agent/registry.rs)
- [agents 与 Multi-Agent V2 并发配置换算](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/src/config/mod.rs)
