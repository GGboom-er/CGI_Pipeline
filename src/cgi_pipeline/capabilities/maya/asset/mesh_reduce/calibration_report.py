"""Fixed calibration layout using the established delivery image viewer."""
from __future__ import annotations
import base64,html,json,re
from pathlib import Path


def write(result_path,views_path,output_path,scene_path):
    from ..proxy_rig.safety import managed
    data=json.loads(managed(result_path).read_text(encoding='utf8'))
    views={v['label']:v for v in json.loads(managed(views_path).read_text(encoding='utf8'))}
    template=Path(__file__).parents[1]/'proxy_rig'/'delivery.html'
    source=template.read_text(encoding='utf8');esc=lambda v:html.escape(str(v))
    source=re.sub(r'<h2>部位结果</h2>\s*<table>.*?%%SUMMARY%%</table>','',source,flags=re.S)
    def image(label):
        view=views[label];encoded=base64.b64encode(managed(view['output_path']).read_bytes()).decode('ascii')
        return '<img alt="'+esc(label)+'" src="data:image/png;base64,'+encoded+'">'
    sections=[];total=0
    for key,part in data['parts'].items():
        title='翅膀 D' if key=='wing_D' else '左侧手臂羽毛'
        original=image(key+'_original');cards=[]
        for row in [{'target':'原始','faces':part['source_faces'],'profiles':[{'fscore':100.}],
                     'target_reached':True}]+part['rows']:
            target=row['target'];actual=row['profiles'][0]['fscore'];total+=1
            picture=original if target=='原始' else image(key+'_F'+str(target))
            name='原始模型' if target=='原始' else '目标 '+str(target)+' · 实测 '+f'{actual:.2f}'
            state='' if row['target_reached'] else '<strong>未命中目标，展示最接近候选</strong>'
            cards.append('<div><h3>'+esc(name)+'</h3><p>'+f"{row['faces']:,} 面"+state+'</p>'
                '<div class="compare" data-mode="after"><div class="pair"><figure>'+original+
                '<figcaption>原始</figcaption></figure><figure>'+picture+'<figcaption>点击放大 · 可切换原始</figcaption>'
                '</figure></div></div></div>')
        sections.append('<section><h2>'+title+'</h2><p>'+f"{part['source_faces']:,} 面 · {part['reference_shells']} 个来源壳 · 两个施工组"
            +'</p><div class="calibration-grid">'+''.join(cards)+'</div></section>')
    note=('F-score@'+str(data['radius_mm'])+'毫米，按表面积均匀采样，逐来源壳匹配；不是减面百分比。'
          '所有模型关闭细分预览，统计与线框均为基础网格。仅作静态几何校准，不含新蒙皮。'
          '模型排列有展示位移，相似度在排列前的共同坐标中计算。')
    diagnosis=('翅膀A：原351件62590基础面，v009为11814基础面；多数沿用24%保留参数，未继续细分羽根密度。'
               '原件全部二级平滑预览，v009代理A也保留displaySmoothMesh=2。选中单片显示4608面对应288基础面×16。'
               '因此显示细分和偏保守策略两者都存在。v009文件保持原样；新输出工作流已加入关闭细分预览。')
    targets=' / '.join(str(v) for v in data['targets'])
    values={'TITLE':'pengMoWang · 相似度校准','TOTAL':targets,
        'FILE_URI':esc(Path(scene_path).as_uri()),'FILE_PATH':esc(scene_path),
        'SOURCE_NOTE':note,'VISUAL_NOTES':'','TIMING':''.join('<tr><td>'+name+'</td><td>缓存Reduce与libigl</td><td>'+detail+'</td><td>'+f'{seconds:.2f} 秒'+'</td></tr>' for name,detail,seconds in [('初始六档寻档','十二个候选',data['seconds']),('初始候选加密复核','固定采样',data.get('verification_seconds',0)),('补充20、35寻档','四个候选，直接使用加密采样',data.get('extension_seconds',0))]),
        'BUILD_SCOPE':'原件保留，正式Master与v009文件不改。','RETRY_NOTE':'','TIMING_NOTE':'每组设置了有限求值预算；达不到目标时展示实际分数，不强行标成目标分数。',
        'SUMMARY':'','PART_NOTE':'从左到右为原始、'+targets.replace(' / ','、')+'。鼠标点击任一图片放大，并切换原始作对比。',
        'REGIONS':'<section><h2>翅膀 A 的原因</h2><p>'+diagnosis+'</p></section>',
        'PARTS':''.join(sections),'WINGS_SECTION':'','THREE_SECTION':'','DISPLAY_NOTE':'相似度为固定采样估计；本轮主距离尺度及分数不能直接跨不同尺寸资产当统一合格线。'}
    source=source.replace('</style>','.calibration-grid{display:grid;grid-template-columns:repeat('+str(len(data['targets'])+1)+',minmax(160px,1fr));gap:12px;overflow-x:auto}.calibration-grid h3{font-size:15px}.calibration-grid p{font-size:14px}</style>')
    for key,value in values.items():source=source.replace('%%'+key+'%%',value)
    if re.search(r'%%[A-Z_]+%%',source):raise ValueError('Unfilled calibration template fields')
    managed(output_path).write_text(source,encoding='utf8')
    return {'path':output_path,'cards':total,'views':len(views)}
