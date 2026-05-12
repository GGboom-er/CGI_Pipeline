"""
在 Maya 中对比三种权重传递方法的结果。
对 TT mesh 创建三个副本，分别应用不同方法的权重，方便可视化对比。

使用方式：在 Maya Script Editor 中执行此脚本
"""
import json
import maya.cmds as cmds
import maya.api.OpenMaya as om2

BASE_PATH = 'Y:/GGbommer/scripts/CGI_Pipeline/_maya_export'


def load_results():
    """加载三种方法的结果"""
    results = {}

    with open(f'{BASE_PATH}/mapping_result.json') as f:
        results['igl'] = json.load(f)

    with open(f'{BASE_PATH}/voxel_result.json') as f:
        results['voxel'] = json.load(f)

    with open(f'{BASE_PATH}/fm_result.json') as f:
        results['fm'] = json.load(f)

    return results


def apply_weights_to_mesh(mesh_name, weights_data, joints):
    """将权重应用到指定 mesh 的 skinCluster"""
    # 获取 skinCluster
    history = cmds.listHistory(mesh_name, pdo=True) or []
    skin_clusters = cmds.ls(history, type='skinCluster')

    if skin_clusters:
        skin = skin_clusters[0]
    else:
        # 创建 skinCluster
        existing_joints = [j for j in joints if cmds.objExists(j)]
        if not existing_joints:
            cmds.warning(f"No joints found for {mesh_name}")
            return False
        skin = cmds.skinCluster(existing_joints, mesh_name, toSelectedBones=True,
                                 maximumInfluences=4, normalizeWeights=1)[0]

    # 设置权重
    n_verts = len(weights_data)
    sel = om2.MSelectionList()
    sel.add(mesh_name)
    dag_path = sel.getDagPath(0)

    fn_skin = None
    it = om2.MItDependencyGraph(sel.getDependNode(0),
                                 om2.MFn.kSkinClusterFilter,
                                 om2.MItDependencyGraph.kDownstream)
    while not it.isDone():
        fn_skin = om2.MFnSkinCluster(it.currentNode())
        break
        it.next()

    if fn_skin is None:
        cmds.warning(f"Cannot find skinCluster for {mesh_name}")
        return False

    # 获取影响骨骼列表
    inf_dags = fn_skin.influenceObjects()
    inf_names = [d.partialPathName() for d in inf_dags]

    # 构建 joint index 映射
    joint_to_inf = {}
    for ji, jname in enumerate(joints):
        short_name = jname.split('|')[-1].split(':')[-1]
        for ii, inf_name in enumerate(inf_names):
            inf_short = inf_name.split('|')[-1].split(':')[-1]
            if short_name == inf_short:
                joint_to_inf[ji] = ii
                break

    # 设置权重
    vert_comp = om2.MFnSingleIndexedComponent()
    vert_obj = vert_comp.create(om2.MFn.kMeshVertComponent)
    vert_comp.addElements(list(range(n_verts)))

    n_inf = len(inf_dags)
    weight_array = om2.MDoubleArray(n_verts * n_inf, 0.0)

    for vi in range(n_verts):
        for ji, w in enumerate(weights_data[vi]):
            if ji in joint_to_inf and w > 0.0:
                inf_idx = joint_to_inf[ji]
                weight_array[vi * n_inf + inf_idx] = w

    inf_indices = om2.MIntArray(list(range(n_inf)))
    fn_skin.setWeights(dag_path, vert_obj, inf_indices, weight_array, normalize=True)

    return True


def create_comparison():
    """创建对比场景"""
    results = load_results()

    # 对 TT 做对比（三种方法都有 TT 的结果）
    target_name = 'TT'
    offset_x = 20.0  # 每个副本的 X 偏移

    methods = []
    if target_name in results['igl']:
        methods.append(('igl', results['igl'][target_name]))
    if target_name in results['voxel']:
        methods.append(('voxel', results['voxel'][target_name]))
    if target_name in results['fm']:
        methods.append(('fm', results['fm'][target_name]))

    # 检查原始 TT 是否存在
    if not cmds.objExists(target_name):
        cmds.warning(f"{target_name} not found in scene")
        return

    for i, (method_name, data) in enumerate(methods):
        copy_name = f'{target_name}_{method_name}'

        # 删除已有副本
        if cmds.objExists(copy_name):
            cmds.delete(copy_name)

        # 复制 mesh
        dup = cmds.duplicate(target_name, name=copy_name)[0]
        cmds.move(offset_x * (i + 1), 0, 0, dup, relative=True)

        # 应用权重
        weights = data['weights']
        joints = data['joints']
        success = apply_weights_to_mesh(copy_name, weights, joints)

        if success:
            print(f"[OK] {copy_name}: weights applied ({method_name})")
        else:
            print(f"[FAIL] {copy_name}: could not apply weights")

    # 同样对 BB 做对比（igl 和 voxel 有 BB）
    target_name = 'BB'
    if cmds.objExists(target_name):
        bb_methods = []
        if target_name in results['igl']:
            bb_methods.append(('igl', results['igl'][target_name]))
        if target_name in results['voxel']:
            bb_methods.append(('voxel', results['voxel'][target_name]))

        for i, (method_name, data) in enumerate(bb_methods):
            copy_name = f'{target_name}_{method_name}'
            if cmds.objExists(copy_name):
                cmds.delete(copy_name)

            dup = cmds.duplicate(target_name, name=copy_name)[0]
            cmds.move(offset_x * (i + 1), 0, 0, dup, relative=True)

            weights = data['weights']
            joints = data['joints']
            success = apply_weights_to_mesh(copy_name, weights, joints)

            if success:
                print(f"[OK] {copy_name}: weights applied ({method_name})")
            else:
                print(f"[FAIL] {copy_name}: could not apply weights")

    print("\nDone! Compare by selecting joints and rotating.")


if __name__ == '__main__':
    create_comparison()
