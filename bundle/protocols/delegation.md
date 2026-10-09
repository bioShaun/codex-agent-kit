# 委派与角色

## 配置入口与角色

实际配置是唯一来源：[`config.toml`](@@CODEX_HOME@@/config.toml) 和 [`agents/`](@@CODEX_HOME@@/agents/)。本文规定用户级默认执行协议；目标项目的 AGENTS.md 和每轮 TaskSpec 提供项目特有的约束、测试命令和验收要求。角色使用 `astra_` 前缀，并与其他用户角色共存。

| Codex agent_type | 模型 | effort | 职责 |
|---|---|---|---|
| Root（主会话） | gpt-6-astra | high | 需求、架构边界、复杂实现、TaskSpec、调度、最终整合 |
| astra_locator | gpt-6-luna | low | 符号、配置、测试定位 |
| astra_explorer | gpt-6.1-sol | medium | 跨文件调用链、状态流和约束 |
| astra_worker | gpt-6.1-sol | medium | 有边界、需要推理的实现 |
| astra_worker_fast | gpt-6-luna | medium | 文件明确的机械性修改 |
| astra_validator | gpt-6-luna | low | 精确确定性命令、断言及结果证据 |
| astra_validator_complex | gpt-6.1-sol | medium | 多阶段 host 行为与关联证据解释 |
| astra_reviewer | gpt-6.1-sol | high | 独立正确性审查 |

2026-09-30 模型迁移：四个 Sol 角色升级至 gpt-6.1-sol，用于跨文件分析、实现、复杂验证和审查；Luna 用于定位、机械修改和确定性验证。各角色 effort 沿用原值，Root 保持 gpt-6-astra/high；复杂或高风险任务由 Root 根据证据决定是否升级到 Astra。默认子模型为 gpt-6.1-sol/medium，顶层 review_model 为 gpt-6.1-sol；三个 lazycodex 审查角色同步升级至 gpt-6.1-sol/high，仅在对应工作流调用。此分配是试运行起点，比较通过率、返工次数、总耗时与总成本后再调整。gpt-6.1-sol 支持 low、medium、high、xhigh、max，不支持 none、minimal、ultra；本机模型目录已校正为默认 medium，2026-09-30 既有调用记录验证五个支持档位可调用，但不代表真实任务质量、速度或本次角色加载已验证。本文旧模型耗时记录仅为历史证据，不代表 GPT-6.1 性能。配置变更后新开主会话，核对实际加载的 model/effort。

模型标识必须与实际运行目录对应，不能把 `gpt-5.6` 默认当成 Sol 的可靠别名。角色文件固定模型和 effort；需要升级时由 Root 明确调整配置/角色，不能假设 spawn 参数一定覆盖角色文件。

以下角色覆盖、权限继承与并发计数说明，以 Codex `rust-v0.160.1` 的源码为核对基线（见[源码参考](environment.md#参考)）。其他版本和宿主以实际工具元数据及权限证据为准；升级后应重新核对，不能将该版本的实现推定为所有宿主的行为。

用户配置的 `[agents] max_concurrent_threads_per_session = 4` 不计 Root。Codex 的线程登记只统计已派生子代理，因此该配置值表示最多 4 个并发子代理；它仍不保证运行时容量，以当前宿主工具元数据及实际限制为准。同名的 `features.multi_agent_v2.max_concurrent_threads_per_session` 按包含 Root 的总会话线程计数，不能与本键混用；本仓库未设置该键。需要委派时通常同时使用 1–3 个子代理，不为了用满额度而派发。接近容量上限或状态不明时查询实际状态；宿主支持释放时才释放已结束线程，否则延后派发。同一容量错误后，在容量或调度条件改变前不重复 spawn 或 followup，也不提高上限；复用已结束线程同样可能占活跃任务容量。相关探索或实现可复用合适的代理，但不得用旧 Reviewer 复用替代修复后的 fresh review。已有会话可能持有旧角色列表，配置变更后从目标项目启动新的主会话。

## 委派与上下文

- 简单任务由 Root 直接完成；高度依赖主会话上下文的复杂实现也可由 Root 完成。只有存在可独立交付的子任务，且并行能减少等待或隔离大量探索上下文时才派发。不要为满足角色清单而串行交接。
- 确认值得委派后，先按任务契约选择最窄的足够角色：已知名称/路径的事实定位用 Locator；命令、断言和环境均明确的确定性检查用 Validator；转换规则、文件范围和排除项完整的机械修改用 Worker-fast。仅当需要跨文件关系推理、行为实现或多阶段 host/证据归因时，分别用 Explorer、Worker、Validator-complex。不要因为已有 Sol 线程可复用，就把新的简单任务默认派给它；也不要为使用 Luna 把一个完整复杂任务拆成更多串行调用。
- 轻角色遇到歧义或缺少前提，回传具体缺口，由 Root 补全或升级，不让它自行扩大范围。首次采用轻角色的同类任务，保留实际 model/effort、结果和返工事实；完成较小的一批工作包后再决定扩大范围。轻模型不可用时按既有规则报告阻碍；不为凑比例降级审查或更改角色模型。无需为路由另建报告，TaskSpec 一句话说明选择理由即可。
- 使用当前宿主实际暴露的子代理工具和 `astra_*` 角色，不要求特定工具名（如 `create_thread`）。所有委派显式使用 `fork_turns="none"`；其他宿主使用对应的无历史机制。hook 已安装并在 `/hooks` 按当前定义信任后，已验证的 `collaborationspawn_agent` 一律以 `fork_turns="none"` 运行，本套件不提供有限历史或全部历史。任务确需对话连续性时，Root 把必要事实写入 TaskSpec，或按既有复用规则复用已有代理。独立审查始终无实现历史。
- 用户级 PreToolUse hook `hooks/force_fork_turns_none.py` 会在已验证的 `collaborationspawn_agent` 调用上把 `fork_turns` 改写为 `"none"`（已经是 `"none"` 则不改）。这是护栏，不是隔离：安装后必须在 `/hooks` 按当前定义信任一次。Codex 0.160.1 `multi_agent_v2` 上 matcher 是 `^collaborationspawn_agent$`；`^Agent$` 和 `^spawn_agent$` 实测不触发，不能当成别名。见 [issue #4](https://github.com/bioShaun/codex-agent-kit/issues/4)。协议仍要求 Root 显式传入 `fork_turns="none"`。
- Codex 0.160.1 上整数 `fork_turns` 表示最近 N 轮；自 rust-v0.162.0 起整数按全部历史处理，文档只接受 `none`/`all`（[openai/codex#51329](https://github.com/openai/codex/pull/51329)、[spawn.rs](https://github.com/openai/codex/blob/rust-v0.162.0/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs)）。数字不得再当作有限历史。此为源码与[发布说明](https://github.com/openai/codex/releases/tag/rust-v0.162.0)核对，不是 0.162.0 运行时验证。
- 每份 TaskSpec 必须自包含，提供路径、已知事实、项目限制与完成条件；不要让子代理重复检索已经充分确认的问题。无历史启动时也必须显式传入当前环境适用的资源调度和临时目录规则。
- 同类后续问题优先复用已有代理，但不复用已参与实现或受结论污染的代理作独立 Reviewer。审查修复轮次遵守 [审查协议](review.md) 的 fresh 规则。用 `followup_task` 复用 Worker 或 Validator 后，任何写入前先确认其写权限；若已被降级，则改为新建代理（[openai/codex#40278](https://github.com/openai/codex/issues/40278) 仍开放：曾把全权限子代理重置为 read-only/on-request，报告于 codex-cli 0.149.0-alpha.4.1，尚未在 0.160.1 验证）。
- 子代理不得派生、调用或请求新的子代理；需要额外工作时只向 Root 返回范围或证据缺口。禁止再派生是行为规则。七个角色文件中的 `[agents] enabled = false` 与 `sandbox_mode` 只是声明，Codex `rust-v0.160.1` 不会把这些键应用到子代理；子代理继承父会话权限。严格只读隔离来自 `review-readonly.sh`。
- 派发后 Root 先做不依赖该结果的工作；只有下一步确实依赖未完成结果时才等待。结果到达即处理；超时后评估进展、缩小范围或接手，不机械循环等待。独立工作仍须遵守唯一写入者规则。
- 普通等待默认 30–60 秒，结果可提前返回；hard 截止时不超过 `min(60 秒, 剩余时间)`。窄任务即将完成、停止确认或排错可短等。优化以同类任务的等待调用次数和结果处理延迟衡量，不以 timeout 参数比例或请求时长之和宣称收益。Root 对用户的必要更新频率不因等待策略降低。
- 通常只回传最终结论、证据和限制。阻塞、重大反证或可解除 Root 依赖的阶段性结论及时发送，不发送固定进度心跳。Root 对用户的必要进度沟通不受此限制。
- Root 采纳充分、可信的常规证据，不默认重读全部文件或重跑全部检查；重点复核冲突、关键高风险结论和修改后的最终行为。独立审查对指定范围的核实、必要状态采集及项目要求的检查仍须执行。

当前会话宿主示例：`collaboration.spawn_agent` 新建子代理（显式 `fork_turns="none"`），`followup_task` 复用已有代理（Worker/Validator 写入前先确认写权限，降级则改为新建代理），`send_message` 发送阶段性信息，`wait_agent` 等待依赖，`interrupt_agent` 发出中断；中断后仍须确认实际停止。`[agents] max_concurrent_threads_per_session = 4` 不计 Root，配置上最多同时运行四个子代理。此示例不固定其他宿主的名称和容量，每个新会话均以实际工具元数据为准。
