# -*- coding: utf-8 -*-
"""
joint_orient_tools.py

Two complementary joint orient management functions for a SINGLE skeleton:

1. bake_to_orient(root)
   - Bake current rotate/rotateAxis into jointOrient
   - Zero rotate and rotateAxis channels
   - World matrix preserved per joint (verified)

2. restore_bind_pose(root)
   - Restore bind pose using rotate channels ONLY
   - JointOrient remains unchanged
   - Data source: skinCluster.bindPreMatrix + dagPose
   - Zero rotate → current orient pose, set rotate → bind pose

Usage:
    import joint_orient_tools as jot

    # Bake current pose into orient (e.g. after posing to T-pose)
    jot.bake_to_orient('root')

    # Restore bind pose via rotate channels (orient untouched)
    jot.restore_bind_pose('root')

@author: GGboom
@license: MIT
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import math


# =============================================================================
# Utilities
# =============================================================================

def _get_world_matrix(node):
    """Get world matrix as MMatrix."""
    return om2.MMatrix(cmds.xform(node, q=True, ws=True, m=True))


def _set_world_matrix(node, matrix):
    """Set world matrix from MMatrix."""
    cmds.xform(node, ws=True, m=list(matrix))


def _matrix_close(m1, m2, tol=1e-5):
    """Check if two MMatrix are approximately equal."""
    for i in range(4):
        for j in range(4):
            if abs(m1.getElement(i, j) - m2.getElement(i, j)) > tol:
                return False
    return True


def _get_all_joints(root):
    """Get all joints under root (inclusive), unsorted."""
    joints = cmds.listRelatives(root, allDescendents=True, type='joint') or []
    joints.append(root)
    return joints


def _sort_by_depth(joints):
    """Sort joints by hierarchy depth (parents first)."""
    return sorted(joints, key=lambda j: cmds.ls(j, long=True)[0].count('|'))


def _is_attr_settable(attr):
    """Check if attribute is not locked and has no incoming connections."""
    if cmds.getAttr(attr, lock=True):
        return False
    if cmds.listConnections(attr, source=True, destination=False):
        return False
    return True


# =============================================================================
# Function 1: Bake rotations into orient
# =============================================================================

def bake_to_orient(root_joint):
    """
    Bake current rotate and rotateAxis values into jointOrient.

    - World matrix is preserved for every joint (verified)
    - Rotate and rotateAxis are zeroed
    - Joints with non-unit scale are skipped (safety)
    - Wrapped in undo chunk; rolls back on any error

    Args:
        root_joint (str): Root joint of the skeleton to process.

    Raises:
        RuntimeError: If matrix verification fails (auto rolled back).
    """
    joints = _get_all_joints(root_joint)

    cmds.undoInfo(openChunk=True)
    processed = 0

    try:
        for jnt in joints:
            # --- Safety: skip non-unit scale ---
            scale = cmds.getAttr(jnt + '.scale')[0]
            if any(abs(s - 1.0) > 1e-5 for s in scale):
                om2.MGlobal.displayWarning(
                    u'[bake_to_orient] Skipped (non-unit scale): {}'.format(jnt))
                continue

            shear = cmds.getAttr(jnt + '.shear')[0]
            if any(abs(s) > 1e-5 for s in shear):
                om2.MGlobal.displayWarning(
                    u'[bake_to_orient] Skipped (shear): {}'.format(jnt))
                continue

            # --- Capture world matrix ---
            W_before = _get_world_matrix(jnt)

            # --- Compute local matrix: L = W * P^-1 (row-vector) ---
            parent = cmds.listRelatives(jnt, parent=True)
            if parent:
                W_parent_inv = _get_world_matrix(parent[0]).inverse()
                L = W_before * W_parent_inv
            else:
                L = W_before

            # --- Extract rotation -> new jointOrient (XYZ order) ---
            q = om2.MTransformationMatrix(L).rotation(asQuaternion=True)
            euler = q.asEulerRotation()
            new_jo = (math.degrees(euler.x),
                      math.degrees(euler.y),
                      math.degrees(euler.z))

            cmds.setAttr(jnt + '.jointOrient', *new_jo)
            cmds.setAttr(jnt + '.rotate', 0, 0, 0)
            cmds.setAttr(jnt + '.rotateAxis', 0, 0, 0)

            # --- Verify world matrix unchanged ---
            W_after = _get_world_matrix(jnt)
            if not _matrix_close(W_before, W_after):
                _set_world_matrix(jnt, W_before)
                raise RuntimeError(
                    'Matrix mismatch after processing: {}'.format(jnt))

            processed += 1

    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise RuntimeError(u'[bake_to_orient] Failed, rolled back: {}'.format(e))

    cmds.undoInfo(closeChunk=True)
    om2.MGlobal.displayInfo(
        u'[bake_to_orient] Done: {} joints processed'.format(processed))


# =============================================================================
# Function 2: Restore bind pose via rotate channels
# =============================================================================

def _collect_bind_world_matrices(joints):
    """
    Collect bind-time world matrices for joints.

    Priority:
    1. skinCluster.bindPreMatrix (most reliable, unaffected by orient changes)
    2. dagPose.worldMatrix (fallback for non-skin joints)

    Returns:
        dict: {joint_name: MMatrix} mapping.
    """
    result = {}
    joint_set = set(joints)

    # --- Source 1: skinCluster.bindPreMatrix ---
    for sc in cmds.ls(type='skinCluster'):
        influences = cmds.skinCluster(sc, q=True, influence=True)
        for i, inf in enumerate(influences):
            if inf in result or inf not in joint_set:
                continue
            bpm = cmds.getAttr('{}.bindPreMatrix[{}]'.format(sc, i))
            result[inf] = om2.MMatrix(bpm).inverse()

    # --- Source 2: dagPose.worldMatrix (for remaining joints) ---
    for dp in cmds.ls(type='dagPose'):
        for jnt in joints:
            if jnt in result:
                continue
            conns = cmds.listConnections(
                jnt + '.message', type='dagPose', plugs=True) or []
            for conn in conns:
                if dp not in conn or '[' not in conn:
                    continue
                idx = int(conn.split('[')[1].split(']')[0])
                try:
                    wm = cmds.getAttr('{}.worldMatrix[{}]'.format(dp, idx))
                    result[jnt] = om2.MMatrix(wm)
                except Exception:
                    pass
                break

    return result


def restore_bind_pose(root_joint):
    """
    Restore bind pose using rotate channels only.

    JointOrient remains unchanged. After this function:
    - Current pose = bind pose (e.g. A-pose)
    - Zero all rotate channels -> orient-defined pose (e.g. T-pose)

    Reads bind-time world matrices from skinCluster.bindPreMatrix
    and dagPose, then uses Maya's matchTransform (via temp locators)
    to compute the correct rotate values. matchTransform internally
    handles jointOrient, rotateAxis, and rotateOrder decomposition.

    Processing order: parent -> child (ensures parent world matrix
    is correct before computing child's local rotation).

    Wrapped in undo chunk; Ctrl+Z reverts all changes.

    Args:
        root_joint (str): Root joint of the skeleton to process.

    Raises:
        RuntimeError: If no bind pose data found, or processing fails.
    """
    joints = _get_all_joints(root_joint)
    joints = _sort_by_depth(joints)

    # --- Collect bind world matrices ---
    bind_data = _collect_bind_world_matrices(joints)

    if not bind_data:
        raise RuntimeError(
            u'[restore_bind_pose] No bind pose data found. '
            u'Ensure skinCluster or dagPose exists in the scene.')

    cmds.undoInfo(openChunk=True)
    temp_loc = None

    try:
        # --- Zero all rotations first (back to orient-defined pose) ---
        for jnt in joints:
            for ch in ('rx', 'ry', 'rz'):
                attr = '{}.{}'.format(jnt, ch)
                if _is_attr_settable(attr):
                    cmds.setAttr(attr, 0)

        # --- Create one reusable temp locator ---
        temp_loc = cmds.spaceLocator(name='_bindPose_temp')[0]

        # --- Match rotation parent -> child ---
        n_set = 0
        for jnt in joints:
            if jnt not in bind_data:
                continue

            W_bind = bind_data[jnt]
            cmds.xform(temp_loc, ws=True, m=list(W_bind))
            cmds.matchTransform(jnt, temp_loc, rot=True, pos=False, scl=False)
            n_set += 1

        # --- Cleanup ---
        cmds.delete(temp_loc)
        temp_loc = None

    except Exception as e:
        if temp_loc and cmds.objExists(temp_loc):
            cmds.delete(temp_loc)
        cmds.undoInfo(closeChunk=True)
        cmds.undo()
        raise RuntimeError(
            u'[restore_bind_pose] Failed, rolled back: {}'.format(e))

    cmds.undoInfo(closeChunk=True)

    # --- Verify ---
    max_err = 0.0
    err_joints = []
    for jnt in joints:
        if jnt not in bind_data:
            continue
        W_now = om2.MMatrix(cmds.getAttr(jnt + '.worldMatrix[0]'))
        W_target = bind_data[jnt]
        delta = max(abs(W_now.getElement(r, c) - W_target.getElement(r, c))
                    for r in range(4) for c in range(4))
        if delta > max_err:
            max_err = delta
        if delta > 0.01:
            err_joints.append(jnt)

    if err_joints:
        om2.MGlobal.displayWarning(
            u'[restore_bind_pose] {} joints error > 0.01: {}'.format(
                len(err_joints), ', '.join(err_joints[:5])))
    else:
        om2.MGlobal.displayInfo(
            u'[restore_bind_pose] Done: {}/{} joints, max error {:.6f}'.format(
                n_set, len(joints), max_err))
