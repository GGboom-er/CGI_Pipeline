from pathlib import Path

import maya.cmds as cmds


OUT_PATH = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test_v086_A_alphaGate.ma"
)


def main():
    if OUT_PATH.exists():
        raise RuntimeError("Refuse to overwrite existing file: %s" % OUT_PATH)
    cmds.file(rename=str(OUT_PATH))
    cmds.file(save=True, type="mayaAscii")
    return {
        "status": "SUCCESS",
        "output_scene": str(OUT_PATH),
        "exists": OUT_PATH.exists(),
    }


result = main()
