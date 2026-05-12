"""
在 Maya Script Editor 中执行此脚本
可视化 5 种方法的对比结果
"""
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om2

# 加载数据
viz = np.load(r'Y:\GGbommer\scripts\CGI_Pipeline\test_fm_smooth_viz.npz')
y_raw = viz['y_raw']
y_spectral = viz['y_spectral']
y_cycle = viz['y_cycle']
y_rhm = viz['y_rhm']
aa_pos = viz['aa_pos']
kkk_pos = viz['kkk_pos']

mesh_name = 'KKK'
n_vtx = len(kkk_pos)

sel = om2.MSelectionList()
sel.add(mesh_name)
dag = sel.getDagPath(0)
fn_mesh = om2.MFnMesh(dag)

aa_y = aa_pos[:, 1]
y_min, y_max = float(aa_y.min()), float(aa_y.max())

def y_to_color_array(y_values, y_min, y_max, n):
    norm = np.clip((y_values - y_min) / (y_max - y_min), 0, 1)
    colors = om2.MColorArray()
    vtx_ids = om2.MIntArray()
    for i in range(n):
        v = float(norm[i])
        if v < 0.25:
            r, g, b = 0.0, v*4, 1.0
        elif v < 0.5:
            r, g, b = 0.0, 1.0, 1.0 - (v-0.25)*4
        elif v < 0.75:
            r, g, b = (v-0.5)*4, 1.0, 0.0
        else:
            r, g, b = 1.0, 1.0 - (v-0.75)*4, 0.0
        colors.append(om2.MColor((r, g, b, 1.0)))
        vtx_ids.append(i)
    return colors, vtx_ids

# 删除旧 colorSets
existing = cmds.polyColorSet(mesh_name, query=True, allColorSets=True) or []
for cs in existing:
    cmds.polyColorSet(mesh_name, delete=True, colorSet=cs)

# 1. Raw p2p
cmds.polyColorSet(mesh_name, create=True, colorSet='1_raw_p2p', representation='RGBA')
cmds.polyColorSet(mesh_name, currentColorSet=True, colorSet='1_raw_p2p')
c, v = y_to_color_array(y_raw, y_min, y_max, n_vtx)
fn_mesh.setVertexColors(c, v)

# 2. Spectral Transfer k=60
cmds.polyColorSet(mesh_name, create=True, colorSet='2_spectral_k60', representation='RGBA')
cmds.polyColorSet(mesh_name, currentColorSet=True, colorSet='2_spectral_k60')
c, v = y_to_color_array(y_spectral, y_min, y_max, n_vtx)
fn_mesh.setVertexColors(c, v)

# 3. Cycle + Adaptive
cmds.polyColorSet(mesh_name, create=True, colorSet='3_cycle_adaptive', representation='RGBA')
cmds.polyColorSet(mesh_name, currentColorSet=True, colorSet='3_cycle_adaptive')
c, v = y_to_color_array(y_cycle, y_min, y_max, n_vtx)
fn_mesh.setVertexColors(c, v)

# 4. RHM
cmds.polyColorSet(mesh_name, create=True, colorSet='4_rhm', representation='RGBA')
cmds.polyColorSet(mesh_name, currentColorSet=True, colorSet='4_rhm')
c, v = y_to_color_array(y_rhm, y_min, y_max, n_vtx)
fn_mesh.setVertexColors(c, v)

# 默认显示 spectral
cmds.polyColorSet(mesh_name, currentColorSet=True, colorSet='2_spectral_k60')
cmds.setAttr('{}.displayColors'.format(mesh_name), 1)

# AA reference
aa_mesh = 'AA'
sel2 = om2.MSelectionList()
sel2.add(aa_mesh)
dag2 = sel2.getDagPath(0)
fn_mesh2 = om2.MFnMesh(dag2)

existing2 = cmds.polyColorSet(aa_mesh, query=True, allColorSets=True) or []
for cs in existing2:
    cmds.polyColorSet(aa_mesh, delete=True, colorSet=cs)

cmds.polyColorSet(aa_mesh, create=True, colorSet='reference', representation='RGBA')
cmds.polyColorSet(aa_mesh, currentColorSet=True, colorSet='reference')
c_aa, v_aa = y_to_color_array(aa_y, y_min, y_max, len(aa_y))
fn_mesh2.setVertexColors(c_aa, v_aa)
cmds.setAttr('{}.displayColors'.format(aa_mesh), 1)

print("Done! KKK colorSets: 1_raw_p2p / 2_spectral_k60 / 3_cycle_adaptive / 4_rhm")
print("Switch colorSet: cmds.polyColorSet('KKK', currentColorSet=True, colorSet='xxx')")
