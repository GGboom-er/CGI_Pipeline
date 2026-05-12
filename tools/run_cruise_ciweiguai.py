"""ciweiguai 巡航测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 覆盖配置后执行
import run_auto_cruise_test as ct
ct.ASSET = "ciweiguai"
ct.X_RIG = r"X:\Project\ysj\pub\assets\chr\ciweiguai\rig\rigMaster\ysj_chr_ciweiguai_rig_rigMaster_v002.ma"
ct.X_BLEND = r"X:\Project\ysj\pub\assets\chr\ciweiguai\tex\texMaster\ysj_chr_ciweiguai_tex_texMaster_v001.blend"

if __name__ == '__main__':
    success = ct.run_test()
    sys.exit(0 if success else 1)
