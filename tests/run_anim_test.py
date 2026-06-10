"""Headless test for the .ast.ads animation importer.

Run with:
  blender --background --factory-startup --python tests/run_anim_test.py

Uses the real battledroid and wed1577 samples in research/.
"""
import importlib
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, "research")

sys.path.insert(0, REPO)
addon = importlib.import_module("io_import_republicheroes_mdl")
addon.register()

import bpy  # noqa: E402

ctx = bpy.context


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# --- battledroid: 92 animations, 70 animated bones --------------------
clear_scene()
bpy.ops.import_scene.republic_heroes_mdl(
    filepath=os.path.join(RESEARCH, "a_battledroid_bindpose.mkmesh.mdl"))
arm = next(o for o in ctx.scene.objects if o.type == "ARMATURE")
assert len(arm.data.bones) == 85, len(arm.data.bones)
ctx.view_layer.objects.active = arm

result = bpy.ops.import_scene.republic_heroes_ads(
    filepath=os.path.join(RESEARCH, "a_battledroid.ast.ads"))
assert result == {"FINISHED"}
actions = [a for a in bpy.data.actions if a.name.startswith("a_battledroid.")]
assert len(actions) == 92, len(actions)

run = bpy.data.actions["a_battledroid.Run"]
assert len(run.fcurves) > 100, len(run.fcurves)
assert all(kp.interpolation == "LINEAR"
           for kp in run.fcurves[0].keyframe_points)

# native frame counts (30 fps): Run = 0.767s = 23 frames, Idle_00 = 101
assert ctx.scene.render.fps == 30, ctx.scene.render.fps
assert tuple(run.frame_range) == (1.0, 24.0), tuple(run.frame_range)
idle = bpy.data.actions["a_battledroid.Idle_00"]
assert tuple(idle.frame_range) == (1.0, 102.0), tuple(idle.frame_range)
# keys snapped onto integer frames of the 30 fps grid
for fc in run.fcurves[:8]:
    for kp in fc.keyframe_points:
        assert abs(kp.co.x - round(kp.co.x)) < 1e-4, kp.co.x

# pose sanity: droid upright (Y-up) through the Run cycle
arm.animation_data.action = run
f0, f1 = run.frame_range
for frac in (0.0, 0.3, 0.6, 1.0):
    ctx.scene.frame_set(int(f0 + (f1 - f0) * frac))
    ctx.view_layer.update()
    head = arm.pose.bones["z_Head"].matrix.translation
    foot = arm.pose.bones["z_L_Foot"].matrix.translation
    up = head.y - foot.y
    assert up > 120, (frac, up)

# basis quats must stay unit-length
for fc in run.fcurves:
    pass
pb = arm.pose.bones["z_Root"]
n = math.sqrt(sum(c * c for c in pb.rotation_quaternion))
assert abs(n - 1.0) < 0.01, n

# --- ahsoka (humanoid): motion plausibility of Run --------------------
clear_scene()
bpy.ops.import_scene.republic_heroes_mdl(
    filepath=os.path.join(RESEARCH, "a_ahsoka_bindpose.mkmesh.mdl"))
arm = next(o for o in ctx.scene.objects if o.type == "ARMATURE")
ctx.view_layer.objects.active = arm
result = bpy.ops.import_scene.republic_heroes_ads(
    filepath=os.path.join(RESEARCH, "a_ahsoka.ast.ads"), name_filter="Run")
assert result == {"FINISHED"}
run = next(a for a in bpy.data.actions if a.name == "a_ahsoka.Run")
arm.animation_data.action = run
f0, f1 = run.frame_range
foot_split = []
for frac in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
    ctx.scene.frame_set(int(round(f0 + (f1 - f0) * frac)))
    ctx.view_layer.update()
    head = arm.pose.bones["z_Head"].matrix.translation
    footl = arm.pose.bones["z_L_Foot"].matrix.translation
    footr = arm.pose.bones["z_R_Foot"].matrix.translation
    assert head.y - min(footl.y, footr.y) > 100, frac  # upright
    for hand in ("z_L_Hand", "z_R_Hand"):
        h = arm.pose.bones[hand].matrix.translation
        assert h.y < head.y, (frac, hand)  # no arms-over-head artifact
    foot_split.append(abs(footl.z - footr.z) + abs(footl.x - footr.x))
assert max(foot_split) > 20, foot_split  # legs actually stride

# --- wed1577: type-16 skeleton variant + 13 animations ----------------
clear_scene()
bpy.ops.import_scene.republic_heroes_mdl(
    filepath=os.path.join(RESEARCH, "a_droid_wed1577.mkmesh.mdl"))
arms = [o for o in ctx.scene.objects if o.type == "ARMATURE"]
assert arms, "type-16 skeleton variant not imported"
arm = arms[0]
assert "Z_Hatch" in arm.data.bones, list(arm.data.bones.keys())[:10]
ctx.view_layer.objects.active = arm

result = bpy.ops.import_scene.republic_heroes_ads(
    filepath=os.path.join(RESEARCH, "a_droid_wed1577.ast.ads"))
assert result == {"FINISHED"}
wed_actions = [a for a in bpy.data.actions
               if a.name.startswith("a_droid_wed1577.")]
assert len(wed_actions) == 13, len(wed_actions)
hatch = next(a for a in wed_actions if a.name.endswith("Hatch Opening"))
paths = {fc.data_path for fc in hatch.fcurves}
assert any("Z_Hatch" in p for p in paths), paths

# name filter option
clear_scene()
bpy.ops.import_scene.republic_heroes_mdl(
    filepath=os.path.join(RESEARCH, "a_battledroid_bindpose.mkmesh.mdl"))
arm = next(o for o in ctx.scene.objects if o.type == "ARMATURE")
ctx.view_layer.objects.active = arm
bpy.ops.import_scene.republic_heroes_ads(
    filepath=os.path.join(RESEARCH, "a_battledroid.ast.ads"),
    name_filter="Idle")
filtered = [a for a in bpy.data.actions
            if a.name.startswith("a_battledroid.")]
assert 0 < len(filtered) < 92, len(filtered)
assert all("idle" in a.name.lower() for a in filtered)

print("TEST OK: 92 + 13 actions, upright Run cycle, type-16 skeleton, "
      "name filter")
