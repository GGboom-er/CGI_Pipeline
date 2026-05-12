"""maYouB 巡航测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_auto_cruise_test as ct

ct.ASSET = "maYouB"
ct.X_RIG = r"X:\Project\ysj\pub\assets\chr\maYouB\rig\rigMaster\ysj_chr_maYouB_rig_rigMaster_v002.ma"
ct.X_BLEND = r"X:\Project\ysj\pub\assets\chr\maYouB\tex\texMaster\ysj_chr_maYouB_tex_texMaster_v001.blend"

if __name__ == "__main__":
    success = ct.run_test()
    sys.exit(0 if success else 1)
