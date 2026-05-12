import sys
import maya.cmds as cmds
import maya.utils as mu
import maya.api.OpenMaya as om
from PySide6 import QtWidgets, QtCore, QtGui

# --- STYLE SHEET ---
MODERN_STYLE = """
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 14px; /* Base font size increased */
    color: #E0E0E0;
    background-color: #2B2B2B;
}

QGroupBox {
    border: 1px solid #444;
    border-radius: 6px;
    margin-top: 22px;
    font-weight: bold;
    font-size: 15px; 
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    color: #AAA;
}

QPushButton {
    background-color: #444;
    border-radius: 4px;
    padding: 6px 12px;
    border: 1px solid #555;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #555;
    border-color: #777;
}

QPushButton:pressed {
    background-color: #333;
}

QPushButton#BtnReset {
    background-color: #883333;
    border-color: #994444;
}
QPushButton#BtnReset:hover {
    background-color: #AA4444;
}

QListWidget {
    background-color: #222;
    border: 1px solid #333;
    border-radius: 4px;
    outline: 0;
}

QListWidget::item {
    padding: 4px;
    margin: 1px;
    border-radius: 2px;
}

QListWidget::item:selected {
    background-color: #DDAA33; /* Orange Highlight */
    color: #111;
    font-weight: bold;
}

QListWidget::item:hover:!selected {
    background-color: #333;
}

QSlider::groove:horizontal {
    border: 1px solid #333;
    height: 6px;
    background: #111;
    margin: 2px 0;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #DDAA33;
    border: 1px solid #DDAA33;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QDoubleSpinBox {
    background-color: #1A1A1A;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 2px;
    selection-background-color: #DDAA33;
    selection-color: #000;
}

QLabel#MonitorLabel {
    color: #888;
    font-size: 12px;
    font-style: italic;
}
"""

def get_maya_window():
    try:
        for w in QtWidgets.QApplication.topLevelWidgets():
            if w.objectName() == 'MayaWindow':
                return w
    except:
        pass
    return None

class BlendShapeModel:
    def __init__(self):
        self.bs_node = None 
        self.fn_node = None
        self.obj = None
        
        self._original_data_cache = {} 
        self._current_scales = {}
        
        self._callback_ids = []
        self._ui_update_func = None 
        
        self._weight_attr_obj = None
        self._dirty_indices = set()
        self._update_pending = False
        
    def cleanup(self):
        self.stop_monitoring()
        self._original_data_cache = {}
        self._current_scales = {}
        
    def load_node_from_selection(self):
        sel = cmds.ls(sl=True, type="transform")
        if not sel:
            return False, "请选择一个模型 (Mesh)"
        
        shapes = cmds.listRelatives(sel[0], shapes=True, fullPath=True)
        if not shapes:
            return False, "未找到形状节点"
            
        history = cmds.listHistory(shapes[0])
        bs_nodes = cmds.ls(history, type="blendShape")
        
        if not bs_nodes:
            return False, "无 BlendShape 节点"
            
        self.set_node(bs_nodes[0])
        return True, self.fn_node.name()

    def set_node(self, bs_node_name):
        self.stop_monitoring()
        
        self.bs_node = bs_node_name 
        self._original_data_cache = {}
        self._current_scales = {}
        self._weight_attr_obj = None
        self._dirty_indices = set()
        self._update_pending = False
        
        sel_list = om.MSelectionList()
        sel_list.add(bs_node_name)
        self.obj = sel_list.getDependNode(0)
        self.fn_node = om.MFnDependencyNode(self.obj)
        
        try:
            plug = self.fn_node.findPlug("weight", False)
            self._weight_attr_obj = plug.attribute()
        except:
            pass

    def start_monitoring(self, ui_update_func):
        if self._callback_ids:
            return
        
        self._ui_update_func = ui_update_func
        
        # 1. 监听值改变 (手动 Set)
        cb_id1 = om.MNodeMessage.addAttributeChangedCallback(
            self.obj, 
            self._attribute_changed_handler,
            None
        )
        self._callback_ids.append(cb_id1)
        
        # 2. 监听脏插头 (连接驱动)
        cb_id2 = om.MNodeMessage.addNodeDirtyPlugCallback(
            self.obj,
            self._dirty_plug_handler,
            None
        )
        self._callback_ids.append(cb_id2)

    def _attribute_changed_handler(self, msg, plug, other_plug, client_data):
        if not (msg & (om.MNodeMessage.kAttributeSet | om.MNodeMessage.kAttributeEval)):
            return
        self._check_and_mark_dirty(plug)

    def _dirty_plug_handler(self, node, plug, client_data):
        self._check_and_mark_dirty(plug)

    def _check_and_mark_dirty(self, plug):
        try:
            current_attr = plug.attribute()
            if plug.isElement:
                # 尝试匹配数组定义的 Attribute
                array_attr = plug.array().attribute()
                if array_attr == self._weight_attr_obj:
                    self._mark_dirty(plug.logicalIndex())
                    return
            
            if current_attr == self._weight_attr_obj:
                self._mark_dirty(plug.logicalIndex())
        except:
            pass

    def _mark_dirty(self, idx):
        self._dirty_indices.add(idx)
        if not self._update_pending:
            self._update_pending = True
            mu.executeDeferred(self._process_dirty_indices)

    def _process_dirty_indices(self):
        self._update_pending = False
        if not self._dirty_indices:
            return
            
        indices_to_process = list(self._dirty_indices)
        self._dirty_indices.clear()
        
        try:
            weight_plug = self.fn_node.findPlug("weight", False)
            for idx in indices_to_process:
                try:
                    w_plug = weight_plug.elementByLogicalIndex(idx)
                    val = w_plug.asDouble() # Force Eval
                    if self._ui_update_func:
                        self._ui_update_func(idx, val)
                except:
                    pass
        except:
            pass

    def stop_monitoring(self):
        for cb_id in self._callback_ids:
            try:
                om.MMessage.removeCallback(cb_id)
            except:
                pass
        self._callback_ids = []
        self._ui_update_func = None
        self._dirty_indices.clear()
        self._update_pending = False

    def get_target_info(self, index):
        """
        获取 Target 信息，使用真实属性名 (e.g., 'smile')
        """
        try:
            weight_plug = self.fn_node.findPlug("weight", False)
            try:
                w_plug = weight_plug.elementByLogicalIndex(index)
                weight = w_plug.asDouble()
                
                # 获取真实属性名
                # MPlug.partialName(useLongNames=True) 返回 "weight[0]"
                # 我们需要别名，或者短名。
                # MPlug.name() 返回 "blendShape1.smile" (如果有别名)
                
                full_name = w_plug.name() # node.attribute
                if "." in full_name:
                    target_name = full_name.split(".")[-1]
                else:
                    target_name = full_name
                    
                # 如果没有别名，显示为 weight[i]，稍微美化一下
                if target_name.startswith("weight["):
                    target_name = f"Target {index}"
                    
            except:
                return None 

            scale = self._current_scales.get(index, 1.0)
            return (index, target_name, weight, scale)
            
        except Exception:
            return None

    def get_all_targets(self):
        indices = []
        try:
            weight_plug = self.fn_node.findPlug("weight", False)
            indices = weight_plug.getExistingArrayAttributeIndices()
        except:
            pass
        return indices

    def _get_points_plug(self, target_index, item_index=6000):
        try:
            input_target_plug = self.fn_node.findPlug("inputTarget", False)
            geom_indices = input_target_plug.getExistingArrayAttributeIndices()
            if not geom_indices: return None
            
            geom_idx = geom_indices[0] 
            
            input_target_group_plug = input_target_plug.elementByLogicalIndex(geom_idx).child(0)
            target_plug = input_target_group_plug.elementByLogicalIndex(target_index)
            target_item_array_plug = target_plug.child(0)
            item_plug = target_item_array_plug.elementByLogicalIndex(item_index)
            
            for k in range(item_plug.numChildren()):
                child = item_plug.child(k)
                if om.MFnAttribute(child.attribute()).name == "inputPointsTarget":
                    return child
        except:
            pass
        return None

    def _cache_target_data(self, target_index):
        if target_index in self._original_data_cache: return True
        points_plug = self._get_points_plug(target_index)
        if points_plug:
            data_handle = points_plug.asMDataHandle()
            if not data_handle.data().isNull():
                pts = om.MFnPointArrayData(data_handle.data()).array()
                self._original_data_cache[target_index] = om.MPointArray(pts) 
                self._current_scales[target_index] = 1.0
                return True
        return False

    def set_target_scale(self, target_index, scale):
        if target_index not in self._original_data_cache:
            if not self._cache_target_data(target_index): return False
        
        if abs(self._current_scales.get(target_index, 1.0) - scale) < 0.0001:
            return True

        original_points = self._original_data_cache[target_index]
        new_points = om.MPointArray()
        new_points.setLength(len(original_points))
        
        for i in range(len(original_points)):
            pt = original_points[i]
            new_points[i] = om.MPoint(pt.x * scale, pt.y * scale, pt.z * scale, pt.w)

        points_plug = self._get_points_plug(target_index)
        if points_plug:
            points_plug.setMObject(om.MFnPointArrayData().create(new_points))
            self._current_scales[target_index] = scale
            return True
        return False

    def get_cached_scale(self, target_index):
        return self._current_scales.get(target_index, 1.0)


class BlendShapeScalerUI(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BlendShape 批量倍率工具 (Pro)")
        self.resize(480, 700)
        self.setWindowFlags(QtCore.Qt.Window)
        
        # Apply Modern Style
        self.setStyleSheet(MODERN_STYLE)
        
        self.model = BlendShapeModel()
        self.is_updating_ui = False
        
        self.init_ui()
        self.connect_signals()
        
    def closeEvent(self, event):
        # 彻底清理
        self.model.cleanup()
        super().closeEvent(event)
        
    def init_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # --- Top: Load ---
        top_group = QtWidgets.QGroupBox("节点加载")
        top_layout = QtWidgets.QHBoxLayout(top_group)
        self.btn_load = QtWidgets.QPushButton("加载选中模型")
        self.btn_load.setMinimumHeight(36)
        self.lbl_node = QtWidgets.QLabel("未加载")
        self.lbl_node.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_node.setStyleSheet("color: #777; font-weight: bold;")
        top_layout.addWidget(self.btn_load)
        top_layout.addWidget(self.lbl_node)
        main_layout.addWidget(top_group)
        
        # --- Filter ---
        filter_group = QtWidgets.QGroupBox("监控与筛选")
        filter_layout = QtWidgets.QVBoxLayout(filter_group)
        
        thresh_layout = QtWidgets.QHBoxLayout()
        thresh_lbl = QtWidgets.QLabel("权重显示阈值:")
        thresh_lbl.setFixedWidth(100)
        thresh_layout.addWidget(thresh_lbl)
        
        self.spin_threshold = QtWidgets.QDoubleSpinBox()
        self.spin_threshold.setRange(0.0, 1.0)
        self.spin_threshold.setSingleStep(0.1)
        self.spin_threshold.setValue(1.0) 
        self.spin_threshold.setFixedWidth(70)
        thresh_layout.addWidget(self.spin_threshold)
        
        self.slider_threshold = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_threshold.setRange(0, 100)
        self.slider_threshold.setValue(100) 
        thresh_layout.addWidget(self.slider_threshold)
        
        filter_layout.addLayout(thresh_layout)
        
        self.lbl_monitor_status = QtWidgets.QLabel("等待加载...")
        self.lbl_monitor_status.setObjectName("MonitorLabel")
        self.lbl_monitor_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        filter_layout.addWidget(self.lbl_monitor_status)
        
        main_layout.addWidget(filter_group)
        
        # --- List ---
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setSpacing(2)
        main_layout.addWidget(self.list_widget)
        
        # --- Edit ---
        edit_group = QtWidgets.QGroupBox("变形倍率控制")
        edit_layout = QtWidgets.QVBoxLayout(edit_group)
        
        scale_layout = QtWidgets.QHBoxLayout()
        scale_lbl = QtWidgets.QLabel("倍率:")
        scale_lbl.setFixedWidth(40)
        scale_layout.addWidget(scale_lbl)
        
        self.slider_scale = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_scale.setRange(0, 200) 
        self.slider_scale.setValue(100)    
        scale_layout.addWidget(self.slider_scale)
        
        self.spin_scale = QtWidgets.QDoubleSpinBox()
        self.spin_scale.setRange(0.0, 50.0) 
        self.spin_scale.setSingleStep(0.1)
        self.spin_scale.setValue(1.0)
        self.spin_scale.setFixedWidth(70)
        scale_layout.addWidget(self.spin_scale)
        
        edit_layout.addLayout(scale_layout)
        
        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_reset = QtWidgets.QPushButton("重置选中项 (Reset)")
        self.btn_reset.setObjectName("BtnReset")
        self.btn_reset.setMinimumHeight(32)
        
        btn_layout.addWidget(self.btn_reset)
        edit_layout.addLayout(btn_layout)
        
        self.lbl_sel_info = QtWidgets.QLabel("请选择 Target")
        self.lbl_sel_info.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_sel_info.setStyleSheet("color: #666; margin-top: 5px;")
        edit_layout.addWidget(self.lbl_sel_info)
        
        main_layout.addWidget(edit_group)
        self.edit_group_widget = edit_group
        self.edit_group_widget.setEnabled(False)
        
    def connect_signals(self):
        self.btn_load.clicked.connect(self.on_load_click)
        
        self.slider_threshold.valueChanged.connect(
            lambda v: self.spin_threshold.setValue(v / 100.0))
        self.spin_threshold.valueChanged.connect(
            lambda v: self.slider_threshold.setValue(int(v * 100)))
        
        self.slider_threshold.valueChanged.connect(self.force_refresh_list)
        
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        
        self.slider_scale.valueChanged.connect(self.on_slider_change)
        self.spin_scale.editingFinished.connect(self.on_spin_change)
        
        self.btn_reset.clicked.connect(self.on_reset_click)
        
    def on_load_click(self):
        success, msg = self.model.load_node_from_selection()
        if success:
            self.lbl_node.setText(f"{msg}")
            self.lbl_node.setStyleSheet("color: #4CAF50; font-weight: bold;")
            self.model.start_monitoring(self.on_node_attribute_changed)
            self.lbl_monitor_status.setText("🟢 实时监控中")
            self.force_refresh_list()
        else:
            self.lbl_node.setText(f"{msg}")
            self.lbl_node.setStyleSheet("color: #F44336;")
            self.list_widget.clear()
            self.edit_group_widget.setEnabled(False)
            self.lbl_monitor_status.setText("⚪ 未激活")

    def on_node_attribute_changed(self, idx, new_weight):
        threshold = self.spin_threshold.value()
        existing_item = None
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(QtCore.Qt.UserRole) == idx:
                existing_item = item
                break
        
        if new_weight >= threshold - 0.0001:
            if existing_item:
                info = self.model.get_target_info(idx)
                if info:
                    _, name, weight, scale = info
                    item_text = f"{name}  (w:{weight:.2f})  [x{scale:.2f}]"
                    existing_item.setText(item_text)
            else:
                info = self.model.get_target_info(idx)
                if info:
                    self._add_list_item(info)
        else:
            if existing_item:
                row = self.list_widget.row(existing_item)
                self.list_widget.takeItem(row)

    def force_refresh_list(self):
        self.list_widget.clear()
        threshold = self.spin_threshold.value()
        all_indices = self.model.get_all_targets()
        
        for idx in all_indices:
            info = self.model.get_target_info(idx)
            if info and info[2] >= threshold - 0.0001:
                self._add_list_item(info)
        self.on_selection_changed()

    def _add_list_item(self, info):
        idx, name, weight, scale = info
        item_text = f"{name}  (w:{weight:.2f})  [x{scale:.2f}]"
        item = QtWidgets.QListWidgetItem(item_text)
        item.setData(QtCore.Qt.UserRole, idx)
        item.setToolTip(f"目标: {name}\n当前权重: {weight:.3f}\n当前倍率: {scale}")
        self.list_widget.addItem(item)

    def on_selection_changed(self):
        selected_items = self.list_widget.selectedItems()
        count = len(selected_items)
        
        if count == 0:
            self.edit_group_widget.setEnabled(False)
            self.lbl_sel_info.setText("请选择 Target")
            return
            
        self.edit_group_widget.setEnabled(True)
        self.lbl_sel_info.setText(f"已选中 {count} 项")
        
        last_idx = selected_items[-1].data(QtCore.Qt.UserRole)
        scale = self.model.get_cached_scale(last_idx)
        
        self.is_updating_ui = True
        self.slider_scale.setValue(min(200, int(scale * 100)))
        self.spin_scale.setValue(scale)
        self.is_updating_ui = False

    def on_slider_change(self, value):
        if self.is_updating_ui: return
        scale = value / 100.0
        self.is_updating_ui = True
        self.spin_scale.setValue(scale)
        self.is_updating_ui = False
        self.apply_batch_scale(scale)

    def on_spin_change(self):
        if self.is_updating_ui: return
        scale = self.spin_scale.value()
        self.is_updating_ui = True
        clamped_val = min(200, max(0, int(scale * 100)))
        self.slider_scale.setValue(clamped_val)
        self.is_updating_ui = False
        self.apply_batch_scale(scale)

    def on_reset_click(self):
        self.slider_scale.setValue(100) 

    def apply_batch_scale(self, scale):
        selected_items = self.list_widget.selectedItems()
        if not selected_items: return
        for item in selected_items:
            idx = item.data(QtCore.Qt.UserRole)
            if self.model.set_target_scale(idx, scale):
                text = item.text()
                # 智能替换 [x1.00] 部分
                if " [x" in text:
                    prefix = text.split(" [x")[0]
                    new_text = f"{prefix} [x{scale:.2f}]"
                    item.setText(new_text)

def show_ui():
    global bs_scaler_ui
    try:
        bs_scaler_ui.close()
        bs_scaler_ui.deleteLater()
    except:
        pass
    
    parent_win = get_maya_window()
    bs_scaler_ui = BlendShapeScalerUI(parent_win)
    bs_scaler_ui.show()

if __name__ == "__main__":
    show_ui()