"""Run pinned upstream activation selection on observed policy definitions.

Only the selected upstream functions are executed. Zone membership and policy
objects are data models; this is not a firewalld daemon or kernel/reload test.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from probe_cp1_minimal_upstream import extract, LOCK
from test_cp1_minimal_change import baseline, m


def probe(directory):
    cls=extract(directory/'fw_policy.py','FirewallPolicy',
                ['get_policies_not_derived_from_zone','get_active_policies_not_derived_from_zone'],{})
    base,_=baseline(); policy=cls(); active_zones=[]
    objects={name:NS(disable=obj['fields']['disable']=='yes',derived_from_zone=False,
                     ingress_zones=obj['fields']['ingress-zones'].split(),
                     egress_zones=obj['fields']['egress-zones'].split())
             for name,obj in base['objects']['runtime']['policies'].items()}
    policy.get_policies=lambda:sorted(objects)
    policy.get_policy=lambda name:objects[name]
    policy._fw=NS(zone=NS(get_active_zones=lambda:active_zones))
    results=[]
    for label,zones in [('before',['public']),('after_cali_binding',['public',m.ZONE]),
                         ('all_referenced_zones',list(base['objects']['runtime']['zones'])+[m.ZONE])]:
        active_zones[:]=zones
        actual=policy.get_active_policies_not_derived_from_zone()
        assert actual==['allow-host-ipv6'],(label,actual)
        results.append({'case':label,'active':actual})
    for name in sorted(m.PRESERVED_DISABLED_POLICIES):
        objects[name].disable=False
        actual=policy.get_active_policies_not_derived_from_zone()
        assert actual==sorted(['allow-host-ipv6',name]),(name,actual)
        results.append({'case':'positive_control_enable_'+name,'active':actual})
        objects[name].disable=True
    return {'scope':'PINNED_UPSTREAM_ACTIVATION_SELECTION_WITH_MODELED_ZONE_CONTEXT',
            'source_url':'https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_policy.py',
            'source_sha256':LOCK['fw_policy.py'],'checks':results,'count':len(results),
            'daemon_tested':False,'reload_tested':False,'kernel_tested':False,'live_cp1_tested':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--upstream-dir',type=Path,required=True)
    print(json.dumps(probe(parser.parse_args().upstream_dir),sort_keys=True,indent=2))
