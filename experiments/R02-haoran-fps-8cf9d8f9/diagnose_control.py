import json,math,statistics
from collections import Counter
from pathlib import Path
angle=lambda x:(x+math.pi)%(2*math.pi)-math.pi
previous=None;yr=[];pr=[];cb={};ret={};examples=[];sites=None
for line in Path('dist/fps-inputs/training-8cf9d8f9-bf77-41ba-92cd-25b865de8d41.jsonl').open(encoding='utf-8'):
 row=json.loads(line)
 if 'observation' not in row:continue
 o=row['observation'];m=o['me'];i=o['round_index'];t=o['match_tick'];a=row['action']
 if 'map' in o:sites=o['map']['sites']
 live=o['phase']=='live' and m['alive'];combat=live and o['visible'] and not m['blind_ticks']
 if previous:
  po,pa,pap=previous;pm=po['me']
  if i==po['round_index'] and t-po['match_tick']==3 and live and pm['alive'] and pm['hp']==m['hp'] and not pa.get('fire'):
   dy=angle(m['yaw']-pm['yaw']);u=pap.get('yaw_rate',0);v=pap.get('pitch_rate',0)
   if abs(u)>.01 and abs(dy)<1:yr.append(dy/u)
   if abs(v)>.01 and abs(m['pitch'])<1.3 and abs(pm['pitch'])<1.3:pr.append((m['pitch']-pm['pitch'])/v)
  if i==21 and combat and po['visible'] and len(examples)<6:
   examples.append(dict(tick=t,yaw=m['yaw'],pitch=m['pitch'],action=a,prior_yaw=pm['yaw'],prior_action=pa))
 if combat:
  e=min(o['visible'],key=lambda e:(e['x']-m['x'])**2+(e['y']-m['y'])**2)
  g=m['weapons'][a['weapon_slot']];p=g.get('aim_punch',{}) if g else {}
  part=next((p for p in ('chest','head','abdomen') if p in e['parts']),'chest')
  h={'chest':1.18,'head':1.64,'abdomen':.88}[part]*(.6 if e['crouch'] else 1)
  yaw=angle(math.atan2(m['y']-e['y'],e['x']-m['x'])-m['yaw']-2*p.get('yaw',0))
  pitch=math.atan2(e['z']+h-m['z']-(.95 if m['crouch'] else 1.62),math.hypot(e['x']-m['x'],e['y']-m['y']))-m['pitch']-2*p.get('pitch',0)
  c=cb.setdefault(i,Counter());c['calls']+=1;c['yaw_gate_fail']+=abs(yaw)>=.04;c['pitch_gate_fail']+=abs(pitch)>=.04
  c['ready']+=bool(g and g['ammo'] and not g['reload_until']);c['fire']+=bool(a['fire'])
 if live and o['side']=='defend' and o['bomb']['state']=='planted':
  r=ret.setdefault(i,dict(first_tick=t,last_tick=t,min_site_distance={},known_calls=0));r['last_tick']=t;r['known_calls']+=bool(o['bomb'].get('position'))
  for s in sites:
   dist=math.dist([m[k] for k in ('x','y','z')],s['center']);r['min_site_distance'][s['id']]=min(r['min_site_distance'].get(s['id'],999),dist)
 previous=o,a,row['applied_action']
events=[json.loads(l) for l in Path('dist/fps-inputs/public-events-8cf9d8f9.jsonl').open()]
plants=[{k:e[k] for k in ('round_index','match_tick','site')} for e in events if e['type']=='plant_complete_detail']
fires=Counter(e['round_index'] for e in events if e['type']=='fire' and e['player']==7)
summary=dict(combat_by_round=cb,retakes=ret,plants=plants,own_actual_fire_by_round=fires,
 yaw_response=dict(samples=len(yr),median=statistics.median(yr),q10=sorted(yr)[int(len(yr)*.1)],q90=sorted(yr)[int(len(yr)*.9)]),
 pitch_response=dict(samples=len(pr),median=statistics.median(pr)),round22_examples=examples,
 note='Observed response ratios describe this match at 3-tick intervals, not an independently verified engine contract.')
Path('dist/fps-inputs/diagnostics-8cf9.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
