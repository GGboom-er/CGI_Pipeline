"""Fixed Agent review data. This validates declarations, not visual understanding."""
from __future__ import annotations

CHECKS = {
    'shape': '形态', 'density': '疏密', 'consistency': '一致性',
    'visibility': '显隐', 'expression': '表情',
}
STATUS_LABELS = {'pass': '通过', 'fail': '未通过', 'pending': '待审查', 'na': '不适用'}
DECISIONS = {'needs_revision': '需修改', 'pending_review': '待审查', 'ready_for_user': '待用户验收'}
PART_DECISIONS = {'reduce_more':'继续减', 'accept':'保留', 'restore':'局部回退', 'need_view':'补充视角'}


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} requires 1..{limit} characters')
    return value.strip()


def _labels(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(name + ' requires a string list')
    if len(set(value)) != len(value):
        raise ValueError(name + ' contains duplicates')
    return value


def part_decisions(rows, parts, reviewed):
    if not isinstance(rows,list):raise ValueError('part_decisions requires a list')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)-{'part','decision','focus','observation','views'}:
            raise ValueError('Unknown part decision field')
        part=row.get('part');decision=row.get('decision')
        if part not in parts or part in seen:raise ValueError('Part decisions require distinct known parts')
        if decision not in PART_DECISIONS:raise ValueError('Unknown visual decision')
        views=_labels(row.get('views',[]),'part.views')
        if not views or set(views)-set(reviewed):raise ValueError('Part decision requires reviewed evidence')
        result.append({'part':part,'decision':decision,'views':views,
                       'focus':_text(row.get('focus'),'part.focus',96),
                       'observation':_text(row.get('observation'),'part.observation',160)})
        seen.add(part)
    return result


def normalize(review, available_views, part_ids, structural_passed, planned_views=(), expression_required=False, visual_decisions_required=False):
    """Missing review stays pending; failures and missing views cannot pass.

    The caller must actually inspect the listed images. A valid JSON declaration
    does not prove that the Agent understood them or followed the work method.
    """
    available, parts = set(available_views), set(part_ids)
    if review is None:
        review = {'schema_version': 1, 'checks': {}, 'reviewed_views': [], 'required_views': [], 'issues': []}
    if not isinstance(review, dict) or type(review.get('schema_version')) is not int or review['schema_version'] != 1:
        raise ValueError('Review requires schema_version=1')
    unknown = set(review) - {'schema_version', 'checks', 'reviewed_views', 'required_views', 'issues', 'part_decisions'}
    if unknown:
        raise ValueError('Unknown review fields: ' + str(sorted(unknown)))
    reviewed = _labels(review.get('reviewed_views', []), 'reviewed_views')
    if set(reviewed) - available:
        raise ValueError('reviewed_views names an unavailable image')
    decisions=part_decisions(review.get('part_decisions',[]),parts,reviewed)
    required = sorted(set(_labels(review.get('required_views', []), 'required_views')) | set(planned_views))
    # Every delivery needs overall views and each declared part. Extra targeted
    # views (for example a braid back view) come from the source observation plan.
    required = sorted(set(required) | {'front', 'side', 'back'} | parts)
    checks = review.get('checks', {})
    if not isinstance(checks, dict) or set(checks) - set(CHECKS):
        raise ValueError('Unknown review check')
    normalized_checks = {}
    for key in CHECKS:
        value = checks.get(key, {'status': 'pending'})
        if not isinstance(value, dict) or set(value) - {'status', 'reason'}:
            raise ValueError('Check requires status and optional reason')
        status = value.get('status')
        if status not in STATUS_LABELS:
            raise ValueError('Invalid review check status')
        item = {'status': status}
        if status == 'na':
            item['reason'] = _text(value.get('reason'), key + '.reason', 80)
        elif value.get('reason'):
            item['reason'] = _text(value['reason'], key + '.reason', 80)
        normalized_checks[key] = item
    issues = review.get('issues', [])
    if not isinstance(issues, list):
        raise ValueError('issues requires a list')
    normalized_issues, ids = [], set()
    for issue in issues:
        allowed = {'id', 'parts', 'check', 'problem', 'cause', 'action', 'views', 'state'}
        if not isinstance(issue, dict) or set(issue) - allowed:
            raise ValueError('Unknown issue field')
        uid = _text(issue.get('id'), 'issue.id', 40)
        if uid in ids:
            raise ValueError('Duplicate issue id')
        ids.add(uid)
        owners = _labels(issue.get('parts', []), 'issue.parts')
        if not owners or set(owners) - (parts | {'overall'}):
            raise ValueError('Issue requires known parts or overall')
        check = issue.get('check')
        if check not in set(CHECKS) | {'coverage', 'structure'}:
            raise ValueError('Invalid issue check')
        state = issue.get('state', 'open')
        if state not in {'open', 'resolved'}:
            raise ValueError('Invalid issue state')
        views = _labels(issue.get('views', []), 'issue.views')
        if set(views) - set(reviewed):
            raise ValueError('Issue evidence was not declared reviewed')
        normalized_issues.append({
            'id': uid, 'parts': owners, 'check': check, 'state': state, 'views': views,
            'problem': _text(issue.get('problem'), 'issue.problem', 72),
            'cause': _text(issue.get('cause'), 'issue.cause', 96),
            'action': _text(issue.get('action'), 'issue.action', 96),
        })
    open_issues = [i for i in normalized_issues if i['state'] == 'open']
    for key, value in normalized_checks.items():
        related = [i for i in open_issues if i['check'] == key]
        if value['status'] == 'fail' and not related:
            raise ValueError('Failed check requires an open issue: ' + key)
        if value['status'] in {'pass', 'na'} and related:
            raise ValueError('Open issue conflicts with passing check: ' + key)
    if expression_required and normalized_checks['expression']['status'] == 'na':
        raise ValueError('An expression chain cannot be declared not applicable')
    normalized_checks['structure'] = {'status': 'pass' if structural_passed else 'fail'}
    missing = sorted(set(required) - available)
    unseen = sorted((set(required) & available) - set(reviewed))
    if not structural_passed or open_issues or any(v['status'] == 'fail' for v in normalized_checks.values()) or any(d['decision'] in {'reduce_more','restore'} for d in decisions):
        decision = 'needs_revision'
    elif missing or unseen or any(v['status'] == 'pending' for v in normalized_checks.values()) or any(d['decision']=='need_view' for d in decisions) or ((visual_decisions_required or 'part_decisions' in review) and {d['part'] for d in decisions}!=parts):
        decision = 'pending_review'
    else:
        decision = 'ready_for_user'
    return {
        'schema_version': 1, 'decision': decision, 'checks': normalized_checks,
        'required_views': required, 'reviewed_views': reviewed,
        'missing_views': missing, 'unreviewed_views': unseen, 'issues': normalized_issues,
        'part_decisions':decisions,
    }
