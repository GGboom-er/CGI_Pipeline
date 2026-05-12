# tests/e2e_abc/check_facesets.py
# 检查 ABC 导入后 Maya 是否生成了 FaceSet / ShadingGroup
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds

cmds.loadPlugin('AbcImport', quiet=True)
cmds.file(new=True, f=True)

abc = 'Y:/GGbommer/scripts/CGI_Pipeline/projects/ysj/pub/assets/chr/hamaguai/tex/paintTex/ysj_chr_hamaguai_tex_paintTex_v001.abc'
cmds.file(abc, i=True, type='Alembic', namespace=':')

print("=" * 60)
print("FACESET CHECK")
print("=" * 60)

# 1. 所有 mesh
meshes = cmds.ls(type='mesh', noIntermediate=True, long=True) or []
print(f"\nMeshes: {len(meshes)}")

# 2. 所有 objectSet（排除默认）
all_sets = cmds.ls(type='objectSet') or []
default_sets = {'defaultLightSet', 'defaultObjectSet', 'initialShadingGroup', 'initialParticleSE'}
custom_sets = [s for s in all_sets if s not in default_sets]
print(f"\nAll objectSets: {len(all_sets)}")
print(f"Custom objectSets: {custom_sets}")

# 3. ShadingEngine
sgs = cmds.ls(type='shadingEngine') or []
custom_sgs = [s for s in sgs if s not in ('initialShadingGroup', 'initialParticleSE')]
print(f"\nCustom ShadingEngines: {custom_sgs}")
for sg in custom_sgs:
    members = cmds.sets(sg, q=True) or []
    print(f"  {sg}: {len(members)} members, first 3: {members[:3]}")

# 4. 查看前 3 个 mesh 的 set 隶属关系
print("\n--- Mesh → Set 映射 ---")
for m in meshes[:3]:
    transform = cmds.listRelatives(m, parent=True, fullPath=True)
    node = transform[0] if transform else m
    render_sets = cmds.listSets(object=node, type=1) or []
    all_obj_sets = cmds.listSets(object=node) or []
    print(f"  {node.split('|')[-1]}:")
    print(f"    render sets (type=1): {render_sets}")
    print(f"    all sets: {all_obj_sets}")

# 5. 检查 mesh shape 上是否有 user-defined attributes (custom properties)
print("\n--- Shape User Attributes ---")
for m in meshes[:3]:
    attrs = cmds.listAttr(m, userDefined=True) or []
    print(f"  {m.split('|')[-1]}: {attrs if attrs else '(none)'}")

# 6. 检查 transform 上是否有 user-defined attributes
print("\n--- Transform User Attributes ---")
for m in meshes[:3]:
    t = cmds.listRelatives(m, parent=True, fullPath=True)
    if t:
        attrs = cmds.listAttr(t[0], userDefined=True) or []
        print(f"  {t[0].split('|')[-1]}: {attrs if attrs else '(none)'}")

# 7. 检查顶层 Group 节点
print("\n--- Top Group Attributes ---")
top_nodes = cmds.ls(assemblies=True) or []
for tn in top_nodes:
    if tn in ('persp', 'top', 'front', 'side'):
        continue
    attrs = cmds.listAttr(tn, userDefined=True) or []
    print(f"  {tn}: {attrs if attrs else '(none)'}")
    children = cmds.listRelatives(tn, children=True) or []
    for ch in children[:3]:
        ch_attrs = cmds.listAttr(ch, userDefined=True) or []
        if ch_attrs:
            print(f"    {ch}: {ch_attrs}")

print("\n" + "=" * 60)
print("CHECK DONE")
print("=" * 60)

maya.standalone.uninitialize()
