# Codex Subagent 配置方案（Astra Planner）

本协议为当前 Linux 用户的原生 Codex 提供默认委派规则。Root 负责需求、范围、路由和最终判断；子角色完成有边界的任务。模型分配是试运行起点，后续以成功率、返工轮数、总耗时和总成本调整。

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

以下角色覆盖、权限继承与并发计数说明，以 Codex `rust-v0.160.1` 的源码为核对基线（见文末源码参考）。其他版本和宿主以实际工具元数据及权限证据为准；升级后应重新核对，不能将该版本的实现推定为所有宿主的行为。

用户配置的 `[agents] max_concurrent_threads_per_session = 4` 不计 Root。Codex 的线程登记只统计已派生子代理，因此该配置值表示最多 4 个并发子代理；它仍不保证运行时容量，以当前宿主工具元数据及实际限制为准。同名的 `features.multi_agent_v2.max_concurrent_threads_per_session` 按包含 Root 的总会话线程计数，不能与本键混用；本仓库未设置该键。需要委派时通常同时使用 1–3 个子代理，不为了用满额度而派发。接近容量上限或状态不明时查询实际状态；宿主支持释放时才释放已结束线程，否则延后派发。同一容量错误后，在容量或调度条件改变前不重复 spawn 或 followup，也不提高上限；复用已结束线程同样可能占活跃任务容量。相关探索或实现可复用合适的代理，但不得用旧 Reviewer 复用替代修复后的 fresh review。已有会话可能持有旧角色列表，配置变更后从目标项目启动新的主会话。

## 委派与上下文

- 简单任务由 Root 直接完成；高度依赖主会话上下文的复杂实现也可由 Root 完成。只有存在可独立交付的子任务，且并行能减少等待或隔离大量探索上下文时才派发。不要为满足角色清单而串行交接。
- 确认值得委派后，先按任务契约选择最窄的足够角色：已知名称/路径的事实定位用 Locator；命令、断言和环境均明确的确定性检查用 Validator；转换规则、文件范围和排除项完整的机械修改用 Worker-fast。仅当需要跨文件关系推理、行为实现或多阶段 host/证据归因时，分别用 Explorer、Worker、Validator-complex。不要因为已有 Sol 线程可复用，就把新的简单任务默认派给它；也不要为使用 Luna 把一个完整复杂任务拆成更多串行调用。
- 轻角色遇到歧义或缺少前提，回传具体缺口，由 Root 补全或升级，不让它自行扩大范围。首次采用轻角色的同类任务，保留实际 model/effort、结果和返工事实；完成较小的一批工作包后再决定扩大范围。轻模型不可用时按既有规则报告阻碍；不为凑比例降级审查或更改角色模型。无需为路由另建报告，TaskSpec 一句话说明选择理由即可。
- 使用当前宿主实际暴露的子代理工具和 `astra_*` 角色，不要求特定工具名（如 `create_thread`）。所有委派默认显式使用 `fork_turns="none"`；其他宿主使用对应的无历史机制。仅在连续对话确有必要且工具支持时使用有限历史，不默认继承全部历史。独立审查始终无实现历史。
- 每份 TaskSpec 必须自包含，提供路径、已知事实、项目限制与完成条件；不要让子代理重复检索已经充分确认的问题。无历史启动时也必须显式传入当前环境适用的资源调度和临时目录规则。
- 同类后续问题优先复用已有代理，但不复用已参与实现或受结论污染的代理作独立 Reviewer。审查修复轮次遵守下文 fresh 规则。用 `followup_task` 复用 Worker 或 Validator 后，任何写入前先确认其写权限；若已被降级，则改为新建代理（[openai/codex#40278](https://github.com/openai/codex/issues/40278) 仍开放：曾把全权限子代理重置为 read-only/on-request，报告于 codex-cli 0.149.0-alpha.4.1，尚未在 0.160.1 验证）。
- 子代理不得派生、调用或请求新的子代理；需要额外工作时只向 Root 返回范围或证据缺口。禁止再派生是行为规则。七个角色文件中的 `[agents] enabled = false` 与 `sandbox_mode` 只是声明，Codex `rust-v0.160.1` 不会把这些键应用到子代理；子代理继承父会话权限。严格只读隔离来自 `review-readonly.sh`。
- 派发后 Root 先做不依赖该结果的工作；只有下一步确实依赖未完成结果时才等待。结果到达即处理；超时后评估进展、缩小范围或接手，不机械循环等待。独立工作仍须遵守唯一写入者规则。
- 普通等待默认 30–60 秒，结果可提前返回；hard 截止时不超过 `min(60 秒, 剩余时间)`。窄任务即将完成、停止确认或排错可短等。优化以同类任务的等待调用次数和结果处理延迟衡量，不以 timeout 参数比例或请求时长之和宣称收益。Root 对用户的必要更新频率不因等待策略降低。
- 通常只回传最终结论、证据和限制。阻塞、重大反证或可解除 Root 依赖的阶段性结论及时发送，不发送固定进度心跳。Root 对用户的必要进度沟通不受此限制。
- Root 采纳充分、可信的常规证据，不默认重读全部文件或重跑全部检查；重点复核冲突、关键高风险结论和修改后的最终行为。独立审查对指定范围的核实、必要状态采集及项目要求的检查仍须执行。

当前会话宿主示例：`collaboration.spawn_agent` 新建子代理（显式 `fork_turns="none"`），`followup_task` 复用已有代理（Worker/Validator 写入前先确认写权限，降级则改为新建代理），`send_message` 发送阶段性信息，`wait_agent` 等待依赖，`interrupt_agent` 发出中断；中断后仍须确认实际停止。`[agents] max_concurrent_threads_per_session = 4` 不计 Root，配置上最多同时运行四个子代理。此示例不固定其他宿主的名称和容量，每个新会话均以实际工具元数据为准。

## Root 的执行顺序

1. 读用户目标、AGENTS.md 和相关域文档，确定信息缺口及是否值得委派。需要委派时，窄定位用 Locator，跨文件理解用 Explorer；只有独立问题才并行探索。
2. 按下文任务层级准备自包含 TaskSpec。范围或公共接口未确定时由 Root 先解决，不把模糊需求直接交给 Worker-fast。
3. 修改前采集本轮执行前状态，登记唯一写入者（Root 或 Worker），再实施。Root 可直接实现，也可把边界明确、可独立交付的实现交给 Worker。保留已有未提交修改，不把整个工作树差异归因于本轮。
4. 实现结束后确认写入停止，采集结果状态并比较实际修改与实现报告。Root 可直接运行必要检查；检查可独立交付且委派有收益时，精确确定性命令交给 Validator，多阶段 host 操作或关联证据解释交给 Validator-complex。保留原始命令、退出码、stdout/stderr、失败尝试和修正证据；实现者自测不能冒称独立验证。
5. 有实质行为变化时冻结中性证据并启动 fresh Reviewer；普通文案或明确机械配置调整无需自动进入完整审查流程。项目要求优先。必需 strict gate 时必须使用下文的只读父 launcher，并确认实际运行时权限；同一父会话中的行为约束审查不能替代 strict gate。Root 根据证据作最终判断。REQUEST_CHANGES 进入有边界的纠正轮次；BLOCKED 先解决证据或环境缺口。
6. 接受前再次采集状态，确认审查对象没有漂移。汇报实际完成、验证和限制。

最终 gate 前由 Root 检查当前范围/快照、写入停止、该 gate 所需的前置测试与真实运行证据、旧 findings 处理状态以及隔离要求。根据本轮实际改动检查已经观察到的失败面：权限/资源限额及开放文件描述符、取消与授权撤销、继承上下文与后续输入入口、首次持久化及崩溃恢复。只选择与当前范围有关的项，为它们提供已有验证证据；不要把这份提示变成所有项目必跑的新增测试清单。修正轮次随包提供上轮 finding、对应变更、当前验证及保留限制的简短映射。代码审查可在全部运行证据就绪前进行，但必须标为 `review_kind=code`，不能当作整体验收。资源调度、权限或宿主缺口先由有执行条件的验证阶段处理；同一范围、快照及证据缺口均未改变时，不重复启动最终 gate。当前 gate 自身产生的权限探针和 verdict 不是它自己的前置条件；需要 strict 时通过相应入口产生证据。

对任务结果解析、用量核算或沙箱目录相关修改，把已知失败面前移到实现者的验收场景：缺失/空/嵌套子结果、缺失退出或用量证据、父子身份不匹配，以及路径别名/挂载别名下的实际写入边界。只检查与本轮改动相关的场景；普通应用任务不继承整张清单。已有测试已覆盖时引用它们，不再重复执行。修复一个 finding 时同时检查同一假设的相邻输入形态，避免每轮只补一个例子；每个仍未关闭的 finding 必须有当前状态和对应验证位置。第一次审查仍提供中性任务与事实，不把 Root 的预判当 Reviewer 结论。

小改动无需强制经历所有角色。角色分离用于降低不确定性；重复读取和重复测试没有新增证据时停止。

## TaskSpec

新申请的普通和严格审查默认使用 `review-workflow.py` 准备、检查及接收，避免手写请求、快照哈希和验收状态。该工具复用既有 `review-contract.py` 校验；直接文件请求的旧 API 仅供已经启动的审查和兼容场景，不能把兼容路径当作绕过校验的理由。

Root 在本会话首次审查及获知协议更新后，运行 `python3 @@CODEX_HOME_SHELL@@/review-workflow.py status`，阅读变更后的相关协议，记录其 `protocol_sha256`。prepare 必须显式传入该值；旧值被拒绝后先刷新相关章节，不自动抓取新值重试来冒充已刷新。此机制检测版本不一致，不能证明模型实际阅读，也不能给旧会话主动推送指令。

准备文件 `spec.json` 包含 `review_kind`、`scope_id`、`isolation_requirement`、`instructions_file`、`files`、`required_files`。路径相对 spec 所在目录解析。instructions_file 是完整、中性的 TaskSpec 文本；files 显式列出要冻结的文件，不递归扫描整个项目；required_files 由 Root 从 Acceptance 独立列出，不能从 files/manifest 自动复制。final 另需非空 `validation_evidence`，所列文件同时显式列入 files 和 required_files；工具只核对冻结与覆盖，不判断日志是否证明通过。

```sh
python3 @@CODEX_HOME_SHELL@@/review-workflow.py prepare --spec spec.json --output review-round-1 --protocol-sha256 RECORDED_SHA256
python3 @@CODEX_HOME_SHELL@@/review-workflow.py check review-round-1
```

输出目录必须不存在，父目录必须已存在。工具流式计算文件哈希、生成 snapshot.json，再按该文件的实际字节计算 snapshot_id，生成 request.json、brief.md 和 workflow.json。派发普通 Reviewer 前 check 成功后，将完整 request.json 原样交给 fresh Reviewer。strict 使用下文 launcher 的目录入口。check 在派发前和接收时检查控制文件绑定及全部已声明文件的内容哈希，包括未变化基线；大文件/大量网络盘读取仍按当前环境的资源规则执行，不以“预检”名义绕过资源限制。

prepare 前确认实现者、测试及后台日志写入均已停止；已冻结的 spec、验证日志和源文件在本轮 receive 完成前不得继续修改。持续追加的日志应在写入结束后保存本轮独立证据文件，并将原始日志位置保留为来源。receive 的状态输出和后续运行日志不能写回本轮绑定输入。遇到 `file_stability` 诊断时保存 path/fd 前后元数据及 changed_fields；这些字段只说明观察到的变化，不能证明具体写入者或内容变更。先核对写入停止和文件系统状态，不循环重试、不关闭 inode/mtime/ctime 校验。若实际内容或范围变化，建立新快照并 fresh review；确认内容未变且原错误仅为暂态文件状态后，才对原包再做一次完整 check/receive，并保留失败证据。

可选 `baseline_manifest` 提供上轮快照，只用于生成新增/修改/删除/未变计数及变更索引；可选 `prior_findings_file` 提供上轮已完成审查的 findings 与本轮处理证据，须显式列入 files 和 required_files。brief 不展开成千上万未变化路径，不继承上次 PASS，不省略内容完整性校验，不自动缩小本轮 Acceptance。Reviewer 根据变更影响决定直接复核边界，不能将 delta=0 当作无需审查。

审查请求使用一份 JSON 作为唯一派发依据（可复用已有任务/审查记录，不另设台账）：四个必填审查字段与非空 `instructions` 在同一对象中，后者保存完整、中性的 TaskSpec。派发普通 Reviewer 前运行 `python3 @@CODEX_HOME_SHELL@@/review-contract.py preflight REQUEST.json`；strict 入口自动调用同一检查。检查失败先修请求，不消耗 Reviewer 调用。检查仅验证请求结构与声明范围，不代替 Root 判断任务是否完整。

使用文件快照时，同时提供 `snapshot_manifest`（绝对路径）和 `required_files`（本轮必需文件的绝对路径列表）。manifest 的 `files` 为绝对路径到小写 SHA256 的映射，`snapshot_id` 为 `sha256:` 加 manifest 文件自身的 SHA256。strict final 必须提供这两项；ordinary 审查不强制逐文件哈希。复用已有快照时转换为这一最小格式，保留原记录，不覆盖业务产物。Root 从 Acceptance 独立列出必需源文件、结果和实质验证证据，不能直接把 manifest 的 keys 当作 required_files，否则无法检测漏项。

preflight 校验快照标识、声明文件存在及覆盖关系；不会重新读取全部大文件计算内容哈希。它不能发现 TaskSpec 自身漏列的验收对象，也不能证明文件仍与哈希一致。Root 的冻结和 Reviewer 的独立核实仍须检查本轮要求的内容新鲜度；当前 gate 将产生的 verdict 和权限探针不得列为已存在的前置文件。

按任务需要提供字段，不为窄任务填写无关模板：

- 普通只读任务：Goal、只读 Scope、Context（路径、已知事实、未决问题和项目约束）、Acceptance、Return（状态、证据位置、限制）。默认使用 advisory 预算，由 Root 记录开始和下次评估时间即可。
- 修改任务：在上述基础上增加具体文件 ownership、Out of scope、Verification（必要命令、预期结果及真实使用场景）和预算安排。Worker-fast 必须另有明确转换规则、排除项、命中范围和验收方法。
- 验证任务：给出完整命令或操作流程、环境和前提、通过条件、允许产生的文件及独占执行窗口；明确 ordinary 或 strict 证据要求。测试不得因名称看似本地就被假定无生产访问或外部副作用。
- 审查任务：必填 `review_kind`（`code` 或 `final`）、`scope_id`、`snapshot_id` 和 `isolation_requirement`（`ordinary` 或 `strict`），并明确当前范围所需证据。kind 描述审查范围，不是预设 verdict；不向 Reviewer 提供 Root 的状态判断来代替中性事实。代码审查仅判断指定代码/文档范围；final 审查对照本轮全部 Acceptance。strict 是隔离要求，与 kind 独立，不得用 `code` 标签免除任务指定的 strict 证据。
- 严格验收任务：额外明确冻结证据范围、前后哈希或快照、完整日志位置、独立审查及运行时权限要求。Budget 写入模式、无进展判定和纠正轮数；advisory 使用 UTC `startedAt` 与 `nextAssessmentAt`，hard 使用 UTC `startedAt`、`deadline` 与取消能力。任何层级使用 hard 截止时都必须提供上述 hard 预算字段。

实现路由：Root 处理简单任务、范围决策及依赖主上下文的复杂实现；明确机械转换用 Worker-fast；边界清晰且委派有收益的行为实现用 Worker。Worker 可自主选择边界内的命名、局部算法和既有模式；Worker-fast 不承担行为设计、跨项目语义重命名、公共接口变更或复杂实现。

原生 Codex 的任务说明与报告是文本协议，不提供额外的 schema 校验保证。结构化外观不等于结构化传输。

## 报告、证据和独立审查

Locator、Explorer 的结果第一行使用 complete / partial / blocked，附结论、证据位置及未覆盖范围；partial 明确证据缺口，由 Root 决定下一步。事实抽取升级到架构分析不由子代理自行派发。

实现报告包含 status、changes、files_changed、checks、risks、blockers。Worker 状态为 COMPLETED / ESCALATE / BLOCKED；COMPLETED 只表示实现者报告完成。Root 直接实现时提供同等事实摘要，作为审查包中的实现报告，不要求另建报告文件。

普通任务不为内部通信另建报告文件；项目、审查流程或 TaskSpec 明确要求的日志和证据产物照常保存。工具输出本身可作为普通检查证据；长日志存于授权路径，回传状态、关键片段和位置即可。strict 验证保留时间、证据路径及规定范围的前后哈希；ordinary 验证保留实际命令、退出码、输出、失败尝试及范围内文件变化，不默认要求逐文件哈希。

Validator 只处理已给出的精确确定性检查；Validator-complex 处理多阶段 host 流程和证据解释。后者遇到缺失或歧义步骤应 BLOCKED，不能自行补程序或宣称 host 不支持。TaskSpec 必须保留目标项目要求的完整调用形式，包括 CLI 输出模式、provider 和参数。两者区分请求的 tool call、实际 tool result 和 ledger/file 变化，保留失败尝试与修正后的重跑。只有所有要求都有当前通过证据才能 PASS；exit 0 本身不构成 PASS。缺依赖、无法运行或跳过必需检查返回 BLOCKED。禁止自动修复，披露生成物和意外变化。

Reviewer 首行严格使用 `PASS (code)` / `REQUEST_CHANGES (code)` / `BLOCKED (code)`，或对应的 `(final)`，必须复述 TaskSpec 的 review_kind。Root 使用 `^(PASS|REQUEST_CHANGES|BLOCKED) \((code|final)\)$` 完整匹配首行，并核对 kind 与原始 TaskSpec 一致。缺失或无效的必填审查字段时，Reviewer 返回裸 `BLOCKED` 并说明契约缺口，不自行猜测；裸状态、格式错误或 kind 不匹配均由 Root 记为 `contract_error`，不能升级为任何范围的 PASS。已知 kind 时其他原因导致的 BLOCKED 仍带该 kind。

新准备的审查在接收后使用统一入口，将原始 Reviewer 文本保存为文件，显式传入 Root 已核实的验证状态及证据：

```sh
python3 @@CODEX_HOME_SHELL@@/review-workflow.py receive review-round-1 --result reviewer-result.txt --validation PASS --evidence validation.log --state-output acceptance.json
```

此例不申请最终通过。仅当结果为 final PASS、必需验证和隔离证据齐全、没有未关闭必需 findings 时，Root 才追加 `--accept-final`；如果项目另要求代码审查，传入已确认的 `--code-review PASS`。code 结果只决定 code_review，不能生成 final PASS。receive 原样保存结果到独立 receipt 目录、重查绑定和内容、复用 result/state 校验，再原子发布标准字段的状态；错误返回非零并保留原始证据和 contract_error。state-output 必须位于包目录之外。能够可信识别范围时，将对应范围写成 BLOCKED，保留实际 contract_error、review_verdict 为 null，不伪造成 Reviewer 的判断；请求身份本身不可信或输出路径冲突时拒绝发布，Root 不得沿用旧台账作为当前通过依据。该工具不能验证证据语义，也不能拦截绕过它的手工写入，不宣称宿主强制门禁。

已经启动的旧审查继续按下面的兼容步骤接收，不为迁移而重跑内容审查。接收结果后，将原始 Reviewer 文本保存于已有证据目录（保留字节，不自行修空格、去 Markdown 或从正文猜测状态），运行 `python3 @@CODEX_HOME_SHELL@@/review-contract.py result REQUEST.json RESULT.txt`。严格入口也会校验其父会话最终转述首行；外层退出成功仅说明契约格式通过，不能据此自动通过验收。旧 Markdown ReviewRequest 在新严格入口中会在模型启动前被拒绝，应先转换为 JSON。

若只有首行格式错误且范围、快照、证据未变，可向原 Reviewer 要求一次仅重发格式正确的既有结论，保留原始错误和更正文本；不让 Root 自行规范化成 PASS。该更正不构成重新审查。若发生实现修改、补充实质证据、范围改变或先前独立性失效，仍按 fresh 规则处理，不以格式更正规避复审。

正文包含独立核实内容、限制，以及按严重性排序的 findings。初次审查包保持中性，不携带 Root 缺陷假设、建议 verdict 或当前 peer findings；污染后的报告不能作为独立 gate。每个 finding 必须有位置、问题、影响、证据和最小修正方向。缺少当前审查范围必需的可执行证据用 BLOCKED，不能以猜测制造修改请求或用 behavior-only review 替代证据。

### Root 的可机读状态记录

Root 在既有台账或最终结果的 JSON 代码块中分别记录代码审查、验证和最终验收；普通任务无需为此另建文件。固定字段如下，示例仅演示代码通过但验证阻塞、尚未申请最终验收，不构成通过证据：

```json
{
  "scope_id": "example-change",
  "snapshot_id": "example-snapshot",
  "code_review": "PASS",
  "validation": "BLOCKED",
  "final_acceptance": "NOT_REQUESTED",
  "review_kind": "code",
  "review_verdict": "PASS",
  "isolation_requirement": "ordinary",
  "evidence_refs": ["review-result.txt#PASS-code"],
  "contract_error": null
}
```

- `code_review`：PASS / REQUEST_CHANGES / BLOCKED / NOT_REQUESTED。
- `validation`：PASS / FAIL / BLOCKED / NOT_RUN。
- `final_acceptance`：PASS / BLOCKED / NOT_REQUESTED。
- `review_kind`：code / final；`review_verdict`：PASS / REQUEST_CHANGES / BLOCKED。审查尚未返回或契约错误时两者可为 null，不能伪造有效 verdict。
- `isolation_requirement`：ordinary / strict，记录任务要求而非声称实际权限。
- `evidence_refs`：结果所依赖的工具记录或文件位置；实际 PASS 必须有对应证据。`contract_error`：null 或具体格式/字段错误说明。

Root 将有效 `(code)` 结果写入 code_review，验证结果来自对应执行证据；不能从 `(code)` PASS 推出 final_acceptance。只有有效 `(final)` PASS、当前范围所需验证和隔离证据齐全、无必需 findings 未关闭且 contract_error 为 null，才可置 final_acceptance=PASS；若项目另要求代码审查，该项也必须通过。已申请的最终验收遇到失败或缺口为 BLOCKED；未申请保持 NOT_REQUESTED。当前代码审查契约错误记 code_review=BLOCKED；当前 final 审查契约错误记 final_acceptance=BLOCKED，保留原始结果，不从正文附注补造 verdict。范围或版本变化时使受影响状态失效并重新评估，不能沿用旧快照 PASS。结构化字段可供消费者解析，但宿主不提供自动 schema 保证；新增或已有消费者必须对契约错误拒绝通过，不把正则前缀匹配当作完整验证。

写入既有验收台账前，先用 `python3 @@CODEX_HOME_SHELL@@/review-contract.py state REQUEST.json RESULT.txt STATE.json` 校验候选状态。字段使用上述扁平枚举，不用 `accepted` 或嵌套 status 对象替代。result/state 检查失败必须记录 contract_error，并阻止对应范围的通过状态；合法 BLOCKED/REQUEST_CHANGES 的解析成功也不表示业务通过。工具输出的 schema_valid 只证明结构和状态关系，证据真实性、未关闭 findings、测试及 strict 父子探针仍由 Root 独立核对。工具无法拦截绕过它的写入，不能宣称已获得宿主级强制门禁。

普通审查的实际 turn_context 由事后审计者从 rollout 读取，取不到则记未知；不要求 Root 运行时取得子线程上下文。strict 由父入口和 Reviewer 分别产生实际权限探针证据，Root 关联入口退出状态和证据作判断。不得仅凭角色配置或要求字段宣称运行时只读隔离；当前 Codex 不应用角色 `sandbox_mode`，子代理继承父会话权限。

### 独立性与接受

Fresh 的必要条件：

- 新会话，未参与本轮实现。
- 在提供 `fork_turns` 的宿主中显式传 `fork_turns="none"`；其他宿主采用对应的无历史启动机制。默认继承不可视为隔离。
- 仅提供原始 TaskSpec、实现报告（Worker 或 Root）、Root 采集的执行前后证据、验证结果、上轮 findings；不提供 Root/Worker 的完整推理对话。
- 修复后新建 Reviewer，并携带待关闭 findings；fresh 不意味着遗忘缺陷。

Root 自行采集每轮 `A_run`、`C_report`，用两者差异判断范围和报告真实性；审查及接受时采集 `C_now`，检查结果是否漂移。原生测试可保存受控文件的执行前副本与哈希；证据格式和验证命令由目标项目 AGENTS.md 与 TaskSpec 明确。

调用成本的事后审计以每次 spawn/followup 对应的任务轮次为单位：按父线程时间线重建待处理子任务，结果尚未回传时仍属待处理。单一待处理任务时的 wait 归属该任务；多个任务时单列 shared_wait，不重复分摊；无法识别的记 unknown。报告可归属任务 wait 次数中位数/P90、样本数和零等待任务，shared/unknown 单列。汇总 wait 次数除以线程数只是比值，不能代替每任务中位数。正常任务与入口调试分组，指标采集复用日志，不强制每次调用另建报告。

优化决策以完整工作包为主要单位，单子代理轮次作为诊断明细。对需要复盘的非平凡任务，复用现有 scope/验收记录，列出 Root 与全部参与子线程的 rollout 和明确 turn_id、任务开始/最终验收时间、任务类型、实际结果、返工轮数与路由选择。`work-package-metrics.py --spec WORK.json` 只读取显式列出的轮次并输出 JSON；Root 必须确认名单覆盖相关执行、重试和审查，工具不自动证明完整。跨日统计使用事件时间；使用每条线程自身的 response_id 去重用量，缺失遥测标为不完整，不能算零成本。业务结果和返工数来自明确记录，task_complete 不等于 PASS。比较同项目同类任务的端到端耗时、Root+子代理用量与返工；未提供可靠价格不换算货币，不能只凭缓存率或子任务中位数宣布降本。小任务不强制增加指标文件，不为收集统计额外派代理。

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

## 全局使用与项目约束

Codex Root 在每个 Root 会话首次执行原生 Codex 实现、验证或独立审查时读取本协议；当前上下文已有时复用。仅在协议发生变化或上下文压缩导致关键规则丢失时由 Root 补读相关章节。获知规则更新后，旧 Root 在下一次派发/接受前运行 status 并重新读取 TaskSpec、结果解析和状态记录章节，记录新协议哈希；新工作优先在新主会话开始，不假定已有会话热加载。astra_* 子角色不读本协议，以 Root 的自包含 TaskSpec 和项目约束为准；缺少完成任务所需的信息时向 Root 返回缺口，不自行加载协议。Root 必须在 TaskSpec 中提供子角色所需的资源约束、ownership、预算、证据及验收要求。普通问答不触发完整委派流程。

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
