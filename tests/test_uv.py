import sys
from alembic.AbcGeom import IPolyMesh, IXform
import alembic

arch = alembic.Abc.IArchive(r'y:\runs\assets\chr\xtbyao\tex\texMaster\ysj_chr_xtbyao_tex_texMaster_v002.abc')
top = arch.getTop()

def find_mesh(node):
    header = node.getHeader()
    if IPolyMesh.matches(header):
        return node
    for i in range(node.getNumChildren()):
        res = find_mesh(node.getChild(i))
        if res: return res
    return None

m = find_mesh(top)
print("Mesh found:", m.getName())

mesh = IPolyMesh(m.getParent(), m.getName())
schema = mesh.getSchema()
uv_param = schema.getUVsParam()

if uv_param.valid():
    print("UV Param Valid! Type:", type(uv_param))
    uv_samp = uv_param.getIndexedValue()
    print("Indexed Value Type:", type(uv_samp))
    print("Vals:", dir(uv_samp.getVals()))
    print("Indices:", dir(uv_samp.getIndices()))
    v = uv_samp.getVals()
    print("Vals length:", len(v))
    print("Example vals:", v[0], v[1])
else:
    print("No UVs")
