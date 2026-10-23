"""One self-contained, image-backed HTML delivery from verified build evidence."""
from __future__ import annotations

import base64
import html
import json
import time
from pathlib import Path


def capture_delivery_views(specifications, run_dir, output_path, panel="modelPanel5", reference_views=None, default_material=False):
    """Workflow supplies semantic targets and optional image_size per view.

    The common framing and native capture share that size; no image stretching.
    """
    for spec in specifications:
        if 'angle' in spec:
            raise ValueError('Use rotation=[x,y,z], not angle; an ignored angle would repeat the front view')
    import maya.cmds as cmds
    from cgi_pipeline.capabilities.maya.scene_view import inspection_display
    from cgi_pipeline.capabilities.maya.scene_inspection_style import inspection_style
    from cgi_pipeline.capabilities.maya.asset.maya_capture_viewport.maya_capture_viewport import execute
    from .safety import managed
    references = {r["label"]: r for r in (reference_views or [])}
    results = []
    try:
        with inspection_style(cmds, panel, default_material=default_material,match_current=True):
            for spec in specifications:
                started = time.perf_counter()
                cmds.modelEditor(panel,e=True,useDefaultMaterial=spec.get('default_material',default_material),
                                 wireframeOnShaded=spec.get('wireframe',True))
                previous = references.get(spec['label'], {})
                reference = dict(previous['camera_state']) if previous.get('camera_state') else None
                if reference and 'aspect' not in reference:
                    pixels = previous.get('observation', {})
                    if pixels.get('width') and pixels.get('height'):
                        reference['aspect'] = pixels['width']/pixels['height']
                with inspection_display(cmds, panel, spec["targets"], rotation=spec.get("rotation", [0, 0, 0]),
                                        camera_state=reference, visibility_overrides=spec.get("visibility_overrides"),
                                        attribute_overrides=spec.get("attribute_overrides"), near_clip=spec.get('near_clip',1.),
                                        reuse_display=True,image_size=spec.get('image_size')) as camera:
                    receipt = execute({"run_dir": run_dir, "parameters": {"scope": "viewport", "panel": panel,
                                       'image_size':spec.get('image_size',[])}})
                    if receipt["status"] != "SUCCESS":
                        raise RuntimeError(receipt.get("error", "Capture failed"))
                    results.append({"label": spec["label"], **receipt["output"], "camera_state": camera,
                                    "display_state": spec.get("attribute_overrides", {}), 'default_material':spec.get('default_material',default_material),
                                    "seconds": time.perf_counter() - started})
    finally:
        managed(output_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def output_sources(plan):
    result={g['output_name']:list(g['sources']) for g in plan['merge_groups']}
    result.update({r['output_name']:[r['source_uuid']] for r in plan['reduce_entries']})
    for entry in plan.get('region_entries',[]):
        for region in entry['regions']:
            owners=result.setdefault(region['output_name'],[])
            if entry['source_uuid'] not in owners:owners.append(entry['source_uuid'])
    return result


def part_summaries(build):
    plan = build['plan']
    before = {r['source_uuid']: r['before']['counts']['faces'] for r in build.get('geometry_rows', [])}
    for row in build.get('regional_outputs',[]):
        before.update(row.get('source_before_faces',{row['source_uuid']:row['before_faces']}))
    mapping=output_sources(plan)
    result = []
    for part in plan.get('review_parts', []):
        ids = set(part['sources'])
        faces = part['before_faces'] - sum(before[u] for u in ids & set(before))
        for output in build['outputs']:
            owners = set(mapping[output['result'].rsplit('|', 1)[-1]])
            if owners & ids:
                if not owners <= ids:
                    raise ValueError('Output crosses semantic parts')
                faces += output['faces']
        merged_ids={u for g in plan['merge_groups'] if set(g['sources']) <= ids for u in g['sources']}
        for output in build.get('regional_outputs',[]):
            owners=set(output.get('source_uuids',[output['source_uuid']]))
            if len(owners)>1 and owners<=ids:merged_ids.update(owners)
        merged=len(merged_ids)
        result.append({**part, 'after_faces': faces, 'merged_models': merged,
                       'reduction_percent': 100*(1-faces/part['before_faces']) if part['before_faces'] else 0})
    return result


def write_delivery(build_path, before_views_path, after_views_path, validation_path, output_path, execution_path=None, review_path=None):
    from .safety import managed
    from . import delivery_review
    load = lambda p: json.loads(managed(p, suffix={'.json'}).read_text(encoding='utf-8'))
    build, before, after, validation = map(load, (build_path, before_views_path, after_views_path, validation_path))
    if not managed(build['output_path'],suffix={'.ma','.mb'}).is_file():
        raise ValueError('Saved Proxy file is missing')
    execution = load(execution_path) if execution_path else {}
    esc = lambda x: html.escape(str(x))
    duration = lambda x: '未单独计时' if x is None else f'{x:.2f} 秒'
    parts = part_summaries(build)
    if parts and sum(p['after_faces'] for p in parts) != build['after']['faces']:
        raise ValueError('Part totals differ from saved candidate')
    prior, current = ({v['label']: v for v in vs} for vs in (before, after))
    if len(prior) != len(before) or len(current) != len(after) or set(prior) != set(current):
        raise ValueError('Before/after view labels must be unique and paired')
    if validation.get('scene') and Path(validation['scene']).resolve() != Path(build['output_path']).resolve():
        raise ValueError('Validation belongs to a different saved candidate')
    planned_views = [v['label'] for v in build['plan'].get('review_views', [])]
    review_input = review_path or execution.get('review_path')
    searches = list(build['plan'].get('geometry_search', {}).values())
    searches += [r['search'] for e in build['plan'].get('region_entries', []) for r in e['regions'] if r.get('search')]
    review = delivery_review.normalize(
        load(review_input) if review_input else execution.get('review'), set(current),
        [p['id'] for p in parts], validation['passed'], planned_views,
        expression_required=any(o.get('blendshape',{}).get('node') for o in build.get('regional_outputs',[])),
        visual_decisions_required=any(s.get('mode')=='visual' for s in searches))
    def image(view):
        encoded = base64.b64encode(managed(view['output_path'], suffix={'.png'}).read_bytes()).decode('ascii')
        return '<img alt="'+esc(view['label'])+'" src="data:image/png;base64,'+encoded+'">'
    def pair(key):
        if key not in prior or key not in current:
            raise ValueError('Missing before/after image: '+key)
        buttons = ''.join('<button data-mode-button="'+mode+'" aria-pressed="'+str(mode=='after').lower()+'">'+label+'</button>'
                          for mode,label in [('before','优化前'),('after','优化后'),('split','并排比较')])
        return '<div class="compare" id="view-'+esc(key)+'" data-mode="after"><div class="toolbar">'+buttons+'<span>点击放大</span></div><div class="pair"><figure>'+image(prior[key])+'<figcaption>优化前</figcaption></figure><figure>'+image(current[key])+'<figcaption>优化后</figcaption></figure></div></div>'
    t = build['timing_seconds']
    weight_time = build.get('source_processing_times', {}).get('source_weights', 0.)
    assembly_time = sum(build.get('source_processing_times', {}).get(k,0.) for k in ('join','bind'))
    regional_weight = build.get('regional_weight_seconds', 0.)
    before_capture_time=(0. if execution.get('reused_before_views') else
                         execution.get('source_capture_batch_seconds',sum(v.get('seconds',0.) for v in before)))
    scored = [s for s in searches if s.get('mode')!='visual']
    bands = sorted({tuple(s['score_band']) for s in scored})
    search_input = 'Maya Reduce'
    if scored:
        search_input += '；目标 '+ ' / '.join(f'{a:g}–{b:g}' for a,b in bands)
        search_input += '；每组最多 '+str(max(s.get('max_evaluations',5) for s in scored))+' 次'
    if any(s.get('mode')=='visual' for s in searches):search_input += '；视觉决策，每施工组一次'
    decision_label = delivery_review.DECISIONS[review['decision']]
    neighbor_rings = build['plan'].get('weights',{}).get('neighbor_rings',build['weights'].get('neighbor_rings'))
    neighbor_text = str(neighbor_rings)+' 圈' if neighbor_rings is not None else '未记录'
    steps = [
      ('1 获取资产与进入沙盒', '角色 '+build['character'], '正式绑定：'+build['source_master']['path']+'\n沙盒输入：'+build['source_scene'], (execution['sandbox_open_seconds']+execution.get('checkpoint_seconds',0.)) if execution.get('sandbox_open_seconds') is not None else None),
      ('2 预清理、分析与观察', '当前角色结构与视图', f"{len(parts)} 个部位", execution.get('preparation_seconds',0.)+execution.get('source_inspection_seconds',0.)+execution.get('source_filter_seconds',0.)+execution.get('source_capture_retake_seconds',0.)+sum(t.get(k,0.) for k in ('inventory_initial','preflight','plan_validation'))+before_capture_time),
      ('3 分件权重剪枝', f"最大影响 {build['weights']['max_influences']}；阈值 {build['weights']['threshold']}；邻域 {neighbor_text}", '权重处理完成', weight_time+regional_weight),
      ('4 分区域减面与继承', search_input, f"{build['before']['faces']:,} → {build['after']['faces']:,} 面", max(0., t.get('reduce',0.)-weight_time-assembly_time)+max(0.,t.get('regional',0.)-regional_weight)),
      ('5 合并与替换', '按绑定与显隐分组', f"{sum(p['merged_models'] for p in parts):,} 个模型参与合并", assembly_time+sum(t.get(k,0.) for k in ('combine','outputs','replace'))),
      ('6 清理、检查与保存', '最终输出', '保存完成', sum(t.get(k,0.) for k in ('weights','discard','material_cleanup','inventory_final','retained_validation','save'))),
      ('8 复开与审查', f"{len(after)} 个结果视图", decision_label, execution.get('reopen_seconds',0.)+execution.get('verification_seconds',0.)+execution.get('verification_retry_seconds',0.)+execution.get('delivery_capture_retake_seconds',0.)+execution.get('delivery_capture_batch_seconds',sum(v.get('seconds',0.) for v in after))+execution.get('final_reopen_seconds',0.)+execution.get('final_verification_seconds',0.)+execution.get('final_capture_seconds',0.))]
    if build.get('finalization'):
        f = build['finalization']
        controls = build.get('controller_setup') or {}
        steps.insert(-1,('7 最终清理与控制器准备', '候选文件',
                      f"删除 {f['deleted_sources']:,} 个施工原件；控制器 Tag {controls.get('tagged_count', 0):,} 个",
                      f['total_seconds']))
    timing = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (n,i,o,duration(sec)))+'</tr>' for n,i,o,sec in steps)
    cards = ''.join('<section><h3>'+esc(p['label'])+'</h3><p class="metric">'+f"{p['before_faces']:,} → {p['after_faces']:,} 面"+' <span>'+f"减少 {p['reduction_percent']:.1f}% · {p['merged_models']} 个模型参与合并"+'</span></p>'+pair(p['id'])+'</section>' for p in parts)
    summary = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (p['label'],p['merged_models'],f"{p['before_faces']:,}",f"{p['after_faces']:,}",f"{p['reduction_percent']:.1f}%"))+'</tr>' for p in parts)
    three = ''.join('<section><h3>'+name+'</h3>'+pair(key)+'</section>' for key,name in [('front','正面'),('side','侧面'),('back','背面')])
    def wing_metric(letter):
        groups=[g for g in build['plan']['merge_groups'] if g['output_name'].endswith('wing_'+letter)]
        ids={u for g in groups for u in g['sources']}; names={g['output_name'] for g in groups}
        old=sum(r['before']['counts']['faces'] for r in build.get('geometry_rows',[]) if r['source_uuid'] in ids)
        new=sum(r['faces'] for r in build['outputs'] if r['result'].rsplit('|',1)[-1] in names)
        return (f'<p class="metric">{old:,} → {new:,} 面 <span>减少 {100*(1-new/old):.1f}% · {len(ids)} 个模型参与合并</span></p>') if old else ''
    wing_states = ''.join('<section><h3>翅膀 '+letter+' 状态</h3>'+wing_metric(letter)+pair('wing_'+letter)+'</section>'
                          for letter in 'ABCD' if 'wing_'+letter in prior and 'wing_'+letter in current)
    region_names={'head_expression':'头部与颈部过渡','hands':'手部保护区','body_surface':'可优化身体','cloth_surface':'连体服装主体'}
    region_rows=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in
        (region_names.get(r['region'],r['region']),f"{r['before_faces']:,}",f"{r['after_faces']:,}",f"{100*(1-r['after_faces']/r['before_faces']):.1f}%"))+'</tr>'
        for out in build.get('regional_outputs',[]) for r in out['regions'])
    regional_table=('<details><summary>区域细分</summary><table><tr><th>区域</th><th>优化前面数</th><th>优化后面数</th><th>减少比例</th></tr>'+region_rows+'</table></details>') if region_rows else ''
    if 'head_detail' in prior and 'head_detail' in current:
        regional_table += '<section><h3>头部表情区近景</h3>'+pair('head_detail')+'</section>'
    if 'hair_front' in prior and 'hair_front' in current:
        regional_table += '<section><h3>头发正面 · 刘海密度</h3>'+pair('hair_front')+'</section>'
    shown=set(p['id'] for p in parts)|{'front','side','back','head_detail','hair_front'}|{'wing_'+c for c in 'ABCD'}
    for view in build['plan'].get('review_views',[]):
        key=view['label']
        if key in shown or key not in prior or key not in current:continue
        sources=set(view.get('sources',[]))
        rows=[r for r in build.get('geometry_rows',[]) if r['source_uuid'] in sources]
        metric=''
        if rows:
            a=sum(r['before']['counts']['faces'] for r in rows);b=sum(r['after']['counts']['faces'] for r in rows)
            metric=f'<p class="metric">{a:,} → {b:,} 面 <span>减少 {100*(1-b/a):.1f}%</span></p>'
        regional_table += '<section><h3>'+esc(view.get('title',key))+'</h3>'+metric+pair(key)+'</section>'
        shown.add(key)
    part_labels = {p['id']:p['label'] for p in parts} | {'overall':'整体'}
    check_labels = delivery_review.CHECKS | {'structure':'结构'}
    badges = ''.join('<span class="review-check" data-status="'+v['status']+'">'+esc(check_labels[k])+'：'+delivery_review.STATUS_LABELS[v['status']]+'</span>' for k,v in review['checks'].items())
    issue_rows = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (
        ' / '.join(part_labels[p] for p in issue['parts']),issue['problem'],issue['cause'],issue['action']))+'</tr>'
        for issue in review['issues'] if issue['state']=='open')
    visual_notes = '<section class="review" data-decision="'+review['decision']+'"><h2>审查结果：'+decision_label+'</h2><div class="review-checks">'+badges+'</div>'
    if review['part_decisions']:
        visual_notes += '<table><tr><th>部位</th><th>决定</th><th>关注点</th><th>实际观察</th></tr>'+''.join(
            '<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (part_labels[d['part']],delivery_review.PART_DECISIONS[d['decision']],d['focus'],d['observation']))+'</tr>'
            for d in review['part_decisions'])+'</table>'
    if issue_rows:
        visual_notes += '<table id="review-issues"><tr><th>部位</th><th>问题</th><th>原因</th><th>处理</th></tr>'+issue_rows+'</table>'
    if review['missing_views'] or review['unreviewed_views']:
        visual_notes += '<details><summary>审查缺口</summary><p>'+esc('缺少视图：'+', '.join(review['missing_views']) if review['missing_views'] else '')+'</p><p>'+esc('尚未审查：'+', '.join(review['unreviewed_views']) if review['unreviewed_views'] else '')+'</p></details>'
    visual_notes += '</section>'
    ratio = 100*(1-build['after']['faces']/build['before']['faces'])
    title = esc(build['character'])+' · Proxy Rig 候选'
    text = Path(__file__).with_name('delivery.html').read_text(encoding='utf-8')
    values = {
        'TITLE': title, 'TOTAL': f"{build['before']['faces']:,} → {build['after']['faces']:,} 面 <span>整体减少 {ratio:.1f}%</span>",
        'FILE_URI': esc(Path(build['output_path']).as_uri()), 'FILE_PATH': esc(build['output_path']),
        'MASTER_VERSION': esc(build['source_master']['version']), 'VISUAL_NOTES': visual_notes,
        'TIMING': timing, 'BUILD_TIME': duration(build['timing_totals_seconds']['api_before_report']),
        'SUMMARY': summary, 'REGIONS': regional_table,
        'PARTS': cards, 'WINGS': wing_states, 'THREE_VIEWS': three}
    values.update({
        'SOURCE_NOTE': 'Master '+esc(build['source_master']['version'])+' · 模型 '+f"{build['before']['meshes']:,} → {build['after']['meshes']:,}",
        'BUILD_SCOPE': '构建 '+duration(build['timing_totals_seconds']['api_before_report']),
        'WINGS_SECTION': '<h2>翅膀切换状态</h2>'+wing_states,
        'THREE_SECTION': '<h2>整体三视图</h2>'+three})
    for key, value in values.items():
        text = text.replace('%%'+key+'%%', value)
    target = managed(output_path, suffix={'.html'})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    model = {'schema_version':1,'character':build['character'],'source_master':build['source_master'],
             'artifacts':{key:str(managed(path).resolve()) if path else None for key,path in
                          [('build',build_path),('before',before_views_path),('after',after_views_path),
                           ('validation',validation_path),('review',review_input)]},
             'source_scene':build['source_scene'],'output_path':build['output_path'],
             'before':build['before'],'after':build['after'],'review':review,
             'steps':[{'name':n,'input':i,'result':o,'seconds':sec} for n,i,o,sec in steps],
             'parts':[{k:v for k,v in p.items() if k!='sources'} for p in parts],
             'views':{'before':before,'after':after}}
    target.with_suffix('.json').write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf8')
    return str(target)


def write_geometry_trial(data, before, after, comparison, output_path):
    """Fill the same fixed viewer template for a bounded geometry experiment."""
    from .safety import managed
    esc = lambda value: html.escape(str(value))
    image = lambda v: '<img alt="前臂羽毛" src="data:image/png;base64,'+base64.b64encode(managed(v['output_path'],suffix={'.png'}).read_bytes()).decode('ascii')+'">'
    buttons = ''.join('<button data-mode-button="'+mode+'" aria-pressed="'+str(mode=='after').lower()+'">'+label+'</button>'
                      for mode,label in [('before','优化前'),('after','优化后'),('split','并排比较')])
    pair = '<div class="compare" data-mode="after"><div class="toolbar">'+buttons+'</div><div class="pair"><figure>'+image(before)+'<figcaption>优化前</figcaption></figure><figure>'+image(after)+'<figcaption>优化后</figcaption></figure></div></div>'
    timing = ''.join('<tr>'+''.join('<td>'+esc(value)+'</td>' for value in (r['step'],r['input'],r['output'],f"{r['seconds']:.3f} 秒"))+'</tr>' for r in data['steps'])
    group_rows = ''.join('<tr>'+''.join('<td>'+esc(value)+'</td>' for value in (str(i+1),len(g['shells']),g['faces'],call['after_faces'],f"{100*(1-call['after_faces']/g['faces']):.1f}%"))+'</tr>' for i,(g,call) in enumerate(zip(data['groups'],data['reduce_calls'])))
    note = '<section class="visual-warning"><h2>试验结论与范围</h2>'+''.join('<p>'+esc(n)+'</p>' for n in data['visual_notes'])+'</section>'
    reduced=100*(1-data['after_faces']/data['before_faces'])
    values = {'TITLE':'pengMoWang · 前臂分组减面试验','TOTAL':f"{data['before_faces']:,} → {data['after_faces']:,} 面 <span>减少 {reduced:.1f}% · 两次 Reduce</span>",
        'FILE_URI':esc(Path(data['output_path']).as_uri()),'FILE_PATH':esc(data['output_path']),
        'SOURCE_NOTE':'来源：v008 中保留的 Master v003 原始前臂模型。原件保留；本文件为局部工艺试验，不是整角色新版交付。',
        'VISUAL_NOTES':note,'TIMING':timing,'BUILD_SCOPE':'本次局部施工 '+f"{data['timings']['construction_total']:.3f}"+' 秒。下表步骤互不重叠；首次载入数值库另列，不把开发及对话时间计入工具执行。',
        'RETRY_NOTE':'没有逐壳创建施工模型；167 个内部壳归为两个面集合。',
        'TIMING_NOTE':esc(data['timing_note']),'SUMMARY':'<tr><td>前臂羽毛局部</td><td>1 个原始合并模型</td><td>'+f"{data['before_faces']:,}"+'</td><td>'+f"{data['after_faces']:,}"+'</td><td>'+f'{reduced:.1f}%'+'</td></tr>',
        'PART_NOTE':'1280 × 768 原生 PNG；前后同相机、同显示条件，点击图片可查看原尺寸。',
        'REGIONS':'<h2>两个施工组</h2><table><tr><th>处理组</th><th>内部壳</th><th>优化前面数</th><th>优化后面数</th><th>减少比例</th></tr>'+group_rows+'</table>',
        'PARTS':'<section><h3>前臂羽毛</h3>'+pair+'</section>',
        'WINGS_SECTION':'','THREE_SECTION':'',
        'DISPLAY_NOTE':'默认灰材质、线框、默认灯光强度 1、双面照明和 Un-tone-mapped (sRGB)。本次只观察一个局部，不重复拍整角色三视图。'}
    text = Path(__file__).with_name('delivery.html').read_text(encoding='utf8')
    for key,value in values.items(): text=text.replace('%%'+key+'%%',value)
    if '%%' in text: raise ValueError('Unfilled trial report field')
    target=managed(output_path,suffix={'.html'});target.write_text(text,encoding='utf8')
    return str(target)
