# Controller tags

入口 `maya.rig.controller_tags`，普通Python共用 `core.apply(cmds, params)`。
`inspect`只读，`apply`执行而不保存；Proxy在finalize最后调用同一实现再保存。

Agent先按已有Tag、作者控制集合、层级语义缩小范围，再核可编辑通道与实际用途。
集合中无shape的Offset/SDK组不打Tag；生成曲线、变形路径曲线不直接当控制器。
集合外的名字只作线索：至少结合约束target/矩阵或自定义属性驱动、控制骨骼的
DAG父子关系确认，再传明确`controllers`。面板边框、装饰线、SDK和FitSkeleton
不因是曲线或能编辑就自动打Tag。不确定对象保留为unclassified，Agent只看异常族。

参数：`rig_root`精确根、`controller_sets`作者集合、`controllers`补充对象、
`exclude`排除对象；`enable_gpu/show_controllers`默认true。返回身份、分类依据、
跳过原因、未分类曲线、实际Tag数量、实际宿主开关、改动及持久化范围。
保留现有Tag父子和pickwalk，不创造与原Rig不同的控制层级。

Tag持久保存prepopulate/parentprepopulate=true与visibilityMode=0（Not Overridden），
不覆盖原控制器/shape/组的显隐驱动。当前会话开启Parallel、deformer evaluator和
原生prepopulateController选项；当前model panels开启Controllers与NURBS Curves。
`modelEditor.controllers`见Maya2025原生createModelPanelMenu.mel，并经2025.3实测，
发布的modelEditor flag目录未列出，不能无验证宣称任意版本支持。

GPU开关、硬件可用、具体节点实际分配与性能收益分别成立；此API只声明前两项。
Tag不等于GPU开关，环境设置不伪装为文件属性。未来会话由打开/准备入口再次调用，
不装scriptNode或自动回调。用户主动关Show Controllers时会按Maya正常语义隐藏，
不能用Not Overridden绕过该过滤器；API只保证准备时开关开启。

官方依据：
- [Maya2025 Tag objects as controllers](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-CharacterAnimation/files/GUID-17AF3B37-8A57-46DD-8808-D4BFF23AD285.htm)
- [Maya2025 controller command](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-Tech-Docs/CommandsPython/controller.html)
- [Maya2025 Set controller visibility](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-CharacterAnimation/files/GUID-2380579F-F59C-4E7D-904C-B6B90E949D35.htm)
- [Maya2025 Evaluation Toolkit](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-Customizing/files/GUID-E22B253D-914B-4056-93F5-755702A6C998.htm)
- [Maya2025 Evaluator Reference](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-Customizing/files/GUID-27845E36-B873-42EB-B06F-9FE983E1080D.htm)

原生Tag/属性为undoable；EM及视口状态独立捕获，失败时恢复。单独执行及Proxy
外层事务共用同一实现；重复执行不添加第二个Tag。2025.3真实证据在PL295。
