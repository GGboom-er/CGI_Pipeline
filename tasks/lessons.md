# 经验沉淀 (Lessons Learned)

## 架构与引擎

- [变量解析失败] → [未处理字典嵌套查找与列表索引混用] → [在 _resolve_template_vars 中通过 isinstance 和 isdigit 进行分流校验] → [当处理 {{config.x.y.0}} 这样的路径变量时，对于数字必须先转换为 int 才能去列表中索引]
- [魔法字符串污染] → [在工作流 json 中硬编码了 'cache' 等规范性词汇，切换项目即崩溃] → [所有目标组必须使用 {{config...}} 形式去读取 registry 或 project_config] → [永远不要在 Workflow 定义文件中写入任何特定于项目的节点/组名]
- [零散MD报告无法对齐实际任务] → [各技能各自写.md导致信息碎片化] → [禁止技能自行落盘报告，统一通过receipt['report_content']回传主控拼装] → [报告生成权必须归属调度层，原子技能只生产内容不决定输出形式]
- [审计日志detail截断导致报告乱码] → [chain引擎对detail做500字符截断，外部解析json.loads失败] → [用正则从截断JSON中提取关键字段] → [审计日志是审计用途非数据传输，调用方必须容忍截断并实现优雅降级]
- [后台hold卡住] → [QC失败复用NEEDS_ATTENTION] → [改AUDIT_FAILED] → [批处理只完成或失败]
- [恢复跳过失败段] → [失败前标记完成] → [成功后再mark] → [只记录成功段]
- [报告噪音过多] → [中间产物外露] → [唯一MD+.info] → [人看报告机器看JSON]
- [职责冲突] → [ABC导出和几何采集都尝试写材质数据] → [导出只产ABC，材质只由extract_materials产出] → [一个机器产物只能有一个权威生产者]
- [采集职责漂移] → [asset_info混入贴图职责] → [几何只写_info,材质写_materials] → [采集节点不得跨域生产材料数据]
- [输出契约变胖] → [统计字段塞进outputs] → [计数走summary_count] → [outputs只放路径和下游机器契约]
- [默认输出污染] → [compare_result回退输入目录] → [只从.info推导或ERROR] → [机器中间产物不得默认写源目录]
- [ABC输出污染] → [导出默认写源目录或pub] → [abc_path显式或.info推导] → [导出类节点不得碰发布目录]
- [材质JSON散落] → [extract_materials按源blend推导] → [output_path显式或.info推导] → [材料中间产物也必须进.info]
- [缓存分支干扰] → [旧JSON可能被复用] → [每任务重采] → [沙盒产物不做mtime缓存]
- [采集混入诊断] → [Orig缺失直接门禁] → [空几何交给对比] → [采集skill只输出事实]

## Worker 与服务管理

- [Maya commandPort 阻塞与静默崩溃] → [在 echoOutput=False 模式下强行通过 socket 发送超长字符串脚本，极易导致端口句柄卡死且无法捕获异常] → [放弃原生 socket 强压，改为使用 commandPort 仅发送单行 Base64 引导脚本，从而拉起独立的 RPyC (Remote Python Call) 服务端接管后续通信] → [必须利用 RPyC 代理机制结合 executeInMainThreadWithResult，彻底解决跨进程大对象回传与多线程安全问题]
- [Worker假活] → [PID存活但队列无心跳] → [PID+active_queues双检] → [提交前心跳异常要重启]
- [心跳误报] → [1秒inspect偶发超时] → [默认5秒窗口] → [服务探测不等于任务超时]
- [代码热更新未生效] → [worker持旧模块缓存] → [重启worker再巡航] → [改运行时代码后必须重启]
- [多Maya误连] → [foreground省略端口会落到默认或首个端口] → [强制显式foreground_port] → [多端口场景禁隐式选择]
- [AI误用Maya直连] → [入口文档仍写自动嗅探/旧maya-live] → [同步MCP说明和skill] → [当前场景必显式端口]
- [原始MCP调用报参错] → [漏FastMCP params外壳] → [文档写明wrapper] → [手测禁平铺]
- [新技能 CHAIN_ABORTED "混合多个DCC类型"] → [长驻 Worker 进程的 skill_registry 快照过期，新技能 get_skill_dcc 返回默认值 'maya'] → [在 tasks.py 三处关键位置注入 _reload_skill_registry()：链DCC校验前、单技能执行前、工作流分段前] → [任何依赖注册表的判断逻辑前，必须先热重载，因为 Worker 可能运行数天]
- [shutdown_all() 无法杀旧Worker] → [只遍历 _managed_procs（空列表，因为 Worker 由其他进程启动）] → [增加 PID 文件扫描逻辑，根据 pidfile 内容 os.kill] → [进程管理不能只依赖内存中的句柄，必须有持久化的 PID 文件作为兜底]
- [DCC启动延迟] → [冷启动耗时5-8s] → [实现WarmWorkerProxy常驻池] → [高频任务用常驻池，50次自动重启]

## 数据安全与路径

- [沙盒路径隔离失败] → [Ai_pub映射代理未校验文件存在] → [移除Ai_pub代理，直接抛出FileNotFoundError并使用runs沙盒] → [沙盒拷贝前必须用.exists()强校验源文件]
- [生产数据安全隐患] → [AI或自动化脚本直接修改或生成文件至X盘服务器] → [管线引擎自动将X盘只做读取源，所有文件统一拷贝至沙盒内闭环执行] → [X盘绝对只读，禁止任何形式的增、删、改、覆盖]
- [.env覆盖默认] → [IPC_TIMEOUT_SEC仍为1800] → [同步改0] → [默认修复查.env]
- [中间产物散落] → [workflow未显式传info_dir] → [统一写沙盒.info] → [JSON/ABC只进.info]
- [沙盒名噪音] → [task_id混入用户路径] → [日期时间_资产名] → [task_id只进manifest]

## 对比与验证

- [对比误报顶点偏移] → [JSON精度与阈值不匹配] → [统一全链路为4位小数] → [序列化精度须与对比算法阈值对齐]
- [对比误报差异导致FAIL] → [绑定中合法的Live BlendShape目标体被认定为源数据中缺失的资产] → [在对比引擎中直接过滤以 _live_ 为标志的合法驱动体] → [管线工具在进行资产对齐验证时，必须能够原生地识别并豁免DCC特有的合法过程节点]
- [预对比重复] → [sync内重算] → [compare_result驱动] → [节点间传决策数据]
- [验证误判失败] → [把ORIG_INJECT算阻断] → [看4去向] → [post验证只拦真差异]
- [拼装漂移] → [sync重算对比] → [前置compare_result] → [拼装不独立对比]
- [Maya采集复用] → [core禁DCC] → [放dccs/maya] → [DCC API不进core]
- [新建mesh空几何] → [无历史不产Orig] → [sync补标准Orig] → [采集器不兜底]
- [sync难单测] → [Maya执行和契约混写] → [纯Python契约层] → [DCC大函数外置可测边界]
- [根组漏配] → [只传首根] → [候选根解析] → [geom_roots全传]
- [Orig误判] → [按名称判断] → [official+tweak+连接] → [图关系优先]
- [报告难读] → [英文枚举外露] → [配置映射] → [人读中文]
- [报告过薄] → [只写验收摘要] → [audit渲染明细] → [唯一MD含步骤]
- [运行中报告缺失] → [只在收尾读audit重建] → [调度层实时upsert REPORT.md] → [报告生命周期必须挂在step事件上]
- [复杂报告不可折叠] → [长Markdown混成一块] → [receipt.report_sections分章] → [复杂skill优先返回结构化事实]
- [报告块外露] → [HTML和旧marker在查看器显示] → [普通Markdown+隐藏引用marker] → [报告渲染不依赖HTML折叠]
- [报告重复] → [sections与旧content同时渲染] → [结构化优先屏蔽旧内容] → [同一事实只出现一次]
- [跨段报告覆盖] → [固定block只存本段记录] → [REPORT内隐藏数据合并] → [跨DCC上下文要累积]
- [入口硬编码] → [巡航脚本写死tex/rig路径] → [resolve_asset_files节点化解析] → [workflow只连outputs不写路径]
- [旧geo残留] → [sync保留审核] → [独立清退] → [拼装不删旧根]
- [层级QC过宽] → [误查辅助mesh] → [只查流程根] → [QC边界跟执行依赖一致]
- [层级修复失败] → [顶层旧资产组被锁] → [先解锁层级再删] → [清理散落根必须处理lockNode]
- [修复目标漂移] → [fix自行扫描导致与check不一致] → [fix消费check_result] → [修复节点不得二次决策目标]
- [节点传参断档] → [只传路径需再读盘] → [直传output.compare_result] → [同段节点可传机器字典]
- [旧geo根被删] → [当extra_top_nodes清理] → [先改Group再迁RIG_geo] → [旧绑定根只归一不删除]
- [RIG_geo匹配丢失] → [根RIG_也被剥掉] → [保根剥子级] → [DAG映射区分根和子节点]
- [层级误阻断] → [额外非空顶层当失败] → [输出风险不默认阻断] → [绑定辅助根默认保留]
- [层级回归难发现] → [只靠专项测试] → [并入总门禁] → [关键 workflow 契约进 verify]

## 技能开发

- [巡航入口启动失败] → [Start-Process截断python -c] → [补tools包装脚本] → [长任务入口用脚本文件]
- [环境误跑] → [PATH命中系统Python/conda输出编码] → [直调env python] → [pipeline命令用绝对解释器]
- [CLI单技能失效] → [WarmWorkerProxy无start] → [start做幂等no-op] → [工厂返回对象接口要稳定]
- [技能文件夹自包含] → [分发部署需打包文件夹，.py散落根目录不便管理] → [adapter改为skills.{id}.{id}加载+__init__.py兼容外部import] → [每个技能=一个文件夹({id}.py+SKILL.md+__init__.py)，可独立打包]
- [自动巡航测试参数透传失效] → [调用 blender_export_abc 时错将参数名设为 export_path，导致内部降级使用 source_path 推导] → [检查并对齐工作流载荷与原子技能的 parameters 键名] → [组装自动化执行链时，必须严格校验上下游节点约定的输入/输出字段名，避免静默降级]
- [工作流引用不存在的skill_id] → [旧工作流JSON用短名(master_cleanup)而非全名(maya_master_cleanup)] → [全量审计修复为正确的skill_id] → [工作流JSON中的skill_id必须与SKILL.md中声明的skill_id完全一致，不能省略前缀]
- [技能审计失配] → [回执仍用旧短名和自定义路径key] → [统一skill_id与output_path/report_path] → [改skill输出前必须同步workflow模板]
- [技能契约漂移] → [frontmatter旧类型] → [统一枚举] → [交付前跑扫描]
- [模板未解析] → [replace回归成非法路径] → [加扫描] → [跨段模板进门禁]
- [多占位符误吞] → [正则跨模板匹配] → [禁止匹配花括号] → [同串多模板必测]
- [节点输出漂移] → [业务字段塞outputs顶层] → [只准三键] → [特殊参数走outputs.result]

## 编码规范

- [API 废弃导致依赖崩溃] → [移除核心路径保护逻辑时未连带清理子模块中对 suggest_ai_publish_path 的 import] → [全局搜索并移除废弃函数的引用] → [废弃或重构核心 API 时，必须执行全仓库 grep 搜索，确保无孤立的跨模块调用残余]
- [Python 语法报错 (SyntaxError)] → [在 logger.info 中直接手敲换行符导致未闭合字符串] → [使用显式的转义字符 \n 或三引号] → [严禁在单行函数调用内混入原生回车换行]
- [测试契约漂移] → [脚本读取旧uvsets字段] → [改测u/v/uv_indices] → [测试字段跟asset_info契约同步]
- [旧测试误导] → [废弃脚本仍在根tests] → [归档legacy_manual] → [当前入口写README]

## 文档治理

- [文档权威漂移] → [历史方案与现契约并存] → [先分权威/专项/历史/归档] → [整理前必须定唯一真相源]
- [专项文档过期] → [重构后旧链路残留] → [重写权威段落] → [bak归档不当现行规范]
- [归档误用] → [历史文档混在主目录] → [移入archive并写索引] → [主docs只放现行契约]
- [报告契约膨胀] → [多套展示字段混用] → [只渲染标准记录] → [报告不二次理解]
- [规范多头] → [指南与总规范重复] → [唯一CONVENTION] → [其他文档只引用]
- [规则仍散] → [文档不是触发入口] → [收敛到构建skill] → [规则必须可被调用]
- [跳转页干扰] → [入口多但无正文] → [只留规范正文] → [规范文件必须确定]
