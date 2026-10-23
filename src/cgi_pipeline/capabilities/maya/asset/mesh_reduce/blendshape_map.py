"""Source-vertex remapping for local, pre-skin native blendShape targets.

This deliberately rejects tangent/transform-space targets and relative inbetweens.
Live target meshes are retained only with unchanged indexed vertex correspondence.
"""
from __future__ import annotations

import re


def component_ids(tokens):
    result = []
    for token in tokens or []:
        match = re.fullmatch(r'vtx\[(\d+)(?::(\d+))?\]', token)
        if not match:
            raise ValueError('Unsupported blendShape component: ' + token)
        start, end = int(match[1]), int(match[2] or match[1])
        result.extend(range(start, end + 1))
    return result


def remap_points(item, original_ids):
    """Keep repeated source components in authored order, including their deltas."""
    reverse = {}
    for new, old in enumerate(original_ids):
        reverse.setdefault(old, []).append(new)
    pairs = [(new, p) for old, p in zip(item['ids'], item['points'])
             for new in reverse.get(old, [])]
    return sorted(pairs, key=lambda pair: pair[0])


def capture(cmds, node):
    if cmds.getAttr(node + '.origin') != 1:
        raise ValueError('Only local-space blendShapes can be remapped')
    if (cmds.getAttr(node + '.inputTarget', multiIndices=True) or []) != [0]:
        raise ValueError('Multiple blendShape geometries are unsupported')
    if cmds.getAttr(node + '.inputTarget[0].baseWeights', multiIndices=True):
        # Sparse authored arrays may contain only defaults; retain them below.
        pass
    aliases = cmds.aliasAttr(node, q=True) or []
    aliases = dict(zip(aliases[1::2], aliases[::2]))
    result = {'source': node, 'targets': [], 'base_weights': {},
              'envelope': cmds.getAttr(node + '.envelope'),
              'envelope_input': cmds.listConnections(node + '.envelope', s=True, d=False, p=True) or []}
    for i in cmds.getAttr(node + '.inputTarget[0].baseWeights', multiIndices=True) or []:
        result['base_weights'][i] = cmds.getAttr(f'{node}.inputTarget[0].baseWeights[{i}]')
    for index in cmds.getAttr(node + '.inputTarget[0].inputTargetGroup', multiIndices=True) or []:
        group = f'{node}.inputTarget[0].inputTargetGroup[{index}]'
        if cmds.getAttr(group + '.postDeformersMode') or cmds.getAttr(group + '.normalizationId'):
            raise ValueError('Non-local or normalized target requires another strategy: ' + group)
        target = {'index': index, 'alias': aliases.get(f'weight[{index}]'), 'items': [],
                  'value': cmds.getAttr(f'{node}.weight[{index}]'),
                  'driver': cmds.listConnections(f'{node}.weight[{index}]', s=True, d=False, p=True) or [],
                  'weights': {}}
        for i in cmds.getAttr(group + '.targetWeights', multiIndices=True) or []:
            target['weights'][i] = cmds.getAttr(group + f'.targetWeights[{i}]')
        for item in cmds.getAttr(group + '.inputTargetItem', multiIndices=True) or []:
            plug = group + f'.inputTargetItem[{item}]'
            if cmds.getAttr(plug + '.inputRelativeComponentsTarget'):
                raise ValueError('Relative inbetween requires another strategy')
            ids = component_ids(cmds.getAttr(plug + '.inputComponentsTarget'))
            points = cmds.getAttr(plug + '.inputPointsTarget') or []
            if len(ids) != len(points):
                raise ValueError('Target component/delta counts differ: ' + plug)
            target['items'].append({'index': item, 'ids': ids, 'points': points,
                'geometry': cmds.listConnections(plug + '.inputGeomTarget', s=True, d=False, p=True) or []})
        result['targets'].append(target)
    return result


def restore(cmds, data, output, original_ids):
    """Build native pre-skin BS with sparse deltas at retained source vertices."""
    reverse = {}
    for new, old in enumerate(original_ids):
        reverse.setdefault(old, []).append(new)
    plans = []
    for target in data['targets']:
        items = []
        for item in target['items']:
            pairs = remap_points(item, original_ids)
            if not pairs:
                continue
            if item['geometry'] and any(reverse.get(old) != [old] for old in item['ids']):
                raise ValueError('Live expression target requires intact indexed head correspondence')
            items.append((item, pairs))
        if items:
            plans.append((target, items))
    if not plans:
        return {'node': '', 'targets': 0, 'live_targets': 0}
    bs = cmds.blendShape(output, frontOfChain=True, origin='local',
                         name=output.rsplit('|', 1)[-1] + '_expression')[0]
    cmds.setAttr(bs + '.topologyCheck', False)
    cmds.setAttr(bs + '.envelope', data['envelope'])
    if data['envelope_input']:
        cmds.connectAttr(data['envelope_input'][0], bs + '.envelope')
    for old, value in data['base_weights'].items():
        for new in reverse.get(old, []):
            cmds.setAttr(f'{bs}.inputTarget[0].baseWeights[{new}]', value)
    live = 0
    for target, items in plans:
        index = target['index']
        group = f'{bs}.inputTarget[0].inputTargetGroup[{index}]'
        for item, pairs in items:
            plug = group + f'.inputTargetItem[{item["index"]}]'
            cmds.setAttr(plug + '.inputPointsTarget', len(pairs), *[p for _, p in pairs], type='pointArray')
            cmds.setAttr(plug + '.inputComponentsTarget', len(pairs),
                          *[f'vtx[{i}]' for i, _ in pairs], type='componentList')
            if item['geometry']:
                cmds.connectAttr(item['geometry'][0], plug + '.inputGeomTarget')
                live += 1
        for old, value in target['weights'].items():
            for new in reverse.get(old, []):
                cmds.setAttr(group + f'.targetWeights[{new}]', value)
        cmds.setAttr(f'{bs}.weight[{index}]', target['value'])
        if target['driver']:
            cmds.connectAttr(target['driver'][0], f'{bs}.weight[{index}]')
        if target['alias']:
            cmds.aliasAttr(target['alias'], f'{bs}.weight[{index}]')
    return {'node': bs, 'targets': len(plans), 'live_targets': live}
