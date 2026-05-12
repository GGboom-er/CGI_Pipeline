import bpy
import json

objs = [o.name for o in bpy.data.objects if o.parent is None]
cols = [c.name for c in bpy.data.collections]

print("=== SCENE CONTENT ===")
print("Roots:", objs)
print("Collections:", cols)
print("=====================")
