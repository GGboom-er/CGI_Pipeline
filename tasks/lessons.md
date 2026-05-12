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

## 技能开发

- [技能文件夹自包含] → [分发部署需打包文件夹，.py散落根目录不便管理] → [adapter改为skills.{id}.{id}加载+__init__.py兼容外部import] → [每个技能=一个文件夹({id}.py+SKILL.md+__init__.py)，可独立打包]
- [自动巡航测试参数透传失效] → [调用 blender_export_abc 时错将参数名设为 export_path，导致内部降级使用 source_path 推导] → [检查并对齐工作流载荷与原子技能的 parameters 键名] → [组装自动化执行链时，必须严格校验上下游节点约定的输入/输出字段名，避免静默降级]
- [工作流引用不存在的skill_id] → [旧工作流JSON用短名(master_cleanup)而非全名(maya_master_cleanup)] → [全量审计修复为正确的skill_id] → [工作流JSON中的skill_id必须与SKILL.md中声明的skill_id完全一致，不能省略前缀]
- [技能审计失配] → [回执仍用旧短名和自定义路径key] → [统一skill_id与output_path/report_path] → [改skill输出前必须同步workflow模板]
- [技能契约漂移] → [frontmatter旧类型] → [统一枚举] → [交付前跑扫描]
- [模板未解析] → [replace回归成非法路径] → [加扫描] → [跨段模板进门禁]

## 编码规范

- [API 废弃导致依赖崩溃] → [移除核心路径保护逻辑时未连带清理子模块中对 suggest_ai_publish_path 的 import] → [全局搜索并移除废弃函数的引用] → [废弃或重构核心 API 时，必须执行全仓库 grep 搜索，确保无孤立的跨模块调用残余]
- [Python 语法报错 (SyntaxError)] → [在 logger.info 中直接手敲换行符导致未闭合字符串] → [使用显式的转义字符 \n 或三引号] → [严禁在单行函数调用内混入原生回车换行]

## 文档治理

- [文档权威漂移] → [历史方案与现契约并存] → [先分权威/专项/历史/归档] → [整理前必须定唯一真相源]
