# 检查 yjTie 场景中 cache 下所有 mesh 的修改器情况
import bpy, json

cache = bpy.data.objects.get("cache")
report = {}

for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    # 检查是否在 cache 下
    p = obj.parent
    under_cache = False
    while p:
        if p == cache:
            under_cache = True
            break
        p = p.parent
    if not under_cache:
        continue

    mods = []
    for m in obj.modifiers:
        mods.append({
            "name": m.name,
            "type": m.type,
            "show_viewport": m.show_viewport,
            "show_render": m.show_render,
        })

    base_verts = len(obj.data.vertices)
    # 获取 evaluated 顶点数
    dg = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(dg)
    eval_mesh = eval_obj.to_mesh()
    eval_verts = len(eval_mesh.vertices)
    eval_obj.to_mesh_clear()

    if mods or base_verts != eval_verts or base_verts > 50000:
        report[obj.name] = {
            "base_verts": base_verts,
            "eval_verts": eval_verts,
            "modifiers": mods,
        }

print("=" * 60)
print(json.dumps(report, indent=2, ensure_ascii=False))
print(f"\nTotal flagged: {len(report)}")
print("=" * 60)
