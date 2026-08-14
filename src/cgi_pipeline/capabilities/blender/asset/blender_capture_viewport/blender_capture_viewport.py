import os
import time
import traceback
from pathlib import Path

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    width = params.get('width', 1920)
    height = params.get('height', 1080)
    
    try:
        import bpy
        
        project_root = os.getenv('CGI_PROJECT_ROOT', '.')
        sandbox_dir = Path(project_root) / 'runs' / 'captures'
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        temp_path = sandbox_dir / f"blender_capture_{timestamp}.png"
        
        scene = bpy.context.scene
        
        # 保存原始设置
        orig_path = scene.render.filepath
        orig_res_x = scene.render.resolution_x
        orig_res_y = scene.render.resolution_y
        orig_res_pct = scene.render.resolution_percentage
        orig_format = scene.render.image_settings.file_format
        
        try:
            # 应用新设置
            scene.render.filepath = str(temp_path)
            scene.render.resolution_x = width
            scene.render.resolution_y = height
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = 'PNG'
            
            # 使用标准渲染，支持纯后台 headless 模式
            bpy.ops.render.render(write_still=True)
            
        finally:
            # 恢复设置
            scene.render.filepath = orig_path
            scene.render.resolution_x = orig_res_x
            scene.render.resolution_y = orig_res_y
            scene.render.resolution_percentage = orig_res_pct
            scene.render.image_settings.file_format = orig_format
            
        if not temp_path.exists():
            raise RuntimeError("截图文件未生成，可能是由于处于纯后台无界面的 Headless 模式导致 OpenGL 渲染被忽略。")
            
        file_path_str = str(temp_path).replace('\\', '/')
        
        from cgi_pipeline.core.receipt import make_receipt
        return make_receipt(
            api_id='blender_capture_viewport',
            status='SUCCESS',
            start_time=t0,
            summary_action="抓取 Blender 活动视口",
            output={'output_path': file_path_str},
            items=[{'name': '截图路径', 'detail': file_path_str}],
            report_content=f"截图已保存至: {file_path_str}\n分辨率: {width}x{height}"
        )

    except Exception as e:
        from cgi_pipeline.core.receipt import make_receipt
        return make_receipt(
            api_id='blender_capture_viewport',
            status='ERROR',
            start_time=t0,
            summary_action="抓取 Blender 视口失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
