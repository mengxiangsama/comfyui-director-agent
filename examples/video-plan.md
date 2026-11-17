# 夜行计划：机甲猫与侦察无人机短视频（人工编写示例）

**状态：这是创作计划，尚未执行或验收任何 ComfyUI 任务。**

## 原始需求

```text
雨夜赛博朋克城市里，一只橙色机甲猫和一架小型青色侦察无人机，先分别生成背景、猫与无人机，再合成竖版画面，制作约5秒短视频。猫缓慢抬头，无人机轻微悬停，镜头轻推；标题“夜行计划”在视频生成后精确叠加。
```

## 成片规格与统一视觉约定

- 类型：视频（先生成合成首帧，再图生视频）
- 最终画布：768 × 1152 像素
- 风格：电影感写实概念视觉；机械结构可信，材质细节清晰，避免卡通比例。
- 色彩：深海军蓝与青色构成夜景；橙色机甲主体为暖色焦点；标题为暖白色。
- 共同光照：左前方暖橙色橱窗光为主光，右后方青色霓虹为轮廓光；阴影朝右后方。各图层保持同样方向与色温。
- 共同镜头与透视：接近猫眼高度的低机位，轻微仰视，三分之四正面；约50mm等效视角，垂直线基本稳定，地面透视通向画面上部中央。
- 构图与留白：2:3竖版。上方约20%为低细节标题区；机甲猫位于中下部，朝镜头偏左；两侧建筑形成框景，湿路面占底部。 小型无人机位于猫的右上方，与耳朵保持间隔，不能遮住标题。

必须共同遵守的约束：

- 画面中只有一只橙色机甲猫和一架青色无人机；背景不重复生成主体。
- 猫的耳朵、四肢和尾巴完整，机械关节和金属壳体结构连续。
- “夜行计划”只通过text_layers原样排版，所有生成素材保持无可读文案。
- 背景、主体使用相同低机位与左暖右冷光照，不擅自改变主体数量。

## 机器分工与输入输出

按 background/foreground/video 能力池分工；实际机器数量、地址、模型和并发数来自集群配置，不固定两台。

| 阶段 | 负责人 | 输入 | 输出 | 依赖 |
| --- | --- | --- | --- | --- |
| 背景生成 | 背景 worker 池 / background | 背景提示词、统一视觉约定、画布 | background.png | 无，可与各前景任务并行 |
| 主前景生成 | 前景 worker 池 / foreground | 主体提示词、统一视觉约定 | foreground.png；已有 alpha/遮罩则一并交付 | 无，可与背景及其他前景并行 |
| 抠图与合成 | 调度机 / compositor | 背景、主体、alpha/前景遮罩、placement | composite.png | 等待背景与全部前景完成 |
| 额外前景 `scout_drone` | 前景 worker 池 / foreground | 该层提示词、统一视觉约定 | scout_drone.png 与对应 alpha/遮罩 | 无，可与背景及其他前景并行 |
| 图生视频 | 视频 worker 池 / video | 无文案的合成首帧、视频提示词与帧率/帧数 | video.mp4 | 合成完成，并配置视频工作流 |
| 精确文字排版 | 后期排版 / typography | 下表逐字文案、字体与位置 | 最终带字图片/视频（视频由 ffmpeg 后期叠加） | 在视觉生成完成后 |

执行顺序：背景与各前景按可用 worker 并行（单机排队） → 等待全部完成 → 按层顺序抠图/合成 → 图生视频 → 精确文字排版。

普通生图未必带透明背景；必须提供有效 alpha/前景遮罩，或为调度机配置抠图能力。
多台机器的显存不自动合并；存在前后依赖的阶段必须等待输入就绪。

## 完整成图提示词（统一构图参考，也可用于单机生成）

### 中文正向提示词

```text
一张2:3竖版电影海报的无字视觉底图。深夜的赛博朋克城市刚下过雨，湿润沥青把两侧青色与暖橙色霓虹拉成长条倒影。中下部一只橙色机甲猫以三分之四正面朝镜头偏左站立，左前爪略向前，耳朵竖起，尾巴沿右侧轻弯。橙色搪瓷金属外壳带细小擦痕，关节处露出深灰机械结构，眼部是柔和青色光。左前方暖橙色橱窗照亮其脸和胸部，右后方青色霓虹勾勒耳朵与背部边缘。低机位接近猫眼高度，50mm等效视角，背景建筑垂直线稳定，远处灯光被细雨和薄雾柔化。上方约20%保持深蓝色低细节空间，供后期绘制标题；不生成标题本身。 猫的右上方有一架小型青色侦察无人机，完整轮廓，与猫和标题区分离；这是用户明确要求的第二个对象。
```

### English positive prompt

```text
A text-free visual plate for a 2:3 vertical cinematic poster. A cyberpunk city at night just after rain, wet asphalt stretching cyan and warm orange neon into long reflections. In the lower middle stands exactly one orange mechanized cat in a front three-quarter view, turned slightly toward camera left. Its left front paw is a little forward, ears upright, tail gently curving on its right. The orange enamel metal armor has fine scratches, dark gray mechanical joints remain visible, and its eyes emit a soft cyan glow. A warm orange storefront at front left lights the face and chest; cyan neon from rear right rims the ears and back. The camera is low, near the cat's eye level, with a 50mm-equivalent perspective and stable vertical building lines. Light rain and thin haze soften distant lights. Reserve the upper fifth as a quiet dark navy area for typography added later. Render the scene without lettering. One small cyan scout drone hovers above and to the right of the cat, with its full silhouette separated from the cat and the reserved title area.
```

### 负向提示词

```text
duplicate cat, cartoon anatomy, cropped ears, cropped tail, extra legs, floating paws, inconsistent lighting, illegible lettering, watermark
```

负向提示词仅在下游模型/工作流支持时使用。

### 验收条件

- [ ] 一只橙色机甲猫，写实机械结构与指定姿态一致。
- [ ] 竖版构图与标题留白清晰，生成底图无文字。
- [ ] 主光、轮廓光、地面透视一致。
- [ ] 恰好一架青色无人机，处于猫右上方且不遮标题。

## 背景 worker：背景生成

### 中文正向提示词

```text
只生成雨夜城市环境底图，768×1152竖版。狭窄赛博朋克街道，两侧深色建筑和抽象发光面板形成框景；不出现人物、猫、动物或车辆中心主体。近景湿沥青带细小积水与破碎反光，中下部中央保留一块连续、没有杂物遮挡的路面，供后期放置机甲猫。街道透视通向上部中央，镜头在接近猫眼高度的低机位，视角约50mm，建筑垂线保持稳定。画面左侧暖橙色橱窗光、右后侧青色霓虹；细雨与远处薄雾降低背景对比。上方20%深海军蓝区域只保留少量朦胧灯光，为暖白标题留出可读空间。所有面板采用抽象色块，不生成可读字符。 无人机也在独立图层生成，背景不要提前画出无人机。
```

### English positive prompt

```text
Generate only the environment plate, a 768 by 1152 vertical rainy cyberpunk street at night. Dark buildings and abstract glowing panels frame both sides. Keep the scene free of people, cats, animals, and hero vehicles. Wet asphalt in the foreground contains small puddles and broken neon reflections. Leave an uninterrupted clear patch of road in the central lower part for a cat to be composited later. The street perspective converges toward the upper center. Use a low camera near cat-eye height, a 50mm-equivalent perspective, and stable vertical building lines. Warm orange storefront light comes from the left; cyan neon comes from rear right. Fine rain and distant haze soften background contrast. Keep the upper fifth predominantly dark navy and low in detail so warm white typography can be added later. Panels contain abstract colored shapes rather than readable characters. The drone is another independent asset and must not appear in this background plate.
```

### 负向提示词

```text
cat, animal, person, central vehicle, central obstacle, title, readable text, watermark, daylight, fisheye distortion, drone
```

负向提示词仅在下游模型/工作流支持时使用。

### 验收条件

- [ ] 背景不含猫和重复主角，中央下部可放主体。
- [ ] 上方标题区保持暗色低细节，未生成文字。
- [ ] 暖橙左光、青色右后光、低机位透视符合共同约定。

## 前景 worker：主前景生成

### 中文正向提示词

```text
单独生成一只完整橙色机甲猫作为抠图素材。三分之四正面，朝镜头偏左，站姿稳定，左前爪略向前；耳朵竖起，尾巴沿身体右侧轻弯，四肢数量正确且所有外轮廓留在图内。橙色搪瓷金属壳体具有细小擦痕，胸口与关节处露出深灰机械结构，青色发光双眼。低机位接近猫眼高度，约50mm等效视角。左前方暖橙色主光照亮脸与胸部，右后方青色轮廓光勾勒背部与耳朵。采用单纯中性灰背景，与猫的轮廓形成充分对比，不要街道、霓虹面板或其他物体。主体占画面大部分但不裁切，便于后续真实抠图或分割；不要把画出的棋盘格当作透明。
```

### English positive prompt

```text
Generate one complete orange mechanized cat as an isolated cutout asset. Front three-quarter view turned slightly toward camera left, stable standing pose with the left front paw a little forward. Ears upright, tail gently curved along its right side, correct limb count, every part of the silhouette fully inside the frame. Orange enamel metal armor shows fine scratches; dark gray mechanisms are exposed at the chest and joints; both eyes glow cyan. Use a low camera near cat-eye height and a 50mm-equivalent perspective. Warm orange key light from front left illuminates the face and chest, with a cyan rim from rear right along the ears and back. Place the subject on a simple neutral gray field with clear silhouette contrast, without a street, neon panels, or extra objects. Let the cat fill most of the frame while keeping every extremity visible. This asset will require actual alpha, a segmentation mask, or background removal; do not render a checkerboard pattern.
```

### 负向提示词

```text
multiple cats, cropped ears, cropped paws, cropped tail, extra limbs, fused joints, complex background, street, text, checkerboard, watermark
```

负向提示词仅在下游模型/工作流支持时使用。

### 验收条件

- [ ] 仅一只橙色机甲猫，耳朵、四肢、尾巴完整。
- [ ] 姿态、金属结构、相机角度与共同光照相符。
- [ ] 交付真实透明alpha或白主体黑背景mask；普通RGB输出需配置rembg。

## 调度机：抠图与合成指令

- 画布：768 × 1152 像素，左上角为原点。
- 主体目标位置：x=144，y=440。
- 主体目标框：width=480，height=640 像素。
- 边缘羽化：0.8 像素。
- 遮罩约定：白色/1 表示保留前景，黑色/0 表示背景；遮罩必须与前景源图对应。
- 缩放时保持主体比例；目标框只定义放置区域，不应通过拉伸改变主体形态。

执行器负责缩放、alpha/遮罩合成和羽化。下面的阴影/光照要求需由素材生成、指定工作流或后续人工调整实现，普通像素合成不会自动重新布光。

```text
按真实alpha范围裁掉透明留白，保持猫的比例缩放到480×640框，水平居中并底部对齐，落点位于画布y=1080附近。背景保留少量雨夜环境反光即可；本示例不要求程序自动生成猫的倒影或接触阴影。标题区不可被猫遮住。
```

### 合成验收

- [ ] 前景边缘没有灰底残留或明显光晕。
- [ ] 主体保持比例，未拉伸；爪部贴近指定路面落点。
- [ ] 标题区域空出，整体光照与透视没有明显冲突。

## 额外前景任务与叠放顺序

先放置主前景，再按下面的顺序叠加额外前景；越靠后越位于画面上层。

## 额外前景 1：scout_drone

### 中文正向提示词

```text
一架小型青色金属侦察无人机，两个封闭式旋翼罩与中央圆形摄像头清晰，三分之四正面偏左视角，全部旋翼轮廓完整。它将出现在雨夜城市的机甲猫右上方，与猫保持同一电影感写实材质。左前方暖橙色光在壳体上留下窄高光，右后方青色光勾边；相机轻微仰视。单纯中性灰对比背景，没有猫、街道或第二架无人机。主体轮廓清晰，供真实分割或抠图使用。
```

### English positive prompt

```text
One small cyan metal scout drone with two enclosed rotor shrouds and one central circular camera, shown in a front three-quarter view angled slightly left. Keep every rotor silhouette complete. It will appear above and to the right of a mechanized cat in a rainy city, sharing the same cinematic realistic material treatment. Warm orange light from front left makes narrow highlights on the shell, and cyan rear-right light rims the edges. View it with a slight upward camera angle. Use a simple contrasting neutral gray background, without a cat, street, or second drone. Keep a clear silhouette for actual segmentation or background removal.
```

### 负向提示词

```text
multiple drones, cropped rotors, cat, street, text, watermark, checkerboard
```

负向提示词仅在下游模型/工作流支持时使用。

### 验收条件

- [ ] 恰好一架无人机，旋翼和机身完整。
- [ ] 暖左光、青色右后光与主画面一致。
- [ ] 有真实alpha或mask，或由配置执行抠图。

- 目标位置：x=540，y=328；目标框：152 × 144 像素。
- 边缘羽化：0.5 像素；使用本层前景对应的 alpha/遮罩。

```text
无人机放在猫右上方，不与猫耳重叠，保持比例，在目标框内水平居中、底部对齐。此层在猫之后叠放。
```

- [ ] 无人机不遮挡标题或猫耳。
- [ ] 尺寸比猫明显小，没有灰色背景残留。

## 精确文字排版清单

文案必须逐字保留，使用实际字体绘制，不交给扩散模型猜字。坐标单位为像素。

| 原样文案 | x | y | 字号 | 颜色 |
| --- | --- | --- | --- | --- |
| 夜行计划 | 104 | 96 | 80 | #F7E6C4 |

逐字文案 1（保留原始换行）：

```text
夜行计划
```

- [ ] 使用覆盖所需中文字符的字体，逐字核对文案。
- [ ] 检查文字实际宽高、换行和边缘，不能只检查起点坐标。
- [ ] 视频中的精确文字应在视频生成完成后叠加，避免字形随画面运动变形。

## 视频 worker：图生视频

输入：上一阶段无文案的合成图片作为首帧。需要为可用 worker 配置 video 角色和对应工作流。

- 目标时长：5.0625 秒
- 输出帧率：16 fps
- 生成帧数：81
- 按帧数计算时长：5.062 秒（与目标在一帧误差内）

### 中文动作与运镜提示词

```text
以无字合成图作为首帧，保持橙色机甲猫、青色无人机、雨夜街道的外观、数量和位置关系。约5秒内，猫缓慢抬起头部并轻微转向左侧，身体和四肢维持稳定站姿，尾尖只做很小幅度摆动；无人机在原位轻微悬停，摄像头方向基本稳定。细雨连续落下，远处霓虹只有柔和亮度变化，地面反光轻微流动。镜头沿原有低机位缓慢向前推进很短距离，无切镜、无快速旋转、无突变透视。画面上方标题区保持低细节。图生视频阶段不生成文字，标题之后用实际字体叠加。
```

### English motion and camera prompt

```text
Use the clean text-free composite as the starting frame. Preserve the appearance, count, and spatial relationship of the orange mechanized cat, cyan scout drone, and rainy city street. Over about five seconds the cat slowly raises its head and turns it slightly left while its body and paws maintain a stable standing pose; only the tail tip makes a tiny movement. The drone gently hovers near its original position with a mostly steady camera orientation. Fine rain falls continuously, distant neon changes brightness softly, and wet-road reflections shift subtly. The camera makes a very short gentle forward push from the original low viewpoint. Keep one continuous shot with stable perspective. Preserve the quiet upper title area. Generate no lettering during image-to-video; exact typography will be composited afterward.
```

### 视频负向提示词

```text
identity drift, duplicate cat, duplicate drone, extra limbs, broken mechanical joints, violent motion, camera spin, hard cut, sudden scene change, generated text, flicker
```

### 视频验收

- [ ] 猫与无人机的外观和数量连续稳定，四肢与旋翼没有明显变形。
- [ ] 雨、悬停和推进运动平缓，没有切镜或场景突变。
- [ ] 视频生成后叠加“夜行计划”四字，字形逐帧保持一致。

## 假设与待实际验证项

- 本文件为人工编写的创作计划示例，不是真实LLM响应，也没有执行GPU出图。
- 用户未指定像素尺寸，示例选768×1152的2:3竖版；实际工作流需支持这些尺寸。
- 标题“夜行计划”在无字视频生成后叠加；需配置覆盖中文字形的字体，并安装ffmpeg/ffprobe。
- 字体由使用者配置，需支持全部中文；普通RGB前景默认通过配置中的rembg抠图，首次使用可能下载模型。
- 没有承诺自动生成精确接触阴影、倒影或局部重绘；画面融合需查看真实结果。
- 81帧、16fps对应5.0625秒；实际视频模型需支持该帧数与画幅，否则应同步调整计划和工作流。
- 提示词可用性、模型效果、素材融合与真实耗时，需在实际 ComfyUI 工作流中验证。
- 本报告不代表已生成图片/视频，也不代表已通过上述验收。
