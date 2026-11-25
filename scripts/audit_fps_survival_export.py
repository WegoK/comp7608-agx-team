"""Audit recorded equipment, contact and deaths; no damage counterfactual."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def audit(path,events):
    rounds={};digest=hashlib.sha256();rows=raw=0;footer=None
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];seat=own['seat']
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation'];m=o['me'];a=r['action']
            q=rounds.setdefault(o['round_index'],dict(side=o['side'],first_live=None,last_alive=None,
                first_contact=None,counts=Counter(),own_buy_events=[],death_events=[],received_head_hits=[],objective_events=[]))
            if o['phase']!='live' or not m['alive']:continue
            if q['first_live'] is None:
                q['first_live']=dict(tick=o['match_tick'],cash=m['cash'],armor=m['armor'],helmet=m['helmet'],kit=m['has_kit'],
                    weapons=[g['id'] if g else None for g in m['weapons']])
            q['last_alive']=dict(tick=o['match_tick'],hp=m['hp'],visible_seats=[e['seat'] for e in o['visible']],alive_by_team=o['alive_by_team'])
            if o['visible'] and not m['blind_ticks']:
                e=min(o['visible'],key=lambda e:(e['x']-m['x'])**2+(e['y']-m['y'])**2)
                q['counts']['visible_combat']+=1;q['counts']['multiple_visible']+=len(o['visible'])>=2
                q['counts']['ads_requested']+=bool(a.get('ads'));q['counts']['fire_requested']+=bool(a.get('fire'))
                if q['first_contact'] is None:
                    q['first_contact']=dict(tick=o['match_tick'],target=e['seat'],distance_m=math.dist([m[k] for k in ('x','y','z')],[e[k] for k in ('x','y','z')]))
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(seat):rows}
    helmet_prices=Counter();buys=Counter();received=Counter();head_fatal=[];before_plant=[]
    all_events=[json.loads(line) for line in events.open(encoding='utf-8')]
    for e in all_events:
        q=rounds.get(e.get('round_index'))
        if e['type']=='buy':
            buys[e['kind']]+=1
            if e['kind']=='helmet':helmet_prices[str(e['price'])]+=1
            if q is not None and e['seat']==seat:q['own_buy_events'].append({k:e[k] for k in ('match_tick','kind','price')})
        if q is None:continue
        if e['type']=='hit' and e.get('target')==seat:
            received[e['part']]+=1
            if e['part']=='head':q['received_head_hits'].append({k:e[k] for k in ('match_tick','player','amount','hp_damage','armor_absorbed') if k in e})
        if e['type']=='kill' and e.get('target')==seat:q['death_events'].append({k:e[k] for k in ('match_tick','killer')})
        if e['type'] in ('plant_complete_detail','defuse_complete_detail','round_end'):
            q['objective_events'].append({k:e[k] for k in ('type','match_tick','site','player','round_winner','reason') if k in e})
    # The event stream contains both 350 upgrade and 1000 bundle purchases.
    # Restrict the affordability screen to full body armour, never unarmoured cash.
    assert '350' in helmet_prices
    helmet_price=350.;affordable=[];unhelmeted=[];never_visible=[];zero_fire=[]
    for i,q in rounds.items():
        live=q['first_live']
        if live and not live['helmet']:
            unhelmeted.append(i+1)
            if live['armor']>=100 and live['cash']>=helmet_price:affordable.append(i+1)
        for death in q['death_events']:
            if any(e['match_tick']==death['match_tick'] for e in q['received_head_hits']):head_fatal.append(i+1)
        if q['death_events'] and not q['counts']['visible_combat']:never_visible.append(i+1)
        if q['death_events'] and not q['counts']['fire_requested']:zero_fire.append(i+1)
        plant=next((e for e in q['objective_events'] if e['type']=='plant_complete_detail'),None)
        end=next((e for e in q['objective_events'] if e['type']=='round_end'),None)
        if q['side']=='attack' and end and end.get('reason')=='defuse' and plant:
            death=q['death_events'][0] if q['death_events'] else None
            before_plant.append(dict(round_1based=i+1,plant_tick=plant['match_tick'],own_death_tick=death['match_tick'] if death else None,
                own_died_before_plant=bool(death and death['match_tick']<plant['match_tick'])))
    return dict(schema='fps-recorded-survival-audit-v1',match_id=h['job_id'],own_seat_zero_based=seat,
        integrity=dict(rows=rows,raw_bytes=raw,sha256=digest.hexdigest()),
        public_helmet_price=helmet_price,helmet_purchase_events_all_players=helmet_prices,
        all_buy_kinds=buys,unhelmeted_first_live_rounds=unhelmeted,
        body_armor_plus_affordable_helmet_first_live_rounds=affordable,
        received_hit_parts=received,fatal_tick_with_head_hit_rounds=head_fatal,
        died_without_any_unblinded_visible_combat_rounds=never_visible,
        died_without_fire_requested_rounds=zero_fire,attack_defused_rounds=before_plant,rounds=rounds,
        note='Equipment and deaths are actual recorded facts. Affordability screens only full body armour and the observed 350 helmet upgrade tier; the stream also has 1000 bundle buys. Purchase eligibility and mitigation need candidate checks. No saved deaths or score improvement inferred.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('log','events','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=audit(a.log,a.events);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:r[k] for k in ('public_helmet_price','body_armor_plus_affordable_helmet_first_live_rounds','fatal_tick_with_head_hit_rounds','attack_defused_rounds')},indent=2))
