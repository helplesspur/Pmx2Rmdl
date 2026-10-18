"""Evidence gate: joining, model application, movement and finite bone samples."""
import json
import math
import re
import sys
from pathlib import Path


def audit(root):
    root=Path(root)
    report=json.loads((root/'report.json').read_text(encoding='utf-8-sig'))
    client=(root/'client/script.log').read_text(encoding='utf-8',errors='replace') if (root/'client/script.log').exists() else ''
    server='\n'.join(x.read_text(encoding='utf-8',errors='replace') for x in (root/'server').glob('*.log'))
    stages={};bad=[];bounds=[];positions={}
    for m in re.finditer(r'\[PMXCONV-QA\] BONE (\w+) (\S+) <([^>]+)> <([^>]+)>',client):
        stages.setdefault(m[1],[]).append(m[2])
        try:
            p=[float(x) for x in m[3].split(',')];a=[float(x) for x in m[4].split(',')]
            positions.setdefault(m[1],{})[m[2]]=p
            if not all(math.isfinite(x) for x in p+a):raise ValueError()
            distance=math.sqrt(sum(x*x for x in p))
            if distance>150:bounds.append({'stage':m[1],'bone':m[2],'distance':distance})
        except ValueError:bad.append({'stage':m[1],'bone':m[2]})
    states=re.findall(r'\[PMXCONV-QA\] STATE (\w+) .*? (true|false)(?: <([^>]+)>)?',client)
    third_person= len(states)==4 and all(x[1]=='true' for x in states)
    moved=any(stage=='moving' and speed and any(abs(float(x))>1 for x in speed.split(',')) for stage,_,speed in states)
    joined=bool(re.search(r'(?:RECV state=8|signon=8)',server)) and bool(re.search(r'acked FULL',server))
    ready='[PMXCONV] client ready' in client and '[PMXCONV] server ready' in server
    applied='[PMXCONV] APPLIED' in server
    complete=bool(report.get('completed')) and '[PMXCONV-QA] COMPLETE' in client
    missing=re.findall(r'\[PMXCONV-QA\] MISSING (\S+)',client)
    same_counts=len(stages)==4 and len({len(x) for x in stages.values()})==1 and bool(stages)
    neck_lengths=[];helper_gaps=[]
    for bones in positions.values():
        if all(name in bones for name in ('def_c_neckA','def_c_neckB','jx_c_neckB')):
            neck_lengths.append(math.dist(bones['def_c_neckA'],bones['def_c_neckB']))
            helper_gaps.append(math.dist(bones['jx_c_neckB'],bones['def_c_neckB']))
    neck_stable=len(neck_lengths)==4 and max(neck_lengths)-min(neck_lengths)<.02 and max(helper_gaps)<.001
    report['audit']={'joined':joined,'ready':ready,'applied':applied,'qa_complete':complete,'third_person':third_person,'movement_observed':moved,'samples':{k:len(v) for k,v in stages.items()},'missing_bones':missing,'nonfinite_samples':bad,'out_of_bounds':bounds,'head_deform_neck_lengths':neck_lengths,'neck_helper_gaps':helper_gaps,'head_neck_continuity':neck_stable,'passed':joined and ready and applied and complete and third_person and moved and same_counts and neck_stable and not missing and not bad and not bounds}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    report=audit(sys.argv[1]);print(json.dumps(report['audit'],ensure_ascii=False,indent=2));sys.exit(0 if report['audit']['passed'] else 1)
