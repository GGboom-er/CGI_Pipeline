# 自动形变继承验证迭代日志

> 本文件从 `docs/architecture/deformation_inheritance_plan_and_validation.md` 拆分而来。
> 包含 §5 本地验证记录和 §8 测试准则中的详细实验迭代日志。
> 规范和决策请看原文件。

```text
M_Jaw_A_ctrl.rotateX = 25
M_Head_base_M_Jaw_Open driver 鈮?0.8333333
```

`v007` 鍩虹嚎璇樊锛?
| 鎸囨爣 | 鏁板€?|
|---|---:|
| body jaw error mean | 0.0041917786 cm |
| body jaw error p95 | 0.0047757517 cm |
| body jaw error max | 2.4614610556 cm |
| max vertex | 9131 |

璇婃柇缁撹锛?
- 鍏抽棴鍐呴儴 BS 鍚庤宸粛鎺ヨ繎 `2.46 cm`锛岃鏄庡唴閮?BS 涓嶆槸涓诲洜銆?- 鍏抽棴 live skin 鍚庤宸害 `0.0044 cm`锛岃鏄?live target skin 鏉冮噸鏄富鍥犮€?- 鏈€澶ч敊璇偣棰勬湡杩愬姩绾?`2.83 cm`锛屽疄闄呯洰鏍囪繍鍔ㄧ害 `0.37 cm`锛屽睘浜?under-moving銆?- 閿欒鐐规潈閲?top owner 钀藉湪 UpLip锛岃€屽疄闄呴渶瑕?lower/jaw 杩愬姩锛岀‘璁や笂涓嬪攪 owner 浜掍覆銆?
### 5.5 v008 缁撴灉

`v008_poseTeethFix.ma` 鐢?residual calibration 鍜?source-only 鍙ｈ厰 mesh 缁戝畾淇瑙嗚闂銆?
鏈€缁?jaw pose body锛?
| 鎸囨爣 | 鏁板€?|
|---|---:|
| error mean | 0.0000106064 cm |
| error p95 | 0.0000082619 cm |
| error max | 0.0156823407 cm |

鍙ｈ厰 mesh锛?
| mesh | error max |
|---|---:|
| lower teeth | 0.0230119259 cm |
| tongue | 0.0184006452 cm |
| upper teeth | 鎶ュ憡涓椤归噰鏍峰嚭閿欙紝闇€鍚庣画淇楠岃瘉鑴氭湰 |

鍒ゆ柇锛?
```text
v008 瑙嗚鏁堟灉鎺ヨ繎閫氳繃锛屼絾瀹冧笉鏄函鏉冮噸淇銆?瀹冭瘉鏄?residual/source-only 鍙ｈ厰閾捐矾鑳芥晳鏁堟灉锛屼絾涓嶈兘鏇夸唬 Skin 褰掑睘绠楁硶銆?```

### 5.6 v009 缁撴灉

`v009_romLabelWeightFix.ma` 浣跨敤 ROM 杩愬姩宸紓鍋氫笂涓嬪攪杞爣绛句氦鎹€?
鏈€浣冲弬鏁帮細

```text
threshold = 0.28 cm
ratio = 0.45
iterations = 2
trial_count = 45
```

鍩虹嚎锛?
| 鎸囨爣 | 鏁板€?|
|---|---:|
| error mean | 0.0041917786 cm |
| error p95 | 0.0047757517 cm |
| error max | 2.4614610556 cm |

淇鍚庯細

| 鎸囨爣 | 鏁板€?|
|---|---:|
| error mean | 0.0019672369 cm |
| error p95 | 0.0047726105 cm |
| error max | 0.5281665876 cm |

閲嶅紑鍦烘櫙鍚庢暟鍊间竴鑷达細

```text
reopen_post.error.max = 0.5281665876 cm
```

鍒ゆ柇锛?
```text
ROM 杞爣绛句氦鎹㈡柟鍚戞垚绔嬨€?浣?max 0.528 cm 浠嶄笉鏄渶缁堝彛鍨嬮獙鏀舵爣鍑嗭紝鍙兘璇存槑鏉冮噸闂琚樉钁楀帇浣庛€?涓嬩竴姝ヨ鎶婂畠娉涘寲涓?motion ownership solver锛岃€屼笉鏄‖缂栫爜鍞囬儴鍚嶅瓧銆?```

### 5.7 v010/v011 缁撴灉

`v010_teethLipBarrierFix.ma` 鍜?`v011_teethLipRomBarrierFix.ma` 鏄 v009 鐨勭户缁獙璇侊紝涓嶆敼鍙樹富绾跨粨璁猴紝鑰屾槸鎶婁袱涓仐婕忕殑宸ョ▼浜嬪疄琛ラ綈锛?
- `cdfBaiXingG_teethlow1` 涓嶆槸鍗曠嫭涓嬬墮锛岀偣鏁?`3716 = M_LoTeeth_base 1664 + M_LoGum_base 2052`锛屽繀椤讳綔涓?lower teeth + lower gum 鐨?composite target 澶勭悊銆?- `cdfBaiXingG_teethup1` 鍚岀悊鏄?upper teeth + upper gum composite target銆?- `cdfBaiXingG_tongue1` 涓嶈兘鍙惉 mesh锛屽繀椤讳粠 `M_Tongue_base` 琛ュ洖 tongue skin銆?- live 澶撮儴灏戦噺涓婁笅鍞囨贩鏉冪偣闇€瑕?ROM 杩愬姩璇佹嵁鍐冲畾 owner锛屼粎闈犻潤鎬?UpLip/LoLip 鍚嶇О鎴栨渶杩戠偣浼氭紡鎺夎钀藉尯鍩熴€?
v010 缁撴瀯淇锛?
| target | 淇鍐呭 | 缁撴灉 |
|---|---|---|
| `cdfBaiXingG_teethlow1` | 浠?`M_LoTeeth_base + M_LoGum_base` 鐢熸垚 composite skin + 4 涓?bend BS target | skinCluster 3 influences锛孊S 4 aliases |
| `cdfBaiXingG_teethup1` | 浠?`M_UpTeeth_base + M_UpGum_base` 鐢熸垚 composite skin + 4 涓?bend BS target | skinCluster 3 influences锛孊S 4 aliases |
| `cdfBaiXingG_tongue1` | 浠?`M_Tongue_base` 缁ф壙 skin | skinCluster 7 influences |

v010 閲嶅紑澶嶉獙锛?
| 鎸囨爣 | 鏁板€?|
|---|---:|
| lower composite neutral mean distance | 0.1038837702 cm |
| lower composite jaw25 mean distance | 0.1034590268 cm |
| upper composite mean distance | 0.1072253860 cm |
| tongue jaw25 mean distance | 0.1006135763 cm |
| body mouth jaw25 p95 distance | 0.1156589021 cm |

v011 ROM 灞忛殰淇锛?
| 鎸囨爣 | 淇鍓?| 淇鍚?|
|---|---:|---:|
| raw lip mix `> 0.1` | 29 | 0 |
| raw lip mix `> 0.2` | 13 | 0 |
| max raw lip mix | 0.3083609983 | 0.0994471669 |
| first-run changed vertices | 29 | 29 |

杩欎簺 29 涓偣鍏ㄩ儴鐢?`M_Jaw_A_ctrl.rotateX=25` 鐨?source motion 鍒ゅ畾涓?lower/jaw-moving 鍖哄煙锛宻ource motion 绾?`1.43-1.96 cm`锛屽洜姝ゆ竻闄?UpLip 鏉冮噸鑰屼繚鐣?LoLip/Jaw 渚ф潈閲嶃€?
娉ㄦ剰锛歚v010` 鐨?`runs\maya_deformation_diag_v010_lip_raw_mix.json` 鍜?`runs\maya_deformation_diag_v010_lip_rom_candidates.json` 鏄娆′慨澶嶅墠璇佹嵁锛涘綋鍓?`v011_teeth_lip_rom_barrier_fix_report.json` 鍦ㄥ凡淇鍦烘櫙涓婇噸璺戞椂 `changed_vertices=0`锛岃〃绀烘棤鍓╀綑楂樻贩鏉冪偣锛屼笉琛ㄧず棣栨娌℃湁淇銆?
鍒ゆ柇锛?
```text
鍙ｈ厰 source-only mesh 涓嶈兘鎸夊崟 mesh 鏈€杩戠偣澶勭悊銆?鐗欓娇/鐗欓緢杩欑被 target merge 蹇呴』鍋?composite owner銆?涓婁笅鍞囧睆闅滀笉鑳藉彧闈犻潤鎬佹爣绛撅紝蹇呴』鎺?ROM motion evidence銆?```

### 5.8 v013 live target skin 缁撴灉

`v012_motionOwnerLipFix.ma` 鏆撮湶浜嗕竴涓叧閿鍒わ細`cdfBaiXingG_body1` 鐨勫熀纭€ skin 瀵?`RIG_body_msh` 宸茬粡鏄鐨勶紝浣?`cdfBaiXingG_body1_live_0` 鏄姩鎬?BS 鐨?live target锛屾潈閲嶆簮搴旇鏄?`M_Head_base`锛屼笉鑳芥嬁 `RIG_body_msh` 浣滀负 face skin 妯℃澘銆?
鑷爺淇閫昏緫锛?
```text
1. 鍏抽棴鐩稿叧 blendShape envelope锛屽彧娴?skin-only motion銆?2. 鍦?neutral pose 涓嬬敤 KDTree 寤?`M_Head_base` 鈫?`cdfBaiXingG_body1_live_0` 鏈€杩戞簮鐐广€?3. 璁＄畻 Jaw pose 鐨?skin-only motion vector error銆?4. 瀵?error > 0.25cm 鐨?23 涓偣锛屼粠 `M_Head_base_skinCluster` 璇诲彇 source 鏉冮噸骞跺啓鍏?`cdfBaiXingG_body1_live_0_skinCluster`銆?5. 涓嶈皟鐢?Maya copySkinWeights銆?```

楠岃瘉缁撴灉锛?
| 鎸囨爣 | v012 淇鍓?| v013 淇鍚?|
|---|---:|---:|
| skin-only live target high error `> 0.25cm` | 23 | 0 |
| skin-only live target max error | 2.3280540696 cm | 0.1656314709 cm |
| skin-only live target p95 | 绾?0.0676 cm | 0.0637098497 cm |
| dynamic final body high error `> 0.25cm` | 22 | 0 |
| dynamic final body max error | 2.3434965278 cm | 0.1662901117 cm |
| dynamic final body p95 | 绾?0.0676 cm | 0.0638083236 cm |

鍒ゆ柇锛?
```text
寮犲槾绮樿繛鐨?Skin 灞傛牴鍥犱笉鏄渶缁?body skin锛岃€屾槸 live target face skin 鎷块敊 source銆?鍔ㄦ€?BS target 鐨?Skin 缁ф壙蹇呴』鎸夌湡瀹?live source `M_Head_base` 鍋氭潈閲嶈縼绉汇€?鍢撮儴楠屾敹涓嶈兘鍙湅鏈€杩戣〃闈㈣窛绂伙紝蹇呴』娴?skin-only motion vector error銆?```

浣?v013 鍚庣画琚瘉鏄庝粛鐒朵笉鏄渶缁堥€氳繃銆傚畠鍙慨澶嶄簡鏈€杩戣繍鍔ㄨ宸渶澶х殑 23 涓偣锛岄獙璇佸彛寰勪粛鏈夌洸鍖猴細

```text
nearest motion error 浣?涓嶇瓑浜?upper/lower lip owner 璇箟姝ｇ‘
```

鍘熷洜鏄槾鍞囦笂涓嬪眰璺濈寰堣繎锛屽悓涓€涓?target 鐐瑰彲鑳藉湪绌洪棿涓婃壘鍒颁竴涓繍鍔ㄦ帴杩戠殑 source 鐐癸紝浣嗘潈閲?owner 浠嶈 `LoLip` / `UpLip` 浜ゆ崲姹℃煋銆倂013 鍙兘璁颁负鈥渓ive target source 淇閫氳繃鈥濓紝涓嶈兘璁颁负鈥滃槾鍞囪涔夊鍒婚€氳繃鈥濄€?
### 5.9 v014-v016 strict lip semantic patch

鐩爣鏀剁獎涓猴細

```text
M_Head_base skinCluster
鈫?cdfBaiXingG_body1_live_0_skinCluster
鈫?鍏堝鍒?M_Head_base 鐨?mouth / lip / jaw Skin 璇箟
```

鍏抽敭淇锛?
- `UpLip` 鎵嶆槸 upper lip 鏍囩銆?- `LoLip` 鎵嶆槸 lower lip 鏍囩銆?- `JawUp` / `Jaw` / `Chin` 蹇呴』褰掍负 jaw/support锛屼笉鑳界畻 upper lip銆?- 鍑犱綍鏈€杩戠偣銆佹硶绾裤€佹嫇鎵戣繛閫氬彧鍋氬€欓€夎繃婊わ紝鏈€缁?owner 鐢?source 鏉冮噸鏍囩鍐冲畾銆?
鎵ц璁板綍锛?
| 鐗堟湰 | 閫昏緫 | 缁撴灉 |
|---|---|---|
| `v014_lipSemanticMHeadFix.ma` | 浠?33 涓珮缃俊 `lower -> upper` 鐐逛负绉嶅瓙锛屾部 target 鎷撴墤鎵╁睍鎴?61 鐐?patch锛屽啓鍏?`M_Head_base` 鏉冮噸 | 61/61 浠?`lower` 鍒囧埌 `upper`锛屾棫鍐茬獊鍓?10 涓綆缃俊鐐?|
| `v015_mheadMouthRoiSkin.ma` | 灏嗛珮缃俊 mouth ROI 鏁翠綋澶嶅埢锛歶pper 721銆乴ower 604銆乯aw 702锛屽叡 2027 鐐?| 楠岃瘉鍙戠幇鏍囩鍙ｅ緞鎶?`JawUp` 閿欑畻鍒?upper锛岄渶淇 |
| `v016_strictUpperLipPatchSkin.ma` | 涓ユ牸鏍囩锛歶pper=`UpLip`锛宭ower=`LoLip`锛宩aw=`Jaw/Chin/JawUp`锛?5 涓弗鏍奸珮缃俊鍐茬獊鐐规嫇鎵戞墿灞曞埌 54 鐐?| 54/54 淇鐐瑰潎涓?`upper`锛? 涓粛琚?`lower` 涓诲锛涘墿浣?9 涓綆缃俊瀛ょ珛/杩囨浮鐐逛繚鐣?|

瀵瑰簲鎶ュ憡锛?
```text
.info\live_bs_productized_sync\v014_lip_semantic_mhead_fix_report.json
.info\live_bs_productized_sync\v015_mhead_mouth_roi_skin_report.json
.info\live_bs_productized_sync\v016_strict_upper_lip_patch_report.json
runs\maya_deformation_v016_strict_upper_lip_verify.json
```

褰撳墠鍒ゆ柇锛?
```text
v016 鏄綋鍓嶅彲渚?Maya 瑙嗚澶嶉獙鐨勬祴璇曞満鏅€?瀹冭В鍐崇殑鏄?live target Skin 鐨勪弗鏍?UpLip/LoLip owner 涓叉潈涓婚棶棰樸€?瀹冭繕涓嶆槸鏈€缁堜骇鍝佸寲 solver锛氫綆缃俊瀛ょ珛鐐广€丅S residual銆佺湡瀹炴帶鍒跺櫒澶氬Э鎬佸洖褰掍粛闇€缁х画鍋氥€?```

## 6. 鍏抽敭鍐崇瓥

### 6.1 楠ㄩ鏉冮噸灏辨槸鏈€寮鸿蒋璇箟鏍囩

楠ㄩ鏉冮噸涓嶆槸鍙湁鍙樺舰鎰忎箟锛屼篃澶╃劧琛ㄨ揪 owner锛?
```text
W(x) = 褰撳墠鐐瑰睘浜庡摢浜涜繍鍔ㄧ郴缁熺殑姒傜巼鍒嗗竷
```

渚嬪锛?
- UpLip / LoLip / Jaw 鍖哄垎鍙ｈ厰涓婁笅灞傘€?- Eye / eyelid / head 鍖哄垎鐪肩悆銆佺溂鐫戙€佸ご閮ㄩ檮浠躲€?- pelvis / spine / leg 鍖哄垎鑵板甫銆佽。鏈嶃€佽韩浣撱€?
鍥犳鍚庣画鍖归厤涓嶅簲璇ュ彧鐢?3D 璺濈锛岃€屽簲璇ユ妸鏉冮噸鍚戦噺褰撲綔楂樼淮鏍囩绌洪棿锛圵eight Vector Space锛夛細

```text
score = geo_score + normal_score + owner_weight_score + motion_score + layer_score
```

瀹炶返纭锛?
```text
褰撳嚑浣曡繎閭诲啿绐佹椂锛屾潈閲嶆爣绛惧拰 ROM 杩愬姩璇佹嵁姣旀渶杩戠偣鏇村彲淇°€?```

### 6.2 灏勭嚎鍜屾硶绾胯繃婊ゅ彲鐢紝浣嗗彧鑳藉仛鍊欓€夎繃婊?
澶氬懡涓皠绾匡紙Multi-hit Ray锛夐€昏緫鍙锛?
```text
娌跨洰鏍囨硶绾?鍙嶆硶绾垮彂灏?鈫?鏀堕泦澶氫釜鍛戒腑
鈫?鎺掑簭
鈫?璺宠繃娉曠嚎鍙嶅悜鎴栬搴﹁繃澶х殑闈?鈫?瀵瑰彲鎺ュ彈鍛戒腑鍋氶噸蹇冩彃鍊?鈫?鏈懡涓繘鍏?fallback / inpaint
```

浣嗗畠涓嶆槸鏈€缁?owner 鍒ゆ柇锛?
- 鐩稿弽娉曠嚎涓嶆€绘槸閿欒锛岃。鏈嶅唴澶栧眰銆佸彛鑵斿唴澹佸彲鑳介渶瑕佺浜岃疆 flipped normal銆?- 鍚屽悜娉曠嚎涔熷彲鑳借鍖归厤锛屼緥濡備笂涓嬪攪璐磋繎鍖哄煙銆?- 灏勭嚎鍙洖绛斺€滃摢涓潰鍑犱綍涓婂彲杈锯€濓紝涓嶈兘鍥炵瓟鈥滆繖涓偣搴旇璺熷摢濂?rig owner鈥濄€?
姝ｇ‘瀹氫綅锛?
```text
ray / closest surface / normal filter = candidate generator
weight label / ROM motion / layer graph = candidate selector
```

### 6.3 BS 杩佺Щ涓嶈兘鍙縼 delta

闈欐€?BS 鍙互璧?residual field锛?
```text
R(x, pose) = final_deformed_position(x, pose) - skinned_position(x, pose)
```

鍔ㄦ€?Live BS 蹇呴』娣卞叆涓€灞傦細

```text
live target geometry
live target skin
live target 鍐呴儴 BS
driver connection
澶栧眰 inputGeomTarget
```

楠屾敹鍙ｅ緞涔熷繀椤绘繁鍏ヤ竴灞傦細

```text
婵€娲绘帶鍒跺櫒鎴?BS 灞炴€?鈫?姣旇緝鏈€缁?body world-space delta
鈫?涓嶈兘鍙瘮杈冧腑闂?target mesh
```

### 6.4 鈥滈珮绾х畻娉曗€濅笉鑳借烦杩囬獙鏀?
VDB銆丟WN銆丅BW銆乬eodesic銆丏em Bones 閮芥湁浠峰€硷紝浣嗕笉鏄秺楂樼骇瓒婂厛鐢ㄣ€傚綋鍓嶉樁娈电殑姝ｇ‘椤哄簭鏄細

```text
1. 鍏堣瘉鏄?owner-filtered local field 瀵圭湡瀹炶祫浜у彲閲嶅銆?2. 鍐嶈ˉ multi-candidate surface matching 涓?ROM motion ownership銆?3. 鍐嶆妸 VDB/GWN/barrier 浣滀负 support domain 缂撳瓨銆?4. 鏈€鍚庢墠鎶?BBW/harmonic 鐢ㄤ綔 refinement銆?```

鍘熷洜锛?
- 褰撳墠鏈€澶ч敊璇笉鏄钩婊戝害涓嶅锛岃€屾槸 owner 閫夐敊銆?- BBW/harmonic 鍙兘璁╅敊璇潈閲嶆洿骞虫粦锛屼笉鑳芥妸閿欒 owner 鍙樺銆?- VDB/GWN 鑳芥敼鍠?support domain锛屼絾浠嶄笉鑳芥浛浠ｈ涔夊綊灞炪€?
## 7. 褰撳墠璁″垝涔?
### 7.1 涓嬩竴闃舵鐩爣

鐭湡鐩爣涓嶆槸鈥滃叏鑷姩鏈€缁堢増鈥濓紝鑰屾槸鎶?cdfBaiXingG 宸查獙璇侀摼璺骇鍝佸寲锛?
```text
Skin 浜у搧鍖栬ˉ寮?鈫?Live BS 浜у搧鍖栬ˉ寮?鈫?ROM motion ownership 娉涘寲
鈫?澶氬€欓€夊嚑浣曞尮閰嶄笌浣庣疆淇¤緭鍑?鈫?鏂囨。鍖栨祴璇曡祫浜т笌鍥炲綊鏍囧噯
```

### 7.2 蹇呴』鍏堝仛鐨勪骇鍝佸寲椤?
1. 灏?`v006` 楠岃瘉杩囩殑 Live BS 缁撴瀯杩佺Щ鍚堝叆 `maya_sync_rig_incremental`锛?   - active 鍖哄煙娓呴浂 body 鏉冮噸銆?   - live target skin 鍐欏洖銆?   - 鍐呴儴 BS 灞炴€ц縼绉汇€?   - driver connection 澶嶆帴銆?   - output history 涓彧淇濈暀棰勬湡 BS 鑺傜偣銆?
2. 灏?`v009` 鐨?ROM 杞爣绛句氦鎹㈡娊璞′负 motion ownership solver锛?   - 杈撳叆涓嶅簲纭紪鐮?`M_Jaw_A_ctrl`銆?   - 鍏佽 profile 鎻愪緵 pose set銆乷wner group 鍜?forbidden pairs銆?   - 杈撳嚭姣忎釜 vertex/patch 鐨?under-moving / over-moving 璇婃柇銆?   - 鍙湪浣庣疆淇℃垨楂樿宸尯鍩熸墽琛屼慨姝ｃ€?
3. 澧炲姞 multi-candidate surface matcher锛?   - 鏈€杩戦潰涓嶆槸鍞竴鍊欓€夈€?   - 鍊欓€夊寘鍚?ray hit銆乧losest point銆乶ormal-compatible faces銆乷wner-compatible faces銆?   - scoring 涓姞鍏ヨ窛绂汇€佹硶绾胯搴︺€佹潈閲嶆爣绛俱€丷OM motion銆乴ayer/barrier銆?
4. 寤虹珛浣庣疆淇′笌澶辫触鍖哄煙鍙鍖栵細
   - 杈撳嚭 JSON/NPZ銆?   - 杈撳嚭 owner銆佸€欓€夊垎鏁般€佹潈閲?top joints銆丷OM error銆?   - 鍙互鍥炲啓 Maya vertex color 鎴?display layer 渚涗汉宸ュ鏌ャ€?
### 7.3 鍚庣画绠楁硶鍗囩骇椤哄簭

```text
V0.1 宸查獙璇侊細owner-filtered local field + component/patch ownership
V0.2锛歮ulti-candidate surface matching + motion ownership
V0.3锛歏DB/GWN support domain cache + barrier/layer graph
V0.4锛歨armonic / BBW refinement
V0.5锛歅SD residual + generalized dynamic BS transfer
V0.6锛欴em Bones / helper bones / runtime collapse
```

## 8. 娴嬭瘯鍑嗗垯

### 8.1 Skin 鍩虹鍥炲綊

蹇呴』淇濈暀浠ヤ笅娴嬭瘯锛?
- 鍚屾嫇鎵戠偣搴忔墦涔憋細璇樊鎺ヨ繎 0銆?- 鍗曟簮鎷嗗 mesh锛氬叏閮ㄧ洰鏍囪兘缁ф壙瀵瑰簲鏉冮噸銆?- 澶氭簮鍚堝崟 mesh锛歝omponent 绾у綊灞為€氳繃銆?- welded/bridge 澶氳涔夛細patch-level ownership 閫氳繃銆?- tight cloth / belt锛歰wner 闄愬煙姣斿叏灞€鍦烘洿濂姐€?- 鐪熷疄 cdfBaiXingG锛?1 mesh 璇婃柇涓庡啓鍥炵ǔ瀹氥€?
楠屾敹鎸囨爣锛?
```text
weight_l1_mean
weight_l1_p95
weight_l1_max
top1_match
surface_error_mean
surface_error_p95
surface_error_max
joint_leakage_count
low_confidence_count
```

### 8.2 BS 鍥炲綊

闈欐€?BS锛?
- 鍚屾嫇鎵?residual 鏌ヨ璇樊鎺ヨ繎 0銆?- 鐐瑰簭鎵撲贡鍚庝粛鑳芥寜绌洪棿鏌ヨ澶嶅師銆?- 婵€娲?BS 灞炴€у悗姣旇緝鏈€缁?mesh world-space delta銆?
Live BS锛?
- 妫€鏌ュ灞?blendShape target 鏄惁杩炴帴 `inputGeomTarget`銆?- 妫€鏌?live target 鏄惁鏈?skinCluster銆?- 妫€鏌?live target 鍐呴儴 BS 灞炴€ф暟閲忋€?- 妫€鏌?driver connection銆?- 妫€鏌?active 鍖哄煙鏄惁娓呴浂 body 鏉冮噸鍚庡啀鍐?live 鏉冮噸銆?- 鎺у埗鍣ㄥЭ鎬侀獙鏀朵紭鍏堜簬鍗曠嫭 alias 婵€娲汇€?
### 8.3 鍙ｈ厰/杩戝眰涓撻」鍥炲綊

蹇呴』鍔犲叆锛?
```text
M_Jaw_A_ctrl.rotateX = 25
```

骞惰褰曪細

- body jaw error baseline/post/reopen銆?- upper/lower lip owner swap 缁熻銆?- under-moving / over-moving vertex 鏁伴噺銆?- lower teeth / upper teeth / tongue 鐨?source-only 缁戝畾璇樊銆?- 鏈€宸偣 vertex id銆佹湡鏈涜繍鍔ㄣ€佸疄闄呰繍鍔ㄣ€乼op joints銆?
鐜伴樁娈靛弬鑰冮槇鍊硷細

```text
v007 baseline max = 2.4614610556 cm
v009 post max = 0.5281665876 cm
v008 residual max = 0.0156823407 cm
```

鏈€缁堜骇鍝佸寲鐩爣搴旀帴杩?v008 鐨勮瑙?璇樊姘村钩锛屼絾瀹炵幇鏂瑰紡蹇呴』浠庢墜鍔?residual 杩囨浮鍒板彲澶嶇敤 motion ownership + residual field銆?
### 8.4 鎷撴墤鏀拺鍩熷尮閰嶅櫒

2026-05-14 鏂板绂荤嚎鏍稿績锛?
```text
core/topology_support_matcher.py
```

瀹氫綅锛?
```text
涓嶆槸鏇夸唬 Skin / BS 鍐欏洖锛?鑰屾槸鍦ㄥ啓鍥炲墠瑙ｅ喅鈥滄姘忚窛绂昏繎銆乵esh 鎷撴墤杩溾€濈殑鍊欓€夊綊灞為棶棰樸€?```

褰撳墠绠楁硶閾撅細

---

## 详细实验迭代日志（§8.4 以下）

after_payload_max_abs_diff                  = 2.4477057603000674e-09
live_delta_max                              = 0.3036400451573549
live_delta_mean                             = 0.23438899049051257
final body_delta_max                        = 0.3036400451573549
final body_delta_mean                       = 0.23438899049051257
```

璇ュ鏍稿彧璇佹槑锛?
```text
strict11 鏉冮噸纭疄鍐欏叆 live target锛?鍐欏叆浼氶€氳繃鍔ㄦ€侀摼璺奖鍝嶆渶缁?cdfBaiXingG_body1锛?褰撳墠璇曞啓鍙綔涓哄眬閮ㄥ€欓€夐獙璇併€?```

璇ュ鏍镐笉鑳借瘉鏄庯細

```text
鏁村湀鍢村攪绮樿繛宸茶В鍐筹紱
鍙傛暟鍥炲綊鍙互缁х画鎵╁ぇ鍐欏洖鑼冨洿锛?Live BS / 琛ㄦ儏鎷撴墤 / residual 宸茬粡姝ｇ‘銆?```

鍥犳涓嬩竴姝ヤ笉搴旂户缁墿澶?skin patch锛岃€屽簲杩涘叆锛?
```text
Live BS target 琛ㄦ儏褰㈡€佸璁?source/target jaw pose residual 瀵归綈瀹¤
涓婁笅鍞囨嫇鎵戣〃杈炬槸鍚﹁冻澶熷垎绂荤殑妫€鏌?```

褰撳墠澶嶅埢瀵硅薄蹇呴』閿佸畾涓猴細

```text
source: M_Head_base
target: cdfBaiXingG_body1_live_0
```

`cdfBaiXingG_body1` 涓嶆槸鏈疆鏄犲皠鐩爣锛屽彧鐢ㄤ簬楠岃瘉 `cdfBaiXingG_body1_live_0` 浣滀负鍔ㄦ€?BlendShape target 杈撳叆鍚庯紝鏈€缁堝彲瑙佽緭鍑烘槸鍚︽纭€傚悗缁?skin銆丅S銆乺esidual/corrective 瀹¤閮藉繀椤诲厛鍦?`M_Head_base -> cdfBaiXingG_body1_live_0` 杩欐潯閾句笂璇佹槑鎴愮珛锛屽啀鐪?final body 杈撳嚭銆?
2026-05-14 final 杈撳嚭閾捐矾涓庡彛鍨嬪垎绂诲璁★細

```text
閾捐矾瀹¤:
.info/topology_support_matcher/deformer_chain_audit_v019.json

final pose 瀵煎嚭:
.info/topology_support_matcher/final_output_pose_v019_strict.npz
.info/topology_support_matcher/final_output_pose_v019_strict_summary.json

鍙ｅ瀷鍒嗙瀹¤:
.info/topology_support_matcher/mouth_expression_separation_audit_v019_strict.json
```

deformer 閾捐矾浜嬪疄锛?
```text
cdfBaiXingG_body1_body_msh_blendShape.envelope = 1
cdfBaiXingG_body1_skinCluster.envelope         = 1
cdfBaiXingG_body1_live_0_M_Head_base_blendShape.envelope = 1
cdfBaiXingG_body1_live_0_skinCluster.envelope             = 1

cdfBaiXingG_body1_live_0Shape.worldMesh
鈫?cdfBaiXingG_body1_body_msh_blendShape.inputGeomTarget

cdfBaiXingG_body1_body_msh_blendShape.outputGeometry[0]
鈫?cdfBaiXingG_body1_skinCluster.input[0].inputGeometry

cdfBaiXingG_body1_live_0_M_Head_base_blendShape.outputGeometry[0]
鈫?cdfBaiXingG_body1_live_0_skinCluster.input[0].inputGeometry
```

杩欒鏄庯細

```text
final body 涓嶆槸鍥犱负 envelope / nodeState 鍏抽棴瀵艰嚧涓嶅姩锛?target final 涓?source final 鐨?deformer 椤哄簭鍚屼负 blendShape 鈫?skinCluster锛?live target 鏄姩鎬?worldMesh 杈撳叆锛屼笉鏄潤鎬佹柇閾俱€?```

final body 涓?live target 閫愮偣澶嶆牳锛?
```text
neutral max difference = 3.2033642570678846e-16
jaw25 max difference   = 3.2033642570678846e-16
motion max difference  = 0.0
```

鍥犳锛?
```text
cdfBaiXingG_body1 鐨?final 杈撳嚭娌℃湁浜屾鐮村潖 cdfBaiXingG_body1_live_0锛?濡傛灉鍙闂浠嶅瓨鍦紝涓诲洜搴旂户缁煡 live target 鐨勫眬閮ㄥ搴?/ residual / 灞€閮?shape 琛ㄨ揪銆?```

鏁翠綋涓婁笅鍞囧垎绂诲璁★細

```text
M_Head_base gap_delta_mean         = 2.2302107729855547
cdfBaiXingG_body1 gap_delta_mean   = 2.4604424893186874
target/source gap_delta_mean ratio = 1.1032331648299432

M_Head_base motion_delta_mean         = 2.2976551696618666
cdfBaiXingG_body1 motion_delta_mean   = 2.5353615651118715
target/source motion_delta_mean ratio = 1.1034560793058372
```

璇ョ粨鏋滃弽璇侊細

```text
涓嶈兘鎶婂綋鍓嶅け璐ョ畝鍗曞綊鍥犱簬鈥滄暣浣?Live BS 娌″紶寮€鈥濄€?鏁翠綋 upper/lower lip separation 涓嶅急浜?source锛?鍚庣画瑕佺湅灞€閮?residual/correspondence锛岃€屼笉鏄户缁皟澶?skin patch 鎴栧叏灞€ jaw 骞呭害銆?```

灞€閮ㄥ啓鍥炲喅绛栧璁★細

```text
.info/topology_support_matcher/local_residual_decision_audit_v019.json
```

缁撴灉锛?
```text
strict count = 11
  transition: upper_lip -> lower_lip
  mean improvement = 0.2192754228950931
  mean matcher_error = 0.1431752982468499
  mean weight_l1 = 0.8756591709728002

soft count = 2
  transition: lower_lip -> jaw
  涓嶅啓锛屽師鍥犳槸 improvement 鍜?weight delta 澶皬

risky count = 29
  transitions:
    upper_lip -> lower_lip = 11
    lower_lip -> jaw       = 18
  mean improvement = -0.15711769195895936
  mean matcher_error = 0.20937644356701407
  mean weight_l1 = 0.7300435197508643
```

杩欒鏄庯細

```text
闄?strict11 浠ュ锛岀户缁墿澶ц涔夊垏鎹㈠啓 skin 浼氳 motion gate 鍙嶈瘉锛?涓嶅皯 risky 鐐硅櫧鐒?weight_l1 寰堝ぇ锛屼絾鎹㈣繃鍘诲悗鐨?Jaw pose motion 鏇村樊锛?楂樿宸?same-family 鐐瑰鏁版槸 source sample/correspondence 闂锛屼笉鏄?target skin 鍐欏洖璁稿彲銆?```

鍚庣画鏂瑰悜锛?
```text
濡傛灉鐢ㄦ埛鍦?v019 strict11 鍦烘櫙浠嶇湅鍒板彲瑙佺矘杩烇紝
涓嬩竴姝ュ簲鍋氬眬閮?residual / corrective 瀹¤锛?  鍦?strict / risky / same-family-high-error 鏍囪鐐瑰懆鍥撮噰鏍峰眬閮?patch锛?  鐢?matcher source 鍙綔涓?motion reference锛?  涓嶅啀鐩存帴鎶?matcher 鏉冮噸鍐欏洖锛?  鍏堣瘉鏄?residual 褰㈠彉宸紓锛屽啀鍐冲畾鏄惁鐢熸垚灞€閮?corrective target銆?```

2026-05-14 live target residual/corrective 瀹¤锛?
```text
宸ュ叿:
tools/live_target_residual_audit.py

杈撳叆:
.info/topology_support_matcher/mhead_to_live_v019_data.npz
.info/topology_support_matcher/topology_candidate_audit_best.json
.info/topology_support_matcher/topology_candidate_audit_best.npz
.info/topology_support_matcher/final_output_pose_v019_strict.npz

杈撳嚭:
.info/topology_support_matcher/live_target_residual_audit_v019_strict_jaw25.json
.info/topology_support_matcher/live_target_residual_audit_v019_strict_jaw25.npz
```

瀹¤杈圭晫锛?
```text
source = M_Head_base
target = cdfBaiXingG_body1_live_0
pose   = Jaw25 strict scene output
```

鏍稿績缁撴灉锛?
```text
ROI count = 2790
overall residual mean = 0.030812018475502943
overall residual p95  = 0.18608287102875587

strict residual:
  count = 11
  mean  = 0.09119799992061674
  max   = 0.11683714266365228

same-family legal residual:
  count = 1769
  mean  = 0.009897060709760251
  p95   = 0.03843089540951784
  max   = 0.10852960238432477

risky residual:
  count = 29
  mean  = 0.20937644356701413
  max   = 0.5028454444198279

legal corrective count = 0
decision = NO_CORRECTIVE_WRITE
```

瑙ｉ噴锛?
```text
strict11 鍐欏畬鍚庯紝鍏佽鍐欑殑鍖哄煙娈嬪樊宸茬粡浣庝簬 corrective 闃堝€硷紱
same-family 鍚堟硶鍖虹殑娈嬪樊涔熶綆锛屼笉闇€瑕侀澶?BS target锛?risky 鍖鸿櫧鐒?residual 楂橈紝浣嗗畠浠殑 source reference 宸茶 motion gate 鍙嶈瘉锛屼笉鑳芥嬁鏉ョ敓鎴?corrective锛?鍥犳褰撳墠涓嶈兘鍐欐柊鐨?corrective / BS target銆?```

涓嬩竴姝ュ鏋滆瑙変粛涓嶅锛屼紭鍏堟柟鍚戜笉鏄敓鎴?corrective锛岃€屾槸锛?
```text
閲嶆柊鎻愰珮灞€閮?correspondence 鐨勫彲淇″害锛?妫€鏌ュ綋鍓嶇敤鎴风湅鍒扮殑闂鏄惁瀵瑰簲 risky 鏍囪鐐广€佸彛鑵?source-only mesh 鎴栨樉绀哄眰/瑙傚療瀵硅薄锛?蹇呰鏃跺鍑虹敤鎴锋寚瀹氭帶鍒跺櫒瑙掑害鍜岃瑙掍笅鐨?source/target 灞€閮?patch锛屽啀閲嶆柊瀹¤銆?```

2026-05-14 skin-only 璇箟鎷撴墤鍐欏洖澶嶆牳锛?
```text
褰撳墠杈撳叆:
.info/topology_support_matcher/v021_current_skin_only_probe.npz

鍊欓€?
.info/topology_support_matcher/v021_skin_only_semantic_candidate.npz
.info/topology_support_matcher/v021_skin_only_semantic_candidate.json

瀹藉啓缁撴灉:
ysj_chr_cdfBaiXingG_rig_rigMaster_v021_semanticSkinOnly.ma
.info/topology_support_matcher/v021_skin_only_actual_validation.json

瀹為檯鏀剁泭闂ㄦ帶:
ysj_chr_cdfBaiXingG_rig_rigMaster_v022_skinActualGated.ma
.info/topology_support_matcher/v022_skin_only_actual_validation.json
```

澶嶆牳杩囩▼锛?
```text
1. 浠庡墠鍙?v020 褰撳墠鍦烘櫙閲嶆柊瀵煎嚭 M_Head_base 涓?cdfBaiXingG_body1_live_0銆?2. target 294 influence 瀵归綈鍒?source 209 influence銆?3. 鐢?upper_lip / lower_lip / jaw 楂樼疆淇℃潈閲嶇瀛愭部 target 鎷撴墤浼犳挱銆?4. 鐢熸垚 conservative skin 鍊欓€?113 鐐瑰苟鍐欏叆 v021銆?5. 閲嶆柊瀵煎嚭瀹為檯 Jaw25 鐐逛綅锛屾瘮杈冨啓鍏ュ墠鍚?motion error銆?6. 鍙戠幇 v021 瀹藉啓 113 鐐逛腑 37 鐐规敼鍠勩€?6 鐐归€€鍖栥€?7. v022 鎭㈠閫€鍖栫偣锛屽彧淇濈暀瀹為檯鏀瑰杽 margin > 0.005 鐨?26 鐐广€?```

鏍稿績缁撴灉锛?
```text
v021 neutral delta max = 0
v021 written count = 113
v021 improved = 37
v021 worse = 76

v022 kept candidate vertices = 26
v022 restored vertices = 87
v022 neutral delta max = 0
v022 written error mean: 0.0600846438 -> 0.0345911722
v022 written improvement mean = 0.0254934717
v022 written worse count = 0
v022 ROI mean: 0.0110525122 -> 0.0107747125
```

缁撹锛?
```text
璇箟鎷撴墤鏀寔鍩熻兘閬垮厤涓€閮ㄥ垎涓婁笅鍢村攪绌洪棿涓叉潈锛?浣嗏€滅绾?motion proxy 鏀瑰杽鈥濅笉绛変簬鈥滃啓杩?skinCluster 鍚庣湡瀹炴敼鍠勨€濄€?
v022 鏄畨鍏ㄥ皬琛ヤ竵锛屼笉鏄畬鏁磋В鍐冲紶鍢存挄瑁傜殑鐗堟湰銆?褰撳墠涓嶈兘缁х画闈犳墿澶ф渶杩戠偣 / support-domain skin patch 瑙ｅ喅鏁村湀闂銆?涓嬩竴姝ュ繀椤昏浆鍚戯細
  LBS 鐭╅樀绾ф潈閲嶅弽姹傦紱
  澶氬Э鎬佸眬閮?correspondence 閲嶅缓锛?  鍐欏洖鍚庣湡瀹?Jaw25 鐐逛綅楠岃瘉锛?  鐢ㄦ埛瑙嗚鍙褰㈡€佸楠屻€?```

2026-05-14 LBS 鐭╅樀绾у弽姹傝瘯楠岋細

```text
鐭╅樀鎺㈤拡:
.info/topology_support_matcher/v023_lbs_matrix_probe.npz
.info/topology_support_matcher/v023_lbs_reconstruction_report.json

鍙嶆眰鍊欓€?
.info/topology_support_matcher/v023_lbs_inverse_candidate.npz
.info/topology_support_matcher/v023_lbs_inverse_candidate.json

鍐欏洖鍦烘櫙:
ysj_chr_cdfBaiXingG_rig_rigMaster_v023_lbsInverseSkin.ma

瀹為檯楠岃瘉:
.info/topology_support_matcher/v023_lbs_inverse_actual_validation.json
.info/topology_support_matcher/v023_lbsInverseSkin_jaw25_view.png
```

鎵ц閫昏緫锛?
```text
1. 閫氳繃 skinCluster.envelope=0 閲囬泦 pre-skin inputGeometry銆?2. 閲囬泦姣忎釜 influence 鐨?bindPreMatrix 涓?neutral / Jaw25 worldMatrix銆?3. 鐢?row-vector 鍏紡 point * bindPreMatrix * worldMatrix 閲嶅缓 Maya skin 杈撳嚭銆?4. 鍙湪閲嶅缓璇樊鎺ヨ繎 0 鍚庯紝杩涘叆闈炶礋褰掍竴鏈€灏忎簩涔樻潈閲嶅弽姹傘€?5. 鐩爣浣嶇Щ浣跨敤 target neutral + matched source motion銆?6. 鍙啓鍏ラ娴嬫敼鍠勪笖 neutral 绾︽潫涓?0 鐨勫眬閮ㄩ《鐐广€?7. 鍐欏洖鍚庨噸鏂板鍑?Maya Jaw25 鐐逛綅鍋氬疄闄呴獙璇併€?```

LBS 閲嶅缓缁撴灉锛?
```text
source Jaw25 max error = 7.841151739061706e-06
target Jaw25 max error = 7.63845047994942e-06
鏈夋晥鐭╅樀椤哄簭 = point * bindPreMatrix * worldMatrix
```

v023 瀹為檯鍐欏洖缁撴灉锛?
```text
written vertices = 78
writeback diff max = 0
neutral delta max = 0

written error mean: 0.0654409069 -> 0.0054724223
written p95 error: 0.1082827329 -> 0.0223911042
written max error: 0.2309140450 -> 0.0422944464
improved written count = 78
worse written count = 0

ROI mean: 0.0107747125 -> 0.0088143010
ROI p95 : 0.0413936555 -> 0.0340878064
ROI max : 0.2309140450 -> 0.1200947639
```

闃舵缁撹锛?
```text
LBS 鐭╅樀绾у弽姹傛瘮缁х画鎵╁ぇ support-domain patch 鏇村彲闈犮€?鍘熷洜鏄畠鐩存帴鍦ㄧ湡瀹?skinCluster 鐭╅樀鍩哄簳涓婃眰鏉冮噸锛屽啓鍥炲悗鐨勫疄闄?Jaw25 杈撳嚭涓庣绾块娴嬩竴鑷淬€?
褰撳墠浠嶄笉鏄渶缁堜骇鍝佸寲瀹屾垚锛?杩欐槸 Jaw25 鍗曞Э鎬佸弽姹傦紝鍙兘瀵瑰崟涓€鎺у埗鍣ㄨ搴﹁繃鎷熷悎銆?涓嬩竴姝ラ渶瑕佸濮挎€佸洖褰掋€侀偦鍩熷钩婊戠害鏉熴€佸奖鍝嶉楠间笂闄愮害鏉熷拰鐢ㄦ埛鎸囧畾瑙嗚澶嶉獙銆?```

2026-05-14 v024 瑙嗚闂ㄦ帶澶嶆牳锛?
```text
杈撳叆:
.info/topology_support_matcher/v024_visual_failure_diagnostic.npz
.info/topology_support_matcher/v024_visual_gated_lbs_candidate.npz

鍐欏洖鍦烘櫙:
ysj_chr_cdfBaiXingG_rig_rigMaster_v024_visualGatedSkin.ma

澶嶆牳:
.info/topology_support_matcher/v024_after_visual_gated_probe.npz
.info/topology_support_matcher/v024_visualGatedSkin_jaw25_view.png
.info/topology_support_matcher/v024_visual_surface_gate_recheck.json
```

鎵ц閫昏緫锛?
```text
1. 浠?v022/v023 Jaw25 杈撳嚭姣旇緝 mouth ROI 鐨勮瑙夐闄┿€?2. 椋庨櫓椤瑰寘鍚?close opposing surface銆侀潰闈㈢Н绐佸彉銆佹硶绾跨炕杞€佽竟闀跨獊鍙樸€?3. v023 鐨?78 涓?LBS 鍙嶆眰鐐逛腑锛屼繚鐣?44 涓綆椋庨櫓鐐癸紝鍥炴粴 34 涓珮椋庨櫓鐐广€?4. 閲嶆柊鍐欏洖 `cdfBaiXingG_body1_live_0_skinCluster`锛屽啀瀵煎嚭 Jaw25 鎴浘鍜岀偣浣嶃€?```

澶嶆牳缁撴灉锛?
```text
v022 fail_count = 996
v023 fail_count = 1002
v024 fail_count = 993

v022 close_opposing_count = 857
v023 close_opposing_count = 854
v024 close_opposing_count = 859

v022 area_bad_faces = 129
v023 area_bad_faces = 133
v024 area_bad_faces = 117

v022 edge_bad_faces = 125
v023 edge_bad_faces = 126
v024 edge_bad_faces = 112

v022 normal_flip_faces = 66
v023 normal_flip_faces = 68
v024 normal_flip_faces = 63
```

缁撹锛?
```text
灏勭嚎/鐩稿弽娉曠嚎/鎶樺彔妫€娴嬪彲浠ヤ綔涓鸿瑙夐闄╅棬鎺э紱
瀹冭兘鍑忓皯涓€閮ㄥ垎鐢?v023 寮曞叆鐨勫潖褰紝浣嗕笉鑳藉崟鐙В鍐冲槾鍞囩矘杩炪€?
鍘熷洜鏄皠绾垮彧鑳借瘉鏄庘€滆繖閲屾湁杩戝眰鍛戒腑鎴栫┛鎻掗闄┾€濓紝
涓嶈兘璇佹槑鈥滆繖涓偣搴旇缁ф壙鍝竴缁?skin 鏉冮噸鈥濄€?
涓嬩竴闃舵涓嶈兘缁х画鍋氶€愮偣鍥炴粴銆?蹇呴』鎶婅瑙夐闄╅」鏀捐繘 patch-level 鏉冮噸姹傝В锛?
E =
  位_pose    * source/target 澶氬Э鎬佷綅绉昏宸?+ 位_neutral * neutral 涓嶅姩绾︽潫
+ 位_weight  * 褰撳墠鏉冮噸鍏堥獙
+ 位_smooth  * 閭绘帴鐐规潈閲嶅钩婊?+ 位_edge    * 杈归暱/闈㈢Н淇濇寔
+ 位_normal  * 娉曠嚎缈昏浆鎯╃綒
+ 位_ray     * 杩戝眰鐩稿弽娉曠嚎/灏勭嚎绌挎彃鎯╃綒

涔熷氨鏄锛屽皠绾挎柟妗堣鐢紝浣嗚鑹叉槸 penalty / reject gate锛?涓嶆槸 owner solver銆?```

浠ｇ爜娌夋穩锛?
```text
core/visual_surface_gate.py
tests/test_visual_surface_gate.py
```

`visual_surface_gate` 褰撳墠鎻愪緵锛?
```text
close_opposing_surface_mask
face_metrics
score_surface_deformation
```

鍗曞厓娴嬭瘯瑕嗙洊锛?
```text
杩戝眰鐩稿弽娉曠嚎鍙娴嬶紱
鍚屽悜杩戝眰涓嶈鍒わ紱
闈㈢墖鎷変几浼氳繘鍏ヨ瑙夐闄┿€?```

2026-05-14 v025/v026 patch-level 澶嶆牳锛?
```text
鏂板浠ｇ爜:
core/patch_lbs_solver.py
tests/test_patch_lbs_solver.py
tools/patch_lbs_candidate_v025.py
tools/visual_candidate_blend_v026.py

绂荤嚎浜х墿:
.info/topology_support_matcher/v025_patch_lbs_candidate.json
.info/topology_support_matcher/v025_patch_lbs_candidate.npz
.info/topology_support_matcher/v026_visual_candidate_blend_candidate.json
.info/topology_support_matcher/v026_visual_candidate_blend_candidate.npz

Maya 鍐欏洖:
ysj_chr_cdfBaiXingG_rig_rigMaster_v026_visualBlendSkin.ma
ysj_chr_cdfBaiXingG_rig_rigMaster_v026_visualBlendSkin_jaw25.ma

瀹為檯鍥炶:
.info/topology_support_matcher/v026_visualBlendSkin_maya_write_verify.json
.info/topology_support_matcher/v026_visualBlendSkin_maya_actual_pose.npz
.info/topology_support_matcher/v026_visualBlendSkin_actual_visual_verify.json
runs/captures/viewport_capture_20260514_223905.png
```

鎵ц閫昏緫锛?
```text
v025:
1. 浠?v023 鐨?78 涓?LBS 鍙嶆眰鐐逛负涓績鎵╁睍 2-ring patch銆?2. 鍦?patch 鍐呭姞鍏ユ潈閲嶅厛楠屻€侀偦鎺ュ钩婊戙€乶eutral lock 鍜?influence top-k 绾︽潫銆?3. 鍋氬弬鏁?sweep锛屽彧鐢熸垚绂荤嚎鍊欓€夛紝涓嶇洿鎺ュ啓 Maya銆?
v026:
1. 鎶?current / v023_lbs / v024_gate / v025_patch 褰撲綔姣忎釜闂鐐圭殑绂绘暎鍊欓€夈€?2. 浠?v024_gate 寮€濮嬶紝鎸夊眬閮?visual gate 闃堝€肩害鏉熼€愮偣鏇挎崲銆?3. 鎺ュ彈鏉′欢鏄眬閮ㄨ瑙夐闄╀笉瓒呰繃 v024锛屽悓鏃堕檷浣?Jaw25 鍐欏叆璇樊銆?4. 绂荤嚎閫氳繃鍚庯紝鎵嶉€氳繃 CGI Pipeline MCP 鍓嶅彴鍐欏叆 Maya锛屽苟瀵煎嚭瀹為檯鐐逛綅澶嶆牳銆?```

绂荤嚎缁撴灉锛?
```text
v024 visual fail_count = 993
v024 write_error mean = 0.0513965805
v024 write_error p95  = 0.1102254631
v024 write_error max  = 0.2435441501

v025 best visual fail_count = 997
v025 best write_error mean = 0.0468010017
v025 best write_error p95  = 0.0832485752
v025 best write_error max  = 0.0998928508

v026 offline visual fail_count = 993
v026 offline close_opposing_count = 852
v026 offline write_error mean = 0.0156715004
v026 offline write_error p95  = 0.0802637399
v026 offline write_error max  = 0.0956858061

v026 choice_counts:
v023_lbs = 64
v025_patch = 6
v024_gate = 5
current = 3
```

Maya 瀹為檯鍐欏洖缁撴灉锛?
```text
鍐欏洖鐩爣:
cdfBaiXingG_body1_live_0_skinCluster

鍐欏叆鐐规暟:
78

鏉冮噸鍥炶:
roundtrip_max_abs = 0.0
roundtrip_mean_abs = 0.0

瀹為檯 Jaw25 鍐欏墠 mean = 0.0513965805
瀹為檯 Jaw25 鍐欏悗 mean = 0.0389714514

瀹為檯 Jaw25 鍐欏墠 p95 = 0.1102254631
瀹為檯 Jaw25 鍐欏悗 p95 = 0.0936969787

瀹為檯 Jaw25 鍐欏墠 max = 0.2435441501
瀹為檯 Jaw25 鍐欏悗 max = 0.1126287370

瀹為檯 visual fail_count:
993 -> 992

瀹為檯 close_opposing_count:
859 -> 854

瀹為檯 area_bad_faces:
117 -> 119

瀹為檯 edge_bad_faces:
112 -> 110

瀹為檯 normal_flip_faces:
63 -> 58
```

闃舵缁撹锛?
```text
v025 璇佹槑 patch-level constrained LBS solver 鍙繍琛岋紝浣?smooth-only 涓嶈冻浠ラ€氳繃瑙嗚楠屾敹銆?v026 鏄涓€涓绾垮拰 Maya 瀹為檯閮芥鏀剁泭鐨勫€欓€夛細鏉冮噸鍐欏叆瀹屽叏涓€鑷达紝瀹為檯 Jaw25 璇樊涓庤瑙夐闄╁潎鏈変笅闄嶃€?
浣?v026 浠嶄笉鑳芥爣璁颁负鏈€缁堣В鍐筹細
1. 瀹為檯 Maya 鍥炬敹鐩婂皬浜庣绾块娴嬨€?2. 璇存槑褰撳墠 LBS 浠ｇ悊娌℃湁瀹屾暣瑕嗙洊 live target 鍐呴儴 BS / 鍔ㄦ€佸彉褰㈤摼銆?3. 缁х画鍙皟 skin 鏉冮噸浼氳繘鍏ユ敹鐩婇€掑噺銆?4. 涓嬩竴姝ュ繀椤绘妸瀹為檯 Maya 杈撳嚭鎴栧姩鎬?BS residual 绾冲叆浼樺寲鐩爣銆?```

涓嬩竴姝ュ噯鍒欙細

```text
涓嶈兘鍐嶅彧鐢ㄧ绾?LBS pose 褰撴渶缁堥獙鏀躲€?蹇呴』鍒嗕袱灞傦細

1. 蹇€熷€欓€夊眰:
   LBS inverse / patch smooth / visual gate 璐熻矗鎻愬嚭鍊欓€夈€?
2. 鐪熷疄楠屾敹灞?
   Maya dependency graph 瀹為檯 Jaw25 / 澶氬Э鎬佽緭鍑鸿礋璐ｆ帴鍙楁垨鎷掔粷鍊欓€夈€?
濡傛灉 v026 瑙嗚浠嶈浜哄伐鍒ゅ畾涓嶅锛?涓嬩竴姝ュ簲鍋?multi-pose actual-graph regression锛?
Maya 瀹為檯杈撳嚭閲囨牱
鈫?skin row / BS residual 鍊欓€?鈫?visual gate + surface error 鑱斿悎鐩爣
鈫?鍙啓鐪熷疄鍥鹃獙璇佹鏀剁泭鐨勭偣鎴?patch
```

2026-05-14 v027 weight-only 鏁版嵁婧愬鏍革細

```text
鏂板瀹¤:
.info/topology_support_matcher/v027_weight_data_source_audit.json
.info/topology_support_matcher/v027_matrix_truth_audit.json
.info/topology_support_matcher/v027_fresh_matrix_probe.npz
.info/topology_support_matcher/v027_fresh_matrix_probe.json

鍊欓€?
.info/topology_support_matcher/v027_source_prior_blend_candidate.npz
.info/topology_support_matcher/v027_source_prior_blend_candidate.json

Maya 鍐欏洖:
ysj_chr_cdfBaiXingG_rig_rigMaster_v027_weightOnlySourcePrior.ma
ysj_chr_cdfBaiXingG_rig_rigMaster_v027_weightOnlySourcePrior_jaw25.ma

瀹為檯鍥炶:
.info/topology_support_matcher/v027_weightOnlySourcePrior_maya_actual_pose.npz
.info/topology_support_matcher/v027_weightOnlySourcePrior_maya_write_verify.json
.info/topology_support_matcher/v027_weightOnlySourcePrior_actual_verify.json
```

鍏抽敭澶嶆牳锛?
```text
1. 鏃?v023 target matrix probe 瀵瑰綋鍓?v026 鍦烘櫙宸茬粡涓嶆槸瀹屾暣鐪熺浉銆?   v026 瀹為檯 Maya 杈撳嚭 vs 鏃?matrix sim:
   mean = 0.0272254234
   p95  = 0.0651688564
   max  = 0.0762234708

2. fresh target probe 鍙噸寤哄綋鍓?target skinCluster锛?   target Jaw25 reconstruct max = 7.6341075878e-06

3. M_Head_base 鍦ㄥ綋鍓嶅満鏅敤 skinCluster.envelope=0 閲?input 浼氶噸寤哄け璐ワ細
   source neutral reconstruct mean = 1.2615380218
   source Jaw25 reconstruct mean = 1.2730729426

   缁撹锛?   target 渚у繀椤荤敤 fresh probe锛?   source 渚х户缁娇鐢ㄦ棫 v023 涓凡楠岃瘉鐨?source matrix/inputGeometry銆?```

鏉冮噸鏍瑰洜鍒ゆ柇锛?
```text
source full motion 涓?upstream/input residual 鍗犳瘮涓嶅ぇ锛?input_residual_ratio mean = 0.0122930703
input_residual_ratio p95  = 0.0340872259
input_residual_ratio max  = 0.0457932322

鐪熸寮傚父鏄弽姹傛潈閲嶅亸绂?mapped source 鏉冮噸澶繙锛?current -> source L1 mean = 0.1531445118
v023    -> source L1 mean = 0.7147431297
v026    -> source L1 mean = 0.6313393616

涔熷氨鏄锛?v023/v026 涓轰簡杩藉崟濮挎€?motion锛屾妸鏉冮噸浠?source 璇箟鍏堥獙涓婃媺寮€浜嗐€?杩欒兘闄嶄綆鏌愪簺 pose error锛屼絾浼氬鍔犲槾鍞?鑴搁/jaw 娣锋潈椋庨櫓銆?```

v027 绛栫暐锛?
```text
涓嶅啀缁х画鎵╁ぇ LBS inverse銆?浠?v026 涓哄綋鍓嶅彲鐢ㄨВ锛屽悜 mapped source 鏉冮噸鍋氬皬姝ュ洖鎷夛細

W_v027 = 0.85 * W_v026 + 0.15 * W_mapped_source

杩欐槸 weight-only 淇锛?涓嶅啓 BS锛?涓嶆敼 live target锛?涓嶆敼鎺у埗鍣紱
鍙啓 cdfBaiXingG_body1_live_0_skinCluster 鐨?78 涓偣銆?```

v027 瀹為檯缁撴灉锛?
```text
鏉冮噸鍐欏叆:
write_count = 78
roundtrip_max_abs = 0.0

瀹為檯 Maya 杈撳嚭 vs v027 fresh matrix sim:
mean = 4.0422077838e-06
p95  = 7.2891439108e-06
max  = 7.5957912456e-06

skin-only p95:
0.0663520328 -> 0.0572382755

skin-only max:
0.0924744562 -> 0.0841200523

skin-only mean:
0.0228273639 -> 0.0228399407

visual fail_count:
1015 -> 1014

close_opposing_count:
865 -> 863

area_bad_faces:
126 -> 125

edge_bad_faces:
127 -> 127

normal_flip_faces:
66 -> 70
```

闃舵缁撹锛?
```text
v027 涓嶆槸瑙嗚澶т慨锛岃€屾槸鏉冮噸鏁版嵁婧愮籂鍋忥細
瀹冪‘璁ゆ纭柟鍚戞槸 source 鏉冮噸鍏堥獙 + fresh target matrix + 瀹為檯 Maya 楠屾敹銆?
涓嶈兘鍐嶇敤鏃?target matrix probe 鎺ㄦ柇褰撳墠鍦烘櫙锛?涔熶笉鑳借鍗曞Э鎬?LBS inverse 浠绘剰杩滅 source 鏉冮噸銆?
鍚庣画 weight-only 姹傝В搴旈噰鐢細

argmin E(W) =
  pose_fit_to_skin_only_target
+ source_weight_prior
+ current_weight_prior
+ patch_smooth
+ influence_family_limit
+ actual_graph_acceptance_gate

骞朵笖姣忔鍐欏洖鍚庡繀椤荤敤褰撳墠 Maya 瀹為檯鍥鹃噸鏂伴噰鏍烽獙璇併€?```

### 8.5 v028-v029 鎵嬬粯瀵圭収涓庣┛鎻掔洰鏍囦慨姝?
鐢ㄦ埛鍦?`cdfBaiXingG_body1_live_0` 涓婃墜缁樹簡涓€鐗堜綆绌挎彃鏉冮噸銆傝鏁版嵁涓嶈兘鐩存帴褰撲綔鏈€缁堢湡鍊硷紝鍥犱负鎵嬬粯缁撴灉涓嶄繚璇佸畬鍏ㄥ鍒?`M_Head_base`锛屼絾瀹冩彁渚涗簡涓€涓叧閿弽渚嬶細

```text
鏇磋创 source pose
涓嶇瓑浜?鏇村皯鍢村攪绌挎彃
```

绂荤嚎楠岃瘉缁撴灉锛?
- 鐢ㄦ埛鎵嬬粯涓?v027 鐨?influence 椤哄簭涓€鑷达紝宸紓涓嶆槸鐭╅樀鍒楅敊浣嶃€?- 鎵嬬粯鍦ㄥ槾閮?ROI 鏄庢樉澧炲姞 jaw / cheek / jaw-up 鏀拺锛屽噺灏戠函 lip chain 杩囨嫙鍚堛€?- 鐢ㄥ悓涓€ LBS matrix 璇勪及鏃讹紝鎵嬬粯鐨?source pose error 姣?v027 鏇村ぇ锛屼絾 visual fail 鏇翠綆銆?- `write78` 杈圭晫 inpaint銆乴ip-mix inpaint銆佹湁闄愭爣绛炬姇褰卞潎鏈夊眬閮ㄦ敼鍠勬垨鍙В閲婃€э紝浣嗕粛鏈揪鍒拌瑙夐棴鐜€?
鏈疆鏂板锛?
```text
core/semantic_label_solver.py
tests/test_semantic_label_solver.py
```

瀹冩妸 owner 鍏堜綔涓烘湁闄愭爣绛惧満姹傝В锛?
```text
E(label) =
  unary(weight / motion / geometry)
+ topology Potts smoothness
+ fixed high-confidence seeds
```

闃舵缁撹锛?
```text
鎵嬬粯鏍锋湰鐢ㄤ簬瀹氫箟鈥滀笉绌挎彃鍙傝€冣€濆拰璇婃柇鏂瑰悜锛?涓嶈兘鎶婃墜缁樻潈閲嶇洿鎺ヨ缁冩垚鍞竴绛旀銆?
涓嬩竴鐗堝繀椤绘妸鐩爣鍑芥暟鎷嗘垚锛?owner label accuracy
+ visual / collision risk
+ source pose error
+ source/current weight prior
+ topology smoothness
```

### 8.6 v031-v032 杩炵画闈?绌挎彃鍒嗙被涓?source-prior 鍥炲綊

鏈疆鍙鐞?skin锛屼笉杩涘叆 BS銆傜洰鏍囦粛閿佸畾锛?
```text
M_Head_base -> cdfBaiXingG_body1_live_0
```

鏂板鍙鐢ㄦā鍧楋細

```text
core/surface_contact_classifier.py
tests/test_surface_contact_classifier.py
tools/contact_aware_skin_candidate_v031.py
```

`surface_contact_classifier` 鐨勫垽瀹氶『搴忥細

```text
1. 鍏堢湅 mesh graph 鎷撴墤鐜窛绂汇€?2. 鎷撴墤杩戯細continuous_surface銆?3. 绌洪棿杩戜絾鎷撴墤杩滐細near_non_contiguous_surface銆?4. 鎷撴墤杩滀笖 owner 涓嶅悓锛歰wner_conflict銆?5. 鎷撴墤杩滀笖鐩稿弽娉曠嚎锛屾垨 owner+motion 鍚屾椂鍐茬獊锛歝ollision_risk銆?```

鍏抽敭纭锛?
```text
娉曠嚎瑙掑害/灏勭嚎绫讳俊鎭笉鑳藉崟鐙垽 owner锛?瀹冨彧鑳戒綔涓衡€滆繎灞傞闄╄瘉鎹€濄€?
涓婁笅鍢村攪鍜屾墜鎸囪繖绫诲尯鍩燂紝蹇呴』鍏堢敤鎷撴墤璺濈鍖哄垎锛?鍚屼竴杩炵画闈㈠眬閮ㄦ姌鍙?vs
绌洪棿杩戜絾鎷撴墤杩滅殑鍙︿竴鐗囪〃闈€?```

娴嬭瘯锛?
```text
python tests/test_surface_contact_classifier.py
python tests/test_visual_surface_gate.py
python tests/test_semantic_label_solver.py
python -m py_compile core/surface_contact_classifier.py tools/contact_aware_skin_candidate_v031.py
```

v031 绗竴鐗堣俯鍧戯細

```text
涓€寮€濮嬭鐢ㄤ簡 v023_lbs_matrix_probe 閲岀殑鏃?target matrix銆?璇?matrix 宸插湪 v027 琚‘璁や笉閫傚悎褰撳墠 v026/v027 鍦烘櫙銆?
淇鍚庯細
target rest / target faces / target weights / bindPre / worldMatrix
鍏ㄩ儴鏀圭敤:
.info/topology_support_matcher/v027_fresh_matrix_probe.npz
```

v031 contact-aware patch solver 缁撴灉锛?
```text
绛栫暐锛?鐢?contact 鍒嗙被闄愬埗 influence family锛?渚嬪 upper_lip 涓嶅厑璁稿悆 lower_lip 閾撅紝
lower_lip/jaw 涓嶅厑璁稿悆 upper_lip 閾俱€?
缁撴灉锛?write78 鐨?owner_conflict 鍙竻闆讹紝
浣?skin-only p95 鍙樺樊锛?v027 baseline p95 鈮?0.0543
v031 best p95 鈮?0.1030

缁撹锛?纭 lip influence 浼氱牬鍧?source-prior 褰㈠彉澶嶅埢锛?涓嶈兘鍐?Maya銆?```

v032 source-prior alpha sweep锛?
```text
鍊欓€?
.info/topology_support_matcher/v032_source_prior_alpha_candidate.npz
.info/topology_support_matcher/v032_source_prior_alpha_candidate.json

绛栫暐:
鍙湪 write78 涓婂仛
W = (1 - alpha) * W_current + alpha * W_source_prior
```

浠ｈ〃缁撴灉锛?
```text
v027 alpha=0.15:
skin-only mean = 0.03609
skin-only p95  = 0.05432
visual fail    = 1014
write78 collision_risk = 8

v032 alpha=1.0:
skin-only mean 鈮?0
skin-only p95  鈮?0
visual fail    = 1012
write78 collision_risk = 8
```

闃舵缁撹锛?
```text
鐩墠鏇村彲淇＄殑鏂瑰悜涓嶆槸鈥滄洿纭殑涓婁笅鍞?influence 绂佹琛ㄢ€濓紝
鑰屾槸鏇村ぇ骞呭害鍥炲埌 M_Head_base 鏄犲皠鍑烘潵鐨?source-prior 鏉冮噸銆?
v032 鍙綔涓轰笅涓€杞?Maya 瀹炴祴鍊欓€夛紱
浣嗗畠娌℃湁娑堥櫎 contact collision risk锛?鎵€浠ヤ笉鑳芥爣璁颁负鏈€缁堣В鍐炽€?
涓嬩竴姝ュ繀椤诲湪 Maya 瀹為檯鍥句腑鍐欏洖 v032 鍊欓€夊苟鐢ㄧ湡瀹?M_Jaw_A_ctrl.rotateX=25 鍥炶锛?1. skinCluster roundtrip
2. live target pose
3. final body visible pose
4. 鍢村攪灞€閮ㄦ埅鍥?纰版挒椋庨櫓
5. 涓庣敤鎴锋墜缁樺弬鑰冨姣?```

v032 Maya 瀹為檯鍐欏洖缁撴灉锛?
```text
鎵ц绔彛:
MCP foreground port 7099

褰撳墠鍦烘櫙:
ysj_chr_cdfBaiXingG_rig_rigMaster_v027_weightOnlySourcePrior_jaw25.ma

鍐欏叆:
cdfBaiXingG_body1_live_0_skinCluster
write_count = 78
roundtrip_max_abs = 0.0

澶囦唤:
_ai_backups/ysj_chr_cdfBaiXingG_rig_rigMaster_v027_weightOnlySourcePrior_jaw25_before_v032_skin_20260515_004325.ma

瀹為檯杈撳嚭:
.info/topology_support_matcher/v032_maya_write_actual_verify.json
.info/topology_support_matcher/v032_maya_write_actual_pose.npz
.info/topology_support_matcher/v032_maya_write_actual_visual_contact.json
```

瀹為檯鍥鹃獙鏀讹細

```text
Jaw25 live/final delta max = 1.2940729518 cm

visual fail:
813 -> 894

close_opposing:
780 -> 800

area_bad_faces:
38 -> 87

edge_bad_faces:
36 -> 93

normal_flip_faces:
0 -> 23

write78 contact:
collision_risk 0 -> 2
```

缁撹锛?
```text
v032 绂荤嚎 source-prior 鎸囨爣鐪嬩技鏇磋创 source锛?浣?Maya 瀹為檯渚濊禆鍥惧洖璇诲悗瑙嗚/鎺ヨЕ椋庨櫓鍙樺樊銆?
v032 涓嶈兘浣滀负閫氳繃鐗堟湰銆?褰撳墠闂浠嶇劧涓嶆槸绠€鍗曟妸 78 鐐瑰畬鍏ㄥ洖鍒?source-prior 鑳借В鍐炽€?涓嬩竴姝ュ繀椤绘敼涓?actual-graph-in-loop 鐨勫弬鏁板洖褰掞細
姣忕粍鏉冮噸鍐欏叆 Maya 鎴栫敤绛変环 actual graph 鏁版嵁璇勪及鍚庡啀鎺ュ彈銆?```

v032 鏍瑰洜澶嶇洏锛?
```text
v032 鍐欏叆閾捐矾鏈韩娌℃湁閿欙細
roundtrip_max_abs = 0.0

閿欒鍙戠敓鍦ㄥ€欓€夋潈閲嶏細
source-prior 鍏佽涓婁笅鍞?dominant-family flip銆?
鐏惧尯锛?upper_lip -> lower_lip: 8 鐐?lower_lip -> upper_lip: 3 鐐?
鍏朵腑 upper_lip -> lower_lip锛?mean delta = 0.8801889 cm
max delta  = 1.2940729 cm

鏈€澶ц烦鍙樼偣:
8796, 1631, 774, 9382, 9342
```

瀹炶返淇锛?
```text
1. v032 宸查€氳繃 MCP foreground 7099 鍥炴粴鍒板啓鍏ュ墠鏉冮噸銆?2. Maya 涓凡鍒涘缓骞堕€変腑:
   CDFDIAG_v032_crossLipBlocked_vtxSet
3. 鍚庣画鍊欓€変笉鑳藉啀鐢?stale fresh_matrix 褰?current銆?   蹇呴』鐢ㄥ疄闄呭啓鍏ュ墠 skinCluster 鍥炶 before_rows銆?4. 鍊欓€夋枃浠跺繀椤讳繚瀛橀€愮偣 provenance:
   target vertex
   source vertex
   source family
   current family
   predicted family
   geodesic/topology distance
   normal dot
   motion error
   actual graph acceptance
```

v020 clean baseline 閲嶅惎锛?
```text
clean baseline:
ysj_chr_cdfBaiXingG_rig_rigMaster_v020_semanticLiveBS.ma

鍘熷洜:
v021 涔嬪悗閮芥槸 skin-only 瀹為獙鏂囦欢锛?缁х画浣跨敤浼氭妸鍘嗗彶鍐欏叆姹℃煋鍜岀畻娉曢棶棰樻贩鍦ㄤ竴璧枫€?
鏂?probe:
.info/clean_v020_lip_probe/v020_clean_actual_probe.npz
.info/clean_v020_lip_probe/v020_clean_actual_probe_validation.json
```

鍏抽敭鏁版嵁淇锛?
```text
鏃ч敊璇?
鎸?0..N 鐗╃悊椤哄簭璇诲彇 bindPreMatrix銆?
瀹為檯:
MFnSkinCluster 鏉冮噸杩斿洖 influenceObjects 椤哄簭锛?bindPreMatrix 蹇呴』浣跨敤 indexForInfluenceObject 寰楀埌 logical index銆?
淇鍚?
source neutral LBS max error 鈮?1.719e-06
source jaw25 LBS max error 鈮?0.182cm  # 鍓╀綑涓哄姩鎬?BS 娈嬪樊
target jaw25 LBS p95 鈮?0.001cm
```

clean quick sweep锛?
```text
杈撳叆:
.info/clean_v020_lip_probe/v020_clean_topology_input.npz

鏃х粨鏋?
53 缁勫弬鏁板叏閮?accepted_count = 0

2026-05-15 澶嶆牳淇:
compute_family_scores 涓嶈兘鐢ㄥ畬鏁?DAG path 鍋?token 鍖归厤銆?瀹屾暣璺緞閲屾墍鏈?lip joint 閮藉寘鍚?M_Head_A_zero / M_HeadBt_A_zero锛?浼氬鑷?head family 瀵瑰槾鍞囩偣铏氬亣鍛戒腑銆?
姝ｇ‘鍙ｅ緞:
鍙敤 influence leaf 鍚嶏紝渚嬪 L_LoLip2_A_jnt / M_Head_A_jnt銆?
淇鍚?
quick sweep 53 缁勪腑 best accepted_count = 2锛?浣?risky_changed_count 浠嶄负 76锛屼粛涓嶈冻浠ュ啓 Maya銆?
瑙ｉ噴:
鍦ㄥ共鍑€銆佺煩闃垫纭殑鏁版嵁涓婏紝
褰撳墠 topology matcher 鍙壘鍒版瀬灏戦噺鍙畨鍏ㄥ啓鍏ョ殑 skin 鐐广€?
杩欏弽璇?v027/v032 鐨勫ぇ閲忓€欓€変富瑕佹潵鑷?
1. stale current probe
2. bindPreMatrix index 閿欎綅
3. source-prior 璺ㄤ笂涓嬪攪 flip 娌℃湁 actual graph gate
```

9194/1165 鐐圭骇褰掑睘鎺㈤拡锛?
```text
鏂板:
tools/lip_sheet_ownership_probe.py

杈撳嚭:
.info/clean_v020_lip_probe/v020_lip_sheet_ownership_probe.json

鍙:
涓嶅啓 Maya锛?涓嶆敼 skinCluster锛?鍙В閲婄偣绾у綊灞炶瘉鎹€?```

鍏抽敭缁撴灉锛?
```text
MCP 鍓嶅彴 7099 鍥炶褰撳墠 v020:
target vtx[9194] 褰撳墠鏉冮噸涓?R_LoLip2/3/1锛?target vtx[1165] 褰撳墠鏉冮噸涓?L_LoLip3/2/4锛?source vtx[1165] 瀹為檯鏄?M_Head + M_Jaw 鏀拺鐐癸紝涓嶆槸 lip 鐐广€?
9194:
绾?source unary 鏈€杩戣瘉鎹亸 lower_lip锛?鍔犲叆 target mesh graph Potts/MRF 鎷撴墤杩炵画鎬у悗锛?pairwise_strength >= 0.8 鏃剁炕鍥?upper_lip銆?
9150:
淇濇寔 lower_lip锛?鍚屾椂琚?surface contact 鏍囪涓?collision_risk銆?```

闃舵缁撹锛?
```text
涓嶈兘鍐嶆妸 target 褰撳墠閿欒鏉冮噸褰?seed锛?鍚﹀垯閿欒浼氶€氳繃鎷撴墤鎵╂暎鑷垜寮哄寲銆?
涓嬩竴姝?owner solver 搴旀敼鎴?
source 鏉冮噸宀?-> unary cost
+ target mesh graph -> Potts/MRF smoothness
+ contact/ray/normal -> risk penalty
+ actual graph -> 鍐欏墠楠屾敹

杩欎笉鏄€滃崟鐐瑰紓甯告嫆鍐欌€濓紝鑰屾槸鎶?9194 杩欑被鐐逛綔涓哄眬閮ㄥ洖褰掔敤渚嬶紝
瑕佹眰 solver 杈撳嚭涓轰粈涔堜粠 lower 鍊欓€夋敼鍒?upper銆?```

Graph Cut owner solver 鎶借薄锛?
```text
鏂板:
core/semantic_label_solver.py::solve_topology_labels_graphcut
core/source_owner_solver.py::solve_source_driven_owner_labels
tools/source_owner_graphcut_probe.py

绠楁硶:
source skin weights -> family unary cost
target mesh faces -> graph pairwise smoothness
surface contact -> 楂橀闄╃偣涓嶄綔鍥哄畾 seed
Graph Cut alpha-expansion -> owner label field

鍏抽敭鍖哄埆:
涓嶅啀鎶?target 褰撳墠閿欒鏉冮噸浣滀负 label seed锛?target 褰撳墠鏉冮噸鍙敤浜庡畾涔夊 ROI 鍜?contact 椋庨櫓璇婃柇銆?```

鐪熷疄 clean v020 缁撴灉锛?
```text
杈撳叆:
.info/clean_v020_lip_probe/v020_clean_topology_input.npz

杈撳嚭:
.info/clean_v020_lip_probe/v020_source_owner_graphcut_probe.json

ROI:
2585 鐐?
9194:
pairwise=0 鏃朵粛涓?lower_lip锛?pairwise>=0.8 鍚庝负 upper_lip锛?璇存槑璇ョ偣涓嶆槸鈥滃紓甯告嫆缁濃€濓紝鑰屾槸闇€瑕?patch 鎷撴墤鎶曠エ瑕嗙洊鍗曠偣鏈€杩戣瘉鎹€?
1165:
motion_weight=0/0.25 涓?pairwise>=0.8 鏃朵负 upper_lip锛?motion_weight=0.75 鏃跺浐瀹氫负 lower_lip锛?璇存槑 1165 浠嶉渶瑕佺敤鐪熷疄濮挎€佽宸?浜哄伐鏍囨敞楠岃瘉鍏惰繍鍔ㄨ涔夈€?
9150:
鎵€鏈夊弬鏁颁繚鎸?lower_lip锛?璇ョ偣鍚屾椂鏄?contact/collision 椋庨櫓鐐癸紝涓嶈兘琚?9194 鐨勪慨澶嶄竴璧风炕杞€?```

褰撳墠鍐崇瓥锛?
```text
涓嬩竴姝ヤ笉鐩存帴鍐?Maya銆?鍏堟妸 Graph Cut owner label 浣滀负鏉冮噸鍊欓€夌殑 family gate锛?
1. 瀵规瘡涓?target 鐐规眰 owner label銆?2. 鍙湪璇?label 鐨?source support 鍐呮壘鏉冮噸鏍锋湰銆?3. 淇濆瓨 best_source_indices / unary_cost / owner_confidence銆?4. 瀵?9194銆?165銆?150 鍋氶€愮偣鍥炲綊銆?5. 鍐嶈繘 actual graph gate 鍒ゆ柇鏄惁鍏佽鍐欏叆銆?```

娴嬪湴鏂规澶嶆牳锛?
```text
鏂板:
tools/geodesic_owner_need_probe.py

杈撳嚭:
.info/clean_v020_lip_probe/v020_geodesic_owner_need_probe.json

瀵规瘮瀵硅薄:
1. Graph Cut owner label
2. mesh graph Dijkstra 鍒?fixed owner seeds 鐨勬祴鍦拌窛绂?3. potpourri3d heat method 鍒?fixed owner seeds 鐨勬祴鍦拌窛绂?
瀹炵幇鍙樺寲:
core/source_owner_solver.py 澧炲姞鍙€?geodesic_prior_weight锛?榛樿 0锛屼笉鏀瑰彉鐜版湁姹傝В锛?鎵撳紑鍚庡彧鍔犲叆杞婚噺 Dijkstra prior锛?涓嶆妸 potpourri3d 浣滀负鐢熶骇纭緷璧栥€?```

鐪熷疄 clean v020 缁撴灉锛?
```text
heat method:
绗竴娆＄洿鎺ヨ窇 ROI 澶辫触锛屽師鍥犳槸 ROI 瀛愮綉鏍兼湁 2 涓湭琚?face 寮曠敤鐨勫鐐癸紱
compact referenced mesh 鍚庢垚鍔熴€?
heat vs Dijkstra:
valid_count = 2583
agree_count = 2385
agree_ratio = 0.9233
mismatch_count = 198

Dijkstra vs Graph Cut:
valid_count = 2584
agree_count = 2547
agree_ratio = 0.9857

9194:
GraphCut = upper_lip
Dijkstra nearest seed = upper_lip
Heat nearest seed = upper_lip

1165:
GraphCut = lower_lip
Dijkstra nearest seed = lower_lip
Heat nearest seed = lower_lip

9150:
GraphCut = lower_lip
Dijkstra nearest seed = lower_lip
Heat nearest seed = lower_lip
contact = collision_risk
```

褰撳墠鍒ゆ柇锛?
```text
闇€瑕佸紩鍏ユ祴鍦颁俊鎭紝浣嗗彧浣滀负 owner confidence / prior锛?涓嶆浛浠?source unary + Graph Cut銆?
鐢熶骇褰撳墠鍏堢敤 Dijkstra锛?渚濊禆灏戙€佽緭鍏ュ閿欓珮銆佸拰 Graph Cut 鏍囩涓€鑷寸巼楂樸€?
potpourri3d heat method 淇濈暀涓鸿瘖鏂?瀵圭収鍚庣锛?瀹冨湪 ROI 涓婅兘鎻愪緵鏇村钩婊戠殑鍐呰暣璺濈锛?浣嗗 unreferenced vertex / ROI mesh 娓呯悊鏇存晱鎰燂紝
鏆備笉浣滀负涓绘祦绋嬬‖渚濊禆銆?```

### 8.24 鏉冮噸鍒嗙澶嶆牳

鐩爣锛?
```text
纭褰撳墠缁撴灉鏄惁鑳戒粠鏄犲皠鎴栨潈閲嶄笂鐪嬪嚭涓婁笅鍞囧垎寮€銆?```

鏂板鍙璇婃柇锛?
```text
tools/source_owner_weight_separation_probe.py
```

杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v020_source_owner_weight_separation_probe.json
```

璇婃柇閾捐矾锛?
```text
current target weight family
鈫?source-driven Graph Cut owner label
鈫?owner-selected source weight family
鈫?safe candidate gate
```

鐪熷疄 clean v020 缁撴灉锛?
```text
ROI = 2585

owner label counts:
upper_lip = 685
lower_lip = 697
jaw = 1203

current target family -> owner:
upper_lip->upper_lip = 628
upper_lip->lower_lip = 24
lower_lip->lower_lip = 613
lower_lip->upper_lip = 2

owner -> chosen source family:
upper_lip->upper_lip = 640
lower_lip->lower_lip = 662
jaw->jaw = 1055

owner/source family match ratio = 0.9118
lip owner/source family match ratio = 0.9421
safe candidate ratio = 0.4723
safe lip candidate ratio = 0.4313

unsafe reasons:
source_family_mismatch = 228
low_owner_mass = 1251
opposite_lip_mass_high = 52
collision_risk = 114
```

鍏抽敭鐐癸細

```text
9194:
current target family = lower_lip 0.9986
owner label = upper_lip
chosen source = 8960
chosen source family = upper_lip 1.0
opposite lower_lip mass = 0.0
contact = clear

1165:
current target family = lower_lip 1.0
owner label = lower_lip
chosen source = 1417
chosen source family = lower_lip 0.9999999
opposite upper_lip mass = 0.0
contact = clear

9150:
current target family = lower_lip 0.99994
owner label = lower_lip
chosen source = 8538
chosen source family = lower_lip 0.99994
opposite upper_lip mass = 0.0
contact = collision_risk
```

褰撳墠鍒ゆ柇锛?
```text
鏄犲皠灞傚凡缁忚兘鎶?9194 杩欑被涓婁笅鍞囪繎灞傜偣鍒嗗紑锛?owner 瀵瑰簲鐨?source 鏉冮噸琛屼篃鑳界粰鍑哄垎寮€鐨?upper/lower lip family銆?
浣嗗綋鍓?target 鏉冮噸鏈韩涓嶈兘浣滀负姝ｇ‘渚濇嵁锛?9194 褰撳墠 target 鏉冮噸浠嶅嚑涔庡叏鏄?lower_lip銆?
鍚屾椂 safe candidate 鍙鐩栫害 47% ROI / 43% lip owner锛?璇存槑鐜板湪鍙兘杩涘叆鍊欓€夌敓鎴愪笌绂荤嚎濮挎€侀獙鏀讹紝
涓嶈兘鏁村湀鐩存帴鍐?Maya銆?```

### 8.25 v035-v036 owner-gated 鏉冮噸鍊欓€変笌 envelope 瀹為檯楠屾敹

鐩爣浠嶅彧澶勭悊 Skin锛屼笉杩涘叆 BS锛?
```text
M_Head_base
-> cdfBaiXingG_body1_live_0
```

鏂板鑴氭湰锛?
```text
tools/source_owner_weight_candidate_v035.py
tools/maya_apply_v035_owner_gated_candidate.py
tools/maya_diagnose_v035_binding_graph.py
tools/maya_capture_v035_envelope1_before_after.py
tools/validate_v035_maya_envelope1_actual.py
tools/analyze_v035_remaining_errors.py
```

鏍稿績绛栫暐锛?
```text
source 鏉冮噸琛屾寜 joint leaf name 瀵归綈鍒?target influences
+ Graph Cut owner label 浣滀负 family gate
+ Dijkstra geodesic prior 浣滀负 owner confidence
+ current/source alpha 鎼滅储
+ motion error / neutral drift / contact risk / owner family 澶氶棬鎺?+ Maya actual graph 鍥炶楠屾敹
```

v035 绂荤嚎鍊欓€夛細

```text
ROI = 2585
safe_base = 1221
accepted = 92
alpha:
  0.25 -> 8
  0.50 -> 6
  0.75 -> 14
  1.00 -> 64

accepted motion error:
mean 0.1380 -> 0.0267
p95  0.5903 -> 0.0691

visual:
fail_count        246 -> 231
close_opposing    865 -> 862
area_bad_faces    152 -> 115
edge_bad_faces    151 -> 114
normal_flip_faces 65  -> 54
collision_added   0
```

Maya 鍐欏洖锛?
```text
MCP foreground port:
7099

鍐欏叆 skinCluster:
cdfBaiXingG_body1_live_0_skinCluster

鍐欏叆鐐?
92

澶囦唤:
_ai_backups/ysj_chr_cdfBaiXingG_rig_rigMaster_v020_before_v035_ownerGatedSkin_20260515_102537.ma

v035 杈撳嚭:
ysj_chr_cdfBaiXingG_rig_rigMaster_v035_ownerGatedSkin.ma
```

鍏抽敭韪╁潙锛?
```text
cdfBaiXingG_body1_live_0_skinCluster.envelope = 0
```

鍥犳绗竴娆?actual graph 楠岃瘉鍑虹幇锛?
```text
skinPercent roundtrip 姝ｇ‘锛?浣?before/after live/final 椤剁偣浣嶇疆瀹屽叏涓嶅彉銆?```

杩欎笉鏄潈閲嶆病鏈夊啓杩涘幓锛岃€屾槸 skinCluster 娌¤鍚敤銆傝瘖鏂剼鏈‘璁よ skinCluster 鍦ㄧ洰鏍囧巻鍙查摼涓婏細

```text
cdfBaiXingG_body1_live_0_M_Head_base_blendShape
-> cdfBaiXingG_body1_live_0_skinCluster
-> visible shape
-> cdfBaiXingG_body1_body_msh_blendShape inputGeomTarget
```

涓存椂鎵撳紑 envelope 鍚庯紝鐐逛綅杩愬姩绔嬪嵆鎭㈠锛?
```text
9194 delta:
envelope=0 绾?0.043cm
envelope=1 绾?1.195cm

1165 delta:
envelope=0 绾?0.053cm
envelope=1 绾?2.944cm

9150 delta:
envelope=0 绾?0.039cm
envelope=1 绾?2.755cm
```

v035 鍐欏墠澶囦唤 vs v035 杈撳嚭锛岀粺涓€ `envelope=1` 鍚庣殑 actual graph 楠屾敹锛?
```text
ROI motion error:
mean 0.02533 -> 0.02126
p95  0.08729 -> 0.07715
p99  0.25313 -> 0.21630

write 鐐?motion error:
mean 0.13801 -> 0.02367
p95  0.59035 -> 0.06477
max  1.65827 -> 0.08127

write 鐐?neutral delta:
0

visual:
fail_count        246 -> 237
area_bad_faces    152 -> 123
edge_bad_faces    151 -> 119
normal_flip_faces 65  -> 53
close_opposing    865 -> 871

contact:
ROI collision_risk 38 -> 36
write collision_added 0
```

鍏抽敭鐐癸細

```text
9194:
accepted = true
chosen_source = 8960
before_error = 1.65777
after_error  = 0.06294
improvement  = 1.59483
contact      = clear -> clear

1165:
accepted = false
before_error = 0.00660
鍘熷洜: 褰撳墠宸茬粡姣?source 琛屾洿濂斤紝no_alpha_improved

9150:
accepted = false
before_error = 0.02987
鍘熷洜: collision_risk / unsafe锛屼笉闅?9194 涓€璧风炕杞?```

淇濆瓨缁欎汉宸ュ楠岀殑鍦烘櫙锛?
```text
ysj_chr_cdfBaiXingG_rig_rigMaster_v036_ownerGatedSkin_envelopeOn_jaw25.ma
```

璇ュ満鏅槸楠岃瘉鍦烘櫙锛?
```text
cdfBaiXingG_body1_live_0_skinCluster.envelope = 1
M_Jaw_A_ctrl.rotateX = 25
```

褰撳墠缁撹锛?
```text
v035/v036 璇佹槑 owner-gated source row + actual graph gate 鍙互淇 9194 杩欑被涓婁笅鍞囪繎灞傞敊璇偣銆?瀹冩槸姝ｆ敹鐩婄増鏈紝涓嶆槸鏈€缁堝畬鍏ㄩ棴鐜増鏈€?
鍓╀綑鏈€楂樿宸偣澶у琚?unsafe_base 鎷掔粷锛?涓嶈兘涓轰簡闄嶄綆鍗曠偣璇樊鐩存帴鎵╁ぇ鍐欏叆銆?鍚庣画瑕侀拡瀵?unsafe 鐐圭粏鍒嗘嫆缁濆師鍥狅紝
鍐嶅仛 patch-level 澶氱洰鏍囦紭鍖栵細
source pose error
+ visual/contact penalty
+ owner/source family confidence
+ topology smoothness
+ actual graph gate
```

v037 缁х画灏濊瘯锛?
```text
鍏佽鈥滃啓鍓嶅凡缁忔槸 collision_risk鈥濈殑鐐硅繘鍏?alpha 鎼滅储锛?浣嗕粛瑕佹眰 owner/source family銆乵otion improvement銆乶eutral delta 閫氳繃銆?```

绂荤嚎缁撴灉锛?
```text
accepted:
92 -> 127

accepted motion error:
mean 0.1908 -> 0.0337
p95  1.2341 -> 0.0710

visual:
fail_count        246 -> 229
area_bad_faces    152 -> 97
edge_bad_faces    151 -> 93
normal_flip_faces 65  -> 37
```

瀹為檯鍥惧洖璇诲悗锛寁037 琚帴瑙﹂闄╁弽璇侊細

```text
ROI collision_risk:
38 -> 41

write collision:
9 -> 12

new collision on accepted:
8
```

鍥犳 v037 涓嶈兘浣滀负鎺ㄨ崘楠屾敹鐗堟湰銆?
v038 淇锛?
```text
浠?v037 涓墧闄ゅ疄闄呮柊澧?collision 鐨?8 涓偣锛?骞舵妸杩欎簺鐐瑰洖婊氬埌 clean v020 鏉冮噸銆?```

v038 瀹為檯鍥鹃獙鏀讹細

```text
accepted = 119
rollback = 8

ROI motion error:
mean 0.02533 -> 0.01911
p95  0.08729 -> 0.07283
p99  0.25313 -> 0.20349

accepted motion error:
mean 0.16421 -> 0.02902
p95  1.22793 -> 0.07606
max  1.65827 -> 0.08424

contact:
ROI collision_risk      38 -> 31
accepted collision_risk 9  -> 4
new accepted collision  0

visual:
fail_count        246 -> 232
close_opposing    865 -> 864
area_bad_faces    152 -> 116
edge_bad_faces    151 -> 115
normal_flip_faces 65  -> 45
```

鍏抽敭鐐癸細

```text
9194:
accepted = true
1.65777 -> 0.06294
clear -> clear

9346:
v037 鍙檷浣?motion error锛?浣嗕細寮曞叆 collision_risk锛?v038 鍥炴粴璇ョ偣锛屼笉鍐欍€?
9380 / 9344 / 9177 / 9139:
琚?v038 淇濈暀锛?motion error 浠庣害 1.22-1.24 闄嶅埌 0.06-0.084銆?```

褰撳墠鎺ㄨ崘浜哄伐澶嶉獙鍦烘櫙锛?
```text
ysj_chr_cdfBaiXingG_rig_rigMaster_v038_collisionFilteredSkin_envelopeOn_jaw25.ma
```

褰撳墠鍐崇瓥锛?
```text
v038 鏄繘鍏?patch-level 鍓嶇殑 skin-only 绋冲畾楠屾敹鐗堟湰锛?瀹冩瘮 v035 淇洿澶氶珮璇樊鐐癸紝
鍚屾椂閬垮厤 v037 鐨勬柊澧?collision銆?
鍚庣画濡傛灉瑙嗚浠嶄笉婊℃剰锛?涓嶈兘鍐嶆寜鍗曠偣 motion error 缁х画鏀惧锛?瑕佽繘鍏?patch-level 澶氱洰鏍囨眰瑙ｏ細
1. 鍙厑璁?contact 涓嶅彉濂?涓嶅彉鍧忕殑鍊欓€夊啓鍏ャ€?2. 瀵?collision 鍖哄煙鍗曠嫭寤哄眬閮?patch锛岃€屼笉鏄€愮偣鏀捐銆?3. 鍔犲叆澶氬Э鎬侊紝鑰屼笉鏄彧鎷熷悎 Jaw25銆?```

### v039 / v040 Patch-Level 灏忔鎺ㄨ繘

v039 棣栧厛鍙皾璇?9346锛?
```text
patch solver:
smooth = 3.0
prior  = 0.05
neutral = 1.0
alpha = 1.0

write rows:
9346
```

瀹為檯鍥鹃獙鏀讹細

```text
9346:
1.66716 -> 0.56043
clear -> clear
new collision = 0
```

v040 灏嗗悓涓€灞€閮?patch 鎵╁睍涓?3 涓啓鍏ョ偣锛?
```text
write rows:
9337
9340
9346
```

瀹為檯鍥鹃獙鏀讹細

```text
ROI motion error:
mean 0.02533 -> 0.01834
p95  0.08729 -> 0.07283
p99  0.25313 -> 0.19381
max  1.66716 -> 0.86657

accepted motion error:
mean 0.94340 -> 0.27854
max  1.66716 -> 0.56043

contact:
ROI collision_risk      38 -> 31
accepted collision_risk 0  -> 0
new accepted collision  0

visual:
fail_count        246 -> 232
close_opposing    865 -> 863
area_bad_faces    152 -> 110
edge_bad_faces    151 -> 107
normal_flip_faces 65  -> 43
```

鍏抽敭鐐癸細

```text
9337:
0.29383 -> 0.12182
continuous_surface -> continuous_surface

9340:
0.86922 -> 0.15336
clear -> continuous_surface

9346:
1.66716 -> 0.56043
clear -> clear
```

缁х画灏濊瘯 9175 / 9176 鎵€鍦?patch 鏃惰闂ㄦ帶鎷掔粷锛?
```text
reject reasons:
new_collision
roi_collision_worse
```

### v041 / v042 澶氬Э鎬佸洖褰?
```text
v040 澶氬Э鎬侀噰鏍凤細
jaw rotateX = 0 / 5 / 10 / 15 / 20 / 25 / 30
```

缁撹锛?
```text
v040 鍦?Jaw25 鍗曞Э鎬佷笂鏇村噯锛?浣?9340 鍦?jaw 5/10/15 浼氫粠 clear 鍙樹负 collision_risk銆?
鎵€浠?v040 涓嶈兘浣滀负鏈€缁堟帹鑽愮増鏈€?鍗曞Э鎬?Jaw25 閫氳繃锛屼笉绛変簬鐪熷疄鎺у埗鍣ㄥ尯闂撮€氳繃銆?```

v041 鍥為€€ 9340锛?
```text
write rows:
9337
9346
```

澶氬Э鎬佺粨鏋滐細

```text
source scene drift = 0
neutral delta = 0
new accepted collision = 0

Jaw25:
ROI mean 0.02533 -> 0.01861
ROI p99  0.25313 -> 0.20187
ROI max  1.66716 -> 0.86922
visual fail 246 -> 232
```

浣嗘槸 v038 鍩虹鏉冮噸鍦ㄤ綆瑙掑害鏈韩鏈?ROI collision 澧為噺锛?
```text
jaw 5:  collision_risk 48 -> 52
jaw 10: collision_risk 49 -> 52
```

缁х画瀹氫綅鍚庣‘璁や綆瑙掑害鏂板 collision 鐨勪富瑕?v038 鍐欏叆鐐癸細

```text
9139
9177
9344
9380
```

杩欎簺鐐圭殑杩愬姩璇樊鏀瑰杽寰堝ぇ锛?浣嗘妸 lower lip 灞€閮ㄦ帴瑙︾姸鎬佹帹鍏?collision_risk銆?
v042 绛栫暐锛?
```text
淇濈暀:
9337
9346

鍥炴粴鍒板師濮嬫潈閲?
9139
9177
9344
9380
```

v042 澶氬Э鎬佸疄闄呭浘楠屾敹锛?
```text
source scene drift = 0
neutral delta = 0
new accepted collision = 0

jaw 5:
ROI collision 48 -> 46
ROI mean      0.00512 -> 0.00412
ROI max       0.33516 -> 0.24991

jaw 10:
ROI collision 49 -> 44
ROI mean      0.01021 -> 0.00823
ROI max       0.67009 -> 0.49933

jaw 15:
ROI collision 46 -> 39
ROI mean      0.01529 -> 0.01231
ROI max       1.00415 -> 0.74783

jaw 25:
ROI collision 38 -> 30
ROI mean      0.02533 -> 0.02041
ROI p99       0.25313 -> 0.21074
ROI max       1.66716 -> 1.24026
visual fail   246 -> 231
area/edge/normal 152/151/65 -> 121/121/50
```

鍙栬垗锛?
```text
v041:
motion 鏇村噯锛孞aw25 max 鏇翠綆锛?浣嗕綆瑙掑害 ROI collision 娌跨敤 v038 椋庨櫓銆?
v042:
浣庤搴︽帴瑙︽洿瀹夊叏锛屾墍鏈夎鍐欏叆鐐规棤鏂板 collision锛?浣?9139/9177/9344/9380 鍥炴粴鍚庡眬閮?motion error 鍙樺ぇ銆?```

v043 绛栫暐锛?
```text
涓嶅啀瀵逛綆瑙掑害椋庨櫓鐐瑰仛浜岄€変竴鍥炴粴锛?鏀逛负澶氬Э鎬?contact-gated alpha 鍥炲綊锛?
safe = v042
risk = v038 / v040 瀵瑰簲鐐?candidate = safe + alpha * (risk - safe)

闂ㄦ帶锛?- 0/5/10/15/20/25/30 搴﹂€愬Э鎬佹鏌?- 涓嶅厑璁?accepted point 鏂板 collision_risk
- 涓嶅厑璁?ROI collision 澧炲姞
- 蹇呴』鏀瑰杽 source motion error
```

v043 alpha 缁撴灉锛?
```text
9139 = 0.35
9177 = 0.75
9340 = 0.50
9344 = 0.50
9380 = 0.75

缁х画淇濈暀 patch 鍐欏叆:
9337
9346
```

v043 Maya 瀹為檯鍥鹃獙鏀讹細

```text
scene:
ysj_chr_cdfBaiXingG_rig_rigMaster_v043_alphaRegressionSkin_envelopeOn_jaw25.ma

write_count = 7
row_max_abs_diff = 3.45e-09
source scene drift = 0
neutral delta = 0
new accepted collision = 0

jaw 5:
ROI collision 48 -> 46
ROI mean      0.00512 -> 0.00388
ROI max       0.33516 -> 0.17463
visual fail   133 -> 123

jaw 10:
ROI collision 49 -> 44
ROI mean      0.01021 -> 0.00775
ROI max       0.67009 -> 0.34891
visual fail   159 -> 154

jaw 15:
ROI collision 46 -> 39
ROI mean      0.01529 -> 0.01159
ROI max       1.00415 -> 0.52252
visual fail   201 -> 192

jaw 25:
ROI collision 38 -> 30
ROI mean      0.02533 -> 0.01922
ROI p99       0.25313 -> 0.21074
ROI max       1.66716 -> 0.86657
visual fail   246 -> 232
```

鐩稿 v042 鐨勫彉鍖栵細

```text
v043 淇濇寔 v042 鐨?contact 瀹夊叏鎬э細
鎵€鏈夋祴璇曡搴?collision count 涓?v042 涓€鑷淬€?
v043 motion 鏇村ソ锛?Jaw25 ROI mean 0.02041 -> 0.01922
Jaw25 ROI max  1.24026 -> 0.86657

浠ｄ环锛?15/20/25/30 搴?visual fail 姣?v042 澶氱害 1 涓紝
鍚庣画鍙妸 visual gate 涔熺撼鍏?alpha 鎼滅储鎴?patch-level LBS penalty銆?```

v044 缁х画澶勭悊 v043 鍚庝粛鏈垎灞傜殑 25 涓?unresolved 鐐癸細

```text
source-prior unresolved candidate:
24 / 25 鐐圭绾块€氳繃锛?386 琚嫆缁濄€?
Maya 瀹為檯澶氬Э鎬侀獙鏀?
鏁翠綋璇樊涓嬮檷锛屼絾 748 / 9175 / 9176 鍦?5/10/15/20/30 搴︽柊澧?accepted collision锛?鍥犳 v044 涓嶈兘浣滀负鎺ㄨ崘鐗堟湰銆?```

v045 杩囨护鎺?v044 涓柊澧?collision 鐨?3 涓偣锛?
```text
鎾ゅ洖:
748
9175
9176

淇濈暀鏂板鍐欏叆:
21 鐐?
Maya 瀹為檯澶氬Э鎬侀獙鏀?
source drift = 0
neutral delta = 0
new accepted collision = 0
Jaw25 ROI mean 0.02533 -> 0.01779
Jaw25 ROI p95  0.08729 -> 0.06961
Jaw25 ROI max  1.66716 -> 0.86657
Jaw25 collision 38 -> 30
Jaw25 visual fail 246 -> 228
```

v046 瀵瑰墿浣欑偣鍋?alpha rescue锛?
```text
748:
alpha = 0.25
Jaw25 error 0.30454 -> 0.22357
澶氬Э鎬?contact / visual gate 閫氳繃

9175 / 9176:
alpha 0.05-0.75 鍧囪鎺ヨЕ鎴?visual gate 鎷掔粷

9386:
鏃?v044 瀹夊叏 delta锛岀户缁嫆缁?
Maya 瀹為檯澶氬Э鎬侀獙鏀?
source drift = 0
neutral delta = 0
new accepted collision = 0
Jaw25 ROI mean 0.02533 -> 0.01776
Jaw25 ROI p95  0.08729 -> 0.06961
Jaw25 ROI max  1.66716 -> 0.86657
Jaw25 collision 38 -> 30
Jaw25 visual fail 246 -> 227
```

v047 灏濊瘯 `9175 / 9176` 鎴愬 alpha 鎼滅储锛?
```text
63 缁勭粍鍚堝叏閮ㄨ鎷掔粷锛?- 楂?alpha 鍦?jaw 5/10 搴︽柊澧?collision 鎴?ROI collision 澧炲姞
- 浣?alpha 鍦?jaw 15 搴?visual fail 澧炲姞

缁撹:
9175 / 9176 涓嶈兘鍐嶉潬褰撳墠 Skin 鏉冮噸琛岀户缁啓銆?9386 涔熸病鏈夊畨鍏?source-prior delta銆?杩欎簺鐐瑰鏋滆繕瑕佺户缁敼鍠勶紝搴旇繘鍏?corrective / residual 鎴栨洿寮?patch-level 褰㈤潰鐩爣锛?涓嶈兘绐佺牬 contact / visual 闂ㄧ纭啓 skin銆?```

v046 褰撴椂鎺ㄨ崘浜哄伐澶嶉獙鍦烘櫙涓猴細

```text
ysj_chr_cdfBaiXingG_rig_rigMaster_v046_remainingAlphaRescueSkin_envelopeOn_jaw25.ma
```

v046 褰撴椂 skin-only 鍐崇瓥锛?
```text
v046 鏄綋鏃舵帹鑽愪汉宸ュ楠岀増鏈€?v045 鏄皯鍐?748 鐨勪繚瀹堝畨鍏ㄥ鐓х増鏈€?v043 鏄繘鍏?unresolved source-prior 鍓嶇殑瀹夊叏鍩虹嚎銆?
鍓╀綑楂樿宸偣:
9175
9176
9386

杩欎簺鐐瑰凡缁忛€氳繃閫愮偣 alpha 鍜屾垚瀵?alpha 鎼滅储琚瘉鏄庝笉鑳藉畨鍏ㄥ啓 Skin銆?褰撳墠闃舵搴斿仠姝㈣拷閫愬崟鐐?max error銆?涓嬩竴姝ヨ嫢瑙嗚浠嶄笉婊℃剰锛屽簲杞悜 residual/corrective 鎴?patch-level surface objective锛?鑰屼笉鏄户缁妸 source-prior 鏉冮噸纭啓杩?skinCluster銆?```

### 8.27 v048 鐢ㄦ埛鎸囧嚭鐐圭殑鍐呰暣瀵瑰簲淇

鐢ㄦ埛浜哄伐鎸囧嚭浠ヤ笅鐐逛粛涓烘潈閲嶈鍒わ細

```text
cdfBaiXingG_body1_live_0.vtx[8796]
cdfBaiXingG_body1_live_0.vtx[8949]
cdfBaiXingG_body1_live_0.vtx[9232]
cdfBaiXingG_body1_live_0.vtx[9233]
cdfBaiXingG_body1_live_0.vtx[9288]
cdfBaiXingG_body1_live_0.vtx[9289]
```

瀹¤缁撹锛?
```text
Maya 褰撳墠鏉冮噸:
6 鐐瑰嚑涔庡叏涓?lower_lip

鏃?Graph Cut owner:
浠嶅垽 lower_lip

鏃у濮挎€佽宸?
鏁板€艰緝浣庯紝鍥犳涓嶄細琚?unresolved error threshold 鎹炲嚭

闂鏍瑰洜:
current target 鏉冮噸鍜屾姘忚繎閭诲叡鍚岃嚜璇?lower_lip锛?浣嗚繖 6 鐐瑰湪鐢ㄦ埛瑙嗚楠屾敹涓睘浜?upper_lip 渚ч敊璇€?```

鏂板鍙璇曢獙锛?
```text
tools/probe_user_flagged_intrinsic_correspondence.py
```

绠楁硶锛?
```text
lip / jaw 灞€閮?patch
-> source / target 鍚勫彇鏋佸€?landmark + family center landmark
-> 鍦?mesh graph 涓婅绠?landmark geodesic distance descriptor
-> 鎷兼帴 normalized xyz / geodesic / normal / Jaw motion
-> target 鐐规煡璇?source 鐐?```

缁撴灉锛?
```text
geo_heavy 鏂规硶灏?6 鐐瑰叏閮ㄦ槧灏勫埌 upper_lip source锛?8796 -> source 8885 upper_lip 0.9536
8949 -> source 8884 upper_lip 0.9384
9232 -> source 9052 upper_lip 0.9093
9233 -> source 8725 upper_lip 0.9656
9288 -> source 8561 upper_lip 0.7497 / lower_lip 0.2311
9289 -> source 8715 upper_lip 0.9806
```

鍐欏叆鏂囦欢锛?
```text
ysj_chr_cdfBaiXingG_rig_rigMaster_v048_userFlaggedIntrinsicSkin_envelopeOn_jaw25.ma
```

Maya 瀹為檯澶氬Э鎬侀獙鏀讹細

```text
source drift = 0
neutral delta = 0
new accepted collision = 0

Jaw25 ROI mean 0.02533 -> 0.01788
Jaw25 ROI p95  0.08878 -> 0.07117
Jaw25 ROI max  1.66716 -> 0.86657
Jaw25 collision 38 -> 30
Jaw25 visual fail 246 -> 226

鐢ㄦ埛鎸囧嚭 6 鐐癸細
8796 error 1.07783 -> 0.08271
8949 error 0.86045 -> 0.08077
9232 error 0.65423 -> 0.07111, collision_risk -> clear
9233 error 0.86413 -> 0.08177
9288 error 0.65930 -> 0.08060
9289 error 0.99413 -> 0.07571
```

鏇存柊鍚庣殑褰撳墠鎺ㄨ崘浜哄伐澶嶉獙鍦烘櫙锛?
```text
ysj_chr_cdfBaiXingG_rig_rigMaster_v048_userFlaggedIntrinsicSkin_envelopeOn_jaw25.ma
```

鏂板鍐崇瓥锛?
```text
1. 浣?pose error 涓嶇瓑浜庢潈閲嶆纭€?2. 鐢ㄦ埛鎸囧嚭鐨勫眬閮ㄩ敊鏉冭杩涘叆 source correspondence 閲嶅锛岃€屼笉鏄彧鐪?unresolved threshold銆?3. 瀵逛笂涓嬪攪銆佹墜鎸囪繖绫昏繎灞傚尯鍩燂紝鍊欓€夋簮搴斿姞鍏?intrinsic/geodesic landmark descriptor銆?4. Graph Cut owner 浠嶆湁浠峰€硷紝浣嗗綋 current target 鏉冮噸鍏堥獙宸茬粡閿欐椂锛屽繀椤诲厑璁?intrinsic correspondence 鎺ㄧ炕瀹冦€?```

### 8.28 v049 鑷姩鍐茬獊鎺㈤拡锛氭潈閲嶇浉浼煎害涓庢祴鍦拌窛绂诲鐓?
鐩爣锛?
```text
楠岃瘉鏄惁鑳藉湪涓嶈緭鍏ョ敤鎴风偣鍙风殑鎯呭喌涓嬶紝鑷姩鍙戠幇鈥滃綋鍓嶆潈閲嶆爣绛锯€濆拰鈥滃唴钑?source 瀵瑰簲鏍囩鈥濆啿绐佺殑杩戝眰鐐广€?```

鏂板鑴氭湰锛?
```text
tools/maya_probe_lip_weight_geodesic_contradiction.py
tools/probe_lip_intrinsic_conflict_sweep.py
```

Maya 鍓嶅彴楠岃瘉锛?
```text
foreground_port = 7099
scene = ysj_chr_cdfBaiXingG_rig_rigMaster_v048_userFlaggedIntrinsicSkin_envelopeOn_jaw25.ma
target = cdfBaiXingG_body1_live_0
```

杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v049_current_weight_geodesic_contradiction.json
.info/clean_v020_lip_probe/v049_intrinsic_conflict_sweep.json
```

鍏抽敭缁撴灉锛?
```text
鐩稿弽鍞囨渶杩戠┖闂村€欓€夋櫘閬嶈〃鐜颁负锛?euclidean 杩?geodesic / euclidean = 2x ~ 5x

璇存槑绾┖闂存渶杩戠偣纭疄浼氳法鍞囧眰璇€夈€?```

鐢ㄦ埛鎸囧嚭鐐圭殑褰撳墠 v048 Maya 鍦烘櫙瀹炴祴渚嬪瓙锛?
```text
8796 -> nearest opposite by space: euclidean 0.6993, geodesic 1.5743, ratio 2.25
8949 -> nearest opposite by space: euclidean 0.4684, geodesic 1.3094, ratio 2.80
9232 -> nearest opposite by space: euclidean 0.3440, geodesic 1.6959, ratio 4.93
9288 -> nearest opposite by space: euclidean 0.3013, geodesic 0.8121, ratio 2.70
9289 -> nearest opposite by space: euclidean 0.6717, geodesic 1.5577, ratio 2.32
```

鏃犱汉宸ョ偣鍙风殑 lip ROI 鍐呰暣鍐茬獊 sweep锛?
```text
hard_conflict_count = 30
borderline_conflict_count = 21
8796 hard
8949 hard
9232 hard
9233 hard
9288 borderline
9289 hard
```

`9288` 娌¤繘 hard 鐨勫師鍥犳槸 source upper score 涓?`0.749596978`锛岀暐浣庝簬 hard 闃堝€?`0.75`锛屽洜姝ゅ綊鍏?borderline锛岃€屼笉鏄柟鍚戝け璐ャ€?
鏂板鍐崇瓥锛?
```text
1. 鑷姩鍙戠幇閿欐潈涓嶅簲浣滀负鍐欏悗琛ヤ竵锛岃€屽簲鎴愪负鍐欏墠 owner prepass銆?2. current target 鏉冮噸鍙綔涓哄啿绐佸璞★紝涓嶈兘浣滀负鍞竴 owner 鐪熷€笺€?3. intrinsic conflict = current lip family 涓?source intrinsic lip family 鐩稿弽銆?4. hard/confidence 鐩存帴杩涘叆鍊欓€夛紝borderline 杩涘叆 patch-level 鎴?actual-graph 澶嶆牳銆?5. 鏉冮噸鐩镐技鏈韩涓嶈冻浠ヨ瘉鏄庡搴旓紝蹇呴』鍚屾椂鐪?mesh graph 娴嬪湴鍏崇郴銆?```

### 8.29 v049 娉曠嚎/鏈濆悜/灞€閮ㄧ幆鐗瑰緛瀵圭収

鐩爣锛?
```text
姣旇緝 intrinsic conflict 鐐逛笌姝ｇ‘鍚屽攪鐐圭殑闈炴祴鍦扮壒寰侊紝鍒ゆ柇娉曠嚎瑙掑害銆佹湞鍚戙€佸眬閮ㄩ偦鍩熸槸鍚﹁兘杈呭姪鍖哄垎銆?```

鏂板鑴氭湰锛?
```text
tools/probe_lip_conflict_feature_contrast.py
```

杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v049_lip_conflict_feature_contrast.json
```

鍒嗙粍瀹氫箟锛?
```text
hard_conflict:
  current target lip family 涓?intrinsic source lip family 鐩稿弽锛宻ource lip score >= 0.75

borderline_conflict:
  current target lip family 涓?intrinsic source lip family 鐩稿弽锛?.70 <= source lip score < 0.75

correct_same_lip:
  current target lip family 涓?intrinsic source lip family 涓€鑷达紝source lip score >= 0.75
```

缁熻缁撴灉锛?
```text
all_scored_rows = 725
hard_conflict = 30
borderline_conflict = 21
correct_same_lip = 597
user_rows = 6
```

鍏抽敭瀵圭収锛?
| 鐗瑰緛 | 鐢ㄦ埛 6 鐐?| hard conflict | correct same-lip | 缁撹 |
|---|---:|---:|---:|---|
| target-source normal angle mean | 8.89掳 | 7.72掳 | 4.38掳 | 閿欑偣娉曠嚎鍖归厤鏇村樊锛屽彲浣滆緟鍔?|
| target-source motion angle mean | 4.03掳 | 3.53掳 | 0.80掳 | 杩愬姩鏂瑰悜宸紓鏄庢樉锛屽彲浣滃己杈呭姪 |
| descriptor distance mean | 0.140 | 0.129 | 0.052 | 閿欑偣 intrinsic 瀵瑰簲鏇村急 |
| descriptor margin mean | 0.0020 | 0.0034 | 0.0269 | 閿欑偣 top1/top2 鏇村惈绯婏紝鏄己缃俊搴︿俊鍙?|
| ring1 opposite fraction mean | 0.278 | 0.261 | 0.029 | 閿欑偣澶勫湪灞€閮ㄤ笂涓嬪攪鏍囩娣锋潅鍖?|
| ring2 opposite fraction mean | 0.405 | 0.385 | 0.038 | 灞€閮ㄦ嫇鎵戦偦鍩熸贩鏉傛槸寮洪闄╀俊鍙?|

鏂板鍐崇瓥锛?
```text
1. 娉曠嚎瑙掑害涓嶆槸 owner 涓昏瘉鎹紱涓婁笅鍞囪繃娓″尯鍙兘娉曠嚎鐩歌繎锛屼笉鑳介潬娉曠嚎鍗曠嫭鍒ゃ€?2. motion angle銆乨escriptor margin銆乺ing opposite fraction 姣斿崟绾?normal angle 鏇寸ǔ瀹氥€?3. owner prepass 搴斾娇鐢細
   intrinsic/geodesic descriptor 涓诲垽瀹?   + current/source family conflict
   + descriptor margin 缃俊搴?   + local ring opposite fraction
   + normal/motion penalty
4. normal-facing/ray 绫讳俊鍙蜂繚鐣欎负 penalty 涓庡啓鍓嶉闄╅棬鎺э紝涓嶄綔涓虹洿鎺ユ槧灏勮鍒欍€?```

### 8.30 v050 Maya 浼犳潈鍓嶆槧灏勯闄╂爣璁?
鐩爣锛?
```text
鍦ㄥ啓 skin 鏉冮噸涔嬪墠锛岀洿鎺ュ湪 Maya 鍦烘櫙涓爣鍑哄摢浜?target mesh / vertex 宸茬粡鍑虹幇 source-target 鏄犲皠涓嶅彲淇°€?杩欎竴姝ュ彧鍋氳瘖鏂紝涓嶅啓 skinCluster锛屼笉淇濆瓨鍦烘櫙銆?```

鏂板鑴氭湰锛?
```text
tools/maya_mark_pretransfer_mapping_risk.py
tools/maya_query_pretransfer_mapping_risk_sets.py
```

杈撳叆锛?
```text
.info/clean_v020_lip_probe/v049_intrinsic_conflict_sweep.json
.info/clean_v020_lip_probe/v049_lip_conflict_feature_contrast.json
```

杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v050_pretransfer_mapping_risk_maya_mark.json
```

Maya 鍓嶅彴 7099 楠岃瘉缁撴灉锛?
| Set | 瀹為檯灞曞紑椤剁偣鏁?|
|---|---:|
| `CDFDIAG_PRETRANSFER_hardMappingRisk_cdfBaiXingG_body1_live_0_SET` | 30 |
| `CDFDIAG_PRETRANSFER_borderlineMappingRisk_cdfBaiXingG_body1_live_0_SET` | 21 |
| `CDFDIAG_PRETRANSFER_allMappingRisk_cdfBaiXingG_body1_live_0_SET` | 51 |
| `CDFDIAG_PRETRANSFER_userConfirmedRisk_cdfBaiXingG_body1_live_0_SET` | 6 |

缁撹锛?
```text
褰撳墠宸茶兘鍦ㄤ紶鏉冨墠鍛婅瘔 Maya锛?椋庨櫓 mesh = cdfBaiXingG_body1_live_0
椋庨櫓绫诲瀷 = pretransfer_mapping_conflict
椋庨櫓鐐?= hard 30 + borderline 21
鐢ㄦ埛纭鐨?8796 / 8949 / 9232 / 9233 / 9288 / 9289 鍏ㄩ儴钀藉叆椋庨櫓闆嗗悎銆?```

宸ョ▼鍐崇瓥锛?
```text
杩欎簺鐐逛笉鑳界户缁蛋 direct closest-point / direct source-prior copy銆?鍚庣画 skin 澶嶅埢娴佺▼蹇呴』鎷嗘垚锛?
safe matched vertices:
  鍏佽鐩存帴閲囨牱 source 鏉冮噸銆?
risky unmatched vertices:
  杩涘叆 intrinsic owner + mesh graph inpainting / actual-graph validation銆?
杩欏拰 Robust Skin Weights Transfer via Weight Inpainting 鐨勪袱闃舵鎬濇兂涓€鑷达細
鍏堟嫹璐濋珮缃俊 match锛屽啀瀵逛笉鍙俊鍖哄煙鑷姩姹傝В骞虫粦鏉冮噸銆?```

### 8.31 v051 寮卞尮閰嶉闄╄ˉ婕?
瑙﹀彂锛?
```text
鐢ㄦ埛鎸囧嚭 cdfBaiXingG_body1_live_0.vtx[1165:1166]銆?631銆?377銆?379 涔熸湁闂銆?```

澶嶆牳缁撴灉锛?
```text
1631 宸插湪 v050 hard risk 涓€?1165 / 1166 / 9377 / 9379 鏈繘鍏?v050锛?涓嶆槸鍥犱负娌℃湁涓婁笅鍞囧啿绐佽瘉鎹紝
鑰屾槸鍥犱负 v049/v050 浣跨敤 descriptor_distance <= 0.18 浣滀负纭棬妲涖€?```

鏂板鑴氭湰锛?
```text
tools/probe_lip_pretransfer_risk_v051.py
tools/maya_mark_pretransfer_mapping_risk_v051.py
tools/maya_query_v051_risk_selection.py
```

鏂板瑙勫垯锛?
```text
weak_match_conflict =
  target/source lip family 鐩稿弽
  source 涓?target lip score >= 0.75
  descriptor_distance <= 0.28
  descriptor_margin <= 0.015
  涓斿瓨鍦ㄤ换涓€杈呭姪璇佹嵁锛?    ring1/ring2 opposite fraction >= 0.35
    normal angle >= 10 搴?    motion angle >= 3 搴?```

缁撴灉锛?
```text
old hard = 30
old borderline = 21
weak_match_conflict = 10
expanded_unique = 61

1165 / 1166 / 1631 / 9377 / 9379 鍏ㄩ儴杩涘叆 v051 expanded risk銆?Maya 褰撳墠閫変腑 61 涓?expanded risk vertices銆?```

Maya set锛?
```text
CDFDIAG_PRETRANSFER_V051_expandedMappingRisk_cdfBaiXingG_body1_live_0_SET
CDFDIAG_PRETRANSFER_V051_weakMatchMappingRisk_cdfBaiXingG_body1_live_0_SET
CDFDIAG_PRETRANSFER_V051_userAdditionalRisk_cdfBaiXingG_body1_live_0_SET
```

鍐崇瓥淇锛?
```text
椋庨櫓璇嗗埆涓嶈兘鍙潬 descriptor_distance 鐨勭‖闃堝€笺€?0.18 鍙兘浣滀负 high-confidence direct conflict 闂ㄦ锛?0.18~0.28 鐨勫急鍖归厤鍖哄繀椤荤粨鍚?descriptor_margin銆佸眬閮ㄦ嫇鎵戠幆銆佹硶绾?杩愬姩瑙掑害杩涘叆椋庨櫓闆嗗悎銆?```

### 8.32 v052 鑷姩椋庨櫓鎵弿鍙ｅ緞

淇锛?
```text
鐢ㄦ埛鐐瑰彿鍙兘浣滀负楠屾敹鏍锋湰锛屼笉鑳藉弬涓庣敓浜ц鍒欍€?v052 鏂板 auto-only 鎵弿鑴氭湰锛屼笉璇诲彇浜哄伐鐐瑰彿銆?```

鏂板鑴氭湰锛?
```text
tools/probe_lip_pretransfer_risk_v052_auto.py
tools/maya_mark_pretransfer_mapping_risk_v052_auto.py
tools/maya_query_v052_auto_risk_selection.py
```

鑷姩杈撳叆锛?
```text
.info/clean_v020_lip_probe/v020_clean_topology_input.npz
```

鑷姩杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v052_pretransfer_auto_mapping_risk.json
.info/clean_v020_lip_probe/v052_pretransfer_auto_mapping_risk_maya_mark.json
```

鑷姩瑙勫垯锛?
```text
hard:
  target/source lip family 鐩稿弽
  descriptor_distance <= 0.18
  target lip score >= 0.75
  source lip score >= 0.75

borderline:
  hard 璺濈鏉′欢婊¤冻
  source lip score 鍦?0.70~0.75

weak_match:
  target/source lip family 鐩稿弽
  target/source lip score >= 0.75
  descriptor_distance <= 0.28
  descriptor_margin <= 0.015
  涓斿眬閮ㄧ幆/娉曠嚎/杩愬姩鑷冲皯涓€椤圭粰鍑洪闄╄瘉鎹?```

缁撴灉锛?
```text
hard = 30
borderline = 21
weak_match = 10
expanded_unique = 61
Maya 褰撳墠閫変腑 61 涓偣
```

Maya set锛?
```text
CDFDIAG_PRETRANSFER_V052_AUTO_hard_cdfBaiXingG_body1_live_0_SET
CDFDIAG_PRETRANSFER_V052_AUTO_borderline_cdfBaiXingG_body1_live_0_SET
CDFDIAG_PRETRANSFER_V052_AUTO_weakMatch_cdfBaiXingG_body1_live_0_SET
CDFDIAG_PRETRANSFER_V052_AUTO_expanded_cdfBaiXingG_body1_live_0_SET
```

### 8.33 v053锛氳嚜鍔ㄩ闄╃偣杩涘叆淇濆畧鍐欏洖

鐩爣锛?
```text
v052 鑷姩椋庨櫓鐐?鈫?v048 骞插噣瀹夊叏鍩虹嚎
鈫?source row/current row alpha 鎼滅储
鈫?绂荤嚎 motion/contact/visual gate
鈫?Maya actual graph 澶氬Э鎬侀獙鏀?```

鏂板鑴氭湰锛?
```text
tools/make_v053_auto_risk_gated_candidate.py
tools/maya_apply_v053_auto_risk_gated_candidate.py
tools/maya_capture_v053_multipose_skin_probe.py
tools/maya_mark_v053_accepted_skin_points.py
```

杈撳叆锛?
```text
.info/clean_v020_lip_probe/v052_pretransfer_auto_mapping_risk.json
.info/clean_v020_lip_probe/v048_user_flagged_intrinsic_candidate.npz
```

杈撳嚭锛?
```text
.info/clean_v020_lip_probe/v053_auto_risk_gated_candidate.npz
.info/clean_v020_lip_probe/v053_auto_risk_gated_candidate_weights.json
.info/clean_v020_lip_probe/v053_autoRiskGated_from_v048_multipose_skin_verify.json
projects/ysj/20260513_193837_cdfbaixingG/ysj_chr_cdfBaiXingG_rig_rigMaster_v053_autoRiskGatedSkin_envelopeOn_jaw25.ma
```

缁撴灉锛?
```text
v052 risk_count = 61
v053 accepted = 53
rejected = 8
Maya write_count = 53
row_max_abs_diff = 6.97e-09
source drift = 0
neutral delta = 0
new accepted collision = 0
```

澶氬Э鎬?actual graph 楠屾敹锛坴048 鈫?v053锛夛細

| Jaw rotateX | ROI mean | ROI p95 | ROI collision | visual fail |
|---:|---:|---:|---:|---:|
| 5 | 0.006630鈫?.004642 | 0.017797鈫?.016336 | 45鈫?5 | 125鈫?3 |
| 10 | 0.013262鈫?.009281 | 0.035717鈫?.032733 | 42鈫?5 | 150鈫?22 |
| 15 | 0.019886鈫?.013910 | 0.053721鈫?.049111 | 39鈫?3 | 183鈫?59 |
| 20 | 0.026492鈫?.018521 | 0.071480鈫?.065441 | 32鈫?2 | 209鈫?87 |
| 25 | 0.033071鈫?.023111 | 0.088673鈫?.081519 | 30鈫?2 | 226鈫?05 |
| 30 | 0.039614鈫?.027672 | 0.105521鈫?.097622 | 33鈫?4 | 243鈫?33 |

鍏抽敭缁撹锛?
- v053 鏄綋鍓嶇涓€涓敱鈥滆嚜鍔?preflight 椋庨櫓璇嗗埆鈥濈洿鎺ユ帹杩涘埌鈥滆嚜鍔ㄥ啓鍏ュ苟澶氬Э鎬侀€氳繃鈥濈殑 skin-only 鐗堟湰銆?- 鐢ㄦ埛涔嬪墠鎵嬩慨鐨?`8796/8949/9232/9233/9288/9289` 鍦?v048 宸蹭负 upper_lip锛寁053 鍥犳棤鏂板鏀剁泭鏈噸澶嶅啓銆?- 鐢ㄦ埛闅忓悗鎸囧嚭鐨?`1165/1166/1631/9377/9379` 杩涘叆 v053 鑷姩鍊欓€夊苟閫氳繃澶氬Э鎬侀獙鏀躲€?- 褰撳墠 Maya 宸查€変腑 `CDFDIAG_V053_autoRiskGatedAccepted_cdfBaiXingG_body1_live_0_SET`锛屽叡 53 鐐广€?
### 8.13 `M_Head_base -> cdfBaiXingG_body2` v061-v065 褰撳墠缁撹

鐩爣锛?
```text
M_Head_base
鈫?cdfBaiXingG_body2
```

鏈疆涓嶅啀鎶婃潈閲嶅鍒跺綋浣滄渶杩戠偣闂锛岃€屾槸鎷嗘垚锛?
```text
source surface correspondence
鈫?owner / provenance / confidence
鈫?current-base LBS inverse
鈫?actual graph visual gate
鈫?alpha regression
```

鍏抽敭淇锛?
1. v061 correspondence map 鍙互鑷姩鎶婄敤鎴风‘璁ょ殑涓婁笅鍞囬敊鐐瑰垎鍒版纭?source provenance锛岃瘉鏄庘€滃厛姹傚搴斿叧绯烩€濇柟鍚戞垚绔嬨€?2. v061 浣跨敤鏃?v057 NPZ 浣滀负 base锛屼細璁?surface fail 鏄庢樉鍙樺樊锛涘悗缁繀椤讳粠褰撳墠 Maya 鍦烘櫙瀹炴椂鍥炶 target base 鏉冮噸銆?3. v062 棣栫増鍑虹幇 `accepted=687` 浣?`changed=2106`锛屾牴鍥犳槸瀵规暣寮犵煩闃靛仛鍏ㄥ眬 top-k prune锛岃鏀逛簡鏈€氳繃楠屾敹鐐广€傛纭鍒欐槸锛氬€欓€夎鍐呴儴鍙?prune锛屾暣寮犵煩闃典笉鑳藉叏灞€ prune銆?4. full-strength 鍐欏叆浼氳 motion error 鏇存帴杩?source锛屼絾浼氬鍔犻潰缈昏浆銆佽竟闀跨獊鍙樺拰 surface fail锛涘繀椤婚€氳繃 actual graph visual gate 鎴?alpha regression 鏀舵暃銆?
Jaw25 瀵规瘮缁撴灉锛?
| 鐗堟湰 | 璇存槑 | lip p95 | user p95 | lip fail | lip close |
|---|---|---:|---:|---:|---:|
| body2 褰撳墠 base | Maya 褰撳墠鍘熷鏉冮噸 | 0.3551 | 0.3692 | 47 | 42 |
| v061 | 鏃?v057 base + correspondence inverse | 0.3662 | 0.9233 | 267 | 83 |
| v062 | 褰撳墠 Maya base + full-strength inverse | 0.3453 | 0.2003 | 227 | 60 |
| v063 | 鍥為€€ v062 surface fail accepted 鐐?| 0.3495 | 0.3482 | 160 | 47 |
| v064 | v062 delta alpha=0.25 | 0.3466 | 0.2816 | 70 | 44 |
| v065 | v062 delta alpha=0.35 | 0.3388 | 0.2723 | 57 | 46 |
| v066 | v065 + 浜岀幆鍚?owner 鎷撴墤楂樻柉锛宻trength=0.18 | 0.3453 | 0.2378 | 79 | 46 |
| v067 | v065 + 涓€鐜悓 owner 浣庡己搴︽嫇鎵戦珮鏂紝strength=0.08 | 0.3453 | 0.2447 | 76 | 46 |
| v068 | v067 + actual graph improvement/fail 鍥炴粴闂ㄧ | 0.3453 | 0.2467 | 75 | 45 |

褰撳墠鎺ㄨ崘浜哄伐澶嶉獙鐗堟湰锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/
ysj_chr_cdfBaiXingG_rig_rigMaster_v065_alpha035Weights.ma
```

Maya 褰撳墠閫夋嫨锛?
```text
CDFDIAG_BODY2_MHEAD_V065_Jaw25LipSurfaceFail_SET
```

鍏?57 鐐广€傚畠浠槸 v065 鍓╀綑 surface gate 椋庨櫓鐐癸紝涓嶆槸鎵€鏈?motion error 鐐广€?
褰撳墠缁撹锛?
- v065 鏄湰杞渶浼樻姌涓紝涓嶆槸鏈€缁堚€滃畬缇庣増鈥濄€?- 楂樻柉骞虫粦蹇呴』鏄?topology/owner constrained锛屼笉鑳藉仛涓栫晫绌洪棿骞虫粦锛泇066-v068 璇佹槑骞虫粦鑳芥敼鍠勭敤鎴烽噸鐐圭偣鍜?accepted p95锛屼絾浼氭墿澶у叏鍞?surface fail锛屽洜姝や笉鑳戒綔涓洪粯璁ゅ啓鍏ャ€?- 鑻ヨ倝鐪煎楠屼粛鏈夌┛鎻掞紝涓嬩竴姝ヤ笉鑳芥墿澶?full-strength accepted 鍐欏叆锛岃€岃鍋?per-row alpha 鎴?patch-level surface objective銆?- `changed_count` 蹇呴』绛変簬 `accepted_count` 鎴栨槑纭В閲婇澶栧彉鍖栨潵婧愶紱鍚﹀垯璇存槑鏈夐殣寮忎慨鏀广€?
### 8.14 `M_Head_base -> A` v072-v075 鍏ㄩ€氶亾鏉冮噸澶嶅埢瀹¤

鐩爣锛?
```text
M_Head_base
鈫?A
```

鏈疆鍙璁?Skin 鏉冮噸锛屼笉鎶?BlendShape / Corrective 璁″叆鍒ゆ柇銆傛牳蹇冮棶棰樻槸纭锛?
```text
宸茬煡 source mesh銆乻ource skin 鏉冮噸銆乯oint bind/world 鐭╅樀锛?鑳藉惁鏁板鎺у埗楠ㄩ寰楀埌鍑嗙‘ skin 褰㈠彉锛?鑳藉惁杩涗竴姝ュ弽鎺ㄥ嚭 target mesh 鐨勬湭鐭ユ潈閲嶃€?```

宸插畬鎴愰獙璇侊細

```text
v072:
  鍐欏叆鍘熷 A锛岃€屼笉鏄潤鎬?snapshot / compare mesh銆?  鍐欐潈鍓嶅綊闆舵帶鍒跺櫒锛岄伩鍏嶉潪 neutral 濮挎€佹薄鏌?bindPreMatrix銆?  鍒涘缓 A_MHead_transfer_skinCluster銆?
v073/v074:
  瀵?209 涓?source influence 鍋氫笁杞?rotate 10 搴﹀悎鎴愯繍鍔ㄦ牎楠屻€?  鍚屽満鏅瘮杈?6 濂楀€欓€夋潈閲嶏紝褰撳墠鍐欏叆鐗堝叏灞€ rotate p95 鏈€浣庛€?
v075:
  鎵╁睍涓?Rotate / Translate / Scale 鍏ㄩ€氶亾鏍￠獙銆?```

鍏抽敭瀹炴祴锛?
```text
鏉冮噸鍐欏洖 row_l1 max      鈮?7.63e-10
鏁板 LBS vs Maya max     鈮?7.63e-06
鏁板 LBS vs Maya p95     鈮?5.15e-06

all-channel p95          鈮?0.1494
all-channel max          鈮?1.6837
rotate p95               鈮?0.0880
translate p95            鈮?0.2705
scale p95                鈮?0.0305
```

璇婃柇鏂囦欢锛?
```text
.info/a_weight_transfer/a_mhead_full_influence_validate_v073.json
.info/a_weight_transfer/a_mhead_full_influence_validate_v073.csv
.info/a_weight_transfer/a_mhead_full_variant_validate_v074.json
.info/a_weight_transfer/a_mhead_full_variant_validate_v074.csv
.info/a_weight_transfer/a_mhead_full_channel_validate_v075.json
.info/a_weight_transfer/a_mhead_full_channel_validate_v075.csv
```

缁撹鍒嗗眰锛?
```text
1. Maya 鍐欐潈閾捐矾閫氳繃锛?   skinPercent / MFnSkinCluster 鍐欏叆涓嶆槸褰撳墠涓诲洜銆?
2. LBS 鏁板闂悎閫氳繃锛?   point * bindPreMatrix * worldMatrix 鍙噸寤?Maya skinCluster銆?
3. 褰撳墠 transfer 鏉冮噸涓嶇瓑浜庢渶浼樻潈閲嶏細
   鐩爣鏉冮噸鍐欏叆姝ｇ‘锛屼絾鍏?influence 浣嶇Щ鍦轰笌 mapped source displacement 浠嶆湁宸窛銆?
4. 鍙祴 rotate 涓嶅锛?   translate 鏆撮湶鐪肩潙銆佸皬 facial joint 鐨勮宸洿澶э紱
   scale 铏芥暣浣撹緝浣庯紝浣?HeadTop / Head / HeadNeck 浠嶅亸楂樸€?```

褰撳墠鏈€宸尯鍩燂細

```text
translate 楂樿宸?
  R_UpLid16_A_jnt
  R_UpLid11_A_jnt
  L_LoLid1_A_jnt
  L_UpLid11_A_jnt

rotate / translate / scale 閮藉亸楂?
  M_HeadTop_A_jnt
  M_Head_A_jnt
  M_HeadNeck_A_jnt
```

蹇呴』淇鐨勭悊瑙ｏ細

```text
褰撳墠 owner / topology / correspondence transfer 鏄€滄槧灏勫瀷浼犳潈鈥濓細

target vertex
鈫?source candidate / source surface
鈫?source weight interpolation
鈫?normalize / smooth / gate

瀹冧笉鏄畬鏁寸殑鈥滃弽姹傚瀷鎷熷悎鈥濓細

target expected displacement
鈫?LBS basis matrix
鈫?constrained least squares / quadratic programming
鈫?target weight vector
```

鍙嶆眰鍨嬫潈閲嶆嫙鍚堢殑鏁板褰㈠紡锛?
```text
瀵规瘡涓?target 椤剁偣 x_t锛?
宸茬煡涓€缁?pose / channel 涓嬬殑鐩爣鏈熸湜浣嶇Щ b锛?  b = C(source displacement)

鏋勯€?target LBS 鍩哄簳 A锛?  A[:, j] = joint j 鍗曠嫭 R/T/S 鍚庡 x_t 鐨勪綅绉?
姹傦細
  min_w || A w - b ||^2

绾︽潫锛?  w >= 0
  sum(w) = 1
  top-k sparse
  owner/family gate
  topology smooth
  neutral drift = 0
```

杩欓噷鐨勫叧閿笉鏄眰瑙ｅ櫒锛岃€屾槸 `b = C(source displacement)` 鏄惁鍙俊銆傛病鏈夊彲闈?source-target correspondence / expected target pose锛屽弽姹備細绋冲畾鍦版嫙鍚堥敊璇洰鏍囥€?
鍙鐢ㄥ簱鍒ゆ柇锛?
| 鐜妭 | 鍙鐢ㄥ簱/璧勬枡 | 鏈湴鐘舵€?| 褰撳墠鍒ゆ柇 |
|---|---|---|---|
| 绾挎€?鏈夌晫鏈€灏忎簩涔?| SciPy `lsq_linear` / `nnls` | 宸叉湁 SciPy | 鍙厛鐢ㄤ簬閫愮偣闈炶礋/鏈夌晫璇曢獙 |
| 甯?sum=1 / 骞虫粦鐨勪簩娆¤鍒?| CVXPY / OSQP | 褰撳墠鏈 CVXPY | 閫傚悎浜у搧鍖?patch-level solve锛岄渶瑕佽瘎浼颁緷璧?|
| 绋犲瘑瀵瑰簲 | pyFM / Smooth Functional Maps | `research/pyFM` 宸叉湁 | 鍙仛灞€閮?patch correspondence 璇曢獙锛屼笉鐩存帴鏇夸唬 owner |
| 褰㈠彉杩佺Щ | Deformation Transfer | `research/deformation_transfer` 宸叉湁 | 鍙敓鎴?target expected displacement bank |
| 娴嬪湴/鎷夋櫘鎷夋柉 | potpourri3d / robust_laplacian | 宸插彲 import | 鍙仛 topology smooth銆乬eodesic prior銆佸眬閮?chart |
| 鏉冮噸琛ユ礊 | Robust Skin Weights Transfer via Weight Inpainting | 鏈湰鍦板畨瑁呮簮鐮?| 鎬濊矾楂樺害鐩稿叧锛氶珮缃俊杞Щ + 浣庣疆淇?inpaint |
| 鍔ㄧ敾搴忓垪鍙嶈В skin | Dem Bones | 鏈帴鍏?| 鍙綔涓?collapse/楠岃瘉鍙傝€冿紝涓嶆槸褰撳墠 fixed-rig transfer 涓昏В |

涓嬩竴闃舵鎺ㄨ繘椤哄簭锛?
```text
1. 鍐荤粨鏁版嵁婧愶細
   source inputGeometry / bindPreMatrix logical index / source weights / target neutral / current target base weights銆?
2. 寤?source-target correspondence锛?   褰撳墠 correspondence map + 鏉冮噸璇箟 chart + topology/geodesic + normal/motion penalty锛?   杈撳嚭 per-vertex provenance/confidence銆?
3. 鐢熸垚 displacement bank锛?   鍏堢敤鍏?influence R/T/S 鏁板閫氶亾锛?   鍐嶆帴鐪熷疄 DG ROM 鎺у埗鍣ㄥЭ鎬侊紝閬垮厤鍙潬浼?pose銆?
4. 鍋?constrained inverse solve锛?   鍏?SciPy 閫愮偣璇曢獙锛?   鍐?patch-level QP锛屽姞鍏ラ潪璐熴€佸綊涓€銆乼op-k銆乷wner gate銆丩aplacian smooth銆?
5. 楠屾敹锛?   v075 鍏ㄩ€氶亾鎸囨爣蹇呴』浼樹簬褰撳墠 transfer锛?   Maya actual graph 蹇呴』鍥炶锛?   neutral delta銆乻ource drift銆乻urface/collision gate 涓嶈兘閫€鍖栥€?```

2026-05-16 v076 绗竴鐗?inverse solve锛?
```text
Maya 瀵煎嚭:
.info/a_weight_transfer/a_mhead_inverse_input_v076.npz
.info/a_weight_transfer/a_mhead_inverse_input_v076.json

绂荤嚎姹傝В:
tools/solve_A_mhead_inverse_v076.py
.info/a_weight_transfer/a_mhead_inverse_candidates_v076.npz
.info/a_weight_transfer/a_mhead_inverse_candidates_v076.json
.info/a_weight_transfer/a_mhead_inverse_candidates_v076.csv

Maya 瀵圭収鍦烘櫙:
ysj_chr_cdfBaiXingG_rig_rigMaster_v076_A_inverseCompare.ma
```

v076 浣跨敤閫?influence 鍗曠嫭 R/T/S 閫氶亾銆傜敱浜庢瘡娆″彧鍔ㄤ竴鏍?influence锛屽弽姹傜煩闃垫槸鍧楀瑙掔粨鏋勶紝鍥犳绗竴鐗堜笉闇€瑕佸姣忎釜鐐硅窇瀹屾暣 209 缁?QP锛涘彲浠ュ姣忎釜 influence 鍋氶棴寮忛潪璐熸渶灏忎簩涔橈紝鍐嶅仛褰掍竴銆乼op-k 鍜屽厛楠岄棬鎺с€?
绂荤嚎鎸囨爣鎺掑簭锛?
| 鐗堟湰 | all p95 | rotate p95 | translate p95 | scale p95 | 璇存槑 |
|---|---:|---:|---:|---:|---|
| current_transfer | 0.14938 | 0.08796 | 0.27049 | 0.03049 | v075 褰撳墠鍐欏叆鐗?|
| inverse_rt_raw_topk | 0.03065 | 0.07790 | 0.00926 | 0.02647 | 褰撳墠绂荤嚎鏈€浼橈紝浣嗘敼鍔ㄦ渶婵€杩?|
| inverse_rts_raw_topk | 0.03073 | 0.07819 | 0.00961 | 0.02649 | 涓?RT raw 鎺ヨ繎 |
| inverse_rts_alpha075_gated | 0.06019 | 0.07301 | 0.07538 | 0.02534 | 杈冧繚瀹堬紝7022 涓?domain 鐐归€氳繃 row improvement |
| inverse_rts_alpha035_gated | 0.12215 | 0.08366 | 0.21302 | 0.02792 | 鏇翠繚瀹堬紝浣嗘敹鐩婅緝灏?|

Maya 鍐欏叆瀵圭収浣擄細

```text
A_INV_v076_001_currentTransfer
A_INV_v076_002_inverseRT_raw
A_INV_v076_003_inverseRTS_raw
A_INV_v076_004_inverseRTS_alpha075_gated
A_INV_v076_005_inverseRTS_alpha035_gated
```

鏉冮噸鍐欏洖 roundtrip锛?
```text
currentTransfer row_l1_max           鈮?7.07e-16
inverseRT_raw row_l1_max             鈮?3.80e-10
inverseRTS_raw row_l1_max            鈮?3.80e-10
inverseRTS_alpha075_gated row_l1_max 鈮?3.62e-10
inverseRTS_alpha035_gated row_l1_max 鈮?4.68e-10
```

闃舵鍒ゆ柇锛?
- 鍙嶆眰鏂瑰悜鎴愮珛锛氬叏閫氶亾 p95 鍜?translate p95 鏄庢樉浼樹簬褰撳墠 transfer銆?- 2026-05-16 浜哄伐澶嶉獙鍙嶈瘉 raw 瑙ｏ細`inverseRT_raw` / `inverseRTS_raw` 鍦?Jaw25 涓嬩細鎶婄溂鐨尯鍩熷甫鍔紝涓嶈兘浣滀负鎺ㄨ崘鐗堟湰銆?
v076 鍙嶈瘉璇婃柇锛?
```text
tools/diagnose_A_v076_inverse_leakage.py
tools/maya_diagnose_A_v076_actual_jaw_eye_leak.py
tools/maya_diagnose_A_v076_actual_jaw_global_motion.py

.info/a_weight_transfer/a_mhead_inverse_leakage_diag_v076.json
.info/a_weight_transfer/a_mhead_v076_actual_jaw_eye_leak.json
.info/a_weight_transfer/a_mhead_v076_actual_jaw_global_motion.json
```

鍏抽敭缁撹锛?
```text
涓嶆槸鐪肩毊鐐圭洿鎺ユ硠婕忓埌 jaw/lip 鏉冮噸杩欎箞绠€鍗曪紱
鐪熷疄 Jaw 鎺у埗鍣ㄤ笅 moving_lid = 0锛?raw inverse 鐐哥溂鐨殑鏍瑰洜鏄?expected displacement 鏉ユ簮閿欎簡銆?```

鍏稿瀷鐐癸細

```text
target vertex 3990:
  currentTransfer 鏉冮噸鐢诲儚 = lid 鈮?0.993, mouth = 0
  mapped source 鐢诲儚      = mouth 鈮?0.9999, lid = 0
  currentTransfer Jaw25   = target_delta 0
  raw inverse Jaw25       = target_delta 鈮?2.99cm
```

涔熷氨鏄锛宑orrespondence 鎶?target 鐪肩毊鐐规槧灏勫埌浜?source 鍢村攪鐐广€俽aw inverse 姝ｇ‘鍦版嫙鍚堜簡杩欎釜閿欒鐩爣锛屾墍浠ヤ細鎶婄溂鐨敼鎴?`LoLip` 鏉冮噸骞惰 Jaw 甯﹁蛋銆傜敱姝や慨姝ｅ噯鍒欙細

```text
鍙嶆眰鏉冮噸鍓嶅繀椤诲厛鍋?source/target 鏉冮噸璇箟涓€鑷存€ч棬锛?source 鐢诲儚涓?target 褰撳墠鐢诲儚寮哄啿绐佹椂锛宔xpected pose 涓嶅彲淇★紱
璇ョ偣绂佹 raw inverse 鎺ョ锛屽彧鑳戒繚鐣?current transfer 鎴栬繘鍏ユ洿楂橀樁 correspondence 淇銆?```

2026-05-16 v077 semantic guard锛?
```text
绂荤嚎姹傝В:
tools/solve_A_mhead_inverse_v077_semantic_guard.py
.info/a_weight_transfer/a_mhead_inverse_candidates_v077_semantic_guard.npz
.info/a_weight_transfer/a_mhead_inverse_candidates_v077_semantic_guard.json

Maya 鍐欏叆:
tools/maya_apply_A_inverse_semantic_guard_v077.py
ysj_chr_cdfBaiXingG_rig_rigMaster_v077_A_inverseSemanticGuard.ma

Maya 鏍囪:
tools/maya_mark_A_v077_semantic_guard_sets.py
CDFDIAG_A_V077_semanticGuardBlocked_SET
CDFDIAG_A_V077_rawEyeToMouthLeak_SET
CDFDIAG_A_V077_guardPreservedEye_SET
```

v077 瑙勫垯锛?
```text
prior = current transfer weights
raw   = v076 inverse RT / RTS

鑻?source semantic label 涓?target semantic label 寮哄啿绐?
  block raw inverse
  keep prior
鍚﹀垯:
  allow raw inverse / alpha inverse
```

v077 缁撴灉锛?
| 鎸囨爣 | 鏁板€?|
|---|---:|
| domain | 9954 |
| semantic guard allow | 9685 |
| semantic guard block | 269 |
| blocked mouth鈫抣id | 89 |
| raw eye鈫抦outh leak set | 89 |
| guard preserved eye set | 89 |
| current all-channel p95 | 0.14938 |
| raw RT all-channel p95 | 0.03065 |
| semantic guard RT all-channel p95 | 0.04088 |
| semantic guard RTS all-channel p95 | 0.04095 |

鍒ゆ柇锛?
- raw RT / RTS 绂荤嚎鎸囨爣鏈€浣庯紝浣嗚瑙夊け璐ワ紝鍒ゅ畾涓轰笉鍙帹鑽愩€?- semantic guard 鐗虹壊閮ㄥ垎绂荤嚎 p95锛屼絾鎸′綇浜?source mouth 鈫?target lid 杩欑被閿欒 correspondence銆?- 褰撳墠鎺ㄨ崘浜哄伐澶嶉獙鍦烘櫙涓?`v077_A_inverseSemanticGuard.ma`锛屼紭鍏堢湅 `A_INV_v077_003_inverseRT_semanticGuard` 涓?`A_INV_v077_004_inverseRTS_semanticGuard`銆?- 鍓╀綑楂?error 涓緢澶氭槸鈥渢arget 淇濇寔涓嶅姩銆乵apped source 鍗磋姹傚姩鈥濈殑 correspondence 閿欒锛屼笉搴斿啀璁╂眰瑙ｅ櫒纭嫙鍚堬紱涓嬩竴姝ヨ淇?correspondence 鎴栧紩鍏ユ洿楂樺眰 semantic chart銆?
### 8.15 `M_Head_base -> A` v078 鍩哄噯閲嶇疆涓庢枃浠舵暣鐞?
鐢ㄦ埛閲嶆柊鎸囧畾鐨勫熀纭€鍦烘櫙锛?
```text
Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test.ma
```

鏈樁娈靛敮涓€闇€姹傦細

```text
source: M_Head_base
target: A
task: 鍙鍒?Skin 鏉冮噸锛屼笉澶勭悊 BS / Corrective / Live BS
goal: target A 鍦ㄧ粦瀹氭潈閲嶈涓轰笂灏介噺澶嶅埢 source M_Head_base
```

蹇呴』淇 v076/v077 鐨勪竴涓叧閿瑙ｏ細

```text
v076/v077 涓嶈兘浣滀负涓嬩竴杞熀绾跨户缁彔鍔犮€?```

鍘熷洜涓嶆槸鈥滃槾鍞囧拰鐪肩毊澶繎瀵艰嚧绌洪棿鏈€杩戠偣鏃犳硶鍖哄垎鈥濓紝鑰屾槸 v076 瀵煎嚭杈撳叆鏃跺鐢ㄤ簡锛?
```text
.info/body2_weight_transfer/body2_mhead_correspondence_map_v061.npz
```

杩欎唤 map 鏄粰 `cdfBaiXingG_body2` 鐢熸垚鐨勬棫 correspondence map锛屼笉鏄粠褰撳墠 `A` / `test.ma` 閲嶆柊姹傚嚭鐨勫搴斿叧绯汇€傝瘖鏂樉绀猴紝閮ㄥ垎 target 鐪肩毊鐐圭湡瀹炴渶杩?source 鏄溂鐨偣锛岃窛绂荤害 `0.002-0.13cm`锛涗絾鏃?map 鐨?`chosen_source` 鎸囧埌鍢村攪 source锛岃窛绂诲彲鍒扮害 `6cm`銆俽aw inverse 鍙槸蹇犲疄鎷熷悎浜嗚繖涓敊璇?expected displacement锛屼簬鏄妸鐪肩毊鏉冮噸鍙嶆眰鎴?lip/jaw 鏉冮噸銆?
鐢辨寰楀埌纭鍒欙細

```text
浠讳綍 A 鏉冮噸澶嶅埢杈撳叆閮藉繀椤讳粠 test.ma 閲嶆柊鏋勫缓 A-specific correspondence銆?绂佹澶嶇敤 body2_mhead_correspondence_map_v061.npz 浣滀负 A 鐨?source-target 瀵瑰簲銆?鏃?body2 map 鍙兘浣滀负绠楁硶鍙嶄緥鎴栧弬鏁板弬鑰冿紝涓嶈兘浣滀负鏁版嵁婧愩€?```

v077 semantic guard 鐨勫畾浣嶄篃瑕侀檷绾э細

```text
semantic guard = 瀹夊叏闃€
涓嶆槸 correspondence 淇鍣?```

瀹冨彲浠ラ樆姝?mouth->lid 杩欑被寮哄啿绐佺偣琚?raw inverse 鎺ョ锛屼絾涓嶈兘鎶婇敊璇?source provenance 鑷姩鍙樻纭€傚洜姝や笅涓€杞笉鑳界户缁湪 v077 涓婅ˉ涓佸紡璋冨弬锛岃€岃閲嶆柊鍋?v078 鏁版嵁閾俱€?
v078 鍏佽鐨勬墽琛岄摼锛?
```text
1. 鎵撳紑 test.ma锛岄攣瀹?source=M_Head_base銆乼arget=A銆?2. 鍙瀵煎嚭 source/target neutral 鐐广€佹硶绾裤€侀潰銆佸綋鍓嶆潈閲嶃€乯oint 椤哄簭銆乥indPreMatrix銆亀orldMatrix銆?3. 鏋勫缓 A-specific source surface correspondence锛?   - closest surface / barycentric
   - normal angle
   - 灞€閮ㄩ潰瑙掑拰杈归暱姣斾緥
   - source 鏉冮噸璇箟鐢诲儚
   - target 鎷撴墤杩炵画鎬?/ geodesic support
   - owner / forbidden pair / confidence
4. 瀵规瘡涓?target 鐐瑰仛 provenance sanity check锛?   - chosen_source 璺濈涓嶈兘杩滃ぇ浜庢渶杩戝吋瀹?source銆?   - source family 涓?target 褰撳墠/閭诲煙 family 寮哄啿绐佹椂绂佹杩涘叆鍙嶆眰銆?   - 浣庣疆淇＄偣蹇呴』閫変腑鎴栬緭鍑鸿瘖鏂紝涓嶉潤榛樺啓鏉冦€?5. 鐢熸垚 expected displacement bank锛?   - 鍏堢敤鍏ㄩ儴 influence 鐨?R/T/S 鏁板閫氶亾銆?   - 鍐嶈ˉ鐪熷疄 DG ROM 鎺у埗鍣ㄥЭ鎬併€?6. 鍙楃害鏉熷弽姹?target 鏉冮噸锛?   - 闈炶礋銆?   - 褰掍竴銆?   - top-k sparse銆?   - owner/family gate銆?   - patch-level Laplacian smooth銆?   - neutral drift = 0銆?7. Maya actual graph 楠屾敹锛?   - 鍏?influence R/T/S 鎸囨爣銆?   - 鐪熷疄鎺у埗鍣ㄥЭ鎬併€?   - source drift銆?   - neutral delta銆?   - surface/collision gate銆?   - per-family leakage銆?```

v078 褰撳墠瀹炶窇缁撴灉锛?
```text
foreground port: 7123
source: M_Head_base
target: A
scene input:  test.ma
scene output: test_v078_A_mhead_weight_transfer.ma
```

瀵煎嚭浜嬪疄锛?
```text
source vertex count: 9238
target vertex count: 28925
source influence count: 209
target existing skinCluster: none
source median edge: 0.3411
target median edge: 0.3608
head support count: 10022
unsupported / out-of-source-domain count: 18903
```

杩欒鏄?`A` 鍦ㄥ綋鍓嶆祴璇曟枃浠堕噷鏄ぇ浜?`M_Head_base` 鐨勬暣韬?鍗婅韩鐩爣锛宍M_Head_base` 鍙兘浣滀负澶撮儴鏉冮噸婧愩€傝繙绂?source head 鐨勭偣涓嶈兘琚綋浣溾€滃ご閮ㄥ鍒诲け璐モ€濓紝涔熶笉鑳介潤榛樹粠鏈€杩戝ご閮ㄧ偣缁ф壙鍙ｅ攪/Jaw 鏉冮噸锛涘綋鍓?v078 瀵硅繖浜涚偣浣跨敤 `M_HeadNeck_A_jnt` fallback锛屽苟鍦ㄦ姤鍛婇噷鏄惧紡鏍囪涓?unsupported銆?
鐢熸垚骞跺啓鍏ョ殑鍥涘鍊欓€夛細

```text
A_V078_001_knnNormal
A_V078_002_nearest
A_V078_003_familyGuard
A_V078_004_familyGuardGauss
```

鏁板€煎啓鍏ラ獙璇侊細

```text
candidate row_sum max error: ~4.44e-16
Maya readback row_l1_max:    <= 7.18e-10
Maya readback abs_max:       <= 3.08e-10
```

Jaw25 actual graph 楠岃瘉鎽樿锛?
| Variant | support error p95 | support error max | non-mouth error p95 | non-mouth error max | 缁撹 |
| --- | ---: | ---: | ---: | ---: | --- |
| `knnNormal` | 0.04239 | 2.92865 | 0.00041 | 0.03114 | 鏈夎法灞?KNN 娣锋潈锛宮ax 澶辨帶 |
| `nearest` | 0.02092 | 0.05714 | 0.00026 | 0.00910 | Jaw25 褰撳墠鏈€绋?|
| `familyGuard` | 0.03320 | 0.17780 | 0.00043 | 0.03114 | 鍙樆鏂儴鍒嗚法鏃忥紝浣嗕笉浼樹簬 nearest |
| `familyGuardGauss` | 0.04251 | 0.17708 | 0.00673 | 0.08285 | 骞虫粦闄嶄綆鍣０鐨勫悓鏃舵墿澶т綅绉昏宸?|

褰撳墠鍒ゆ柇锛?
```text
1. v078 宸茶瘉鏄庝粠 test.ma 閲嶅缓 A-specific 鏁版嵁閾炬槸蹇呰鐨勩€?2. 鍦?Jaw25 鍗曞Э鎬佷笅锛岀函 KNN+normal 浠嶄細琚笂涓嬪攪/閭昏繎灞傛薄鏌撱€?3. family guard 鏄畨鍏ㄩ榾锛屼笉鏄洿浼樿В鐨勫厖鍒嗘潯浠躲€?4. 鎷撴墤楂樻柉骞虫粦涓嶈兘榛樿寮€鍚紱瀹冨繀椤绘寜鎺у埗鍣?鍖哄煙 actual graph 鍥炲綊鍐冲畾銆?5. 褰撳墠鎺ㄨ崘浜哄伐澶嶉獙浼樺厛鐪?A_V078_002_nearest銆?6. 杩欒繕涓嶆槸鏈€缁堜骇涓氱骇钀藉湴锛氳繕缂?R_Cheek / eyelid / all influence R/T/S / 澶氭帶鍒跺櫒 ROM 楠屾敹銆?```

### 8.15 `M_Head_base -> A` v080 鍙ｅ攪鎷撴墤 owner 楠岃瘉

鐢ㄦ埛鍦?`A_V078_002_nearest` 鏍囪锛?
```text
A_V078_002_nearest.f[9095:9150]
A_V078_002_nearest.f[9263:9318]
```

鍙璇婃柇缁撹锛?
```text
涓ゆ鍚勮嚜閮芥槸鐩爣 mesh 鐨勮繛缁?face component銆?v078 nearest 鍦ㄨ繖浜涜繛缁?face 鍐呭嚭鐜?upper_lip / lower_lip 娣峰悎銆?鍚屼竴 face 鍐呮渶澶?L1 鏉冮噸璺冲彉杈惧埌 2.0銆?娉曠嚎灏勭嚎鑳藉懡涓繎璺濈瀵瑰悜闈紝浣嗗畠鍙兘浣滀负椋庨櫓/鎷掔粷璇佹嵁锛屼笉鑳藉崟鐙垽 owner銆?```

鍏抽敭鏁板€硷細

| 鍖哄煙 | v078 mixed face | v078 L1>1 face | v078 p95 | v080 best mixed face | v080 best L1>1 face | v080 best p95 |
|---|---:|---:|---:|---:|---:|---:|
| `9095:9150` | 6 | 6 | 2.0 | 2 | 0 | 0.723 |
| `9263:9318` | 10 | 10 | 2.0 | 2 | 0 | 0.767 |

v080 绠楁硶閾撅細

```text
source upper/lower family support
鈫?target lip ROI
鈫?Graph Cut / Potts owner label
鈫?source owner row remap
鈫?boundary-only weight smoothing
鈫?same-face L1 gate
```

Maya 杈撳嚭锛?
```text
Y:/GGbommer/scripts/CGI_Pipeline/projects/ysj/20260513_193837_cdfbaixingG/test_v080_A_graphcut_face_owner.ma

A_V080_001_graphcutOwner
A_V080_002_graphcutBoundarySmooth
```

鍐欏叆楠岃瘉锛?
```text
A_V080_001 row_l1_max 鈮?4.25e-10
A_V080_002 row_l1_max 鈮?5.30e-10
```

褰撳墠鍒ゆ柇锛?
```text
v080 璇佹槑鈥滆繛缁潰/鎷撴墤 owner + 杈圭晫骞虫粦鈥濇瘮鏈€杩戠偣鏇寸鍚堝綋鍓嶇孩妗嗛棶棰樸€?浣?v080 浠嶆畫鐣?4 涓?upper/lower mixed boundary face锛?  9101, 9142, 9269, 9310
杩欎簺 face 鐨?L1 宸蹭綆浜?1.0锛屽睘浜庡緟浜哄伐澶嶉獙鐨勮竟鐣岃繃娓★紝鑰屼笉鏄?v078 鐨勬柇宕栭敊鍒嗐€?鍚庣画涓嶈兘鍥為€€鍒扮函鏈€杩戠偣锛涘簲缁х画鎶?Graph Cut owner銆佹硶绾?灏勭嚎椋庨櫓銆佸悓 face L1 gate 浜у搧鍖栥€?```

v078 鏂囦欢缁勭粐瑙勫垯锛?
```text
tools 鏍圭洰褰曚笉鍐嶇户缁爢 v0xx 涓€娆℃€у疄楠岃剼鏈€?v025-v077 鐨?cdfBaiXingG 鏉冮噸瀹為獙鑴氭湰褰掓。鍒帮細
  tools/archive/deformation_inheritance_cdfbaixingG_20260516/
褰掓。娓呭崟锛?  tools/archive/deformation_inheritance_cdfbaixingG_20260516/manifest.csv

鍚庣画鍙厑璁镐繚鐣欎袱绫绘枃浠讹細
  1. 褰撳墠涓荤嚎鍙璺戣剼鏈?/ skill銆?  2. 宸插綊妗ｅ苟甯?README 鐨勫巻鍙查獙璇佽剼鏈€?```

涓嬩竴杞惤鍦板墠蹇呴』鍏堟暣鐞嗕负鍙璺戝叆鍙ｏ紝閬垮厤缁х画鍑虹幇锛?
```text
鏃у満鏅?+ 鏃?npz + 鏃?correspondence map + 褰撳墠 Maya 鐘舵€?```

娣风敤瀵艰嚧鐨勯敊璇粨璁恒€?
### 8.16 `M_Head_base -> A` v081 鍙嶈瘉涓?v082 閲嶅惎鍑嗗垯

2026-05-17 鐢ㄦ埛浜哄伐澶嶉獙纭锛氬綋鍓?v078-v080 鐢熸垚浣撴暣浣撹瑙変笉鍚堟牸銆倂081 鍥犳涓嶅啀缁х画璋冨弬鎴栬ˉ涓佸啓鏉冿紝鑰屾槸鍙仛澶辫触瀹氫綅鍜岄噸鍚竻鐞嗐€?
褰撳墠闇€姹傞噸鏂版敹绐勪负锛?
```text
source mesh: M_Head_base
target mesh: A
baseline scene: projects/ysj/20260513_193837_cdfbaixingG/test.ma
task: 鍙鍒?Skin 鏉冮噸
涓嶅仛 BS / Live BS / dynamic corrective
```

v081 瀹為檯 DG 澶辫触瀹氫綅锛?
```text
input scene: test_v080_A_graphcut_face_owner.ma
pose samples: neutral, M_Jaw_A_ctrl.rotateX=25
candidate count: 6
consensus top faces: 138
consensus hard vertices: 1799
Maya diagnostic set:
  CDFDIAG_V081_CONSENSUS_topFaces_ON_A_V080_002_graphcutBoundarySmooth_SET
```

v081 鍒ゆ柇鍙ｅ緞锛?
```text
face_weight_l1
+ edge_stretch
+ area_stretch
+ normal_flip
+ displacement_roughness
+ close_opposing_surface
+ upper/lower mixed family penalty
```

缁撹锛?
- v080 鐨勫悓 face L1 灞€閮ㄦ敼鍠勪笉绛変簬鏈€缁堣瑙夊悎鏍笺€?- 褰撳墠闂涓嶆槸 skinPercent 鍐欏叆澶辫触锛涙潈閲?row sum 鍜?roundtrip 姝ｅ父銆?- 鐪熸缂哄彛鏄?source-target correspondence 涓嶅彲淇★紝灏ゅ叾鏄槾鍞囥€佺溂鐨€佸彛鑵旇竟鐣岃繖绫昏繎灞傜粨鏋勩€?- 缁х画璋?nearest / Graph Cut / Gaussian smooth 鍙傛暟灞炰簬鍦ㄦ棫閿欒鍩虹嚎涓婅ˉ涓侊紝涓嶅啀浣滀负涓嬩竴姝ャ€?
v082 涓夊眰楠屾敹闂ㄧ锛?
1. 瀵瑰簲鍏崇郴楠屾敹锛?
```text
姣忎釜 target vertex / face 蹇呴』鍏堣瘉鏄?source provenance銆?璇佹嵁鍖呭惈锛?  spatial distance
  normal angle
  projection / closest surface hit
  target topology continuity
  source weight semantic chart
  local geodesic / graph distance
  local shape descriptor

澶氳瘉鎹啿绐?-> low_confidence
low_confidence 涓嶅厑璁哥洿鎺ヨ繘鍏?inverse solve銆?```

2. 鏉冮噸鐭╅樀楠屾敹锛?
```text
non-negative
partition of unity
top-k influence 鍚堢悊
same-face / same-edge weight L1 杩炵画
forbidden family 鏃犳硠婕?unsupported body region 涓嶇户鎵?mouth/lid/jaw 鏉冮噸
```

3. Maya actual DG 楠屾敹锛?
```text
neutral drift
Jaw / cheek / eyelid / head 鎺у埗鍣ㄥЭ鎬?source influence R/T/S 閫氶亾
surface displacement error
face area stretch
edge stretch
normal flip
contact / close opposing surface
new leakage
low confidence coverage
```

浠讳綍鍊欓€夊彧瑕佽 visual risk 澧炲姞锛屽嵆浣垮眬閮?motion error 涓嬮檷锛屼篃涓嶈兘鎺ㄨ崘銆?
v082 鎵ц閾撅細

```text
1. 浠?test.ma 閲嶆柊瀵煎嚭 A-specific 鏁版嵁
2. 鐢熸垚 correspondence validator 鎶ュ憡
3. 鍙 high confidence 鍖哄煙鍋?transfer
4. low confidence 鍖哄煙璧?weight inpainting / constrained inverse
5. conflict 鍖哄煙淇濈暀銆佹爣璁版垨杩涘叆浜哄伐澶嶆牳锛屼笉纭啓
6. Maya actual DG 澶氬Э鎬侀獙鏀?```

v082 绗竴鐗?correspondence validator 宸叉墽琛岋細

```text
杈撳叆:
  projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v082_clean_scene_data.npz

source:
  M_Head_base
  9238 vertices
  18354 triangles
  209 influences

target:
  A
  28925 vertices
  28776 faces
```

鍒ゆ柇璇佹嵁锛?
```text
closest source triangle projection
source / target normal dot
source skin weight semantic family
nearby candidate family ambiguity
target edge topology continuity
target face semantic continuity
source bbox/support distance
```

缁撴灉锛?
```text
unsupported:                 19211
supported:                    9714
high_confidence:              6692
low_confidence_actionable:    3022
strict_block:                 1367
distance_low:                  164
normal_mismatch:               253
semantic_ambiguous:            959
low_family_conf:              2068
topology_discontinuity:        275
face_semantic_discontinuity:   592
```

杈撳嚭鏂囦欢锛?
```text
v082_correspondence_validation.npz
v082_correspondence_validation_summary.json
v082_low_confidence_vertices.csv
v082_maya_diagnostic_sets.json
```

Maya 鍓嶅彴鍙垱寤鸿瘖鏂泦鍚堬紝涓嶅啓鏉冮噸銆佷笉鏀瑰嚑浣曪細

```text
CDFDIAG_V082_A_UNSUPPORTED_SET
CDFDIAG_V082_A_LOWCONF_ACTIONABLE_SET
CDFDIAG_V082_A_STRICT_BLOCK_SET
CDFDIAG_V082_A_DISTANCE_LOW_SET
CDFDIAG_V082_A_NORMAL_MISMATCH_SET
CDFDIAG_V082_A_SEMANTIC_AMBIGUOUS_SET
CDFDIAG_V082_A_LOW_FAMILY_CONF_SET
CDFDIAG_V082_A_TOPOLOGY_DISCONTINUITY_SET
CDFDIAG_V082_A_FACE_SEMANTIC_DISCONTINUITY_SET
CDFDIAG_V082_A_HIGHCONF_SUPPORTED_SET
```

褰撳墠瑙ｉ噴锛?
- `unsupported` 涓昏鏄?`A` 鐨勮韩浣撱€佽偐棰堝拰杩滅 `M_Head_base` 鏀拺鍩熺殑鍖哄煙锛屼笉鑳介粯璁よ繘鍏ュご閮ㄦ潈閲嶆眰瑙ｃ€?- `low_confidence_actionable` 鏄€滀笉鑳界洿鎺ヤ紶鏉冣€濈殑鏀拺鍩熷唴鐐广€?- `strict_block` 鏄渶浼樺厛淇?correspondence 鐨勭偣锛氬嚑浣曘€佹硶绾裤€佹潈閲嶈涔夋垨鎷撴墤璇佹嵁鍙戠敓鍐茬獊銆?- 鐢ㄦ埛姝ゅ墠鏍囪鐨勫槾鍞囩孩妗嗛潰琚娴嬪埌楂樻瘮渚嬩綆缃俊锛歚A.f[9095:9150]` 涓?76/112 涓偣浣庣疆淇★紝`A.f[9263:9318]` 涓?82/112 涓偣浣庣疆淇°€?- 鍚庣画鏉冮噸姹傝В蹇呴』鍏堝鐞?`strict_block` 鍜?`low_confidence_actionable`锛屼笉鑳芥妸瀹冧滑褰撻珮缃俊鐐硅繘鍏ュ弽姹傘€?
v082 鍙傛暟鍥炲綊纭锛?
```text
baseline v082p00_distance_k64:
  low_confidence_actionable: 3022
  strict_block:              1367
  normal_mismatch:            253
  red face 9095-9150 low:      76 / 112
  red face 9263-9318 low:      82 / 112

distance_k128:
  low_confidence_actionable: 3019
  strict_block:              1364
```

缁撹锛氬崟绾墿澶?`candidate_k` 鍩烘湰鏃犳敹鐩婏紝璇存槑闂涓嶆槸鈥滃€欓€変笁瑙掗潰鏁伴噺涓嶅鈥濓紝鑰屾槸鍊欓€夐潰鎺掑簭浠嶇劧琚渶杩戣窛绂讳富瀵笺€?
鏂板 energy candidate 鎺掑簭锛?
```text
score =
  distance / near_distance
+ normal_weight * normal_penalty
+ family_purity_weight * source_weight_semantic_penalty
```

褰撳墠鏈€骞宠　閰嶇疆锛?
```text
v082p03_energy_n100
candidate_k:           128
selection_mode:        energy
normal_weight:         1.0
family_purity_weight:  0.15
family_conf_threshold: 0.58
face_semantic_l1:      1.35
```

缁撴灉锛?
```text
low_confidence_actionable: 3022 -> 2769
strict_block:              1367 -> 1104
normal_mismatch:            253 -> 1
red face 9095-9150 low:      76 -> 23
red face 9263-9318 low:      82 -> 49
```

鏇存縺杩涚殑 `normal_weight=1.6` 浼氱户缁檷浣庢€婚闄╂暟锛屼絾浼氭紡鎺夋洿澶氱敤鎴峰凡鐭ラ闄╃偣锛屽洜姝ゆ殏涓嶄綔涓洪粯璁ゃ€?
Maya 璇婃柇闆嗗悎锛?
```text
CDFDIAG_V082P03_A_LOWCONF_ACTIONABLE_SET
CDFDIAG_V082P03_A_STRICT_BLOCK_SET
CDFDIAG_V082P03_A_HIGHCONF_SUPPORTED_SET
```

涓嬩竴姝ュ噯鍏ヨ鍒欙細

- `V082P03_A_HIGHCONF_SUPPORTED` 鍙綔涓虹涓€鎵圭洿鎺ヤ紶鏉冨€欓€夈€?- `V082P03_A_STRICT_BLOCK` 涓嶅緱鐩存帴浼犳潈锛岄渶杩涘叆灞€閮?patch correspondence 鎴?inpainting / constrained inverse銆?- 宸蹭粠 low confidence 闄嶅嚭鐨勭偣涓嶈兘鐩存帴瑙嗕负瀹屾垚锛屼粛闇€鍚庣画 Maya actual DG 濮挎€侀獙鏀躲€?
v082 鏉冮噸鍊欓€夊疄娴嬪弽璇侊細

```text
娴嬭瘯浣?
  A_V082TEST_001_distanceK64
  A_V082TEST_002_energyP03

濮挎€?
  M_Jaw_A_ctrl.rotateX = 25

楠岃瘉:
  motion_delta = (target_jaw - target_neutral) - (source_jaw - source_neutral)
```

缁撴灉锛?
```text
supported motion p95:
  distance_k64: 0.01374
  energy_p03:  0.01518

red face 9095-9150 motion p95:
  distance_k64: 0.03061
  energy_p03:  0.06734

red face 9263-9318 motion p95:
  distance_k64: 0.04105
  energy_p03:  0.08422
```

缁撹锛?
- `energy_p03` 鑳藉噺灏?low-confidence 鍜?normal mismatch锛屼絾鐩存帴鎷垮畠浼犳潈浼氳 Jaw25 motion-delta 鍙樺樊銆?- 浣庣疆淇″噺灏戝彧鏄?correspondence 璇婃柇鏀剁泭锛屼笉绛変簬鏉冮噸缁撴灉姝ｇ‘銆?- 褰撳墠涓嶈兘鎶?`energy_p03` 浣滀负榛樿鍐欐潈鏂规锛涘畠鍙敤浜庡彂鐜版洿骞插噣鐨勫€欓€?provenance锛屼絾鏈€缁堟潈閲嶅繀椤荤粡杩?actual DG motion-delta / surface gate 楠屾敹銆?
v082 cross-validation 涓?motion-gate hybrid 缁撹锛?
```text
鏂板瀵圭収浣?
  A_V082TEST_003_hybridM0010
  A_V082TEST_004_hybridM0050

canonical provenance:
  v082p00_distance_k64

瑙勫垯:
  榛樿淇濈暀 distance_k64 鏉冮噸锛?  鍙湁 energy_p03 鍦ㄥ悓涓€ canonical provenance 涓嬬殑 Jaw25 motion-delta 鏄庢樉鏇村皬鏃舵墠鏇挎崲锛?  鏇挎崲 mask 闇€缁忚繃 target 鎷撴墤杩為€氬垎閲忚繃婊わ紝閬垮厤闆舵暎鍗曠偣鍣０銆?
supported motion p95:
  distance_k64:       0.013739
  hybrid_m0010_cc3:   0.013281
  hybrid_m0050_cc3:   0.013395

strict_block motion p95:
  distance_k64:       0.029067
  hybrid_m0010_cc3:   0.027131
  hybrid_m0050_cc3:   0.027354

red face 9095-9150 motion mean:
  distance_k64:       0.013744
  hybrid_m0010_cc3:   0.012830
  hybrid_m0050_cc3:   0.013031

red face 9263-9318 motion mean:
  distance_k64:       0.017476
  hybrid_m0010_cc3:   0.015652
  hybrid_m0050_cc3:   0.016038
```

瑙ｉ噴锛?
- `hybrid_m0010_cc3` 鏄綋鍓?v082 涓?Jaw25 motion-delta 鏁板瓧鏈€濂界殑鍊欓€夛紝浣嗘敹鐩婂緢灏忋€?- hybrid 鐨?absolute p95 鐣ュ崌锛岃鏄庡畠鍙槸灞€閮ㄨ繍鍔ㄨ宸敼鍠勶紝涓嶇瓑浠蜂簬鏈€缁堝舰闈㈠悎鏍笺€?- 涓嶈兘鎶婅繖绫?motion-gate 褰撶敓浜ф柟妗堬紱瀹冨彧鑳借瘉鏄庘€滃眬閮ㄦ浛鎹㈤渶瑕?actual DG 闂ㄦ帶鈥濓紝涓嬩竴姝ヤ粛搴旈拡瀵?`strict_block` / high-error ROI 鍋氬眬閮?constrained inverse 鎴?inpainting銆?
v083 weight inpainting 鍙嶈瘉锛?
```text
杈撳叆:
  base = hybrid_m0010_cc3

ROI:
  high_error_vertices:      486
  strict_error_vertices:    318
  core_vertices:            607
  domain_1ring_vertices:   1164

鍊欓€?
  A_V083TEST_001_inpaintA025
  A_V083TEST_002_inpaintA050
  A_V083TEST_003_inpaintA100

supported motion p95:
  hybrid_m0010_cc3: 0.013281
  inpaint_a025:     0.025328
  inpaint_a050:     0.045600
  inpaint_a100:     0.090811

strict_block motion p95:
  hybrid_m0010_cc3: 0.027131
  inpaint_a025:     0.197965
  inpaint_a050:     0.401865
  inpaint_a100:     0.821151

red face 9095-9150 motion mean:
  hybrid_m0010_cc3: 0.012830
  inpaint_a025:     0.102161

red face 9263-9318 motion mean:
  hybrid_m0010_cc3: 0.015652
  inpaint_a025:     0.165269
```

缁撹锛?
- 鏅€?topology weight inpainting 琚疄闄?DG 鏄庣‘鎷掔粷銆?- 澶辫触鍘熷洜鏄槾鍞?鍙ｈ厰鍖哄煙鐨勬潈閲嶈涔夎竟鐣屼笉鑳借閭绘帴骞冲潎鏇夸唬锛涘钩鍧囦細鎶?upper/lower銆乵outh/jaw銆乧heek/lip 鐨勮涔夋贩鍦ㄤ竴璧枫€?- 鍚庣画涓嶈兘鍐嶅仛鏃犵害鏉?smoothing/inpainting锛涘繀椤昏浆鍚?constrained inverse锛岀害鏉熼」鑷冲皯鍖呭惈闈炶礋銆佸綊涓€銆乼op-k銆乻ource/target 鏉冮噸璇箟涓€鑷淬€乫ace/edge 杩炵画鎬у拰 actual DG motion gate銆?
### 8.23 `M_Head_base -> A` v084 灞€閮ㄥ彈绾︽潫鍙嶆眰閫氳繃 Jaw25 瀹為檯鍥鹃獙鏀?
鐩爣锛?
```text
鍙湪 v083 core / one-ring ROI 鍐呭仛灞€閮?constrained inverse銆?涓嶅仛鍏ㄥ眬 raw inverse锛屼笉鎺ョ unsupported 韬綋杩滃尯锛屼笉鍐?test.ma銆?```

鏂板鑴氭湰锛?
```text
tools/deformation_inheritance/maya_export_v084_inverse_input.py
tools/deformation_inheritance/solve_v084_constrained_inverse.py
tools/deformation_inheritance/maya_apply_and_test_v084_inverse_candidates.py
```

杈撳叆涓庝骇鐗╋細

```text
杈撳叆:
  .info/a_weight_transfer_v082_reboot/v084_inverse_input.npz

绂荤嚎鍊欓€?
  .info/a_weight_transfer_v082_reboot/v084_weight_candidates_constrained_inverse.npz
  .info/a_weight_transfer_v082_reboot/v084_weight_candidates_constrained_inverse_summary.json

Maya 瀹為檯鍥炬姤鍛?
  .info/a_weight_transfer_v082_reboot/v084_maya_inverse_candidate_test_report.json
  .info/a_weight_transfer_v082_reboot/v084_maya_inverse_error_arrays.npz
```

鍏抽敭楠岃瘉锛?
```text
ROI 椤剁偣鏁? 1164
core 椤剁偣鏁? 607
influence 鏁? 209

LBS 鏁板 basis 瀵?Maya skinCluster 杈撳嚭閲嶅缓:
  formula_error mean: 4.02e-06
  formula_error p95:  8.63e-06
  formula_error max:  1.215e-05
```

杩欒瘉鏄?`basis_jaw25_roi` 鍙敤浜庢湰杞眬閮ㄥ弽姹傘€倂084 棣栨杩愯鏃舵毚闇蹭竴涓疄鐜伴敊璇細瀵煎嚭鐨?basis 褰㈢姸鏄?`[roi, influence, xyz]`锛屾眰瑙ｅ櫒鍐呴儴蹇呴』杞疆涓?`[xyz, influence]`锛屽惁鍒欎細鎶?influence 鍒楀綋 xyz 缁村害绱㈠紩銆傚凡淇銆?
绂荤嚎 SLSQP 鍙嶆眰璁剧疆锛?
```text
绾︽潫:
  non-negative
  sum(weights) = 1

姣忕偣鍊欓€?influence:
  base top
  expected source top
  target 涓€鐜偦鐐?base top
  鏈€澶?28 鍒?
鎺ュ彈闂?
  predicted improvement > 0.001
  row_l1 <= 0.85
```

涓夌粍鍊欓€夛細

| 鍊欓€?| base 位 | expected 位 | accepted 鐐?| core accepted | 绂荤嚎 accepted p95 | core accepted p95 |
|---|---:|---:|---:|---:|---:|---:|
| `v084_inverse_strongBase` | 0.02 | 0.004 | 964 | 588 | 0.01575 | 0.02000 |
| `v084_inverse_balanced` | 0.006 | 0.003 | 955 | 580 | 0.01529 | 0.01960 |
| `v084_inverse_motion` | 0.0015 | 0.001 | 960 | 583 | 0.01280 | 0.01606 |

Maya foreground 7002 瀹為檯 DG Jaw25 楠屾敹锛岀粺涓€浣跨敤 `distance_k64` canonical provenance 璇勬祴锛?
| 鍊欓€?| supported p95 | high p95 | low p95 | strict p95 | red 9095-9150 p95 | red 9263-9318 p95 |
|---|---:|---:|---:|---:|---:|---:|
| `hybrid_m0010_cc3` baseline | 0.013281 | 0.008212 | 0.020754 | 0.027131 | 0.028649 | 0.037674 |
| `v084_inverse_strongBase_accepted` | 0.006237 | 0.004504 | 0.009248 | 0.012338 | 0.010515 | 0.025383 |
| `v084_inverse_balanced_accepted` | 0.005558 | 0.003843 | 0.008337 | 0.010534 | 0.008887 | 0.021844 |
| `v084_inverse_motion_accepted` | 0.004658 | 0.002978 | 0.006584 | 0.008273 | 0.006951 | 0.014410 |

缁撹锛?
- v084 璇佹槑鈥滃眬閮?expected displacement bank + 闈炶礋褰掍竴 constrained inverse + actual DG gate鈥濇瘮缁х画璋?nearest/energy/inpainting 鏇存湁鏁堛€?- 褰撳墠鏈€浣虫暟鍊煎€欓€夋槸 `v084_inverse_motion_accepted`銆?- 杩欎粛鐒跺彧鏄?Jaw25 鍗曞Э鎬侀獙鏀讹紝涓嶈兘鏍囪涓烘渶缁堢敓浜ф柟妗堛€?- 涓嬩竴姝ュ繀椤诲仛澶氬Э鎬佸疄闄呭浘鍥炲綊锛欽aw 5/10/15/20/25/30銆丆heek銆丩id銆丠ead锛屼互鍙婂繀瑕佺殑 R/T/S 閫氶亾銆?- 鑻ュ濮挎€佸嚭鐜扮溂鐨甫鍔ㄣ€侀潰鐗囪啫鑳€銆佷笂涓嬪攪鏂版帴瑙︽垨灞€閮ㄦ姈鍔紝蹇呴』鍥為€€鍒?`balanced` 鎴栭€愮偣 alpha / patch surface objective锛屼笉鍏佽 raw inverse 鍏ㄩ噺鍐欏叆銆?
v084 澶氬Э鎬佸疄闄?DG 鍥炲綊锛?
```text
鑴氭湰:
  tools/deformation_inheritance/maya_validate_v084_multipose.py

鎶ュ憡:
  .info/a_weight_transfer_v082_reboot/v084_multipose_actual_dg_report.json
  .info/a_weight_transfer_v082_reboot/v084_multipose_actual_dg_error_arrays.npz

濮挎€?
  Jaw rotateX = 5 / 10 / 15 / 20 / 25 / 30
  R_CheekA_A_ctrl.translateY = 1
  L_CheekA_A_ctrl.translateY = 1
  R/L UpLidMid translateY = 0.5
  R/L LoLidMid translateY = -0.5
```

缁撴灉锛?
| 鍊欓€?| 12濮挎€?supported p95 max | 12濮挎€?supported p95 mean | strict p95 max | 鐩稿 hybrid 鍥炲綊鐐?|
|---|---:|---:|---:|---:|
| `v084_inverse_strongBase_accepted` | 0.007446 | 0.002237 | 0.014554 | 420 |
| `v084_inverse_balanced_accepted` | 0.006685 | 0.002027 | 0.012486 | 467 |
| `v084_inverse_motion_accepted` | 0.005378 | 0.001739 | 0.012008 | 494 |

瑙ｉ噴锛?
- Jaw sweep 5-30 搴﹀叏閮ㄦ鏀剁泭锛屼笖娌℃湁 `>0.005` 鐨勫洖褰掔偣銆?- 鍥炲綊鐐逛富瑕佹潵鑷?Cheek 浣嶇Щ濮挎€侊紝`R_CheekA_A_ctrl.translateY=1` 鍛戒腑 292 涓紝`L_CheekA_A_ctrl.translateY=1` 鍛戒腑 202 涓€?- Lid 娴嬭瘯鍦ㄦ湰杞槇鍊间笅鏈彂鐜版柊澧炲洖褰掋€?
v085 澶氬Э鎬侀棬鎺э細

```text
鑴氭湰:
  tools/deformation_inheritance/build_v085_multipose_gated_candidate.py

鍊欓€?
  .info/a_weight_transfer_v082_reboot/v085_weight_candidates_multipose_gate.npz
  key = v085_inverse_motion_multiposeGated

閫昏緫:
  浠?v084_inverse_motion_accepted 涓鸿緭鍏ワ紱
  鑻ヤ换涓€瀹為檯 DG 濮挎€佷腑 candidate_error - hybrid_error > 0.005锛?  鍒欒椤剁偣鏁磋鍥為€€鍒?hybrid_m0010_cc3銆?```

v085 鏁版嵁锛?
```text
v084_motion 鏀瑰姩鐐? 960
鍥為€€鐐? 494
淇濈暀鐐? 466
```

Maya 瀹為檯 DG 缁撴灉锛?
| 鍊欓€?| 12濮挎€?supported p95 max | 12濮挎€?supported p95 mean | strict p95 max | 鐩稿 hybrid 鍥炲綊鐐?|
|---|---:|---:|---:|---:|
| `v084_inverse_motion_accepted` | 0.005378 | 0.001739 | 0.012008 | 494 |
| `v085_inverse_motion_multiposeGated` | 0.011309 | 0.003373 | 0.018936 | 0 |

缁撹锛?
- v085 鎴愬姛鎶?Cheek/Lid 鍥炲綊鐐规竻闆讹紝鏄畨鍏ㄥ鐓с€?- v085 鐨?Jaw 鏀剁泭鏄庢樉浣庝簬 v084 motion锛屼笉搴旂洿鎺ユ浛浠?v084 浣滀负鏈€浣宠瑙夊€欓€夈€?- 涓嬩竴姝ヤ笉搴旂户缁‖鍥為€€鏁磋锛岃€屽簲鍋?per-row / per-pose alpha gate 鎴?patch surface objective锛氫繚鐣?Jaw 灞€閮ㄦ敹鐩婏紝鍚屾椂鍘嬩綇 Cheek 鍥炲綊銆?
v086 per-row / per-pose alpha gate锛?
```text
鏂板鑴氭湰:
  tools/deformation_inheritance/maya_export_v086_alpha_pose_vectors.py
  tools/deformation_inheritance/build_v086_alpha_pose_gate_candidate.py
  tools/deformation_inheritance/maya_apply_and_validate_v086_alpha_candidates.py

杈撳叆:
  v082 clean scene data
  v082 distance_k64 canonical correspondence
  v082 hybrid_m0010_cc3
  v084_inverse_motion_accepted
  v084/v085 actual DG comparison meshes

浜х墿:
  .info/a_weight_transfer_v082_reboot/v086_alpha_pose_vectors.npz
  .info/a_weight_transfer_v082_reboot/v086_weight_candidates_alpha_pose_gate.npz
  .info/a_weight_transfer_v082_reboot/v086_multipose_actual_dg_report.json
  .info/a_weight_transfer_v082_reboot/v086_multipose_actual_dg_error_arrays.npz
```

v086 涓嶅啀鎶婂洖褰掔偣鏁磋鍥為€€銆傚畠鍏堝湪 Maya 瀹為檯 DG 涓鍑烘瘡涓Э鎬佺殑璇樊鍚戦噺锛?
```text
error_vec(pose, vertex) =
  (target_pose - target_neutral)
- (expected_source_pose - expected_source_neutral)
```

鐒跺悗鍦ㄦ湰鍦板姣忎釜椤剁偣鎼滅储锛?
```text
W_alpha = W_hybrid + alpha * (W_v084_motion - W_hybrid)
alpha 鈭?{0, 0.05, 0.10, ..., 1.0}
```

鎺ュ彈瑙勫垯锛?
```text
Jaw 濮挎€佽鏈夋敹鐩婏紱
Cheek / Lid 濮挎€佷笉鑳芥瘮 hybrid 鍥炲綊瓒呰繃闃堝€硷紱
閫愮偣淇濈暀鏈€澶у畨鍏?alpha锛?涓嶆弧瓒崇殑鐐?alpha = 0銆?```

绂荤嚎 alpha 缁撴灉锛?
| 鍊欓€?| changed 鐐?| partial alpha 鐐?| full alpha 鐐?| blocked 鐐?|
|---|---:|---:|---:|---:|
| `v086_alphaGate_safe005` | 894 | 431 | 463 | 66 |
| `v086_alphaGate_strict003` | 795 | 381 | 414 | 165 |
| `v086_alphaGate_balanced` | 861 | 419 | 442 | 99 |

Maya foreground 7002 瀹為檯 DG 12 濮挎€侀獙鏀讹細

| 鍊欓€?| 12濮挎€?supported p95 max | 12濮挎€?supported p95 mean | strict p95 max | 鐩稿 hybrid 鍥炲綊鐐?|
|---|---:|---:|---:|---:|
| `v084_inverse_motion_accepted` | 0.005378 | 0.001739 | 0.012008 | 494 |
| `v085_inverse_motion_multiposeGated` | 0.011309 | 0.003373 | 0.018936 | 0 |
| `v086_alphaGate_safe005` | 0.009740 | 0.002961 | 0.017078 | 0 |
| `v086_alphaGate_strict003` | 0.010283 | 0.003102 | 0.018821 | 0 |
| `v086_alphaGate_balanced` | 0.009924 | 0.002992 | 0.017541 | 0 |

鎸夊叧閿Э鎬佺湅锛?
| 濮挎€?| hybrid p95 | v084 p95 | v085 p95 | v086 safe005 p95 |
|---|---:|---:|---:|---:|
| Jaw 25 | 0.013281 | 0.004658 | 0.009551 | 0.008297 |
| Jaw 30 | 0.015859 | 0.005378 | 0.011309 | 0.009740 |
| R Cheek TY 1 | 0.000008 | 0.000505 | 0.000012 | 0.000184 |
| L Cheek TY 1 | 0.000009 | 0.000071 | 0.000011 | 0.000051 |

缁撹锛?
- v086 璇佹槑鈥滈€愮偣 alpha鈥濇瘮 v085 鐨勬暣琛岀‖鍥為€€鏇村悎鐞嗭細鍚屾牱鎶?`>0.005` 鐨勫濮挎€佸洖褰掔偣鍘嬪埌 0锛屼絾淇濈暀浜嗘洿澶?Jaw 鏀剁泭銆?- 褰撳墠鏁板€兼帹鑽愬€欓€夋槸 `v086_alphaGate_safe005`锛屼綔涓轰笅涓€杞汉宸ヨ瑙夊楠岀殑涓诲€欓€夈€?- v086 浠嶄笉鏄渶缁堜骇鍝佸寲 solver锛氬畠鍙湪 v084 鐨勫眬閮?ROI 涓婂仛 alpha gate锛屽皻鏈妸 surface/collision/闈㈢墖浣撶Н鍙樺寲浣滀负浼樺寲鐩爣銆?- 涓嬩竴姝ヨ嫢鐢ㄦ埛鑲夌溂浠嶇湅鍒板眬閮ㄨ啫鑳€鎴栧槾瑙?闈㈤鍣０锛屽簲鍦?`v086_alphaGate_safe005` 鍩虹涓婂仛 patch-level surface objective锛岃€屼笉鏄洖閫€鍒版渶杩戠偣銆丟raph Cut 鎴栨棤绾︽潫骞虫粦銆?
鏂囦欢娓呯悊锛?
```text
鏃?v078-v081 涓€娆℃€ц剼鏈綊妗ｏ細
  tools/archive/deformation_inheritance_reboot_20260517/legacy_scripts/

鏃?v002-v081 椤圭洰鍦烘櫙鍜?.info 瀹為獙浜х墿褰掓。锛?  projects/ysj/20260513_193837_cdfbaixingG/archive/deformation_inheritance_reboot_20260517/

褰掓。娓呭崟锛?  projects/ysj/20260513_193837_cdfbaixingG/archive/deformation_inheritance_reboot_20260517/manifest.csv

v082 宸ヤ綔鐩綍锛?  projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/
```

淇濈暀瑙勫垯锛?
- 椤圭洰鏍圭洰褰曞彧鎶?`test.ma` 褰撴湰杞緭鍏ュ熀绾裤€?- `.info/a_weight_transfer_v081_failure_probe` 浠呬綔涓哄け璐ュ畾浣嶆姤鍛婁繚鐣欍€?- `.info/a_weight_transfer_v082_reboot` 浣滀负鏂颁竴杞敮涓€杩愯浜х墿鐩綍銆?- 绂佹璇诲彇褰掓。鐩綍閲岀殑鏃?`.npz` / 鏃?`.ma` 浣滀负 v082 杈撳叆銆?
### 8.21 `M_Head_base -> A` v087 鍏?influence 鏉冮噸鐭╅樀澶嶅埢

鐢ㄦ埛绾犳锛歚Jaw` 鍙槸鏆撮湶闂鐨勪竴涓Э鎬侊紝涓嶆槸鏈€缁堢洰鏍囥€傜湡姝ｇ洰鏍囨槸锛?
```text
M_Head_base 鐨勬墍鏈?Skin influence 鏉冮噸
鈫?澶嶅埢鍒?A
鈫?鎵€鏈夊弬涓庤挋鐨楠煎湪 R/T/S 閫氶亾涓嬮兘涓嶈兘涓插尯銆佸櫔澹般€佸眬閮ㄨ啫鑳€
```

鍥犳 v087 鎶婇獙鏀朵粠 `Jaw` 灞€閮ㄥЭ鎬佸垏鍥炲叏 influence锛?
```text
source = M_Head_base
target = A
scene  = projects/ysj/20260513_193837_cdfbaixingG/test.ma
influence_count = 209
target_vertex_count = 28925
support_domain = 9714
high_confidence = 6692
low_confidence = 3022
unsupported = 19211
```

v087 鏂板鑴氭湰锛?
```text
tools/deformation_inheritance/maya_export_v087_all_influence_input.py
tools/deformation_inheritance/solve_v087_all_influence_inverse.py
tools/deformation_inheritance/maya_apply_v087_all_influence_candidates.py
tools/deformation_inheritance/maya_mark_v087_diagnostic_sets.py
tools/deformation_inheritance/maya_save_v087_scene.py
tools/deformation_inheritance/maya_validate_v087_actual_dg_multipose.py
```

鏍稿績绠楁硶锛?
```text
1. 浠庡共鍑€ test.ma 瀵煎嚭锛?   source/target neutral points
   source skin weights
   source/target skin matrices
   209 influence names
   A-specific correspondence confidence

2. 瀵规瘡鏍?influence 鍚堟垚 R/T/S 閫氶亾锛?   rx/ry/rz = 10 degrees
   tx/ty/tz = 1.0 cm
   sx/sy/sz = 1.1 scale

3. 鐢?mapped source displacement 浣滀负 expected target displacement銆?
4. 瀵规瘡涓?target vertex / influence 鍋氶棴寮忛潪璐熸渶灏忎簩涔橈細
   w = dot(target_basis, expected_motion) / dot(target_basis, target_basis)

5. top-k prune + normalize銆?
6. 閫氳繃 gate 鍐冲畾鏄惁鎺ュ彈 raw inverse锛?   correspondence 涓?unsupported
   鏉冮噸璇箟 L1 涓嶈秴杩囬槇鍊?   row L1 涓嶈秴杩囬槇鍊?   鍏ㄩ€氶亾璇樊蹇呴』姣?base 鏀瑰杽
   strict/balanced 鍙澶栭樆鏂?topology/semantic discontinuity
```

绂荤嚎鍏ㄩ€氶亾缁撴灉锛?
| 鍊欓€?| all-channel p95 | all-channel max | rotate p95 | translate p95 | scale p95 | 鏀瑰姩鐐?|
|---|---:|---:|---:|---:|---:|---:|
| `v087_rts_raw_topk` | 0.005805 | 0.465963 | 0.005899 | 0.009366 | 0.002203 | 6849 |
| `v087_rt_raw_topk` | 0.005806 | 0.465963 | 0.005898 | 0.009368 | 0.002203 | 6847 |
| `v087_rt_guard_wide` | 0.005880 | 0.465963 | 0.005926 | 0.009572 | 0.002213 | 58 |
| `v087_rt_guard_balanced` | 0.005883 | 0.465963 | 0.005926 | 0.009576 | 0.002213 | 54 |
| `v087_rts_guard_balanced` | 0.005883 | 0.465963 | 0.005926 | 0.009576 | 0.002213 | 54 |
| `v087_rt_guard_strict` | 0.005947 | 0.465963 | 0.005942 | 0.009722 | 0.002221 | 28 |
| `v087_base_hybrid_m0010` | 0.006007 | 0.465963 | 0.005953 | 0.009921 | 0.002226 | 0 |

Maya 鍐欏洖锛?
```text
output scene:
  projects/ysj/20260513_193837_cdfbaixingG/test_v087_A_allInfluence_compare.ma

created:
  A_V087_001_baseHybrid
  A_V087_002_rawRT_DIAGNOSTIC
  A_V087_003_guardStrictRT
  A_V087_004_guardBalancedRT
  A_V087_005_guardBalancedRTS

write roundtrip:
  row_l1_max 鈮?4.8e-08
```

璇婃柇 set锛?
```text
CDFDIAG_V087_A_lowConfidence_SET              3022
CDFDIAG_V087_A_unsupported_SET                19211
CDFDIAG_V087_A_highConfidence_SET             6692
CDFDIAG_V087_A_topologyDiscontinuity_SET      275
CDFDIAG_V087_A_semanticAmbiguous_SET          959

CDFDIAG_V087_A_V087_003_guardStrictRT_changed_SET       28
CDFDIAG_V087_A_V087_004_guardBalancedRT_changed_SET     54
CDFDIAG_V087_A_V087_005_guardBalancedRTS_changed_SET    54
```

鐪熷疄 Maya DG 澶氬Э鎬侀獙鏀讹細

```text
report:
  projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v087_actual_dg_multipose_report.json

scene:
  projects/ysj/20260513_193837_cdfbaixingG/test_v087_A_allInfluence_compare.ma

source BS disabled:
  M_Head_base_blendShape.envelope

poses:
  27 涓湡瀹炴帶鍒跺櫒濮挎€?  Jaw / Mouth / Cheek / UpCheek / Upper Lid / Lower Lid / Lip / Nose / Chin
```

杩欓噷涓嶆槸鍙祴 `Jaw25`銆傚疄闄?DG 楠屾敹鎸夊悓涓€ canonical provenance 璁＄畻锛?
```text
candidate_motion_delta
vs
mapped_source_motion_delta
```

鐒跺悗鍜?`v087_base_hybrid_m0010` 鍋氬洖褰掑姣旓紝闃堝€间负 `candidate_error - base_error > 0.005`銆?
缁撴灉锛?
| 鍊欓€?| pose count | supported p95 max | supported p95 mean | high p95 max | low p95 max | strict p95 max | 鍥炲綊娆℃暟 | 鏀瑰杽娆℃暟 | 鍞竴鍥炲綊鐐?|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v087_base_hybrid_m0010` | 27 | 0.022191 | 0.002409 | 0.014578 | 0.035338 | 0.032522 | 0 | 0 | 0 |
| `v087_rt_raw_topk` | 27 | 0.022850 | 0.002465 | 0.014793 | 0.036763 | 0.034291 | 150 | 175 | 55 |
| `v087_rt_guard_strict` | 27 | 0.022390 | 0.002423 | 0.014753 | 0.035338 | 0.032522 | 47 | 50 | 18 |
| `v087_rt_guard_balanced` | 27 | 0.022552 | 0.002428 | 0.014796 | 0.035637 | 0.032522 | 81 | 103 | 31 |
| `v087_rts_guard_balanced` | 27 | 0.022552 | 0.002428 | 0.014796 | 0.035637 | 0.032522 | 81 | 102 | 31 |

鍥炲綊涓昏闆嗕腑鍦?Jaw sweep锛?
```text
v087_rt_guard_strict:
  jaw_rx_30 regressed=17
  jaw_rx_25 regressed=17
  jaw_rx_15 regressed=9

v087_rt_guard_balanced / v087_rts_guard_balanced:
  jaw_rx_30 regressed=30
  jaw_rx_25 regressed=28
  jaw_rx_15 regressed=16

v087_rt_raw_topk:
  jaw_rx_30 regressed=54
  jaw_rx_25 regressed=49
  jaw_rx_15 regressed=31
```

褰撳墠瑙ｉ噴淇锛?
- `raw` 鐗堟湰鏁板鎸囨爣鏈€濂斤紝浣嗕竴娆℃敼 6800+ 鐐癸紝鎸?v076 缁忛獙鏈夎瑙変覆鍖洪闄╋紝鍙兘浣滀负璇婃柇锛屼笉鎺ㄨ崘鐢熶骇銆?- `guard` 鐗堟湰鍙敼 28/54 鐐癸紝浣嗙湡瀹?DG 澶氬Э鎬佷粛鍑虹幇 18/31 涓敮涓€鍥炲綊鐐癸紝涓嶈兘鐩存帴浣滀负榛樿鏉冮噸銆?- `baseHybrid` 鐨勫叏 influence R/T/S 绂荤嚎鎸囨爣宸茬粡寰堜綆锛岃鏄?v086 鍚庝笉搴旂户缁彧鍥寸粫 Jaw 鍋氫慨琛ャ€?- 绂荤嚎鍏ㄩ€氶亾鎸囨爣涓嶈兘瀹氱増銆倂087 绂荤嚎 raw/guard 鐣ヤ紭锛屼絾瀹為檯 DG 鏄剧ず `baseHybrid` 浠嶆槸褰撳墠鏈€绋冲鐓с€?- v087 鐨勬湁鏁堜环鍊兼槸鎶婇獙鏀跺彛寰勪粠鍗?Jaw 鎵╁睍鍒?`209 influence 脳 R/T/S`锛屽苟璇佹槑鍚庣画 solver 蹇呴』 actual-DG-in-loop銆?- 涓嬩竴姝ヤ笉鏄墿澶?raw锛屼篃涓嶆槸缁х画璋?nearest / Graph Cut / smoothing锛岃€屾槸鎶婂け璐?patch 鐨?surface objective 绾冲叆鍙嶆眰锛氳竟闀裤€侀潰绉€佹硶绾裤€佹帴瑙?绌挎彃銆侀偦鎺ヨ繛缁€с€?- 鏂板€欓€夊彧鏈夊湪绂荤嚎鍏?influence R/T/S 鍜岀湡瀹?Maya DG 澶氬Э鎬佷袱灞傞兘涓嶅鍔犺瑙夐闄╂椂锛屾墠鍏佽鍐欏叆鐢熶骇鐩爣銆?
### 8.22 `M_Head_base -> A` v088 灏勭嚎/鍙鎬ч珮绾у搴旈獙璇?
鐢ㄦ埛鎻愬嚭锛氳繎灞傜┛鎻掍笉搴斿彧闈犳渶杩戣窛绂伙紝搴旇澧炲姞灏勭嚎銆佹硶绾胯搴︺€佽川蹇冪瓑鍒ゆ柇銆倂088 鍙楠岃瘉璇ユ柟鍚戞槸鍚︽湁瀹為檯棰勬祴鑳藉姏锛屼笉鍐欐潈閲嶃€佷笉鏀瑰嚑浣曘€?
鏂板鑴氭湰锛?
```text
tools/deformation_inheritance/probe_v088_visibility_correspondence.py
tools/deformation_inheritance/maya_mark_v088_visibility_sets.py
tools/deformation_inheritance/maya_save_v088_visibility_scene.py
```

楠岃瘉杈撳叆锛?
```text
scene data:
  v082_clean_scene_data.npz

correspondence:
  v087_all_influence_input.npz

actual DG ground truth:
  v087_actual_dg_multipose_error_arrays.npz
```

v088 涓嶆槸鐩镐俊灏勭嚎锛岃€屾槸鎶婂皠绾胯瘉鎹拰鐪熷疄 DG 閿欒浜ゅ弶楠岃瘉锛?
```text
source_first_not_best:
  target 鐐瑰埌 chosen source 鐐圭殑绾挎涓婏紝source ray 棣栧厛鍛戒腑鐨勪笉鏄?chosen triangle銆?
target_segment_blocked:
  target 鐐瑰埌 chosen source 鐐圭殑绾挎琚?target 鑷韩闈炵浉閭讳笁瑙掗潰鎸′綇銆?
target_normal_layer_hit:
  娌?target normal / -normal 鍙戝皠鐭皠绾匡紝杩戣窛绂诲懡涓彟涓€灞?target 闈€?
target_normal_front_layer_hit:
  normal 鐭皠绾垮懡涓?front-facing 杩戝眰闈€?
visibility_risk:
  涓婅堪寮鸿瘉鎹粍鍚堛€?```

缁撴灉锛?
| 鎸囨爣 | 鏁伴噺 |
|---|---:|
| supported | 9714 |
| source_first_not_best | 447 |
| target_segment_blocked | 212 |
| target_normal_layer_hit | 1055 |
| target_normal_front_layer_hit | 399 |
| visibility_risk | 703 |

鍜岀湡瀹?DG 閿欒瀵圭収锛?
| 椋庨櫓 | 鐩爣 | precision | recall | lift |
|---|---|---:|---:|---:|
| `visibility_risk` | base high error > 0.015 | 0.2788 | 0.1892 | 7.78 |
| `visibility_risk` | base high error > 0.020 | 0.2390 | 0.2113 | 8.69 |
| `visibility_risk` | candidate regression any | 0.0270 | 0.3455 | 14.21 |
| `target_normal_front_layer_hit` | candidate regression any | 0.0326 | 0.2364 | 17.13 |
| `v087_low_confidence` | candidate regression any | 0.0113 | 0.6182 | 5.92 |

绾㈡ mouth face range 瑕嗙洊锛?
| 鍖哄煙 | 鐐规暟 | visibility risk | target normal front hit | actual high error > 0.015 | candidate regression |
|---|---:|---:|---:|---:|---:|
| `9095-9150` | 112 | 59 | 59 | 58 | 9 |
| `9263-9318` | 112 | 43 | 33 | 78 | 9 |

Maya 璇婃柇鍦烘櫙锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/test_v088_A_visibility_probe.ma
```

涓昏 set锛?
```text
CDFDIAG_V088_A_visibilityRisk_SET                 703
CDFDIAG_V088_A_sourceFirstNotBest_SET             447
CDFDIAG_V088_A_targetSegmentBlocked_SET           212
CDFDIAG_V088_A_targetNormalLayerHit_SET           1055
CDFDIAG_V088_A_targetNormalFrontLayerHit_SET      399
CDFDIAG_V088_A_actualBaseHigh015_SET              1036
CDFDIAG_V088_A_candidateRegressionAny_SET         55
```

缁撹锛?
- 灏勭嚎/鍙鎬ц瘉鎹湁鐢細`visibility_risk` 瀵瑰疄闄呴珮璇樊鍜屽€欓€夊洖褰掔殑 lift 鏄庢樉楂樹簬 1銆?- 浣嗗畠涓嶆槸瀹屾暣瑙ｏ細precision 绾?24%-28%锛宺ecall 绾?19%-35%锛屼笉鑳藉崟鐙喅瀹氭潈閲嶃€?- `source_occluded` 鍦ㄥ綋鍓嶆暟鎹噷涓?0锛岃鏄庡崟绾€渢arget 鍒?source 绾挎琚?source 鎸′綇鈥濅笉鏄綋鍓嶅け璐ヤ富鍥犮€?- `target_normal_front_layer_hit` 瀵瑰€欓€夊洖褰?lift 鏈€楂橈紝璇存槑鐢ㄦ埛鎻愬嚭鐨勨€滄硶绾垮皠绾块亣鍒拌繎灞?front-facing 闈⑩€濈‘瀹炶兘浣滀负杩戝眰/绌挎彃椋庨櫓璇佹嵁銆?- 鍥犳涓嬩竴姝ュ簲鎶?ray/visibility 鍔犲叆 correspondence validator 鐨?penalty / hard-risk set锛屼絾浠嶅繀椤昏仈鍚堟潈閲嶈涔夈€乼arget 鎷撴墤杩炵画銆侀潰鐗?surface objective 鍜?actual DG 楠屾敹銆?- 涓嶈兘鎶婂皠绾垮懡涓鍒欑洿鎺ュ崌绾ф垚鈥滃垹鎺?source 鏄犲皠鈥濈殑鍞竴鍑嗗垯锛涘惁鍒欎細婕忔帀澶ч噺鐪熷疄楂樿宸偣锛屼篃浼氳鏉€涓€閮ㄥ垎鍙敤鏄犲皠銆?
### 8.24 `M_Head_base -> A` v089b 蹇€熻繛缁満涓庣湡瀹?DG 闂ㄦ帶

鐢ㄦ埛瑕佹眰鍋氫竴涓€滄渶缁堢増鏈畻娉曟祴璇曗€濓紝涓旀瘡涓€姝ュ繀椤绘湁姝ｇ‘銆佸畬鏁磋緭鍑恒€傚厛灏濊瘯鐨?v089 杩炵画鍦虹洿鎺ュ弽姹傜増鏈湪 30 鍒嗛挓鍐呮湭瀹屾垚锛屽垽瀹氫负涓嶅彲鐢細瀹冨湪姣忎釜 joint/channel 涓婇噸澶嶉噸绠?continuous expected motion锛岀粨鏋勪笂杩囬噸锛屼笉鑳借繘鍏ョ敓浜ч摼璺€?
v089b 鏀规垚鍙畬鎴愮殑鍒嗗眰娴嬭瘯锛?
```text
v082/v087/v088 杈撳叆
-> 蹇€?continuous source weight field
-> 澶嶇敤 v087 鍏?influence raw inverse
-> actual-DG-in-loop 鍥炲綊闂ㄦ帶
-> v088 visibility / normal-front / segment 椋庨櫓闂ㄦ帶
-> 璇箟銆佹嫇鎵戙€乪dge jump 闂ㄦ帶
-> Maya 瀵圭収浣撳啓鍏?-> 鐪熷疄 Maya DG 27 濮挎€侀獙鏀?```

鏂板鑴氭湰锛?
```text
tools/deformation_inheritance/solve_v089b_fast_continuous_field.py
tools/deformation_inheritance/maya_apply_v089b_fast_candidates.py
tools/deformation_inheritance/maya_validate_v089b_actual_dg_multipose.py
```

绂荤嚎杈撳嚭锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_fast_continuous_field_candidates.npz
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_fast_continuous_field_summary.json
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_fast_continuous_field_summary.csv
```

绂荤嚎闃舵鐢ㄦ椂锛?
| 姝ラ | 绉?|
|---|---:|
| load_inputs | 0.712 |
| build_fast_continuous_field | 8.637 |
| prepare_solver_inputs | 26.190 |
| build_guarded_variants | 2.596 |
| offline_all_influence_evaluation | 104.815 |
| write_outputs | 1.156 |

绂荤嚎鎺掑簭锛?
| 鍊欓€?| all p95 | all max | changed vs base |
|---|---:|---:|---:|
| `v089b_actual_safe_rt` | 0.005929 | 0.465963 | 806 |
| `v089b_visibility_guard_rt` | 0.005986 | 0.465963 | 764 |
| `v089b_final_alpha035_rt` | 0.005999 | 0.465963 | 764 |
| `v089b_base_hybrid_m0010` | 0.006007 | 0.465963 | 0 |
| `v089b_final_strict_rt` | 0.006007 | 0.465963 | 0 |
| `v089b_field_prior_topk_DIAGNOSTIC` | 0.014845 | 1.064686 | 7165 |

鍏抽敭绂荤嚎缁撹锛?
- `field_prior` 涓嶅悎鏍硷紝涓嶈兘褰撴渶缁堟潈閲嶏紱continuous field 鍙兘浣滀负 source 鏉冮噸鍏堥獙鍜岄闄╄瘉鎹€?- `v089b_actual_safe_rt` 绂荤嚎鏈€浣筹紝浣嗗畠鍙潬 actual raw no-regression / improvement 闂ㄦ帶浠嶄笉澶熶弗鏍笺€?- `v089b_visibility_guard_rt` 鏄繘鍏?Maya actual DG 楠屾敹鐨勬渶鍚堢悊鍊欓€夛細瀹冩瘮 actual_safe 澶氱敤浜?visibility銆乶ormal-front銆乻egment銆佽涔夊拰鎷撴墤闂ㄦ帶銆?
Maya 鍐欏叆锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/test_v089b_A_fastField_compare.ma
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_maya_apply_report.json
```

鍐欏洖闂悎锛?
| 鍊欓€?mesh | 鏈€澶?row L1 | 鏈€澶?abs |
|---|---:|---:|
| `A_V089B_001_baseHybrid` | 4.805e-08 | 2.974e-08 |
| `A_V089B_002_fieldPrior_DIAGNOSTIC` | 4.470e-08 | 2.974e-08 |
| `A_V089B_003_actualSafeRT` | 4.805e-08 | 2.974e-08 |
| `A_V089B_004_visibilityGuardRT` | 4.805e-08 | 2.974e-08 |
| `A_V089B_005_finalAlpha035RT` | 4.805e-08 | 2.974e-08 |
| `A_V089B_006_finalStrictRT` | 4.805e-08 | 2.974e-08 |

Maya actual DG 杈撳嚭锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_actual_dg_multipose_report.json
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_actual_dg_multipose_summary.csv
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v089b_actual_dg_multipose_error_arrays.npz
```

鐪熷疄 Maya DG 27 濮挎€侀獙鏀讹細

| 鍊欓€?| supported p95 max | supported p95 mean | low p95 max | strict p95 max | 鍥炲綊鐐规暟 | 鏀瑰杽鐐规暟 | 鍞竴鍥炲綊鐐?|
|---|---:|---:|---:|---:|---:|---:|---:|
| `v089b_base_hybrid_m0010` | 0.022191 | 0.002409 | 0.035338 | 0.032522 | 0 | 0 | 0 |
| `v089b_field_prior_topk_DIAGNOSTIC` | 0.030220 | 0.003874 | 0.082808 | 0.189099 | 4332 | 604 | 1269 |
| `v089b_actual_safe_rt` | 0.022306 | 0.002415 | 0.035351 | 0.032522 | 43 | 74 | 17 |
| `v089b_visibility_guard_rt` | 0.022112 | 0.002399 | 0.035263 | 0.032522 | 0 | 23 | 0 |
| `v089b_final_alpha035_rt` | 0.022147 | 0.002404 | 0.035331 | 0.032522 | 4 | 8 | 1 |
| `v089b_final_strict_rt` | 0.022191 | 0.002409 | 0.035338 | 0.032522 | 0 | 0 | 0 |

缁撹锛?
- `v089b_field_prior_topk_DIAGNOSTIC` 鏄庣‘澶辫触锛屼笉鑳藉啓鍥炵敓浜х洰鏍囥€?- `v089b_actual_safe_rt` 铏界劧绂荤嚎鏈€浼橈紝浣嗙湡瀹?DG 浠嶆湁 17 涓敮涓€鍥炲綊鐐癸紝涓嶈兘瀹氱増銆?- `v089b_final_alpha035_rt` 浠嶆湁 1 涓敮涓€鍥炲綊鐐癸紝涓嶈兘浣滀负涓ユ牸榛樿銆?- `v089b_final_strict_rt` 鎺ュ彈 0 鐐癸紝绛夊悓 base锛屽彧鑳戒綔涓哄畨鍏ㄧ┖鎿嶄綔瀵圭収銆?- `v089b_visibility_guard_rt` 鏄綋鍓嶅敮涓€閫氳繃涓ユ牸闂ㄦ鐨勫€欓€夛細27 濮挎€佷笅 `>0.005` 鍥炲綊鐐逛负 0锛宻upported p95 max 浠?`0.022191` 闄嶅埌 `0.022112`锛宻upported p95 mean 浠?`0.002409` 闄嶅埌 `0.002399`銆?
褰撳墠鎺ㄨ崘浜哄伐澶嶉獙瀵硅薄锛?
```text
鍦烘櫙: projects/ysj/20260513_193837_cdfbaixingG/test_v089b_A_fastField_compare.ma
鍊欓€? A_V089B_004_visibilityGuardRT
璇婃柇闆? CDFDIAG_V089B_VISIBILITYGUARDRT_ACCEPT_SET
鐪熷疄鍥炲綊闆? CDFDIAG_V089B_ACTUAL_VISIBILITYGUARDRT_REGRESSION_SET
```

褰撳墠宸ョ▼鍒ゆ柇锛?
- 杩欎笉鏄€滃畬缇庤嚜鍔ㄥ鍒烩€濈殑缁堢偣锛岃€屾槸棣栨鎶?`M_Head_base -> A` 鐨勫叏 influence 鏉冮噸澶嶅埢鍋氭垚浜嗗彲璺戝畬銆佸彲鍐欏洖銆佸彲鐪熷疄 DG 楠屾敹銆佷笖鏃犳柊澧?`>0.005` 鍥炲綊鐐圭殑鍊欓€夈€?- 鏀瑰杽骞呭害寰堝皬锛岃鏄庡ぇ閮ㄥ垎瀹夊叏绌洪棿宸茬粡琚?baseHybrid 鍚冩帀锛涚户缁彁楂樻晥鏋滀笉鑳藉啀鎵╁ぇ raw锛岃€岃鍋?patch-level surface objective锛屾妸闈㈤潰绉€佽竟闀裤€佹硶绾裤€佹帴瑙?绌挎彃浣滀负鐪熷疄浼樺寲鐩爣銆?- 鐢熶骇鍖栭粯璁ょ瓥鐣ュ簲鏄細raw / field_prior 姘歌繙鍙綔璇婃柇锛涙渶缁堝啓鍥炲繀椤荤粡杩?actual-DG-in-loop 涓?visibility/semantic/topology/edge 鑱斿悎闂ㄦ帶銆?
### 8.18 v090 Patch-Level Surface Objective 棣栬疆

鐩爣锛?
```text
M_Head_base -> A
鍙鐞?Skin 鏉冮噸澶嶅埢
鍦?v089b 鍙鎬ч棬鎺у€欓€夊熀纭€涓婏紝寮曞叆 patch-level surface objective
楠屾敹椤瑰寘鍚《鐐逛綅绉汇€佽竟闀垮簲鍙樸€侀潰闈㈢Н搴斿彉銆佹硶绾垮彉鍖?```

鏈疆鍏堝仛鐨勬槸鈥滃眬閮ㄥ€欓€夌瓫閫?+ 鐪熷疄 Maya DG 琛ㄩ潰鐩爣楠屾敹鈥濓紝杩樹笉鏄畬鏁磋繛缁紭鍖栧櫒銆傚€欓€夌敓鎴愬彧鍏佽淇敼 v089b 澶氬Э鎬侀珮璇樊涓斿眬閮ㄨ〃闈㈤闄╁彲瑙ｉ噴鐨勭偣锛岀劧鍚庢寜涓夋。 mask 杈撳嚭锛?
| 妗ｄ綅 | changed rows | 鐢ㄩ€?|
|---|---:|---|
| strict | 10 | 闆跺洖褰掍紭鍏堬紝楠岃瘉 patch 鐩爣鏄惁鑳藉畨鍏ㄧ敓鏁?|
| balanced | 94 | 淇濈暀鏇村灞€閮ㄦ敹鐩婏紝浣嗗厑璁稿皯閲忛闄╁璁?|
| broad | 272 | 璇婃柇涓婇檺锛屼笉浣滀负榛樿鎺ㄨ崘 |

杩囩▼閲岀‘璁や簡涓や釜宸ョ▼鍧戯細

| 闂 | 鏍瑰洜 | 淇 |
|---|---|---|
| 灞€閮ㄥ€欓€夊叏灞€鍙樺舰 | top-k prune 瀵规暣寮犳潈閲嶇煩闃垫墽琛岋紝鏈帴鍙楃偣涔熻鏀?| `top-k prune` 鍙綔鐢?changed mask锛屾湭鏀圭偣 L1 宸紓绾?`3.7e-09` |
| v090 琛ㄩ潰鍒嗘暟寮傚父鐖嗙偢 | 瀵圭収浣撳厛甯?display offset锛屽啀鍒涘缓 skinCluster锛屽亸绉昏繘鍏?bind 璁＄畻 | 澶嶅埗鍩哄噯鍚庡垹鍘嗗彶锛岀粦瀹氬墠 `translateX=0`锛屽啓鏉冨悗鍐嶈缃睍绀哄亸绉?|

鐪熷疄 Maya DG 27 濮挎€侀獙鏀惰緭鍑猴細

```text
projects/ysj/20260513_193837_cdfbaixingG/test_v090_A_patchSurface_candidates_eval.ma
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v090_patch_surface_candidates_objective_report.json
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v090_patch_surface_candidates_objective_summary.csv
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v090_patch_surface_candidates_objective_arrays.npz
```

Surface objective 璇勫垎锛?
```text
surface_score = vertex_p95
              + 0.50 * edge_p95
              + 0.35 * area_p95
              + 0.10 * normal_p95
```

鍏抽敭缁撴灉锛?
| 鍊欓€?| supported score max | supported score mean | total regressed tri | improved tri | unique regressed faces |
|---|---:|---:|---:|---:|---:|
| `v089b_base_hybrid_m0010` | 0.098668 | 0.049075 | 0 | 0 | 0 |
| `v090_strict_patch_direct` | 0.098623 | 0.049067 | 1 | 41 | 1 |
| `v090_strict_patch_blend035` | 0.098645 | 0.049071 | 0 | 16 | 0 |
| `v090_balanced_patch_blend035` | 0.098175 | 0.049018 | 27 | 188 | 15 |
| `v090_broad_patch_blend060_smooth` | 0.097144 | 0.048905 | 1045 | 1385 | 294 |

褰撳墠缁撹锛?
- `v090_strict_patch_blend035` 鏄敮涓€鈥滈浂鏂板閫€鍖栭潰鈥濈殑瀹夊叏鍊欓€夛細鏀剁泭寰堝皬锛屼絾绗﹀悎涓ユ牸闂ㄦ銆?- `v090_strict_patch_direct` 鍙湁 1 涓€€鍖栭潰銆?1 涓敼鍠勯潰锛屽彲浣滀负浜哄伐瀵圭収锛屼絾涓嶈兘榛樿鍐欏叆銆?- `balanced/broad` 鑳藉帇浣庡钩鍧?surface score锛屼絾鏂板閫€鍖栭潰鏄庢樉澧炲锛屽彧鑳戒綔涓鸿瘖鏂紝涓嶈繘鍏ラ粯璁ょ粨鏋溿€?- 杩欒疆璇佹槑 patch-level surface objective 鐨勯棬绂佹湁鏁堬細瀹冭兘闃绘鈥滀綅绉昏宸彉濂戒絾闈㈢墖鑶ㄨ儉/杈归暱鎷変几/娉曠嚎鍧忔帀鈥濈殑鍊欓€夎璇垽涓烘垚鍔熴€?
褰撳墠鎺ㄨ崘浜哄伐澶嶉獙瀵硅薄锛?
```text
鍦烘櫙: projects/ysj/20260513_193837_cdfbaixingG/test_v090_A_patchSurface_candidates_eval.ma
鍊欓€? A_V090_002_strictBlend035
鏁版嵁閿? v090_strict_patch_blend035
```

涓嬩竴姝ヨ鍒欙細

- 涓嶈兘鍐嶆墿澶?raw / field_prior / broad patch 浣滀负榛樿缁撴灉銆?- 濡傛灉 `A_V090_002_strictBlend035` 瑙嗚浠嶄笉澶燂紝涓嬩竴姝ュ簲鍋氱湡姝ｇ殑 patch-level constrained optimization锛氭瘡涓眬閮?patch 鍚屾椂浼樺寲鏉冮噸璇樊銆佽竟闀垮簲鍙樸€侀潰绉簲鍙樸€佹硶绾裤€佹帴瑙﹂闄╁拰鍚?patch 鏉冮噸杩炵画鎬с€?- 鍐?Maya 鍓嶅繀椤诲厛璺?unchanged-row 瀹¤銆乶eutral 瀵归綈瀹¤銆乤ctual DG 琛ㄩ潰鐩爣楠屾敹锛涘鐓?mesh 鐨勫睍绀哄亸绉讳笉寰楄繘鍏?skin bind 璁＄畻銆?
### 8.19 v091/v092锛氱湡姝?patch-level 绾︽潫浼樺寲涓庣湡瀹炶〃闈㈤棬绂?
鐢ㄦ埛鍙嶉 v090 澶氫釜鍊欓€夎瑙夊樊寮備笉鏄庢樉鍚庯紝鏈疆缁х画鍙鐞嗭細

```text
M_Head_base -> A
鍙鐞?Skin 鏉冮噸澶嶅埢
涓嶆帴鍏?BS / 鍔ㄦ€佽〃鎯呴摼
```

v091 鍋氫簡绗竴鐗堢湡姝ｇ殑 patch-level constrained optimization銆傛眰瑙ｅ櫒涓嶅啀鍙浛鎹㈠€欓€夋潈閲嶏紝鑰屾槸鍦?260 涓?ROI 椤剁偣銆?89 鏉″眬閮ㄨ竟銆?15 涓眬閮ㄤ笁瑙掗潰涓婂仛绂绘暎鍧愭爣涓嬮檷锛岀洰鏍囧嚱鏁板寘鍚細

```text
vertex displacement error
edge length strain
triangle area strain
triangle normal direction
base / expected weight prior
same-patch weight smoothness
```

v091 绂荤嚎 objective 纭疄涓嬮檷锛屽苟鍐欏叆 3 涓€欓€夛細

```text
projects/ysj/20260513_193837_cdfbaixingG/test_v091_A_patchConstrained_candidates_eval.ma
```

浣嗙湡瀹?Maya DG 27 濮挎€侀獙鏀舵病鏈夐€氳繃榛樿鍊欓€夐棬妲涳細

| 鍊欓€?| supported max | supported mean | low max | strict max | red left max | red mid max | regressed tri | improved tri | unique regressed faces |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v089b_base_hybrid_m0010` | 0.098668 | 0.049075 | 0.204815 | 0.203464 | 0.830102 | 0.644758 | 0 | 0 | 0 |
| `v091_surface_balanced` | 0.099387 | 0.049168 | 0.210011 | 0.205781 | 0.791013 | 0.657627 | 735 | 1031 | 183 |
| `v091_surface_visible` | 0.099387 | 0.049168 | 0.210011 | 0.205781 | 0.791013 | 0.657627 | 735 | 1031 | 183 |
| `v091_surface_shape_guard` | 0.099253 | 0.049154 | 0.209767 | 0.204944 | 0.791013 | 0.629153 | 729 | 1041 | 181 |

缁撹锛?
- v091 璇佹槑鈥滈《鐐广€佽竟闀裤€侀潰绉€佹硶绾库€濆凡缁忚繘鍏ョ湡瀹炴眰瑙ｇ洰鏍囥€?- 浣?v091 鐨勭绾跨洰鏍囧嚱鏁颁粛浼氭妸灞€閮ㄦ敼鍠勬崲鎴愬ぇ閲忕湡瀹?DG 鍥炲綊闈€?- 涓嶈兘鎶?v091 浣滀负鎺ㄨ崘鏉冮噸锛屽彧鑳戒綔涓?patch 浼樺寲婧愬€欓€夈€?
v092 鍦?v091 鐨?`shape_guard` 鍩虹涓婃柊澧?actual-surface gate锛氬厛鐢?v091 鐨勭湡瀹?Maya DG tri-score 鏁扮粍鍒ゆ柇姣忎釜鍊欓€?patch 闈㈡槸鍚﹀湪澶氬Э鎬佷笅鐪熺殑鏀瑰杽锛屽啀鍐冲畾鍝簺椤剁偣鍏佽浠?base 鏉冮噸鍒囧埌 v091 鏉冮噸銆傛崲鍙ヨ瘽璇达細

```text
patch objective 璐熻矗鎵惧彲鑳芥洿濂界殑灞€閮ㄦ潈閲?actual surface gate 璐熻矗鎷掔粷鐪熷疄 DG 涓€犳垚鍥炲綊鐨勯潰鐗?```

v092 杈撳嚭锛?
```text
projects/ysj/20260513_193837_cdfbaixingG/test_v092_A_surfaceGate_candidates_eval.ma
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v092_surface_gate_objective_summary.csv
projects/ysj/20260513_193837_cdfbaixingG/.info/a_weight_transfer_v082_reboot/v092_surface_gate_objective_report.json
```

鐪熷疄 Maya DG 27 濮挎€侀獙鏀讹細

| 鍊欓€?| changed vertices | supported max | supported mean | low max | strict max | red left max | red mid max | regressed tri | improved tri | unique regressed faces |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `v089b_base_hybrid_m0010` | 0 | 0.098668 | 0.049075 | 0.204815 | 0.203464 | 0.830102 | 0.644758 | 0 | 0 | 0 |
| `v092_gate_tight` | 1 | 0.098682 | 0.049075 | 0.204815 | 0.203464 | 0.830102 | 0.644758 | 3 | 4 | 2 |
| `v092_gate_balanced` | 4 | 0.098636 | 0.049070 | 0.204815 | 0.203464 | 0.830102 | 0.644758 | 5 | 22 | 3 |
| `v092_gate_visible` | 10 | 0.098636 | 0.049071 | 0.204827 | 0.203611 | 0.830102 | 0.644758 | 6 | 28 | 4 |
| `v092_gate_patch_loose` | 42 | 0.098523 | 0.049067 | 0.204377 | 0.203234 | 0.830102 | 0.643326 | 5 | 57 | 5 |
| `v092_gate_patch_votes` | 80 | 0.098472 | 0.049056 | 0.204377 | 0.204621 | 0.830102 | 0.643358 | 3 | 88 | 3 |

褰撳墠鎺ㄨ崘浜哄伐澶嶉獙瀵硅薄锛?
```text
鍦烘櫙: projects/ysj/20260513_193837_cdfbaixingG/test_v092_A_surfaceGate_candidates_eval.ma
鍊欓€? A_V092_004_patchLoose
鏁版嵁閿? v092_gate_patch_loose
褰撳墠 Maya 宸茶缃?M_Jaw_A_ctrl.rotateX = 25锛屽苟閫変腑 A_V089B_001_baseHybrid 涓?A_V092_004_patchLoose
```

褰撳墠缁撹锛?
- `v092_gate_patch_loose` 鏄綋鍓嶆渶绋冲€欓€夛細supported / low / strict / red_mid 閮借緝 base 灏忓箙涓嬮檷锛屼笖鍥炲綊闈㈡帶鍒跺湪 5 涓€?- `v092_gate_patch_votes` supported 鎸囨爣鏇翠綆锛屼絾 strict max 鍙樺樊锛屼笉鑳戒綔涓洪粯璁ゆ帹鑽愩€?- `v092_gate_tight/balanced/visible` 澶繚瀹堬紝涓昏鐢ㄤ簬璇佹槑闂ㄧ涓嶄細鎵╁ぇ椋庨櫓锛屼笉婊¤冻鍙鏀瑰杽鐩爣銆?- 褰撳墠闃舵浠嶆湭瀹屾垚鈥滄渶缁堜骇鍝佺骇鏉冮噸澶嶅埢鈥濄€傚畠鍙槸鎶?v091 鐨勭湡 patch 浼樺寲缁撴灉鍘嬪洖鏇村畨鍏ㄧ殑鐪熷疄 DG 鍙帴鍙楀煙銆?
涓嬩竴姝ヨ鍒欙細

- 浜哄伐浼樺厛澶嶉獙 `A_V092_004_patchLoose`銆?- 鑻ヤ粛鐪嬩笉鍑烘湁鏁堟敼鍠勶紝涓嬩竴姝ヤ笉鑳界户缁斁瀹介棬绂侊紱搴旀敼杩?expected target surface锛氫緥濡傛洿濂界殑灞€閮?correspondence / local chart锛屽啀閲嶆柊杩涘叆 patch objective銆?- 鑻ヨ瑙夊眬閮ㄥ彲鎺ュ彈浣嗕粛鏈夊皬鍥炲綊闈紝涓嬩竴姝ュ簲鍙拡瀵?`CDFDIAG_V092_SURFACE_*_REGRESSIONFACE_SET` 鍋氫簩娆″眬閮ㄤ慨姝ｏ紝绂佹鍏ㄥ眬閲嶇畻銆?
## 9. 澶辫触鍒ゅ畾

鍑虹幇浠ヤ笅鎯呭喌锛屼笉鑳芥爣璁板畬鎴愶細

- retopo body 鐨?ROM surface error 涓嶇ǔ瀹氥€?- merge/split 鍚?joint leakage 楂樹簬 `copySkinWeights`銆?- tight cloth 鎴?belt 澶ч潰绉户鎵块敊璇?owner銆?- confidence map 涓嶈兘棰勬祴澶辫触鍖哄煙銆?- patch smoothing 鍚庝粛鍑虹幇楂橀鏉冮噸鍣０銆?- 鍚屼竴杈撳叆澶氭杩愯缁撴灉涓嶇‘瀹氥€?- Live BS 鍙縼浜嗛潤鎬?delta锛屾病鏈夎縼 live skin / inner BS / driver銆?- 鎺у埗鍣ㄥЭ鎬佸け璐ワ紝浣?alias 鍗曠嫭婵€娲婚€氳繃銆?- 鍙ｈ厰 source-only mesh 闈欐€佷笉鍔ㄦ垨缂哄皯鍘?rig 鐨?BS/skin 閾捐矾銆?
瀹氫綅瑙勫垯锛?
| 澶辫触琛ㄧ幇 | 浼樺厛鎬€鐤?|
|---|---|
| 鏉冮噸鍣０ | smoothing / pruning / normalization |
| 璺ㄥ眰姹℃煋 | owner / layer / barrier 涓嶈冻 |
| 杩戝眰涓叉潈 | motion ownership 缂哄け |
| ROM 璇樊澶?| field 鍒嗚鲸鐜囥€乷wner 閿欒鎴?corrective 缂哄け |
| Corrective 涓㈠け | residual field / BS 閾捐矾鏈帴鍏?|
| 闀垮彂/鎶澶辫触 | 鎶?loose structure 褰?body-bound structure |
| 鎺у埗鍣ㄥЭ鎬佸け璐?| 鍙祴 alias锛屾病娴嬬湡瀹?rig network |

## 10. 褰撳墠浜嬪疄蹇収

鐜锛?
```text
浠撳簱: Y:\GGbommer\scripts\CGI_Pipeline
Maya: 2025, API 20250300
椤圭洰 conda 鐜: cgi_pipeline
褰撳墠 shell Python: base Python 3.13.12
Global_Lessons.md: 涓嶅瓨鍦?```

褰撳墠鏈畬鎴愪簨椤癸細

- `maya_sync_rig_incremental` 鐨?Live BS 浜у搧鍖栬ˉ寮轰粛鏈叏閮ㄩ棴鐜€?- v011/v013 鐨?composite mouth target銆丷OM motion barrier銆乴ive target face skin 婧愪慨姝ｄ粛鏄獙璇佽剼鏈骇鎴愭灉锛岄渶鎶借薄涓哄彲閰嶇疆 solver銆?- 鍙ｈ厰楠屾敹鑴氭湰瑕佷互 composite owner 涓哄噯锛屼笉鑳藉啀鎶?merged teeth/gum target 褰撳崟鐙?teeth銆?- 鍔ㄦ€?BS live target 鐨?Skin 楠屾敹瑕佹寜鐪熷疄 source mesh锛屼緥濡?`M_Head_base`锛屼笉鑳芥贩鐢ㄦ渶缁?`RIG_body_msh`銆?- v025 宸茶瘉鏄?patch-level constrained LBS inverse 鍙繍琛岋紝浣?smooth-only 涓嶅锛泇026 宸插疄闄呭啓鍥炲苟鍙栧緱姝ｆ敹鐩婏紝浣嗕粛闇€ Maya 瀹為檯渚濊禆鍥惧濮挎€佸洖褰掞紝涓嶈兘鍙浉淇＄绾?LBS 浠ｇ悊銆?- `M_Head_base -> A` v072-v075 宸茶瘉鏄庢潈閲嶅啓鍏ュ拰 LBS 鏁板闂悎鍙潬锛屼絾鍏?influence R/T/S 浣嶇Щ鍦轰粛涓嶅璐?source锛涗笅涓€闃舵瑕佷粠鈥滄槧灏勫瀷浼犳潈鈥濆崌绾у埌鈥渃orrespondence + expected displacement bank + constrained inverse solve鈥濄€?- 鍙嶆眰鏉冮噸鍓嶅繀椤诲厛璇佹槑 expected target displacement 鍙俊锛涘惁鍒?SciPy/CVXPY 鍙細绋冲畾鎷熷悎閿欒 correspondence銆?- `M_Head_base -> A` 涓嬩竴杞熀鍑嗗凡閲嶇疆涓?`test.ma`锛泇076/v077 鍙繚鐣欎负鍙嶄緥鍜屽畨鍏ㄩ榾楠岃瘉锛屼笉鑳界户缁鐢?body2 鐨勬棫 correspondence map銆?- `core/deformation_field.py` 鏈夐儴鍒嗘敞閲?鍘嗗彶浠ｇ爜琛ㄨ揪浜嗙洰鏍囪兘鍔涳紝鍚庣画搴旀竻鐞嗘垚鈥滃綋鍓嶅疄鐜扳€濆拰鈥滆鍒掕兘鍔涒€濅袱灞傦紝閬垮厤璇銆?
## 11. 璧勬枡绱㈠紩

- Autodesk `copySkinWeights` 鏂囨。锛歔copySkinWeights command](https://help.autodesk.com/cloudhelp/2022/JPN/Maya-Tech-Docs/CommandsPython/copySkinWeights.html)
- OpenVDB 瀹樻柟浠嬬粛锛歔OpenVDB About](https://www.openvdb.org/about/)
- libigl Generalized Winding Number 鏁欑▼锛歔libigl tutorial](https://libigl.github.io/tutorial/)
- libigl `igl::winding_number` 鎺ュ彛锛歔winding_number.h](https://libigl.github.io/dox/winding__number_8h.html)
- Geodesic Voxel Binding 璁烘枃椤碉細[Geodesic Voxel Binding for Production Character Meshes](https://diglib.eg.org/items/3d3458d9-bdf2-41c7-8b84-5da16b5cd637)
- libigl BBW 鎺ュ彛锛歔bbw.h](https://libigl.github.io/dox/bbw_8h.html)
- Deformation Transfer 璁烘枃椤碉細[Deformation Transfer for Triangle Meshes](https://people.csail.mit.edu/sumner/research/deftransfer/)
- zMayaTools BS 杩佺Щ璇存槑锛歔Retarget Blend Shapes](https://zewt.github.io/zMayaTools/tool-retarget-blend-shapes.html)
- AKEric Skinner锛歔GitHub - AKEric/skinner](https://github.com/AKEric/skinner)
- MayaMeshRetarget锛歔GitHub - yamahigashi/MayaMeshRetarget](https://github.com/yamahigashi/MayaMeshRetarget)
- EA SEED Dem Bones锛歔Open Source Dem Bones](https://www.ea.com/seed/news/open-source-dem-bones)
- Graph Cut alpha-expansion锛歔Fast Approximate Energy Minimization via Graph Cuts](https://www.csd.uwo.ca/~yboykov/Abstracts/pami01-abs.shtml)
- pyFM functional maps锛歔GitHub - RobinMagnet/pyFM](https://github.com/RobinMagnet/pyFM)
- potpourri3d / heat method geodesic锛歔GitHub - nmwsharp/potpourri3d](https://github.com/nmwsharp/potpourri3d)
- Robust Skin Weights Transfer via Weight Inpainting锛歔preprint PDF](https://www.dgp.toronto.edu/~rinat/projects/RobustSkinWeightsTransfer/preprint.pdf)
- Unreal Chaos Cloth Transfer Skin Weights Method锛歔Epic Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/ChaosClothAssetTransferSkinWeightsMethod?application_version=5.6)
