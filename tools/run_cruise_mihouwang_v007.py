"""
mihouwang v007 对比拼装测试
源: S:\Project\ysj\work\assets\chr\mihouwang\rig\rigMaster\ysj_chr_mihouwang_rig_rigMaster_v007.ma
贴图: X:\Project\ysj\pub\assets\chr\mihouwang\tex\texMaster\ysj_chr_mihouwang_tex_texMaster_v003.blend
"""
import sys
sys.path.insert(0, r"Y:\GGbommer\scripts\CGI_Pipeline")

import run_auto_cruise_test as ct

# 覆盖配置
ct.X_RIG = r"S:\Project\ysj\work\assets\chr\mihouwang\rig\rigMaster\ysj_chr_mihouwang_rig_rigMaster_v007.ma"
ct.X_BLEND = r"X:\Project\ysj\pub\assets\chr\mihouwang\tex\texMaster\ysj_chr_mihouwang_tex_texMaster_v003.blend"

if __name__ == '__main__':
    success = ct.run_test()
    sys.exit(0 if success else 1)
