"""
Re-validate every garment through the Step-0 baseline gate, using the stable
`cloth.FABRIC` recipe. Each skirt is draped STATICALLY on a proper body and
measured at rest - the foundation their animations should have been built on.

  blender --background --python cottage/validate_garments.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cloth
import clothdiag

# geometry copied from the twirl / dance / march labs
SPECS = {
    "twirl": dict(r_waist=0.14, r_hem=0.22, ztop=0.95, zbot=0.30,
                  segs=56, loops=34, flutes=14, amp_waist=0.02, amp_hem=0.13),
    "dance": dict(r_waist=0.15, r_hem=0.33, ztop=0.95, zbot=0.16,
                  segs=64, loops=42, flutes=18, amp_waist=0.015, amp_hem=0.075),
    "march": dict(r_waist=0.15, r_hem=0.27, ztop=0.95, zbot=0.35,
                  segs=56, loops=36, flutes=14, amp_waist=0.015, amp_hem=0.055),
}


def main():
    results = {}
    for name, spec in SPECS.items():
        print(f"\n==== {name} ====", flush=True)
        # longer skirts take longer to settle; give them headroom
        settle = 85 + int((spec["ztop"] - spec["zbot"]) * 60)
        report = cloth.drape_baseline(spec, settle=settle, fabric=cloth.FABRIC)
        ok, text = clothdiag.baseline_verdict(report)
        print(text, flush=True)
        results[name] = (ok, report)

    print("\n================ SUMMARY ================")
    for name, (ok, r) in results.items():
        print(f"  {name:<7} {'PASS' if ok else 'FAIL':<5} "
              f"pen={r['settled_penetration_m']}  "
              f"stretch={r['rest_max_stretch']}  "
              f"settle={r['settle_speed_m_s']}")


main()
