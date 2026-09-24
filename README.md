# 孟柏汝 BoRu → X4: Foundations **Argon 女性** NPC

把《女鬼桥 开魂路》(The Bridge Curse: Road to Salvation) 的女主角**孟柏汝**
（UE4 骨骼网格 `SK_BR`，80 骨）移植成《X4：基石》里的 Argon 女性 NPC 外观。

这是本工作区的第四个 X4 角色 mod，也是第一个**来源不是 MMD/PMX 或 RE Engine**
的项目（UE4 / ActorX `.psk` + 蒙皮权重），所以坐标约定、骨架层级、贴图打包
全部重新量过一遍 —— 见 [`docs/孟柏汝移植进展.md`](docs/孟柏汝移植进展.md)。

```bash
python tools/build_all.py --mode replace --deploy   # 一条命令：重定向→贴图→导出→组装→打包→安装
python tools/verify_mod.py --mode replace           # 发版前自检
```

## 两种形态（同一个 mod，装之前二选一）

| 形态 | 你会得到什么 | 什么时候用 |
|---|---|---|
| `--mode add`（**最终成果**） | 新增一条 macro 并往 6 个 Argon 女性外观池各加一条 `<select>`。**原版 macro 一条不动**，她只是随机出现的其中一种（3 候选池里约 1/4）；其余女性保持自己的脸/名字/语音。**剧情/任务 NPC 不走外观池，保持原版。** | 正式游玩、发布 |
| `--mode replace`（**中间测试用**） | 把 **122 个** Argon 女性 macro 的 `<models>` 逐个改写成孟柏汝，含不走池的剧情 NPC。 | 调试新模型：不用等池子随机选中她 |

两种形态共用同一个扩展 id（`x4_boru_mod`），所以游戏里同时只能装一个 ——
`tools/deploy.py` 每次都是**整个 mod 目录全量替换**（删掉重建），不会留下
新旧混杂的 `.cat`/`.dat`。

游戏内：`扩展 / Extensions` 菜单里启用 `BoRu (The Bridge Curse)`。

## 管线做了什么

```
SK_BR.psk（UE4，含权重）  →  逐骨绑定姿态转移  →  boru_x4_stage1.blend
                          →  X4CharacterConverter 导出  →  .xac + DDS + xml
                          →  XRCatTool 打包  →  ext_01.cat
```

核心是第一步：**X4 的 NPC 替换是「换网格、留骨架」**。
`character_components.xml` 里的共享 component `character_argon_female_01`
拥有骨骼与 1100+ 条动画，macro 只挑 head/torso/props 三个网格槽位 ——
所以替换物必须带上**逐字节相同的 91 骨骼 Biped 骨架**。

`tools/` 里的脚本按顺序：

| 脚本 | 作用 |
|---|---|
| `paths.py` | 所有路径解析（项目相对 + 环境变量覆盖，无硬编码盘符） |
| `probe_x4.py` | Blender：导入原版宿主，dump 绑定姿态 / 槽位 / 绕序判据 |
| `psk_src.py` | `.psk` 解析（点/楔/面/骨架/权重/顶点色/UV2） |
| `diag_source.py` | 源网格体检：材质槽、绕序、UV、权重分布 |
| `ue4_to_x4.py` | 骨映射表（80→54 直接 + 折叠）+ 源骨架适配器 + 坐标映射 |
| `retarget_core.py` | 逐骨绑定姿态转移（算法层，与来源无关） |
| `build_boru_x4.py` | 阶段 1：重定向 + 按材质/权重拆成 head / torso 两个资产 |
| `prepare_textures_boru.py` | PNG → DDS（BC1/BC3/BC4/BC5）+ manifest |
| `build_boru_mod.py` | 阶段 2：填宿主槽位、调 X4CC 导出 `.xac` |
| `make_mod.py` | 组装 mod 树（content.xml / macros / pools / material library） |
| `find_female_macros.py` | 枚举某种族全部女性 macro（`--mode replace` 用，含完整性自检） |
| `deploy.py` | 全量替换安装到游戏的 `extensions/` |
| `verify_mod.py` | 发版前自检（骨架逐字节、材质路径、XML 语义） |
| `verify_xac.py` | 导入 `.xac` 看绕序/法线/顶点预算（拿原版当基准） |
| `render_*.py` | Blender 离线渲染判据图（含背面剔除版本） |

## 已验证

| 项 | 结果 |
|---|---|
| 骨架 | 91/91 bind payload 逐字节相同（head 与 body 两个资产） |
| 骨骼映射 | 80 骨 → 54 直接 + 18 折叠（权重合并）+ 0 未映射 |
| 源权重 | 每顶点权重和 = 1.0000，0 个无权重顶点 |
| 绕序 | 源空间 signed volume −215 594 → 映射后 +215 594（与原版同号）；开背面剔除渲染与原版一致 |
| 顶点预算（引擎口径） | head 13 678（原版 4 693，2.9×）/ body 12 194（原版 3 601，3.4×），低于 6× 安全线 |
| 贴图 | 6 个材质 / 18 张贴图，全部带完整 mip 链，路径在 catalog 内可解析 |
| 覆盖完整性 | Argon 女性 macro 共 147 个：122 直接替换 + 25 经 `ref` 覆盖，**0 漏网** |
| 发版自检 | `verify_mod.py` 两种形态各自全过 |
| 眼睛 | 眼球 UV 归一化 + `p1_eye_ball`；虹膜可见 |
| 头部位置 | 与原版同高度带比较 y 中心：脖子 −0.2 cm / 脸 −0.3 cm / 头顶 −0.7 cm |

## 已知取舍（不是 bug，是换来的）

| 取舍 | 换来什么 | 代价 |
|---|---|---|
| 手指绑手掌 | 虎口不裂（源手型是 T-pose 张开） | 手指不单独做动作（NPC 看不出来） |
| 眼镜镜片几何删除 | 脸和眼睛不被不透明黑片挡住 | 只剩镜框，没有镜片反光 |
| 皮肤槽按**权重**切分 head/body | T-pose 下手臂与脖子同高，按高度切会把手臂切进头部资产 | 切口在颅底（被头发和衬衫领遮住） |
| 腿骨朝源的位置拉回 65%（`BORU_LEG_PULL`） | 下半身不再被 X4 的宽骨架撑开 | 顶点略偏离驱动骨，走路时摆幅稍大 |
| 躯干径向放大 14%（`BORU_TORSO`） | 腰臀过渡协调（源比 X4 原版瘦） | 不是源的原始比例 |
| 头前移 2.2 cm（`BORU_HEAD_FWD`，纯 Z 窗口） | 脸与原版同高度对齐 | 不是源的原始头位 |
| 脖子保持源的倾斜（`BORU_NECK_TILT=0`） | 忠于源角色 | 低领衬衫下脖子显得前倾（源自己斜 13.2°，转移后 7.9°） |
| 手指跟自己的骨 + 每节卷曲 20°（`BORU_FINGERS`/`BORU_FINGER_CURL`） | 自然半握拳（源是 T-pose 摊平的手）；卷曲用每根手指自己的关节轴、从根部累积 | 不是源的原始手型 |
| 关节处保留残余缝隙 | 不改骨架（硬规则） | 肩/肘有轻微棱角（源 T-pose 与 X4 A-pose 的比例差） |
| 贴图降到 1024 | 包体 6.3 MB | 近距离细节略软 |

后三项是**第一轮实机反馈**之后加的，判据与推导写在
[`docs/孟柏汝移植进展.md`](docs/孟柏汝移植进展.md) §11。

## 依赖

- **X4: Foundations**（开发于 9.00）+ [X Tools](https://www.egosoft.com/download/x4/bonus_en.php)（`XRCatTool.exe`）
- [X4 Character Converter](https://www.nexusmods.com/x4foundations/mods/2152)（Blender 插件 v0.8.7）
- Blender 4.2+（实测 5.2 LTS）、Python 3.13、numpy、Pillow
- 源模型：`D:\dsh-nvguiqiao\repo\models\08_孟柏汝_BoRu_女`（可用 `BORU_SRC` 覆盖）

跨项目共享的资产（解包的游戏根、Blender 插件、参考 mod）在工作区的 `shared/` 下，
脚本用 `paths.py` 统一引用；游戏安装位置、源模型目录都可用环境变量覆盖
（`X4_GAME` / `X4_WORKSPACE` / `XRCAT_TOOL` / `BORU_SRC` / `BLENDER`），
项目本身被移动或换机器后仍可直接跑。

## 版权

《女鬼桥 开魂路》及其角色模型版权归原权利人；本仓库**只包含工具与文档**，
不含模型、贴图或游戏本体资产。
