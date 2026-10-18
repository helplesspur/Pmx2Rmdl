"""Choose surfaces receiving the additional inverse-hull outline."""
import re


def outline_surface(model, material):
    if model == 'lucy' or model.startswith('lucy_'):
        return material.casefold() in {'cloth', 'up', 'down', 'face'}
    return not re.search(r'^Eye|\u76ee|\u7709|\u53e3|\u9f7f|\u776b|\u4e8c\u91cd|\u8d34\u56fe', material, re.I)


def face_contour_surface(model, material):
    return (model == 'lucy' or model.startswith('lucy_')) and material.casefold() == 'face'


def convex_face_contour(vertices):
    import bmesh
    mesh=bmesh.new()
    original=mesh.verts.layers.int.new('source_vertex')
    source=[]
    positions=set()
    for vertex in vertices:
        position,normal,links=vertex
        key=tuple(round(v,4) for v in position)
        if key in positions:continue
        positions.add(key)
        item=mesh.verts.new(position)
        item[original]=len(source)
        source.append(vertex)
    # Closing eye/mouth recesses prevents extra lines within the face.
    bmesh.ops.convex_hull(mesh,input=list(mesh.verts),use_existing_faces=False)
    bmesh.ops.triangulate(mesh,faces=list(mesh.faces))
    mesh.normal_update()
    triangles=[[(v.co.copy(),v.normal.copy(),source[v[original]][2]) for v in face.verts]
               for face in mesh.faces]
    mesh.free()
    return triangles
