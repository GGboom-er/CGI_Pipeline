"""快速测试 mayapy 加载场景，跳过 IPC 层定位弹窗"""
import os, sys, time

# 设置所有防弹窗环境变量
os.environ['MAYA_SKIP_UNSUPPORTED_PLUGINS_DIALOG'] = '1'
os.environ['MAYA_NO_WARNING_FOR_MISSING_DEFAULT_RENDERER'] = '1'
os.environ['MAYA_DISABLE_ADP'] = '1'
os.environ['MAYA_OPENCL_IGNORE_DRIVER_VERSION'] = '1'
os.environ['PYMEL_SKIP_MEL_INIT'] = '1'
os.environ['MAYA_NO_HOME'] = '1'
os.environ['MAYA_DISABLE_CIP'] = '1'

print('[1] 初始化 maya.standalone...')
t0 = time.time()
import maya.standalone
maya.standalone.initialize(name='python')
print(f'[2] maya.standalone 初始化完成 ({time.time()-t0:.1f}s)')

import maya.cmds as cmds
cmds.optionVar(iv=('fileIgnoreVersion', 1))
cmds.optionVar(iv=('useSaveScenePanelLayout', 0))

scene = r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\pub\assets\chr\xiaotianquan\rig\rigMaster\ysj_chr_xiaotianquan_rig_rigMaster_v003.ma'

print(f'[3] 打开场景 ({os.path.getsize(scene)/1024/1024:.0f}MB)...')
t1 = time.time()
cmds.file(scene, open=True, force=True, ignoreVersion=True,
          loadReferenceDepth='none', prompt=False)
print(f'[4] 场景加载完成 ({time.time()-t1:.1f}s)')

all_dag = cmds.ls(dag=True, long=True) or []
print(f'[5] DAG 节点数: {len(all_dag)}')

# 显示顶层节点
top_nodes = [n for n in all_dag if n.count('|') == 1]
print(f'[6] 顶层节点 ({len(top_nodes)}):')
for n in top_nodes[:30]:
    print(f'    {n}')
if len(top_nodes) > 30:
    print(f'    ... 还有 {len(top_nodes)-30} 个')

maya.standalone.uninitialize()
print('[7] 完成')
