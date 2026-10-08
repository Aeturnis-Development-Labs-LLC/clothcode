"""
Demonstrate the self-calibrating pipeline (clothtune).

A) auto-settle: find the convergence frame F for each fabric on the study
   garment, next to the old hand-tuned settle budget.
B) auto-tune: start from FABRIC on the SHOWCASE garment - which fails the gate
   with ~56mm body penetration - and let the search fix it automatically.

  blender --background --python cottage/tune_demo.py -- [--part A|B|all]
          [--max-settle N] [--iters N]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import math
import bpy
import utils as U
import cloth
import clothtune
import clothdiag

STUDY = dict(r_waist=0.15, r_hem=0.30, ztop=0.95, zbot=0.30,
             segs=60, loops=40, flutes=10, amp_waist=0.015, amp_hem=0.06)
SHOWCASE = dict(r_waist=0.15, r_hem=0.27, ztop=0.95, zbot=0.32,
                segs=64, loops=40, flutes=12, amp_waist=0.015, amp_hem=0.06)
OLD_SETTLE = {"FABRIC": 90, "WOOL": 110, "LINEN": 120, "VELVET": 150}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    def arg(flag, default, cast=str):
        return cast(argv[argv.index(flag) + 1]) if flag in argv else default

    part = arg("--part", "all")
    max_settle = arg("--max-settle", 170, int)
    iters = arg("--iters", 4, int)

    if part in ("A", "all"):
        print("\n################  A) AUTO-SETTLE CALIBRATION  ################")
        print(f"{'fabric':<8} {'auto_F':>7} {'old':>5} {'pen_mm':>7} {'v_mm/s':>7} {'verdict':>8}")
        for name, cfg in cloth.FABRICS.items():
            F, rep = clothtune.auto_settle(STUDY, fabric=cfg["recipe"],
                                           max_settle=max_settle)
            import clothdiag
            ok, _ = clothdiag.baseline_verdict(rep)
            print(f"{name:<8} {F:>7} {OLD_SETTLE[name]:>5} "
                  f"{rep['settled_penetration_m']*1000:>7.1f} "
                  f"{rep['settle_speed_m_s']*1000:>7.1f} "
                  f"{'PASS' if ok else 'FAIL':>8}", flush=True)

    if part in ("B", "all"):
        print("\n################  B) AUTO-TUNE (fix showcase penetration)  ########")
        print("starting recipe: FABRIC  |  garment: showcase SPEC "
              "(sinks ~56mm into the body by settle=140)")
        fabric, F, rep, ok, hist = clothtune.auto_tune(
            SHOWCASE, fabric0=cloth.FABRIC, max_iters=iters, settle=140)
        print(f"\n{'iter':>4} {'pen_mm':>7} {'stretch':>8} {'compr':>7} "
              f"{'settle':>6} {'pass':>5}  applied-before-this-iter")
        for h in hist:
            print(f"{h['iter']:>4} {h['pen_mm']:>7.1f} {h['stretch']:>8.3f} "
                  f"{h['compr']:>7.3f} {h['settle']:>6} "
                  f"{'yes' if h['passed'] else 'no':>5}  {h['change']}", flush=True)
        print(f"\nRESULT: {'PASS' if ok else 'FAIL'} after {len(hist)} i- "
              f"final distance_min={fabric['distance_min']} mass={fabric['mass']} "
              f"tension={fabric['tension']} bending={fabric['bending']}")

    if part in ("C", "all"):
        print("\n################  C) MOTION GATE (verdict on a baked CLIP)  ######")
        TAU = 2 * math.pi
        settle, motion = 45, 45
        total = settle + motion
        U.clear_scene()
        col = U.get_collection(cloth.COL)
        skirt, _w = cloth.build_skirt(STUDY, col)
        arm, pb = cloth.waist_bone(skirt, STUDY["ztop"], collection=col)
        cloth.lower_body(STUDY, col)
        cl = cloth.add_cloth(skirt, "Waist", cloth.WOOL)
        bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
        for f in range(1, total + 1):
            u = max(0.0, (f - settle) / motion)
            ang = 3 * TAU * (u * u * (3 - 2 * u))        # eased 3-turn twirl
            pb.rotation_euler = (0, ang, 0)
            pb.keyframe_insert('rotation_euler', frame=f)
        cloth.bake(skirt, cl, total)
        ok, text, rep = clothtune.motion_gate(skirt, total, looping=False)
        print(text, flush=True)
        print(f"MOTION_GATE: {'ACCEPT' if ok else 'REJECT'}")
    print("\nTUNE_DEMO_DONE")


main()
