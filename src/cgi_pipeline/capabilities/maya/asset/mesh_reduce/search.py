"""Bounded single-target search; no Maya or numerical-worker side effects."""
from __future__ import annotations
import math


def validate(value):
    if not isinstance(value,dict):raise ValueError('search must be an explicit object')
    if value.get('mode') == 'visual':
        defaults = {'mode':'visual', 'initial_reduction':90., 'minimal_faces':12,
                    'max_evaluations':1, 'work_groups':[]}
        if set(value)-set(defaults):raise ValueError('Visual construction does not accept score thresholds')
        result = {**defaults, **value}
        amount = result['initial_reduction']
        if type(amount) not in (int,float) or not math.isfinite(amount) or not 0<amount<100:
            raise ValueError('Invalid visual initial_reduction')
        if type(result['max_evaluations']) is not int or result['max_evaluations'] != 1:
            raise ValueError('Visual construction runs once; the Agent reviews before revising')
        if type(result['minimal_faces']) is not int or not 1<=result['minimal_faces']<=1000:
            raise ValueError('Invalid visual minimal_faces')
        validate_work_groups(result['work_groups'])
        return result
    if not {'score_band','radius'}<=set(value):raise ValueError('search requires explicit score_band and scene-space radius')
    defaults={'score_band':[18.,25.],'radius':.01,'max_evaluations':5,
              'initial_reduction':90.,'samples_per_shell':512,'minimal_faces':12}
    if set(value)-set(defaults):raise ValueError('Unknown search option: '+str(sorted(set(value)-set(defaults))))
    result={**defaults,**value};band=result['score_band']
    if not isinstance(band,(list,tuple)) or len(band)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in band) or not 0<band[0]<band[1]<100:
        raise ValueError('search.score_band must satisfy 0 < low < high < 100')
    for key,low,high in [('radius',0.,float('inf')),('initial_reduction',0.,100.)]:
        x=result[key]
        if type(x) not in (int,float) or not math.isfinite(x) or not low<x<high:
            raise ValueError('Invalid search.'+key)
    for key,low,high in [('max_evaluations',1,8),('samples_per_shell',32,4096),('minimal_faces',1,1000)]:
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError('Invalid search.'+key)
    return result


def validate_work_groups(groups):
    """Explicit source-face groups, not one Maya object for each shell."""
    if not isinstance(groups,list):raise ValueError('work_groups must be a list')
    faces=set();names=set()
    for group in groups:
        if not isinstance(group,dict) or set(group)-{'name','faces','reduction','reason'}:
            raise ValueError('Unknown visual work group field')
        name=group.get('name');ids=group.get('faces');amount=group.get('reduction')
        if not isinstance(name,str) or not name.isidentifier() or not name.isascii() or name in names:
            raise ValueError('Work group names must be unique ASCII identifiers')
        if not isinstance(ids,list) or not ids or any(type(i) is not int or i<0 for i in ids):
            raise ValueError('Work groups need source polygon IDs')
        if len(set(ids))!=len(ids) or faces.intersection(ids):raise ValueError('Work groups overlap')
        if type(amount) not in (int,float) or not math.isfinite(amount) or not 0<=amount<100:
            raise ValueError('Work group reduction must be in [0,100)')
        if not isinstance(group.get('reason'),str) or not group['reason'].strip():
            raise ValueError('Work group requires visual or functional reason')
        faces.update(ids);names.add(name)


def acceptable(row,options):
    return not row.get('missing_shells',0) and options['score_band'][0]<=row['score']<=options['score_band'][1]


def choose(rows,options):
    safe=[r for r in rows if not r.get('missing_shells',0) and r['score']>=options['score_band'][0]]
    return min(safe,key=lambda r:(r['faces'],r['reduction'])) if safe else None


def next_reduction(rows,options):
    """Observed bracket only; never require a strictly monotone sampled score."""
    if not rows:return options['initial_reduction']
    lo,hi=options['score_band'];too_many=[r for r in rows if not r.get('missing_shells',0) and r['score']>hi]
    too_few=[r for r in rows if r.get('missing_shells',0) or r['score']<lo]
    brackets=[(a,b) for a in too_many for b in too_few if a['reduction']<b['reduction']]
    if brackets:
        a,b=min(brackets,key=lambda pair:pair[1]['reduction']-pair[0]['reduction'])
        candidate=(a['reduction']+b['reduction'])*.5
    elif too_many:
        last=max(too_many,key=lambda r:r['reduction']);candidate=100.-(100.-last['reduction'])*.5
    else:
        last=min(too_few,key=lambda r:r['reduction']);candidate=max(0.,100.-(100.-last['reduction'])*2.)
    if any(abs(r['reduction']-candidate)<1e-7 for r in rows):return None
    return candidate
