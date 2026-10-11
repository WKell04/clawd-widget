"""Render Clawd sprite sequences (transparent PNGs) for the desktop widget.

Usage:  blender -b clawd.blend -P render_sprites.py
The .blend file is not saved; the extra "turn" animation only exists in memory.
"""
import math
import os

import bpy
from mathutils import Vector

OUT = os.path.join(os.path.dirname(bpy.data.filepath), "assets")
RES = 288            # px; ~1.1x of the logical size (screen scale 1.1), keeps RAM low
ORTHO = 2.2          # metres visible across the frame
FACE_ANGLE = 34      # degrees Clawd turns to face the camera
VIEW_DIR = Vector((-1, -0.9, 0.35)).normalized()   # same angle as the mockup
TARGET = Vector((0.15, 0, 0.62))

sc = bpy.context.scene
coll = bpy.data.collections["Clawd"]
root = bpy.data.objects["clawd"]
body = bpy.data.objects["body"]
sl = bpy.data.objects["speed_lines"]
legs = {n: bpy.data.objects[n] for n in ("leg_FL", "leg_FR", "leg_BL", "leg_BR")}
animated = [o for o in coll.objects if o.animation_data]


def setup_camera_and_light():
    cam = bpy.data.objects.get("sprite_cam")
    if not cam:
        cam = bpy.data.objects.new("sprite_cam", bpy.data.cameras.new("sprite_cam"))
        sc.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ORTHO
    cam.location = TARGET + VIEW_DIR * 10
    cam.rotation_euler = (-VIEW_DIR).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam

    sun = bpy.data.objects.get("preview_sun")
    if sun:
        sun.data.energy = 4.0
        sun.rotation_euler = (0.9, 0.2, -0.8)
    # soft fill from the camera side so the face isn't dark
    fill = bpy.data.objects.get("sprite_fill")
    if not fill:
        fill = bpy.data.objects.new("sprite_fill", bpy.data.lights.new("sprite_fill", "SUN"))
        sc.collection.objects.link(fill)
    fill.data.energy = 0.6
    fill.rotation_euler = (-VIEW_DIR).to_track_quat("-Z", "Y").to_euler()


def setup_render():
    r = sc.render
    r.resolution_x = r.resolution_y = RES
    r.resolution_percentage = 100
    r.film_transparent = True
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    views = [i.identifier for i in bpy.types.ColorManagedViewSettings.bl_rna
             .properties["view_transform"].enum_items]
    # "Standard" keeps the orange saturated (AgX would wash it out)
    if "Standard" in views:
        sc.view_settings.view_transform = "Standard"
    if hasattr(sc, "eevee"):
        sc.eevee.taa_render_samples = 32


def key(o, f, **vals):
    for prop, v in vals.items():
        setattr(o, prop, v)
        o.keyframe_insert(prop, frame=f)


def add_turn_track():
    """Small two-step shuffle while turning to face the camera (12 frames)."""
    def track(o, fill_keys):
        ad = o.animation_data or o.animation_data_create()
        ad.action = bpy.data.actions.new(f"turn_{o.name}")
        rest = (o.location.copy(), o.rotation_euler.copy(), o.scale.copy())
        fill_keys(o)
        act = ad.action
        ad.action = None
        o.location, o.rotation_euler, o.scale = rest
        t = ad.nla_tracks.new()
        t.name = "turn"
        t.strips.new("turn", 1, act)

    track(root, lambda o: (key(o, 1, rotation_euler=(0, 0, 0)),
                           key(o, 12, rotation_euler=(0, 0, math.radians(FACE_ANGLE)))))
    bl = body.location.copy()
    track(body, lambda o: (
        key(o, 1, location=bl, rotation_euler=(0, math.radians(-4), 0)),
        key(o, 4, location=bl + Vector((0, 0, 0.015)), rotation_euler=(0, math.radians(-2), 0)),
        key(o, 8, location=bl + Vector((0, 0, 0.015))),
        key(o, 12, location=bl, rotation_euler=(0, 0, 0))))
    s0 = sl.scale.copy()
    track(sl, lambda o: (key(o, 1, scale=(0.75, 1, 1)), key(o, 5, scale=(0, 0, 0)),
                         key(o, 12, scale=(0, 0, 0))))
    for name, peak in (("leg_FL", 4), ("leg_BR", 4), ("leg_FR", 8), ("leg_BL", 8)):
        track(legs[name], lambda o, p=peak: (
            key(o, 1, rotation_euler=(0, 0, 0)),
            key(o, p, rotation_euler=(0, math.radians(15), 0)),
            key(o, p + 3, rotation_euler=(0, 0, 0)),
            key(o, 12, rotation_euler=(0, 0, 0))))
    sl.scale = s0


def solo(track_name):
    for o in animated + [root]:
        if o.animation_data:
            for t in o.animation_data.nla_tracks:
                t.mute = t.name != track_name


# name: (frames to render, root yaw in degrees when the root has no track)
ANIMS = {
    "walk": (range(1, 17), 0),
    "turn": (range(1, 13), None),
    "idle": (range(1, 49), FACE_ANGLE),
    "poof": (range(1, 19), FACE_ANGLE),
}


def render(only=None, frames=None):
    setup_camera_and_light()
    setup_render()
    if not any(t.name == "turn" for t in (root.animation_data.nla_tracks if root.animation_data else [])):
        add_turn_track()
    for name, (rng, yaw) in ANIMS.items():
        if only and name not in only:
            continue
        solo(name)
        if yaw is not None:
            root.rotation_euler = (0, 0, math.radians(yaw))
        os.makedirs(os.path.join(OUT, name), exist_ok=True)
        for i, f in enumerate(rng, 1):
            if frames and i not in frames:
                continue
            sc.frame_set(f)
            sc.render.filepath = os.path.join(OUT, name, f"{i:04d}.png")
            bpy.ops.render.render(write_still=True)
        print(f"{name}: rendered")


if __name__ == "__main__":
    render()
