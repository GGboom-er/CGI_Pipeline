# 鑷姩褰㈠彉缁ф壙鏂规銆侀獙璇佺粨鏋滀笌涓嬩竴闃舵鍑嗗垯

鏇存柊鏃堕棿: 2026-05-16

鏈枃妗ｈ褰?cdfBaiXingG 闃舵鐨勮嚜鍔ㄥ舰鍙樼户鎵匡紙Automatic Deformation Inheritance锛夌爺绌剁粨璁恒€傚畠涓嶆槸瀹ｄ紶绋匡紝涔熶笉鏄渶缁堜骇鍝佽鏄庯紱瀹冪敤浜庨槻姝㈠悗缁紑鍙戝繕璁拌竟鐣屻€侀噸澶嶈俯鍧戯紝灏ゅ叾瑕佸尯鍒嗏€滅悊璁鸿矾绾库€濃€滃綋鍓嶅凡瀹炶窇鑳藉姏鈥濆拰鈥滀粛闇€浜у搧鍖栫殑鑳藉姏鈥濄€?
## 1. 杈圭晫缁撹

鑳戒弗璋ㄨ窇閫氱殑涓嶆槸鈥滀换鎰忓鎬ā鍨嬨€侀浂鍏堥獙銆佸畬缇庣户鎵夸竴鍒団€濄€傞偅鏄瑺绾︽潫闂锛圲nder-constrained Problem锛夈€?
鏈樁娈电‘璁ゅ彲鎵ц鐨勭洰鏍囨槸锛?
```text
鏃?Rig锛圧igging锛?鈫?鍙噰鏍峰舰鍙樻暟鎹紙Sampled Deformation Data锛?鈫?杩炵画绌洪棿鍦猴紙Continuous Spatial Field锛?鈫?鏂?Mesh 鏌ヨ锛圦uery锛?鈫?鏂?Skin / Corrective / Helper Rig 鍥炲啓锛圕ollapse锛?```

涔熷氨鏄竴涓湁鍏堥獙銆佹湁璇婃柇銆佹湁澶辫触妫€娴嬬殑鑷姩缁戝畾缁ф壙绯荤粺銆傜涓€闃舵浼樺厛绋冲畾 Skin 鏉冮噸锛圫kin Weights锛夛紝鍐嶉€愭鎺ュ叆 BlendShape锛堟贩鍚堝舰鍙橈級鍜?Corrective锛堟牎姝ｅ舰鍙橈級銆?
鏄庣‘涓嶈兘鎵胯锛?
- 闆跺厛楠屻€?- 闆惰鍒欍€?- 闆跺け璐ャ€?- 浠绘剰 kitbash 妯″瀷瀹岀編缁ф壙銆?- 鍙潬娆ф皬鏈€杩戣窛绂昏В鍐冲ご鍙戙€佺潾姣涖€佺墮榻裤€佸彛鑵斻€佸灞傝。鏈嶇瓑杩戝眰绌挎彃闂銆?
鏄庣‘搴旇鎵胯锛?
- 鍦ㄦ棫璧勪骇瀛樺湪鍙噰鏍?Skin / BS / ROM 鐨勫墠鎻愪笅锛岃兘鎶婂舰鍙樿涓鸿浆鎴愬彲鏌ヨ鐨勭┖闂存暟鎹€?- 瀵?body銆乼ight cloth銆乺etopo銆乵erge/split锛屽厛瀹炵幇鍙獙璇佺殑绋冲畾缁ф壙銆?- 瀵硅繎灞傛涔夊尯鍩熻緭鍑?confidence 涓庤瘖鏂紝鑰屼笉鏄洸鐚溿€?- 瀵瑰け璐ュ尯鍩熷繀椤绘湁鍙鐜版祴璇曞満鏅€佽宸寚鏍囧拰涓嬩竴姝ュ喅绛栥€?
## 2. 绗竴鎬у師鐞?
绾挎€ф贩鍚堣挋鐨紙Linear Blend Skinning, LBS锛夊彲浠ュ啓鎴愶細

```text
y(x, pose) = 危_i W_i(x) * T_i(pose) * x
```

鍏朵腑锛?
```text
x          = 闈欐€佺┖闂寸偣
W_i(x)     = 绗?i 鏍归楠煎 x 鐨勬潈閲?T_i(pose)  = 绗?i 鏍归楠煎湪褰撳墠濮挎€佷笅鐨勫彉鎹?y(x, pose) = 鍙樺舰鍚庣殑鐐?```

鎵€浠?Skin 缁ф壙鐨勭涓€鐩爣涓嶆槸澶嶅埗 vertex id锛岃€屾槸棰勬祴杩炵画鏉冮噸鍑芥暟锛?
```text
W_i(x)
```

杩欒В閲婁簡涓轰粈涔堢偣搴忥紙Vertex Order锛夈€佺偣鏁般€乵esh rename銆乵erge/split銆乺etopo銆乁V 鍙樺寲閮戒笉搴旇鎴愪负鏍稿績渚濊禆銆傛纭殑鎶借薄鏄細

```text
world / canonical space point x 鈫?deformation field query
```

鑰屼笉鏄細

```text
old vertex index 鈫?new vertex index
```

璇樊涔熸湁鏄庣‘涓婄晫銆傝嫢鏃ф潈閲嶄负 `W_i(x)`锛岄娴嬫潈閲嶄负 `W_hat_i(x)`锛?
```text
|| y - y_hat ||
= || 危_i (W_i - W_hat_i) T_i x ||
鈮?危_i |W_i - W_hat_i| * ||T_i x||
```

鍥犳鍔ㄧ敾璇樊鐢扁€滄潈閲嶈宸?脳 楠ㄩ鍙樻崲骞呭害鈥濇帶鍒躲€傞獙璇佷笉鑳藉彧闈犺倝鐪硷紝蹇呴』鍚屾椂娴嬶細

- Weight Error锛氭潈閲嶅悜閲忚宸€?- Surface Error锛氬Э鎬佸悗琛ㄩ潰浣嶇疆璇樊銆?- Joint Leakage锛氫笉璇ュ嚭鐜扮殑 influence 娉勬紡銆?- ROM Pose Error锛氬姩浣滆寖鍥村Э鎬佽宸€?
## 3. 澶栭儴渚濇嵁涓庡綋鍓嶅畾浣?
杩欎簺璧勬枡鏀寔鏂瑰悜锛屼絾涓嶇瓑浜庡綋鍓嶄唬鐮佸凡缁忓畬鏁村疄鐜颁簡瀵瑰簲绠楁硶锛?
| 瀛愰棶棰?| 澶栭儴渚濇嵁 | 褰撳墠瀹氫綅 |
|---|---|---|
| Maya 琛ㄩ潰鏉冮噸澶嶅埗 | Autodesk `copySkinWeights` 鎻愪緵 `closestPoint`銆乣rayCast`銆乣closestComponent`銆乣uvSpace` 绛?Surface Association 鏂瑰紡 | 宸查獙璇佽繖浜涙ā寮忎笉鑳藉崟鐙В鍐?cdfBaiXingG 鍢村攪杩戝眰涓叉潈 |
| 绋€鐤忎綋绉〃绀?| OpenVDB锛圫parse Volume Library锛夋敮鎸佺█鐤忎綋绱犮€丼DF/Fog volume銆乵esh/particle 鍒?volume 杞崲 | 宸茶渚濊禆骞跺仛鏈€灏忛獙璇侊紱灏氭湭鎴愪负瀹炴椂涓昏矾寰?|
| 鑴忔ā鍨?inside/outside | Generalized Winding Number锛堝箍涔夌粫鏁帮級閫傚悎 troublesome meshes | libigl 鍙敤锛涘綋鍓嶇敤浜?layer/support 鎬濊矾锛屼粛闇€缂撳瓨鍖?|
| Production 鑴忚鑹茬粦瀹?| Geodesic Voxel Binding 鍙鐞?non-manifold銆乶on-watertight銆乮ntersecting銆乵ulti-component production meshes | 浣滀负 V0.2 support domain 璺嚎锛涘綋鍓嶄富绾夸笉鏄畬鏁?geodesic voxel binding |
| 骞虫粦杩炵画鏉冮噸 | Bounded Biharmonic Weights锛堟湁鐣屽弻璋冨拰鏉冮噸锛孊BW锛? harmonic field | 浣滀负楂樼骇 refinement锛涘綋鍓嶆湭寮哄埗浣跨敤 |
| 涓嶅悓鎷撴墤褰㈠彉杩佺Щ | Deformation Transfer 璇佹槑涓嶅悓 vertex/triangle/connectivity 鍙縼绉伙紝浣嗛渶瑕?correspondence map | 璇佹槑鈥滀笉鍚屾嫇鎵戝彲琛屸€濓紝鍚屾椂璇佹槑璇箟瀵瑰簲涓嶈兘蹇界暐 |
| Pose residual | Pose Space Deformation锛圥SD锛夋妸 pose space 鏄犲皠鍒?local displacement | 鏀寔 Corrective/BS residual 璺嚎 |
| 澶嶆潅褰㈠彉 bake | Dem Bones 鍙粠 animated mesh sequence 姹?skinning model 涓?bone transforms | 鏈潵 runtime-friendly collapse 璺嚎锛涘綋鍓嶆湭鎺ュ叆 |
| BS 杩佺Щ宸ュ叿瀹炶返 | zMayaTools 鐢ㄤ复鏃?wrap deformer 杩佺Щ BS锛孉KEric/skinner 鐢?normal filter 鍜?fallback 浼犳潈閲嶏紝MayaMeshRetarget 浣跨敤 RBF 涓?skin weight clustering | 鏀寔鈥滃嚑浣曞€欓€?+ 娉曠嚎杩囨护 + 鏉冮噸鏍囩 + fallback鈥濈殑缁勫悎璺嚎 |
| 鍙楃害鏉熸潈閲嶅弽姹?| SciPy bounded least squares / NNLS銆丆VXPY quadratic programming 鍙仛闈炶礋銆佹湁鐣屻€佸綊涓€鍜屽钩婊戠害鏉熸眰瑙?| 杩欐槸涓嬩竴闃舵涓荤嚎涔嬩竴锛氱敤 source displacement bank 鍙嶆帹 target skin weights锛涘綋鍓嶅皻鏈骇鍝佸寲 |
| 楂樼疆淇¤浆绉?+ 浣庣疆淇¤ˉ鏉冮噸 | Robust Skin Weights Transfer via Weight Inpainting 浣跨敤鈥滃厛浼犻珮缃俊锛屽啀琛ヤ綆缃俊鍖哄煙鈥濈殑鎬濇兂 | 涓庡綋鍓?preflight risk + inpainting / inverse solve 璺嚎涓€鑷达紝鍙綔涓哄疄鐜板弬鑰?|

瀹炶返鍚庡繀椤讳慨姝ｇ殑鐐癸細

- 鍘熷澶ф柟妗堥噷鐨?VDB/GWN/BBW/Dem Bones 鏄妧鏈矾绾匡紝涓嶆槸褰撳墠宸蹭骇鍝佸寲鑳藉姏銆?- 褰撳墠宸蹭骇鍝佸寲绋嬪害鏈€楂樼殑鏄?owner-filtered Skin 缁ф壙鍜岄儴鍒?Live BS 楠岃瘉閾捐矾銆?- 鍙ｈ厰/鍢村攪杩欑被杩戝眰绌挎彃闂涓嶈兘鍙潬鍑犱綍鏈€杩戠偣銆乁V 鎴?component 杩為€氭€цВ鍐筹紝蹇呴』鍔犲叆鏉冮噸璇箟鏍囩鍜?ROM 杩愬姩璇佹嵁銆?- 褰撳墠宸茬粡楠岃瘉 Maya LBS 鏁板闂悎锛氱粰瀹氭纭?`bindPreMatrix` 閫昏緫绱㈠紩銆乯oint worldMatrix 鍜屾潈閲嶇煩闃碉紝鏁板 LBS 鍙噸寤?Maya skinCluster 杈撳嚭锛涚湡姝ｆ湭闂幆鐨勬槸 source-target correspondence 涓?target 鏉冮噸鏈€浼樺弽姹傘€?
## 4. 褰撳墠瀹炵幇閫昏緫

### 4.1 Skin 褰撳墠涓荤嚎

褰撳墠 `maya_deformation_inherit_skin` 鐨勪富绾挎槸锛?
```text
鏃?Rig mesh + skinCluster 閲囨牱
鈫?鏋勫缓 source groups
鈫?鐩爣 mesh 鎸?owner / component / patch 姹?source subset
鈫?瀵规瘡涓眬閮?subset 鏋勫缓 DeformationField
鈫?鏌ヨ鏉冮噸
鈫?top-k prune / normalize
鈫?鍙€夊啓鍥?skinCluster
鈫?杈撳嚭 diagnostic JSON + weights NPZ
```

鍏抽敭绛栫暐锛?
- 涓嶇敤 vertex id銆?- 涓嶈姹?source/target mesh 鏁伴噺涓€鑷淬€?- 瀵瑰崟婧愭媶澶氱洰鏍囥€佸婧愬悎鍗曠洰鏍囷紝鐢ㄧ┖闂?field 鍜?owner 鍒ゆ柇澶勭悊銆?- disconnected component 鍏堟寜 component 绾у綊灞炪€?- welded 鎴?bridge 鍚?component 鎷嗕笉寮€鏃讹紝杩涘叆 patch-level ownership銆?- 瀵?belt銆乧loth銆乵outh銆乪ye銆乭ead attachment 绛夋晱鎰熷尯鍩熷仛 owner intent 涓?forbidden owner 闄愬埗銆?
### 4.2 DeformationField 褰撳墠鑳藉姏

`core/deformation_field.py` 褰撳墠鍋氱殑鏄?SuperMesh 绾?field锛?
```text
source vertices / faces / weights
鈫?SuperMesh
鈫?closest surface barycentric interpolation
鈫?normal angle filter
鈫?winding/layer filter锛堝婧愭椂锛?鈫?KDTree / IDW fallback
鈫?normalize
```

娉ㄦ剰锛氫唬鐮佹敞閲婁腑鎻愬埌浣撶Н鍖栥€乺ay cast銆佺儹鎵╂暎绛夎兘鍔涳紝浣嗗綋鍓嶆祴璇曡瘉鏄庣湡姝ｇǔ瀹氱殑涓嶆槸鈥滃叏灞€浣撶Н鍦轰竾鑳解€濓紝鑰屾槸锛?
```text
owner-filtered local field + component/patch ownership + 鏉冮噸/ROM 楠岃瘉
```

### 4.3 Live BS 褰撳墠閾捐矾

Live BS锛堝姩鎬?BlendShape锛変笉鑳藉綋鎴愰潤鎬?delta 鐩存帴澶嶅埗銆傚凡纭閾捐矾鏄細

```text
澶栧眰 blendShape target
鈫?澶嶅埗鍑?live target mesh
鈫?杩佺Щ live target skinCluster
鈫?杩佺Щ live target 鍐呴儴 BS 灞炴€?鈫?杩炴帴鍒版柊 body 鐨?inputGeomTarget
鈫?閲嶆柊鎺?driver
鈫?浠ユ帶鍒跺櫒濮挎€侀獙璇佹渶缁堣緭鍑?```

鏂板绾暟鎹伐鍏凤細

```text
compose_live_target_weights(base_weights, live_weights, active_mask)
```

鏍稿績瑙勫垯锛?
- 闈?active 鍖哄煙淇濈暀鏂?body 鏉冮噸銆?- active 鍖哄煙鍏堟竻闆?body 鏉冮噸锛屽啀鍐?live target 鏉冮噸銆?- 杩欐牱閬垮厤 face active 鍖哄煙鍚屾椂淇濈暀 body 鏉冮噸鍜?live 鏉冮噸瀵艰嚧鍗婃薄鏌撱€?
## 5. 鏈湴楠岃瘉璁板綍

娴嬭瘯璧勪骇鐩綍锛?
```text
Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG
```

鍏抽敭鍦烘櫙锛?
| 鍦烘櫙 | 鐢ㄩ€?|
|---|---|
| `ysj_chr_cdfBaiXingG_rig_rigMaster_v007_syncLiveBS_productized.ma` | Live BS 浜у搧鍖栧垵鐗堬紝鏆撮湶 jaw pose 鍢村攪涓叉潈 |
| `ysj_chr_cdfBaiXingG_rig_rigMaster_v008_poseTeethFix.ma` | 鍙ｈ厰 source-only mesh 涓?residual 淇楠岃瘉 |
| `ysj_chr_cdfBaiXingG_rig_rigMaster_v009_romLabelWeightFix.ma` | ROM 杞爣绛句氦鎹㈠悗鐨勬潈閲嶄慨澶嶉獙璇?|
| `ysj_chr_cdfBaiXingG_rig_rigMaster_v011_teethLipRomBarrierFix.ma` | 鍙ｈ厰 composite mesh 缁戝畾琛ラ綈 + ROM 杩愬姩灞忛殰淇 |
| `ysj_chr_cdfBaiXingG_rig_rigMaster_v013_liveSkinMHeadFix.ma` | live target face skin 婧愪慨姝ｏ細`M_Head_base` 鈫?`cdfBaiXingG_body1_live_0` |

鍏抽敭鎶ュ憡锛?
```text
.info\live_bs_productized_sync\v008_pose_teeth_fix_report.json
.info\live_bs_productized_sync\v009_rom_label_weight_fix_report.json
.info\live_bs_productized_sync\v009_rom_lip_swap_tuning.json
.info\live_bs_productized_sync\v010_teeth_lip_barrier_fix_report.json
.info\live_bs_productized_sync\v011_teeth_lip_rom_barrier_fix_report.json
.info\live_bs_productized_sync\v013_live_skin_mhead_fix_report.json
```

### 5.1 Skin 鏉冮噸鍩虹楠岃瘉

宸查€氳繃锛?
- 鏃?rig 鏉冮噸鍦烘煡璇紝21 涓?cache mesh 鍐欏叆 skinCluster锛岄噸寮€澶嶉獙閫氳繃銆?- 16 涓湁鍩虹嚎 mesh 鍚堟垚 LBS ROM 鍏ㄩ儴 PASS銆?- 5 涓柊澧?mesh 鏃?ground truth锛屼絾浜х敓闈為浂杩愬姩锛屽苟杈撳嚭鍙В閲?owner銆?- 鏉冮噸鍚戦噺 / 鏉冮噸浜戦獙璇?PASS銆?- 鐐瑰簭鎵撲贡鍚庨噸閲囨牱璇樊涓?0銆?- split銆乵erge銆佸瓙閲囨牱娴嬭瘯 PASS銆?- 鍗曟簮 body 鎷嗗涓洰鏍?mesh PASS銆?- disconnected merge PASS銆?- welded 澶氳涔?mesh 鍒濆澶辫触锛屽姞鍏?patch-level ownership 鍚?PASS銆?
纭缁撹锛?
```text
Skin 鐨勪富璺緞鍙墽琛屻€?鍗?mesh 鈫?澶?mesh 鐨勬暟閲忓彉鍖栦笉鏄牴闂銆?鐪熸椋庨櫓鍦ㄨ繎灞傜┛鎻掋€佸璇箟杩炵画闈€佹棤鍏堥獙鏂板鐗╀綋銆?```

### 5.2 琚瘉浼垨涓嶈冻鐨勬柟妗?
鍦?cdfBaiXingG 鍢村攪/鍙ｈ厰鍖哄煙锛屼互涓嬫柟妗堜笉鑳藉崟鐙В鍐抽棶棰橈細

- 鐩存帴 component ordinal mapping銆?- Maya `copySkinWeights` 鐨?`closestPoint` / `rayCast` / `closestComponent`銆?- UV transfer銆?- Base body skin 鏇挎崲銆?- 涓ユ牸閲嶅績鎻掑€?+ normal angle filter銆?- 鍙寜 connected component 杩囨护銆?- 鍗曠函 suppress ROM error mask銆?
缁撹锛?
```text
鍑犱綍鍊欓€夊彲浠ュ噺灏戦敊璇紝浣嗕笉鑳藉喅瀹氳涔?owner銆?鍢村攪涓婁笅灞傜┛鎻掓椂锛岀┖闂翠綅缃€乁V銆佹硶绾裤€佽繛閫氶潰閮藉彲鑳界粰鍑洪敊璇瓟妗堛€?```

### 5.3 Live BS 楠岃瘉

宸查€氳繃锛?
- 鍚堟垚 live target锛氱‘璁?`inputGeomTarget`銆乴ive mesh skin 涓?delta 鑳藉畬鏁撮噰闆嗐€?- `v004_deformSkinBS_clean.ma`锛歜ody 鍙湁鍗曚竴 `M_Head_base` BS锛寃orld-space 寮€鍏?delta 涓庨噰鏍?delta 瀵归綈銆?- `v005/v006`锛氬鍒?`M_Head_base` live target锛岃縼绉?live target skin锛岃繛鎺ュ埌鏂?body `inputGeomTarget`銆?- `v006_deformSkinBS_liveFull.ma`锛氳ˉ榻?124 涓唴閮?BS 灞炴€с€?- 澶栧眰/鍐呴儴鎶芥牱婵€娲昏宸?`l2_max <= 7.64e-06`銆?- active 鏉冮噸棰濆 joint 璐ㄩ噺 `max = 3.33e-16`銆?
杩欎簺楠岃瘉璇存槑锛?
```text
鍔ㄦ€?BS 鐨勭粨鏋勫鍒堕摼璺彲琛屻€?鍙 source/target 瀵瑰簲姝ｇ‘锛孊S 婵€娲诲悗鐨?world-space 褰㈡€佸彲浠ュ榻愩€?```

### 5.4 Jaw 鎺у埗鍣ㄧ湡瀹炲Э鎬佹毚闇茬殑闂

鐪熷疄鎺у埗鍣ㄦ祴璇曪細

```text

（§5 本地验证记录已移至 `docs/archive/history/deformation_inheritance_validation_log.md`）

褰撳墠绠楁硶閾撅細

```text
source skin weights
鈫?joint family score
鈫?source support island
鈫?target seed label
鈫?target topology/geodesic propagation
鈫?family-restricted source candidate query
鈫?normalized transferred weights
```

宸查獙璇佺殑鏈€灏忓帇鍔涙祴璇曪細

```text
tests/test_topology_support_matcher.py
```

娴嬭瘯鏋勯€犱笂涓嬩袱鏉＄┖闂磋窛绂诲緢杩戠殑甯︾姸闈紝骞舵晠鎰忚 lower target 鍦ㄦ姘忚窛绂讳笂鏇撮潬杩?upper source锛?
- naive KDTree 鏈€杩戠偣浼氭妸 lower strip 褰掑埌 upper銆?- `TopologySupportMatcher` 閫氳繃鎵嬪姩/鑷姩 seed 鍜?target 鎷撴墤浼犳挱锛屾妸鏈?seed 鐨?lower 椤剁偣涔熼檺鍒跺湪 `lower_lip` source support 鍐呫€?- 杈撳嚭鏉冮噸褰掍竴鍖栵紝source support island 鏁伴噺姝ｇ‘銆?
褰撳墠杈圭晫锛?
- 杩欐槸绂荤嚎鏍稿績妯″潡锛屽皻鏈帴鍏?`maya_sync_rig_incremental` 鎴栧綋鍓?Maya 鍦烘櫙鍐欏洖銆?- cdfBaiXingG 鐨勭湡瀹?`M_Head_base -> cdfBaiXingG_body1_live_0` 浠嶉渶涓嬩竴姝ュ鍑?neutral / jaw pose / motion delta 鏁版嵁鍚庤窇璇婃柇銆?- 鐪熷疄璧勪骇閫氳繃鏍囧噯蹇呴』鐪?Jaw pose world-space error 鍜屼綆缃俊鐐规姤鍛婏紝涓嶈兘鍙湅鍚堟垚娴嬭瘯銆?
2026-05-14 鐪熷疄璧勪骇绂荤嚎璇婃柇锛?
```text
鏁版嵁瀵煎嚭: .info/topology_support_matcher/mhead_to_live_v019_data.npz
棣栬疆鎶ュ憡: .info/topology_support_matcher/mhead_to_live_v019_matcher_report.json
ROI鎶ュ憡:  .info/topology_support_matcher/mhead_to_live_v020_roi_topology_report.json
绐凴OI鎶ュ憡: .info/topology_support_matcher/mhead_to_live_v021_lipjaw_only_report.json
```

宸茬‘璁わ細

- MCP 鍓嶅彴瀵煎嚭鎴愬姛锛屽満鏅负 `v018_liveMouthErrorSkinPatch.ma`锛屽鍑哄悗鎭㈠ `M_Jaw_A_ctrl.rotateX = 36.99733300328051`銆?- `M_Head_base`锛?238 椤剁偣銆?8354 涓夎闈€?09 influence銆?- `cdfBaiXingG_body1_live_0`锛?8925 椤剁偣銆?7530 涓夎闈€?94 influence銆?- `TopologySupportMatcher` 鑳芥娊鍑?upper/lower/jaw/head 绛?source support island銆?- v019 鍏ㄥ眬 auto seed 澶縺杩涳紝鍑犱箮鍏ㄨ韩鎵撶瀛愶紝涓嶈兘浣滀负鍐欏洖渚濇嵁銆?- v020 ROI 鍖呭惈 cheek/head 鍚庡嚭鐜版嫇鎵戞爣绛捐繃鎵╂暎锛宮otion error 鍙樺樊銆?- v021 lip/jaw-only ROI 鏀剁獎鍚庝粛涓嶈兘鐩存帴鍐欙細24 涓?upper鈫抣ower/jaw 璇箟鍊欓€変腑锛屽彧鏈?2 涓悓鏃舵弧瓒?`matcher_motion_error < naive_motion_error`銆?
2026-05-14 鍙傛暟鍥炲綊锛?
```text
quick鎶ュ憡: .info/topology_support_matcher/topology_param_regression_quick.json
full鎶ュ憡:  .info/topology_support_matcher/topology_param_regression_full.json
CSV鏄庣粏:   .info/topology_support_matcher/topology_param_regression_full.csv
```

鍥炲綊鍙ｅ緞锛?
```text
鍥哄畾 neutral/jaw25 绂荤嚎鏍锋湰锛屼笉鍐?Maya 鍦烘櫙銆?姣忕粍鍙傛暟姣旇緝 naive 鏈€杩戠偣鏄犲皠涓?TopologySupportMatcher 鏄犲皠鐨?Jaw pose motion error銆?鍙湁 confidence銆佽涔夊垏鎹€乻ource support銆乵otion improvement 鍚屾椂閫氳繃鐨勭偣鎵嶇畻鍙啓鍊欓€夈€?```

full sweep 鍏?207 缁勶紝鍏ㄩ儴鎴愬姛銆傛渶浣冲弬鏁伴珮搴﹂泦涓細

```text
support_threshold = 0.25
seed_score        = 0.60
k                 = 8
motion_weight     = 8.0
roi_score         = 0.28
roi_bbox_pad      = 0.90
gate_confidence   = 0.50
gate_min_improve  = 0.02
gate_max_error    = 0.25
```

鏈€浣崇粨鏋滐細

```text
ROI vertex count        = 2790
semantic change count   = 42
accepted write candidate= 13
risky changed count     = 29
worse changed count     = 20
naive mean error        = 0.03708
matcher mean error      = 0.03102
matcher p95 error       = 0.18660
```

鍙傛暟缁撹锛?
```text
鍙傛暟涓嶆槸瀹屽叏鏃犳晥锛屼綆 support threshold + k=8 + 楂?motion_weight 鑳芥敼鍠勪竴灏忔壒鐐广€?浣嗗畨鍏ㄥ彲鍐欎笂闄愬彧鏈?13 鐐癸紝鏃犳硶瑙ｉ噴鎴栦慨澶嶆暣鍦堝槾鍞囧紶鍢寸矘杩炪€?鍥犳褰撳墠闂涓嶈兘缁х画褰掑洜浜庘€滃弬鏁版病璋冨ソ鈥濄€?涓嬩竴闃舵蹇呴』妫€鏌?topology/BS 琛ㄦ儏鐩爣/鍔ㄦ€?live target 鏄惁缂哄皯瓒冲鐨勪笂涓嬪攪鍒嗙琛ㄨ揪锛屾垨闇€瑕侀澶?pose residual 绾︽潫銆?```

2026-05-14 閫愮偣鍊欓€夊璁★細

```text
閫愮偣JSON: .info/topology_support_matcher/topology_candidate_audit_best.json
閫愮偣CSV:  .info/topology_support_matcher/topology_candidate_audit_best.csv
鍙鍖朜PZ: .info/topology_support_matcher/topology_candidate_audit_best.npz
```

鍦ㄦ渶浣冲弬鏁颁笅澶嶇幇锛?
```text
ROI vertex count       = 2790
semantic change count  = 42
accepted count         = 13
strict write count     = 11
soft accepted count    = 2
risky count            = 29
worse count            = 20
```

鏇翠弗鏍肩殑鍐欏洖瑙勫垯锛?
```text
status == STRICT_ACCEPTED
improvement >= 0.10
weight_l1_current_to_matcher >= 0.40
```

瀹為檯 strict 鍐欏洖鍊欓€夛細

```text
11 涓偣锛屽叏閮ㄤ负 upper_lip -> lower_lip銆?绌洪棿浣嶇疆闆嗕腑鍦ㄥ乏鍙冲槾瑙掗檮杩戙€?2 涓?lower_lip -> jaw 鍙畻 SOFT_ACCEPTED锛屽洜涓?improvement 绾?0.03銆亀eight_l1 绾?0.167锛屾殏涓嶅啓銆?```

鏂扮殑鍏抽敭鍒ゆ柇锛?
```text
楂?naive motion error 涓嶇瓑浜?target 鏉冮噸閿欒銆?鏈疆鍙戠幇 naive_error > 0.5 鐨?36 涓偣鍏ㄩ儴鏄?upper_lip -> upper_lip 鍚屽鏃忥紝
matcher 鍙壘鍒版洿濂界殑 source sample锛屼絾 current target 鏉冮噸涓?matcher 鏉冮噸 L1 鍧囧€煎彧鏈?0.0518銆?杩欑被鐐硅鏄庘€滄渶杩戞簮鏍锋湰閫夐敊鈥濓紝涓嶆槸鈥滃簲璇ユ敼 target skin鈥濄€?```

鏂板纭鍒欙細

```text
璇箟鍒囨崲鍙槸鍊欓€夛紝涓嶆槸鍐欏洖璁稿彲銆?鍐欏洖鍓嶅繀椤婚€氳繃 motion gate锛?  confidence 瓒冲楂?  source support family 鍚堟硶
  matcher Jaw pose motion error 浼樹簬褰撳墠/naive 鏄犲皠
  浣庣疆淇″尯鍩熷彧杈撳嚭鎶ュ憡锛屼笉鍐欐潈閲?```

鍥犳褰撳墠闃舵缁撹鏄細

```text
鎷撴墤鏀拺鍩熸柟鍚戞湁鏁堬紝浣嗙湡瀹炶祫浜у繀椤诲彔鍔?motion gate銆?涓嶈兘鎶?v021 鐨勫叏閮?predicted label 鍐欏洖 skin銆?涓嬩竴姝ュ彧鍏佽鍙鍖?璇曞啓 STRICT_ACCEPTED 鐨?11 涓偣銆?鍏朵綑鍊欓€夊彧鑳戒綔涓哄け璐ヨ瘖鏂紝杞幓妫€鏌?Live BS / 琛ㄦ儏鎷撴墤 / residual 琛ㄨ揪銆?```

2026-05-14 strict11 Maya 鍓嶅彴璇曞啓澶嶆牳锛?
```text
娴嬭瘯鍦烘櫙:
Y:/GGbommer/scripts/CGI_Pipeline/projects/ysj/20260513_193837_cdfbaixingG/ysj_chr_cdfBaiXingG_rig_rigMaster_v019_strict11SkinTest.ma

鍐欏叆瀵硅薄:
cdfBaiXingG_body1_live_0_skinCluster

鍐欏叆鐐?
11 涓?STRICT_ACCEPTED 鐐?
鍘熸潈閲嶅浠?
.info/topology_support_matcher/strict11_original_weights_before_write.json

鍐欏叆 payload:
.info/topology_support_matcher/topology_candidate_audit_best_strict_write_payload.json

澶嶆牳鎶ュ憡:
.info/topology_support_matcher/strict11_write_impact_measurement.json
```

MCP 鍓嶅彴澶嶆牳缁撴灉锛?
```text
M_Jaw_A_ctrl.rotateX                     = 36.99733300328051
current_payload_max_abs_diff_before_measure = 2.4477057603000674e-09
after_payload_max_abs_diff                  = 2.4477057603000674e-09

（§8.4 以下的详细实验迭代日志已移至 `docs/archive/history/deformation_inheritance_validation_log.md`）

- Unreal Chaos Cloth Transfer Skin Weights Method锛歔Epic Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/ChaosClothAssetTransferSkinWeightsMethod?application_version=5.6)
