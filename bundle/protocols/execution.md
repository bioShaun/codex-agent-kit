# 执行、预算与并发

## Root 的执行顺序

1. 读用户目标、AGENTS.md 和相关域文档，确定信息缺口及是否值得委派。需要委派时，窄定位用 Locator，跨文件理解用 Explorer；只有独立问题才并行探索。
2. 按 [TaskSpec](tasks.md) 的任务层级准备自包含 TaskSpec。范围或公共接口未确定时由 Root 先解决，不把模糊需求直接交给 Worker-fast。
3. 修改前采集本轮执行前状态，登记唯一写入者（Root 或 Worker），再实施。Root 可直接实现，也可把边界明确、可独立交付的实现交给 Worker。保留已有未提交修改，不把整个工作树差异归因于本轮。
4. 实现结束后确认写入停止，采集结果状态并比较实际修改与实现报告。Root 可直接运行必要检查；检查可独立交付且委派有收益时，精确确定性命令交给 Validator，多阶段 host 操作或关联证据解释交给 Validator-complex。保留原始命令、退出码、stdout/stderr、失败尝试和修正证据；实现者自测不能冒称独立验证。
5. 有实质行为变化时冻结中性证据并启动 fresh Reviewer；普通文案或明确机械配置调整无需自动进入完整审查流程。项目要求优先。必需 strict gate 时必须使用下文的只读父 launcher，并确认实际运行时权限；同一父会话中的行为约束审查不能替代 strict gate。Root 根据证据作最终判断。REQUEST_CHANGES 进入有边界的纠正轮次；BLOCKED 先解决证据或环境缺口。
6. 接受前再次采集状态，确认审查对象没有漂移。汇报实际完成、验证和限制。

最终 gate 前由 Root 检查当前范围/快照、写入停止、该 gate 所需的前置测试与真实运行证据、旧 findings 处理状态以及隔离要求。根据本轮实际改动检查已经观察到的失败面：权限/资源限额及开放文件描述符、取消与授权撤销、继承上下文与后续输入入口、首次持久化及崩溃恢复。只选择与当前范围有关的项，为它们提供已有验证证据；不要把这份提示变成所有项目必跑的新增测试清单。修正轮次随包提供上轮 finding、对应变更、当前验证及保留限制的简短映射。代码审查可在全部运行证据就绪前进行，但必须标为 `review_kind=code`，不能当作整体验收。资源调度、权限或宿主缺口先由有执行条件的验证阶段处理；同一范围、快照及证据缺口均未改变时，不重复启动最终 gate。当前 gate 自身产生的权限探针和 verdict 不是它自己的前置条件；需要 strict 时通过相应入口产生证据。

对任务结果解析、用量核算或沙箱目录相关修改，把已知失败面前移到实现者的验收场景：缺失/空/嵌套子结果、缺失退出或用量证据、父子身份不匹配，以及路径别名/挂载别名下的实际写入边界。只检查与本轮改动相关的场景；普通应用任务不继承整张清单。已有测试已覆盖时引用它们，不再重复执行。修复一个 finding 时同时检查同一假设的相邻输入形态，避免每轮只补一个例子；每个仍未关闭的 finding 必须有当前状态和对应验证位置。第一次审查仍提供中性任务与事实，不把 Root 的预判当 Reviewer 结论。

小改动无需强制经历所有角色。角色分离用于降低不确定性；重复读取和重复测试没有新增证据时停止。

## 升级、预算与终止

以下情况升级：需要决定需求/公共接口/架构边界；修改实质超出 ownership；同一假设连续两次失败且无新证据。普通局部实现选择不升级。

升级报告应说明失败假设、证据、已产生的修改和待决问题。Root 区分任务拆分、环境故障、工具失败与任务固有难度，然后选择补充探索、修订范围、修复环境或调整模型。

默认使用 advisory 预算；180 秒仅作为窄任务首次进展评估的起点，复杂探索、实现、审查和长验证按实际范围安排预算。默认最多 2 次纠正；同一假设连续两次失败且无新证据时先升级，不机械重试。

- advisory：到评估时间检查已有证据和剩余工作，决定继续、缩小范围或接手；继续时记录原因和下一次评估时间，不因到点自动中断有进展的任务。
- hard：启动前明确 UTC `startedAt`、`deadline` 和取消能力；调度边界检查时钟，等待最多 `min(60 秒, deadline 剩余时间)`。到期立即发出中断并确认终态；如需延长，必须在原 deadline 前记录新 deadline 和原因，不能事后追认。阻塞工具可能延迟调度，不能把提示词中的截止时间宣称为精确硬限制。
- 两种模式下，取消或接手都必须先确认原写入者及相关进程停止，再转交写入权。严格只读 launcher 仍有独立的外部时限（`REVIEW_READONLY_TIMEOUT`，默认 600 秒，依据 2026-09-20 的实测：astra_reviewer 在 gpt-5.6-sol/high 下完成一次 strict 审查需 115–524 秒，广泛审查多在 400 秒以上，父入口自身约 30–50 秒；420 默认曾两次截断无 verdict），不因 advisory 模式自动延长。

这是 native Root 的主动调度协议，不是 TOML 自动硬限制，也不保证消除模型错误。文本 `STOP` 和用于记录中断消息的配置项 `agents.interrupt_message` 都不是预算执行器，也不是取消工具。没有硬取消能力时记录能力缺口；外部 CLI 进程组时限不能证明每个 Worker 的取消机制。取消后检查残留进程；停止未确认时保留写入占用。

## 并发与权限

同一 cwd 同时最多一个写入者，包括会生成文件的 Validator；Validator 与 Worker 顺序运行。文件不重叠也不能绕过该规则。并行写入必须使用隔离工作目录，并有单独的整合与验证任务。

Explorer、Locator、Reviewer 的角色文件声明 `sandbox_mode = "read-only"`；Worker、Validator 声明 `workspace-write`。这些键只是声明，Codex `rust-v0.160.1` 不会应用到子代理，子代理继承父会话权限。Validator 禁止源码修改仍是行为约定，必须由状态比较检测。Root 的 workspace-write 也不提供“只能规划”的工具层强制限制。严格只读隔离来自 `review-readonly.sh`。

父会话 `/permissions` 或命令行权限决定父会话权限，子代理继承该快照，而不是改用角色文件中的沙箱声明。测试应检查实际生效权限；不能仅根据 TOML 宣称隔离。不要用 `--yolo` 测试 read-only 行为。外部连接器/网络副作用不由文件系统 read-only 自动约束。

历史观察（配置源服务器的 Codex CLI 0.154.0，非本次安装服务器的验证结果）：即使未传 `--yolo`，workspace-write Root 下的 astra_reviewer 仍能写入工作区，角色文件中的 read-only 没有形成运行时隔离。Locator/Explorer 的元数据也显示 workspace-write。该历史结果不证明本次安装服务器的行为。核对的 Codex `rust-v0.160.1` 源码同样不把角色 `sandbox_mode` 应用到子代理，而是用父会话权限快照覆盖子权限；同一写入会话中的“只读角色”仍只按行为约定处理。

需要文件系统隔离的 strict 审查必须使用全局安装的独立只读父入口。新包 spec 必填 `budget` 对象：正整数 `timeout_seconds`、正整数 `review_seconds` 和非空 `reason`。总时限至多 86400 秒，且至少给父入口预留 60 秒；Root 根据范围与近期同类样本确定预算，不把最低预留量当作足够的性能承诺。review_seconds 是子审查的 advisory 评估点，timeout_seconds 由外部 watchdog 执行。9/22 已完成父子配对的额外耗时中位数约 133 秒，仅作安排预算参考，不代表每次固定开销。示例 900/660 不是新的全局默认。

严格 CLI 审查启动前，按当前环境的用户级和项目级 AGENTS.md 完成所需的资源检查与日志记录，再从被审查项目 cwd 执行。以下示例展示审查入口命令；若适用规则要求资源调度工具，实际执行时必须按规则包装命令。原始结果及阶段时间保留在全新目录中：

```sh
bash @@CODEX_HOME_SHELL@@/review-readonly.sh review-round-1 --artifacts review-run-1 > review-events.jsonl 2> review-stderr.log
```

新包入口使用已冻结的 budget，不接受与其不一致的环境时限。完成后用 review-run-1/final.txt 调用 receive；非零退出先处理失败，不提交 final PASS。timing.jsonl 记录外层预检、模型运行、结果校验等阶段，不把它冒充子代理耗时或资源调度排队时间。超时后先确认终止、检查已有覆盖与剩余工作，缩小合理范围或在新包中记录调整后的预算及原因；禁止同包盲目循环重试或全局加大时限来掩盖问题。

已经启动的兼容流程可继续传 JSON 文件（默认外部时限仍为 600 秒），也可使用 --artifacts 保留结果：

```sh
bash @@CODEX_HOME_SHELL@@/review-readonly.sh /path/to/review-request.json > /path/to/review-events.jsonl
```

入口以 `codex exec --sandbox read-only` 启动独立 Root，再以无历史方式调用 astra_reviewer；子代理继承该只读父会话的权限。必须以实际权限探测证明 Root/Reviewer 的运行时隔离；角色 TOML 或模型拒绝写入不是证据。入口要求 Bash、Python 3 和 Codex；stdlib watchdog 建立独立进程组，`REVIEW_READONLY_TIMEOUT`（默认 600）秒后 TERM，宽限 10 秒后对进程组 KILL。父 Root 以 low effort 运行，只做权限探针、spawn、wait 和转述；Reviewer 的 model/effort 仍由角色文件固定，须以子会话 turn_context 核对。每次 strict 运行记录 child 实际耗时，用于日后收紧默认值；子契约应写明软预算（例如 300 秒），超时时带覆盖范围返回 BLOCKED 而不是被截断。退出 0 仅表示 session 完成，仍需检查 Reviewer verdict 与权限证据；124 表示超时后终止，137 表示已强杀且仍需确认无残留进程。依赖缺失是 BLOCKED；strict 失败不能降级为 behavior-only PASS。
