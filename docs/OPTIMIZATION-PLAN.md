# The Badge 优化方案

针对圆形 AMOLED 徽章固件（Waveshare ESP32-S3-Touch-AMOLED-1.75C，466×466、32MB Flash、无 RTC）的四项改进诉求与后续追加需求，给出改动点、风险、预期收益与验证方式。

本文档中的每一项都标注了 `文件:行号`。行号基于本文写作时的仓库状态（`main` 分支，HEAD `c994edd`，2026-09-14）。**开始实施前请重新确认行号**——上游随时可能移动代码。

---

## 目录

1. [总体结论与优先级](#1-总体结论与优先级)
2. [中文化与字体重建](#2-中文化与字体重建)
3. [App 集合与首页布局](#3-app-集合与首页布局)
4. [画质精修](#4-画质精修)
5. [功耗优化](#5-功耗优化)
6. [录音重构与音频增强](#6-录音重构与音频增强)
7. [录音上云](#7-录音上云)
8. [分阶段实施路线](#8-分阶段实施路线)
9. [不改清单与待确认项](#9-不改清单与待确认项)

---

## 1. 总体结论与优先级

### 1.1 三类问题要分开看

需求里的问题性质差别很大，混在一起做会失焦。分成三类：

| 类别 | 内容 | 特点 |
| --- | --- | --- |
| **A 确定性缺陷** | 会真实坏事的功能故障，有明确对错 | 必须修，优先级最高，且都可独立验证 |
| **B 优化** | 现状能跑，但表现不理想 | 收益可量化，风险可控 |
| **C 重构** | 架构层面改动，为满足新需求 | 工作量大，需分阶段 |

### 1.2 A 类：确定性缺陷（建议无条件先修）

| # | 缺陷 | 位置 | 后果 |
| --- | --- | --- | --- |
| A1 | **录音空间永不回收** | `main/rec_store.c:360` `rec_mark_sent`、`:317` `rec_oldest_unsent`、`:315` `rec_partition` **全仓库无调用者**（已用全仓库检索确认，含头文件、extern、函数指针、构建脚本） | `sent` 恒为 0 → `purge_sent()`（`:342`）空转 → 24MB 录满一次后**永久录不了**，除非重刷固件 |
| A2 | **导出 WAV 头长度字段少 4 字节** | `main/usb_export.c:182` 写成 `36 + data`（44 字节 PCM 头的老公式），48 字节头应为 `40 + data` | 严格解析器报 "RIFF chunk size wrong"；`ffmpeg`/VLC 因只看 data 块长度而容忍 |
| A3 | **目录擦写间隙断电丢全部索引** | `main/rec_store.c:71-72` `dir_save()` 先 `erase_range` 再 `write`；`:84-90` 校验失败会重建空目录 | 擦写窗口内断电 → **所有录音索引一次性消失** |
| A4 | **闹钟在熄屏后不响**（本次新发现） | `alarm_tick()` 唯一调用点是 `main/launcher.c:242`，位于 `idle_cb`（`s_idle` 定时器，`launcher.c:912`）；而 `idle_timers(false)` 的 `launcher.c:76` 正好 `lv_timer_pause(s_idle)` | 熄屏 → `idle_cb` 停跑 → 闹钟到点不触发。而 `launcher.c:237-241` 注释明写「要在熄屏时也每秒检查」，**注释与实现自相矛盾** |
| A5 | 录音界面提示与行为不符 | `main/apps/app_meet.c:44` 写 `"hold = export"`，但长按实际是开始英文录音（`:110`） | 误导用户 |
| A6 | 60 秒电量日志定时器逃过暂停 | `main/launcher.c:916` 的 `lv_timer_create(batt_log_cb, 60000, NULL)` **返回值未保存**，`idle_timers` 无法暂停它 | 熄屏后仍每 60 秒 I2C 读电池；同类问题还有 `:920` 的 `housekeep_cb`（30 分钟，同样无句柄） |
| A7 | 文档分支名错误 | `docs/SETUP.md:48` 写「日常开发在 `master` 分支」，实际远端只有 `main` | 误导协作者 |
| A8 | 死代码 | `main/launcher.c:137` `launcher_screen_off()`（零调用者）、`main/adpcm.c:69` `adpcm_wav_header()`（零调用者）、`main/rec_store.c:315` `rec_partition()` | 增加维护困惑；`launcher_screen_off` 是「touch 可唤醒」的变体，其路径不暂停定时器，将来若被误用会重新引入「熄屏仍在刷新」 |
| A9 | 语言参数无人读取 | `main/rec_store.c:284` `port_rec_start(lang)` 存入 `rec_ent_t.lang` 后**从无人读取**，不影响编码也不进文件名 | 功能形同虚设 |

**A4 值得单独说明**：这是本次调研新发现的，此前不在需求清单里。它的修法与第 5 节的功耗方案**强耦合**——因为「熄屏统一挂起定时器」会把这个缺陷从一个偶发问题变成必然问题。所以 A4 与第 5 节必须一起做，不能分开。

### 1.3 B 类：优化

| # | 优化 | 预期收益 | 量化依据 |
| --- | --- | --- | --- |
| B1 | 图标改为一比一贴图 | 边缘锐利、无重采样锯齿；顺带省掉每像素变换运算 | 见第 4 节：`scale != 256` 会走逐像素双线性路径 |
| B2 | 熄屏统一挂起定时器 | 熄屏时少 4~6 个周期唤醒 | 见第 5 节：实测统计 |
| B3 | 陀螺仪空闲自动关 | 陀螺仪约 1.5 mA，是加速度计的 50 倍 | `main/port.h:57-58` 原文 |
| B4 | 亮屏刷新周期 15ms → 30ms | 绘制功耗约减半 | `sdkconfig.defaults:27` |
| B5 | 两段式熄屏 | 亮度线性影响 AMOLED 电流 | 见第 5 节 |
| B6 | 音频 DSP（高通/AGC/噪声门） | 会议录音可听度显著提升 | 见第 6 节 |
| B7 | 图标标签改真粗体 + 18px | 消除发糊，中文标签可读 | 见第 4 节：现用「画三遍错位」偏移量自相矛盾 |

### 1.4 C 类：重构

| # | 重构 | 为什么必须 |
| --- | --- | --- |
| C1 | 全界面上云中文化 + 自建字体 | 19 个界面文件、约 145 条待译字符串；LVGL 内置 CJK 字体不可用（见第 2 节），必须先建字体管线 |
| C2 | App 集合精简 + 首页单页 | 用户需求；触及首页、Games、`app.h`、模拟器与 6 个工具脚本 |
| C3 | 录音改环形缓冲 + 上云 | 24MB 只能存 51.7 分钟，8 小时需求物理上必须外部承接 |

### 1.5 建议实施顺序与理由

```
第 0 阶段  环境搭建（ESP-IDF 5.5 + 字体工具链）
第 1 阶段  A 类确定性缺陷（除 A4）+ 回归护栏同步
第 2 阶段  字体管线打通 + 全界面中文化          ← C1，是后续所有界面改动的前提
第 3 阶段  App 集合精简 + 首页 90px + 画质精修   ← C2 + B1 + B7
第 4 阶段  功耗优化（含 A4 闹钟迁移）            ← B2~B5
第 5 阶段  录音存储重构（环形缓冲）              ← C3 前半
第 6 阶段  音频 DSP                              ← B6
第 7 阶段  上云（服务端 + 固件客户端）           ← C3 后半
第 8 阶段  录音可用性（回放/暂停/电平表/列表）
```

**为什么中文化排这么前**：字体决定所有中文标签的度量，而标签尺寸又反过来影响首页布局。先做中文化，首页排版可以一次定稿；反过来做就要返工。

**为什么画质与 App 精简合并**：两者都改 `tools/mkassets.py` 与 `launcher.c`，合并做只跑一次资产重建。

---

## 2. 中文化与字体重建

### 2.1 需求范围（用户已确认）

**要中文化**：首页图标名、设置、Wi-Fi、蓝牙、USB 导出、主机选择、空中鼠标、按键、计算器、录音、文本注入、演示遥控、时钟面、计时器、秒表、闹钟、游戏菜单与分数、关机提示。

**保留拉丁写法（用户明确要求）**：
- 功能键名 `F1`/`F2`/`F4`/`F5`/`F12`/`Esc`/`Tab`/`Ent`/`Del`
- 输入法键面 `abc`/`ABC`/`123`/`!@#` 与 `main/apps/keypad.c:36-41` 的 `SET[4][10]`
- 计算器键面 `7 8 9 / 4 5 6 * 1 2 3 - C 0 = +`（`main/apps/app_calc.c:127-132`）
- `LV_SYMBOL_*` 全部符号图标
- 硬件型号串 `ESP32-S3 . 466`（`main/lcdface.c:234`）
- **27 项时区地名**（`main/apps/app_settings.c:186-194`，Honolulu…UTC+14）—— 用户要求保留英文
- **开机画面文字**（`main/splash.c:30-31` 的 "badge"）—— 用户要求不翻译

保留后两项的直接好处：**这两处进不了字符集**，可省掉一批汉字，字体更小。

### 2.2 为什么必须自建字体（已从源码核实）

全仓库 19 个文件、约 75 处使用 `lv_font_montserrat_*`。**Montserrat 不含任何 CJK 字形**，不换字体中文会渲染成空白或方框。

**LVGL 内置 CJK 字体不可用**。`sim/lv_conf.h:697-698` 提供 `LV_FONT_SOURCE_HAN_SANS_SC_14_CJK` / `_16_CJK`，但实测其字符集不可用于简体界面：

- 生成参数（见 `sim/lvgl/src/font/lv_font_source_han_sans_sc_16_cjk.c:4`）为 `--bpp 4 --no-compress --font SourceHanSansSC-Normal.otf -r 0x20-0x7f --symbols …`
- 字符集是「1338 most common CJK radicals」的**中日文混合通用集**，同时含**日文假名**（ぁぃぅゃゅょ）与**繁体字**（録、電、們、実、無、覚、體、邊）
- 简体界面必需的「录」「钟」「键」「响」「暂」这类字**大概率缺失**
- 且只有 **14px 与 16px** 两个字号，而本项目界面用字覆盖 **14/16/18/20/24/26/32/40/48** 共 9 个在用字号

**结论：自建子集字体。**

### 2.3 字体管线（已端到端实测跑通）

新增 `tools/mkfonts.py`。实测验证过的流程：

**第 1 步：从 `.ttc` 提取单个 TTF。** `lv_font_conv` 依赖 opentype.js，对字体集合支持有限，必须先提取。用 `fontTools`：

```python
from fontTools.ttLib import TTCollection
c = TTCollection("/System/Library/Fonts/Hiragino Sans GB.ttc")
c.fonts[0].save("w3.ttf")   # 常规
c.fonts[2].save("w6.ttf")   # 粗体
```

**实测结果（本机 macOS）**：三个候选源字体全部可用，且**待译汉字 100% 覆盖（missing=0）**：

| 源字体 | 索引 | 名称 | 字形数 |
| --- | --- | --- | --- |
| `/System/Library/Fonts/Hiragino Sans GB.ttc` | 0 | Hiragino Sans GB W3（常规） | 29318 |
| 同上 | 2 | **Hiragino Sans GB W6（粗体）** | 29318 |
| `/System/Library/Fonts/STHeiti Light.ttc` | 1 | Heiti SC Light | 37708 |
| `/System/Library/Fonts/STHeiti Medium.ttc` | 1 | Heiti SC Medium | 37274 |

**推荐用 Hiragino Sans GB**：同一个 `.ttc` 里就有 W3 与 W6，**真粗体原生可用**，不需要合成粗体，正好替代现有「画三遍错位」的伪粗体（见第 4 节）。

**第 2 步：用 `lv_font_conv` 生成。实测可以直接吃提取出的 TTF，不需要预先子集化**——管线比预想简单：

```sh
npx --yes lv_font_conv --no-compress --bpp 4 --format lvgl \
    --font w3.ttf -r '0x20-0x7f' --symbols "$CHINESE" --size 18 -o font_zh_18.c
```

**第 3 步：按「字号 → 字符集」逐尺寸生成。** 不同字号只放该字号实际用到的字符，而不是每个字号都塞全量。这是压小体积的关键。

### 2.4 实测体积（真实数字，非估算）

181 个去重汉字 + ASCII `0x20-0x7f`，各字号实测：

| 字号 | 实测数据体积 |
| --- | --- |
| 14 | 2877 B |
| 16 | 3597 B |
| 18 | 4671 B |
| 20 | 6348 B |
| 24 | 7978 B |
| 26 | 8252 B |
| **合计（6 个需 CJK 字号）** | **约 33.7 KB** |

粗体 W6 只需在首页标签（18px）等处用，**再补约 5~10 KB**。

**总体结论：字体净增约 40~50 KB。** `partitions.csv:7` 的 `factory` 分区为 6M，注释写明当前应用 3.6MB，余量约 2.4MB。**字体开销不到余量的 2%，flash 完全不是问题**——这一点比预期好得多，原先担心字体撑爆分区是多余的。

> 补充实测：用 `--bpp 1`（只有黑白，无抗锯齿）100 字 @18px 只需 1934 B。但中文笔画密集，1bpp 会明显发糊，**不建议**。

### 2.5 字号分类：哪些字号根本不需要中文字形（关键优化）

已逐处回溯每个 `lv_font_montserrat_XX` 引用点确定其承载文字：

| 字号 | 是否需 CJK | 依据 |
| --- | --- | --- |
| 14 | **是** | wifi 行副标题、usb 屏底部提示、host_pick 副标题 |
| 16 | **是** | 首页图标名、lcdface `SEC/BAT/UP`、设置列表值、各 app 提示 |
| 18 | **是** | 设置列表行名、WiFi 中央提示、usb 副标题 |
| 20 | **是** | 大部分标题/提示/按钮（用得最多的字号） |
| 22 | **未使用** | 全仓库零引用，可直接从字体表移除 |
| 24 | **是** | lcdface 日期行、闹钟 on/off、游戏菜单、`game over`/`all clear!`、秒表 `lap` |
| 26 | **是** | launcher `power off`、时区 roller（时区名保留英文，故实际只 `power off` 三字） |
| 32 | **否** | 仅 `splash.c:31` 的 "badge"（保留英文）+ present 符号 |
| 40 | **否** | 仅计算器主显示与闹钟响铃，全为数字格式串 |
| 48 | **否** | 7 处引用中 6 处是数字/符号；仅 `app_meet.c:173` 的 `REC`/`OK` 是文字，**二者属功能缩写，建议一并不译**，则 48 号零 CJK |

**收益**：只需生成 14/16/18/20/24/26 六个字号（26 号甚至只含 3 个汉字）。32/40/48 完全复用现有 Montserrat。

### 2.6 符号兜底（方案能一次改完 19 个文件的关键）

`LV_SYMBOL_LEFT`/`OK`/`DRIVE`/`EDIT`/`GPS`/`UP`/`DOWN` 等符号遍布各界面，它们在 Montserrat 的私有区，中文字体里没有。逐个排查符号标签极其繁琐。

**已核实两条关键事实**：

1. `lv_font_t` 有 fallback 字段，注释写明「Fallback font for missing glyph. Resolved recursively」：

```116:116:sim/lvgl/src/font/lv_font.h
const lv_font_t * fallback;     /**< Fallback font for missing glyph. Resolved recursively */
```

2. **`lv_font_conv` 生成的字体结构体里已经带了这个字段**，受 LVGL 版本保护：

```c
#if LV_VERSION_CHECK(8, 2, 0) || LVGL_VERSION_MAJOR >= 9
    .fallback = NULL,
#endif
```

所以只需把生成的字体里 `.fallback = NULL` 改成 `.fallback = &lv_font_montserrat_18`（同字号），**中文走 CJK 字形、符号自动回落 Montserrat，不必逐个排查符号标签**。

同时：**数字与符号类标签继续用 Montserrat**（时钟面、计时器、秒表、闹钟的大数字），因为 Montserrat 的数字字形更好看，也不该被中文字体的拉丁字形替换。

### 2.7 待译词条清单

全仓库**当前没有任何汉字**（正则 `[\x{4e00}-\x{9fff}]` 检索为 0）。待译字符串约 145 条，按去重后的分布：

- **首页图标名（6 个）**：Games、Air Mouse、Clock、Calc、Meet、Keys
  建议译法：**游戏、空中鼠标、时钟、计算器、录音、按键**（Settings 走齿轮按钮，译为 设置）
- 字号 14：9 条（wifi 副标题、usb 底部提示等）
- 字号 16：约 53 条（各 app 的提示与状态）
- 字号 18：约 20 条（设置行名、WiFi 提示、usb 副标题）
- 字号 20：约 48 条（标题、按钮、提示——最多的字号）
- 字号 24：约 13 条（星期/月缩写、on/off、游戏菜单、`game over`、`all clear!`、`lap`）
- 字号 26：1 条（`power off`）

### 2.8 中文化时必须一并改的动态格式串

含英文单词的拼接串，改中文要同步改格式串，否则会出现「剩余 30 分钟 free」这类混搭：

| 位置 | 现状 | 建议 |
| --- | --- | --- |
| `app_meet.c:43` | `"%lu min free"` | `"剩余 %lu 分钟"` |
| `app_meet.c:54` | `"saved  %d on device"` | `"已保存 %d 个"` |
| `app_settings.c:400` | `"%d%%  %s"`（on/off） | `"%d%%  %s"` + 译 on/off |
| `app_settings.c:410` | `"%s  %s"`（时区名+状态） | 时区名保留英文，状态译中文 |
| `app_settings.c:417` | `"%d saved"` | `"%d 个已存"` |
| `app_settings.c:421` | `"%d paired"` | `"%d 台已配对"` |
| `app_settings.c:428` | `"%d%%  charging"` | `"%d%%  充电中"` |
| `app_settings.c:431` | `"%d%%  measuring"` | `"%d%%  测量中"` |
| `app_settings.c:432` | `"%d%%  %dh %02dm"` | `"%d%%  %d 时 %02d 分"` |
| `wifi_setup.c:220-222` | `"%d dBm  -  connected / saved"` | 后缀译中文 |
| `usb_screen.c:28,47` | `"%d recordings"` / `"%d files"` | `"%d 条录音"` / `"%d 个文件"` |
| `app_mouse.c:233` | `"speed %d/%d"` | `"速度 %d/%d"` |
| `app_mouse.c:508` | `"forgot %d device%s"` | ⚠️ **英文复数后缀 `%s` 必须删掉**，中文无复数形态 |
| `app_type.c:36` | `"sent  %s"` | `"已发送 %s"` |
| `app_games.c:520/608/623/964` | `"L%d  %d  o%d"`（L=级别, o=命） | `"关%d  分%d  命%d"` |
| `lcdface.c:294` | `"%s  %s %02d"`（星期+月+日） | `"%s %d月%d日"`，且需复核 `LAB_BOX 240` 盒宽 |

### 2.9 长度风险点（中文化后必须逐一核对）

中文与英文的宽度关系不一样：CJK 是全宽字形（每字宽 ≈ 字号），英文是比例宽度。**有时中文更窄**（「空中鼠标」4 字 @18px = 72px，而 "Air Mouse" @16px ≈ 70px），**有时更宽**（「亮度」@18px = 36px vs "Brightness" @18px ≈ 88px——这项反而是中文更窄）。

真正需要核对的是**固定宽度容器**：

| # | 位置 | 容器 | 风险 |
| --- | --- | --- | --- |
| 1 | `main/launcher.c:583-589` | 圆屏，图标正下方居中，**无宽度限制**（实测无 `lv_obj_set_width`、无 `lv_label_set_long_mode`） | 超出会画到 466 画布外，被圆形面板物理切掉。详见第 3.4 节的实测余量 |
| 2 | `main/apps/app_settings.c:369-392` | 按钮 340×62，左名右值两端对齐 | 右值 `"%d%%  %d 时 %02d 分"` 本已偏长 |
| 3 | `main/apps/keypad.c:210-216` | 输入框宽 156、`LV_LABEL_LONG_CLIP`；标题对齐 `-206`（注释：>218px 会裁切） | 标题是 SSID 或「为此主机命名」 |
| 4 | `main/apps/app_alarm.c:249-259` | `hour`/`min` 位置固定 `±112, -148` | 译「小时」「分钟」后变长，会侵入箭头键 |
| 5 | `main/apps/app_stopwatch.c:247-251` | **用 `lv_text_get_size("lap", …)` 测量宽度来布局** | ⚠️ **改成中文必须同步改测量代码**，否则数字行错位 |
| 6 | `main/apps/app_present.c:53-56` | 按钮 96×110 / 110×52 固定 | `B`/`F5`/`Esc` 保留英文，无风险 |
| 7 | `main/lcdface.c:154,219` | `LAB_BOX 240` 固定盒 + 居中 | `SEC`/`BAT`/`UP` 译 2 字中文可容 |
| 8 | 小固定缓冲 | `wifi_setup.c:219 sub[44]`、`host_pick.c:126 sub[24]`、`app_meet.c:33 sub[40]`/`big[16]` | 接入中文格式串要复核 `sizeof`，**中文按 UTF-8 每字 3 字节**，`sub[24]` 只够 8 个汉字 |
| 9 | `main/apps/app_games.c:520 等` | `s_score` 在 `LV_ALIGN_CENTER,0,116` | 改 `"关%d  分%d  命%d"` 后复核圆屏排布 |
| 10 | `app_keys.c:115-119` / `app_mouse.c:1068-1072` | 16~20 号小字夹在按钮之间 | 中文提示变长要避免覆盖按钮 |

### 2.10 验证方式

- **模拟器可直接验证**：模拟器渲染中文与真机同码，改动后 `python3 sim/build.py && ./sim/badge_sim` 出 PNG，**逐屏肉眼核对是否有豆腐块、裁切、溢出**。这是最快的验证路径
- **回归护栏新增一项**：`tools/regress.sh` 增加「字体覆盖检查」——扫描 `main/` 下所有界面字符串，提取其中的汉字集合，断言全部包含在生成字体的字符集清单里。**这能防止「改文案时引入新字却忘了重新生成字体」这类静默缺陷**
- **实机验证**：中文渲染与真机一致（同一 LVGL 软件渲染器），实机只需确认字号观感

---

## 3. App 集合与首页布局

### 3.1 删除 Water 与 Orbit

**先说一个容易搞错的点**：Games 菜单里**已经不含** Water/Orbit。`main/apps/app_games.c:1618-1622` 的 `NAME[4]` 只有 `{ "Bricks", "Pinball", "Marble", "Pop" }`，且 `:1618-1620` 注释明说「Only the games are listed — water and the planets became their own apps on the home screen」。**所以游戏菜单数组与 `-135 + i*90` 布局无需改动。**

**要删除的文件**

| 文件 | 说明 |
| --- | --- |
| `main/apps/app_water.c` | Water 实现（`water_start:1574`、`water_stop:1663`、`water_sim_tilt:1686`、`water_debug:1693`） |
| `main/apps/water.h` | 声明 `water_start/stop/sim_tilt/debug`（`:4-7`） |
| `main/apps/orb.c` | Orbit 实现（`orb_start:868`、`orb_stop:946`、`orb_set_lean_deg:970`、`orb_set_lon:977`、`orb_set_tilt_deg:983`、`orb_debug:990`） |
| `main/apps/orb.h` | `orb_kind_t`（`:5`）、`ORB_NAME`（`:6`） |
| `main/assets/orb_tex_{earth,jupiter,moon,sun}.h` | 仅 `orb.c:61-74` 以 `__has_include` 引用 |
| `main/assets/app_icon_water.c` | 仅 `app_games.c:1753` 使用 |
| `main/assets/app_icon_moon.c` | 仅 `app_games.c:1757` 使用 |
| `main/assets/app_icon_earth.c` | **已无任何 app 引用**（只有定义 + `assets.h:16` 声明 + 生成脚本），是既有孤儿 |
| `tools/make-orb-texture.py` | 纯为 orb 服务 |

**`main/apps/app_games.c` 精确删除清单**

| 行号 | 符号 |
| --- | --- |
| `:12` | `#include "water.h"` |
| `:13` | `#include "orb.h"` |
| `:186` | `static void do_orb(void);` |
| `:201-202` | `clear_board()` 内的 `water_stop();` / `orb_stop();`（**只删这两行**） |
| `:275` | `static void orb_menu(void);` |
| `:280-285` | `do_orb_back()` |
| `:1668-1669` | `leave()` 内的 `water_stop();` / `orb_stop();`（**只删这两行**） |
| `:1677-1687` | 注释 + `enter_water()` |
| `:1689-1747` | orb 整段：`s_orb_pick`(`:1693`)、`orb_go`(`:1695`)、`do_orb`(`:1701`)、`orb_menu`(`:1711`)、`enter_orb`(`:1741`) |
| `:1749-1750` | `tint_water()` / `tint_orb()` |
| `:1752-1759` | `app_water` / `app_orb` 定义 |
| `:760-766` | `games_debug_play_orb()`（`main.c:224` 调用） |

**⚠️ 必须保留的共用机制（最易误删）**：`s_root`(`:47`)、`s_loop`(`:130`)、`stop_loop()`(`:169-172`)、`clear_board()`(`:195-223`)、`s_defer_fn`/`defer_cb`/`defer()`(`:250-266`，被 bricks `:614,624`、pinball `:1172`、bubble `:1476` 用)、`do_back()`(`:268-273`)、`add_back_xy()`/`add_back_to()`(`:303-317`，名字带 orb 语境但被 `add_back()` `:318` 复用)、`add_back()`(`:318`，被 marble `:1100`、pop `:1611` 调)、`back_cb()`(`:287-291`)、`show_menu()`(`:1616-1650`)、`enter()`(`:1652-1661`)、`leave()`(`:1663-1673`)、`tint()`(`:1675`)、`pick_cb()`(`:1453-1477`)、`games_debug_tilt0()`(`:754-758`)

**⚠️ 绝不能删的东西**：`main/nightsky.c` / `nightsky.h`。它名字像 Orbit 的星空背景，**实际是首页代码绘制壁纸**，只有 `main/launcher.c:5`（include）与 `launcher.c:549-550`（`nightsky_create(s_home)`）使用，另在 `main/CMakeLists.txt:9`。`tools/dim-wallpaper.py` 同理保留。

**其他改动**

| 位置 | 改动 |
| --- | --- |
| `main/launcher.c:262-263` | 首页数组去掉 `&app_orb, &app_water`；`:259-261` 注释更新 |
| `main/app.h:31-32` | 删两行 extern |
| `main/main.c`（`#ifdef BADGE_APPBENCH`，`:173-235`） | 删 `:180` `games_debug_play_orb` extern、`:182` `orb_set_lon` extern、`:212-218` water 段（含 `:215 launcher_open(&app_water)`）、`:219-229` globe 段（含 `:224`/`:227`）。**保留 `:179` bricks 与 `:181 games_debug_tilt0`** |
| `sim/main_sim.c` | 删 case `'Z'`(`:348-355`)、`'Y'`(`:374-378`)、`'O'`(`:379-383`)、`'V'`(`:386-393`)、`'L'`(`:394-398`)；`'A'` 的 `list[]`(`:452-455`) 删 `&app_water, &app_orb` 两项，**索引位移：`app_settings` 由 A8 变 A6** |
| `main/CMakeLists.txt` | **自动生成**（`tools/sync_cmake.py` 扫 `*.c`/`apps/*.c`/`ble/*.c`/`assets/*.c` 后整体重写；`mkassets.py:340` 会调它）。删文件后**必须重跑**，否则 CMake 报 "Cannot find source file"。涉及 `:27 apps/app_water.c`、`:30 apps/orb.c`、`:38 assets/app_icon_earth.c`、`:42 assets/app_icon_moon.c`、`:47 assets/app_icon_water.c` |
| `main/assets/assets.h` | **自动生成**（`mkassets.py:329-338`）。`:14-16` 需从 `mkassets.py:331-335` 的元组里同步删名 |
| `NOTICE` | `:29-32`、`:34-37`、`:39-45`、`:47-50`（moon）、`:52-54`（earth）、`:56-59`（jupiter）、`:61-63`（sun）、`:65-66`（NASA 免责） |

**⚠️ 顺带发现：`port_perf_hold` 会变成死代码。** `main/port_esp.c:144` 定义的 `port_perf_hold` 全仓库**唯一持有者是 `app_water.c:154`**（经 `water_fast`/`s_fast_held`，`:150-155`）。删除 `app_water.c` 后它只剩定义、`port.h:300` 声明与 `sim/port_sim.c:382` 空桩。`app_water.c:145-147` 的注释本身也已自认「它注定要死」。**处置：与 `app_water.c` 一并删除，或保留接口但注释说明当前无使用者。**

**`tools/regress.sh` 会因此失效（共 13 处）**

| 行号 | 后果 |
| --- | --- |
| `:4-6` 文档字符串提到 orbs/water | 文案失效 |
| `:67-68` grep `orb_stop` in `orb.c` | ✗ |
| `:69-70` grep `water_stop` in `app_water.c` | ✗ |
| `:71-72` grep `draw_cb` in `app_water.c` | ✗ |
| `:77-78` grep `ORB_R\|s_R` in `orb.c` | ✗ |
| `:157-163` grep `port_imu_accel3`/`if (!have)` in `app_water.c` | ✗ |
| `:257-259` grep `lv_display_trigger_activity` in `app_water.c` | ✗ |
| **`:269-280`** | python `open("main/apps/app_water.c")`(`:271`) → **FileNotFoundError 未捕获异常，脚本直接中断** |
| `:282-287` grep `real interval` | ✗ |
| **`:297-317`** | python `open("main/apps/orb.h")`(`:299`) + `open("main/apps/orb.c")`(`:300`) → **FileNotFoundError** |
| `:321` grep `s_lean` in `orb.c` | ✗ |
| `:324` grep `float bx =`/`fabsf(bgy)` in `orb.c` | ✗ |
| `:327-329` `! grep -qi saturn orb.c orb.h` | grep 返回 2 → `!` 为真 → **误判为通过（隐蔽失效，最危险）** |
| `:346-348` 注释「one orb photograph is 256KB」 | 文案失效 |

**其他工具脚本**

| 文件 | 行号 | 影响 |
| --- | --- | --- |
| `tools/capture-icons.py` | `:3-4, 41-48` | 用 sim `A 7`/`A 6` 拍 moon/earth/water 图标 |
| `tools/capture-shots.py` | `:90-92, 144-145` | `app(6)`/`app(7)` 拍 water/orbit → `docs/img/water.png`、`orbit.png` |
| `tools/sim-stress.py` | `:34-35` | GRID 含 `Water/Moon/Earth` |
| `tools/sim-exit-check.py` | `:4, 32-33, 36` | 遍历 Water/Moon/Earth |
| `tools/mkassets.py` | `:262-278`（生成水/月/地图标）、`:334`（assets.h 清单） | 需删 |
| `tools/bss-size.py` | `:5` | 注释提到 water |
| 无需改 | `tools/check-run.sh`、`tools/sync_cmake.py`（只需重跑）、`sim-hold-check.py`、`sim-swipe-check.py`（**但单页化后 swipe 检查要改**）、`sim-wifi-stall-check.py` | |

**⚠️ 回归护栏里还有一条会因单页化失效**：`tools/regress.sh` 的「do the home pages still turn」检查——**单页后翻页不存在了，这条检查必须改写为「首页 6 个图标都在且在圆内」**，而不是直接删掉。

**`docs/` 文案与图片**

| 位置 | 内容 |
| --- | --- |
| `README.md` | `:9`（water.jpg）、`:34-35`（"the water, the orbs and the games"）、`:55`（water.gif）、`:58-59`、`:77`（**Fidgets** 行） |
| `docs/index.html` | `:121-123`、`:167-173`（Water 卡片）、`:174-180`（Orbit 卡片） |
| `docs/SETUP.md` | `:23`（"the water particles, the orb rotation"） |
| 图片（可删） | `docs/img/water.png`、`docs/img/orbit.png`、`docs/img/real/water.gif`、`docs/img/real/water.jpg` |
| `sim/` 注释 | `sim/server.py:31,36,141`、`sim/build.py:91-96`、`sim/port_sim.c:82-83`、`sim/main_sim.c:346` |

### 3.2 合并 Air Mouse 与 Trackpad（用户已拍板）

**方案：删 `app_air`，保留 `app_mouse`，改名为中文「空中鼠标」，进入即空中模式。**

两者本就是 `main/apps/app_mouse.c` 里指向**同一套实现**的两个入口：

- `app_mouse`（`:1205-1209`，`.name = "Trackpad"`）
- `app_air`（`:1214-1218`，`.name = "Air Mouse"`），其 `enter_air()`（`:1212`）只是 `s_air_default = true; enter(root); s_air_default = false;`

**改动**：
1. 删 `app_air`（`:1214-1218`）与 `enter_air()`（`:1212`）
2. `app_mouse` 的 `.name` 由 `"Trackpad"` 改为 `"空中鼠标"`
3. 把「进入即空中模式」并入 `enter()`：`app_mouse.c:1075` 的 `s_air = s_air_default;` 改为进入时直接置空中模式并加注释；`s_air_default`（`:146`）可一并清理
4. `main/app.h:30` 删 `app_air` 一行，保留 `app_mouse`
5. `main/launcher.c` 首页数组只保留 `&app_mouse`

**应用内切换逻辑已存在且完整，不需要新写**：

| 部件 | 位置 |
| --- | --- |
| 状态量 `s_air` | `app_mouse.c:145` |
| 切换按钮 `s_air_btn` | `:1077-1090` |
| 切换回调（`s_air = !s_air`） | `:511` |
| 模式应用函数 `air_paint()` | `:466-500`（按钮配色、提示文字、控件显隐、`:476 port_imu_gyro_enable(s_air)`、`:500 launcher_handle_passthrough(!s_air)`） |
| 原始动机注释 | `:3-5`（「RDP 下 touch/mouse 模式来回切太烦」） |

**保留 `app_mouse` 这个名字的额外好处**：`sim/main_sim.c:452-455` 的应用列表与 `tools/capture-*.py` 的索引引用**不受本次合并影响**，只有 water/orbit 被删导致的索引位移需要同步。

**一个已做的判断（如不合意可改）**：进入即空中模式、**不跨次记住上次模式**。理由是这个图标的名字代表它的主身份。若希望记住上次选择，需存进 NVS。

### 3.3 首页改单页 6 图标

**改动**：`main/launcher.c:259-274` 的 `s_page0`(6 项)/`s_page1`(3 项)/`PAGE_N 2` → **`PAGE_N = 1`**，单页 6 项：

| 顺序 | app | 中文名 |
| --- | --- | --- |
| 1 | `app_games` | 游戏 |
| 2 | `app_mouse` | 空中鼠标 |
| 3 | `app_clock` | 时钟 |
| 4 | `app_calc` | 计算器 |
| 5 | `app_meet` | 录音 |
| 6 | `app_keys` | 按键 |

（`app_settings` 走 `launcher.c:615` 的齿轮按钮，不在圆环内，不受影响。）

**可一并清理的分页代码**（行号已核实）：

| 内容 | 行号 |
| --- | --- |
| 页表与宏 `s_page0`/`s_page1`/`PAGE_N`/`s_pages`/`s_page_cnt`/`s_page` | `:259-274` |
| 翻页常量与状态 `SWIPE_PX`/`s_press_x`/`s_swiped` | `:358-362` |
| `home_press_cb` | `:364-372` |
| `home_release_check`（真正改页逻辑） | `:375-396` |
| `home_swipe_cb` | `:398-402` |
| 两处 `home_release_check()` 调用 | `:407`、`:537` |
| `home_press_cb` 的两处绑定 | `:573`、`:621` |
| 分页指示点循环 | `:593-602` |

**⚠️ 必须写入风险项：这与上游设计意图直接冲突。** `main/launcher.c:259-261` 原作者注释原文：

> Home is two pages. Six icons on one page shrinks them to 92 px, which is hard to hit; split in two they are 120 px.

**作者正是为避开小图标才分两页的。** 用户要求合回一页并定 90px，等于主动选择可点面积更小的状态。

**备选方案（如需更大图标）**：改成 3+3 两页可得 126px；或单页但只微调放大 `ICON_D`——但受第 3.4 节的标签越界约束。

### 3.4 90px 图标与标签可读性（实测几何）

**用户要求 `ICON_D = 90`。** 几何实测（`RING_R` 由 `launcher.c:13` 公式得 150，6 图标间隔 60°）：

| i | 角度 | 图标圆心（屏幕坐标） | 标签中心 |
| --- | --- | --- | --- |
| 0 | -90° | (233, 83) | (233, 149) |
| 1 | -30° | (363, 158) | (363, 224) |
| 2 | 30° | (363, 308) | (363, 374) |
| 3 | 90° | (233, 383) | (233, 449) |
| 4 | 150° | (103, 308) | (103, 374) |
| 5 | 210° | (103, 158) | (103, 224) |

标签定位公式（`launcher.c:579`）：`ny = y_i + ICON_D/2 + 21`。

**可用宽度算法**：屏幕是 r=233 的圆。标签中心 `(cx, cy)`（相对屏幕中心）、宽 W、半高 H/2，离圆心最远的角点是 `(|cx|+W/2, |cy|+H/2)`，故：

```
W_max = 2 · ( sqrt(233² − (|cy| + H/2)²) − |cx| )
```

**实测结果**（按 18px CJK，H/2 ≈ 11）：

| 标签 | cx | cy | W_max | 18px 所需宽 | 余量 |
| --- | --- | --- | --- | --- | --- |
| 游戏 | 0 | -84 | 425.5 | 36 | +389 |
| 空中鼠标 | 130 | -9 | 204.3 | 72 | +132 |
| **时钟** | 130 | 141 | **93.2** | 36 | **+57** |
| 计算器 | 0 | 216 | 105.1 | 54 | +51 |
| **录音** | -130 | 141 | **93.2** | 36 | **+57** |
| 按键 | -130 | -9 | 204.3 | 36 | +168 |

**结论：18px 中文标签全部放得下，最紧的「时钟」「录音」仍有约 57px 余量，不会越界或被圆屏裁切。**

**但要注意两个紧邻约束**：
- **最受限的是左右中下位置**（`时钟`/`录音`），可用宽仅 **93px ≈ 5 个全宽汉字**。所以译名**不要超过 4 字**——「空中鼠标」正好 4 字，安全
- **正下方「计算器」可用 105px**，3 字 54px 安全；若将来改成 4 字（如「计算器」保持 3 字即可），仍在范围内

**图标不重叠**：6 图标间隔 60°，相邻圆心弦长 = `2·150·sin30° = 150`，净间隙 = `150 − 90 = 60px`。
**标签不碰撞**：相邻标签中心距 ≈ 150px（如 i0↔i1 = √(130²+75²) = 150.1），最长标签 72px，远小于 150。

**标签字号建议从 16px 提到 18px**（`launcher.c:585-586` 现为 `APP_CNT <= 5 ? montserrat_20 : montserrat_16`，6 图标会落到 16px，对汉字偏小）。横向有充足余量支撑这个提升。

**同时去掉伪粗体**：`launcher.c:576-588` 现在把同一标签画三遍用错位偏移（`k=0..2`，`OX[3]={1,0,1}`、`OY[3]={2,0,0}`）冒充粗体。**这套偏移量本身自相矛盾**（`OX` 是 1,0,1 而 `OY` 是 2,0,0，两轴不一致），是发糊的直接来源。改用第 2.3 节的 W6 真粗体字体。

**验证方式**：模拟器出 PNG 逐屏核对（能直接看到是否裁切、是否碰撞），这是本项最可靠的验证方式。

---

## 4. 画质精修

### 4.1 颗粒感的根因：运行时非整数缩放（已从 LVGL 源码核实）

现状 `main/launcher.c:563` 执行：

```c
lv_image_set_scale(tile, ICON_D * 256 / 120);   /* scale the 120 px source to fit */
```

源图由 `tools/mkassets.py:31` 的 `ICON = 120` 统一烘焙为 120px，而显示尺寸 `ICON_D` 是 90（或原公式的 92）→ **0.75~0.766 倍非整数重采样**。

**LVGL 的分叉阈值不是「比例是否整齐」，而是 scale 是否正好等于 256**：

```223:224:sim/lvgl/src/draw/sw/lv_draw_sw_img.c
    bool transformed = draw_dsc->rotation != 0 || draw_dsc->scale_x != LV_SCALE_NONE ||
                       draw_dsc->scale_y != LV_SCALE_NONE ? true : false;
```

- `scale != 256` → 走 `transform_rgb565a8()`（`sim/lvgl/src/draw/sw/lv_draw_sw_transform.c:656`）的**逐像素重采样**
- `scale == 256` → 走 `LV_COLOR_FORMAT_RGB565A8` 的**专用直通路径**（`sim/lvgl/src/draw/sw/lv_draw_sw_img.c:254-273`），无变换

**所以把 `ICON_D` 从 92 改成 90 并不能提升清晰度**——两者都不等于 256，差别只在 0.766 与 0.75；90 更小反而丢得更多。

**重采样质量差的两个具体原因**：

1. **双线性只取 2×2 邻域、不做面积平均**。代码算亚像素小数再混邻域：

```682:685:sim/lvgl/src/draw/sw/lv_draw_sw_transform.c
        /*Get the direction the hor and ver neighbor
         *`fract` will be in range of 0x00..0xFF and `next` (+/-1) indicates the direction*/
        int32_t xs_fract = xs_ups & 0xFF;
        int32_t ys_fract = ys_ups & 0xFF;
```

2. **步进用整数除法截断、误差逐像素累积**：

```211:211:sim/lvgl/src/draw/sw/lv_draw_sw_transform.c
            xs_step_256 = (256 * xs_diff) / (dest_w - 1);
```

除数是 `dest_w - 1` 而不是 `dest_w`，且整数截断。所以**即使 90/120 是「整齐的 3/4」也救不了场**。

### 4.2 正确解法：按最终尺寸烘焙、运行时 1:1

**改动**：

1. `tools/mkassets.py` 的 `ICON` 由 120 改为 **90**，图标按 90px 烘焙
2. `main/launcher.c:563` 改为 `lv_image_set_scale(tile, 256)`（即 `LV_SCALE_NONE`）
3. `main/launcher.c:564` 的 `lv_image_set_pivot(tile, 60, 60)` 需同步改为 `45, 45`（pivot 是源图坐标）
4. `main/launcher.c:14` 的 `ICON_D` 宏改为常量 90（单页后 `APP_CNT` 不再变化，条件表达式失去意义）

**收益**：
- `transformed == false`，**逐像素重采样彻底消失**
- 缩放在**离线**阶段由 PIL 的 LANCZOS 完成（`mkassets.py:99-102` 已有 4 倍超采样 + LANCZOS，质量远高于 LVGL 的 2×2 双线性）
- 顺带**省掉每像素的变换运算**，绘制更快

**风险**：`ICON` 从 120 改 90 后，`mkassets.py` 里所有以 `ICON` 为基准的绘制比例（`disc()`、`clock_icon()`、`gear_icon()`、keys/games/meet 的 `S4`/`S5` 超采样尺寸）会整体缩放。因为是按比例计算的，理论上是等比缩放；但 **`gear_icon(40)`（`mkassets.py:169`）传的是固定 40 而非 `ICON`**，不受影响。改动后**必须出 PNG 逐个核对 12 个图标**。

### 4.3 抖动：不能直接启用（重要）

`tools/mkassets.py:37-58` 的 `emit(name, img, fmt, dither=False)` 有完整 Bayer 抖动实现，但**所有调用都没传 `dither`**（调用点 `:161,167,169,193,201,208,215,240,260,278,290`）——是死代码。

**但直接启用会更糟**。它的实现是：

```python
p = (cl(p[0] + (t - 8) // 2), cl(p[1] + (t - 8) // 4), cl(p[2] + (t - 8) // 2), p[3])
```

这是**无条件加噪**——不看量化误差，对纯色区块也照样撒噪点。RGB565 下纯色像素的低位本来就是 0，加噪纯属有害。**直接开会加重颗粒感，与目标相反。**

**正确做法**：改为按**量化残差**做误差扩散（Floyd–Steinberg，或至少让偏移量正比于该像素的量化余数）。

**验证要求**：**必须先用模拟器出 PNG 做 A/B 对比再决定是否启用**。这是唯一能判断抖动到底帮忙还是帮倒忙的方法。

### 4.4 其余可顺带处理

- `main/assets/app_icon_*.c` 全为 `LV_COLOR_FORMAT_RGB565A8`（各文件 2710 行处 dsc），`icon_gear.c` 为 `A8` —— 格式本身没问题，保持
- `tools/mkassets.py:293-315` `gear_icon()` 与 `:112-146 clock_icon()` 已是 4 倍超采样，质量足够
- `main/nightsky.c:302`、`main/apps/app_calc.c:265` 用 RGB565 canvas —— 是绘制缓冲，与图标质量无关，不动
- **色深保持 16 位**（用户已确认）。这意味着**渐变色带这一项**（RGB565 在 466×466 高 PPI 屏上的可见色阶）不会消除。本方案通过消除缩放锯齿 + 合理抖动来改善观感，但**不承诺消除色带**——若要根除必须升 24 位，那会带来 50% 显存与 QSPI 带宽增长，用户已决定不做

### 4.5 验证方式

- **模拟器出 PNG 直接比对**（改前改后对照，肉眼可见缩放锯齿消失）
- 用 `tools/capture-icons.py` 重新生成截图类图标（注意它会驱动模拟器，且当前用的是 `A 7`/`A 6` 索引，需随 `sim/main_sim.c` 的列表改动同步）
- 实机确认图标观感与模拟器一致

---

## 5. 功耗优化

### 5.1 先澄清「黑底能不能不显示」

**首页背景已经是纯黑**：

```546:547:main/launcher.c
    lv_obj_set_style_bg_color(s_home, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(s_home, LV_OPA_COVER, 0);
```

而且 AMOLED 的黑色像素本身就几乎不耗电。**所以「黑底不显示」这条省不了多少**——面板的扫描动作才是功耗大头，而面板在熄屏时已经被真正关掉了：

- `main/display.c:248` `badge_display_on()` 关屏序列：亮度 `0x51`→0（`:267`）→ `0x28` Display Off（`:268`）→ `0x10` Sleep In（`:269`）
- 触摸也已发深睡 `{0xD1,0x05}`（`main/port_esp.c:1246-1256`）
- AXP2101 十四路 rail 已关到只剩两路（`port_esp.c:1304`，寄存器 `0x80/0x90/0x91` 见 `:1338-1340`）

**这三块作者已经啃透了，不需要再动。**

真正可做的是两件**反向**的事：把非黑处变黑、以及两段式熄屏。

### 5.2 主因：熄屏后仍被定时器唤醒

**这是功耗问题的真正所在**，不是屏幕。

`main/launcher.c:62-97` 的 `idle_timers(false)` 逐行核实的实际行为：

| 行号 | 对哪个 timer | 动作 |
| --- | --- | --- |
| `:70-74` | 显示刷新 timer（`lv_display_get_refr_timer`） | 暂停 |
| `:76` | `s_idle`（1000ms，`idle_cb`） | 暂停 |
| `:78-85` | 触摸读取 timer（`lv_indev_get_read_timer`） | 暂停（恢复时周期设回 12ms） |
| `:86-90` | `s_rotate_timer`（200ms） | 按条件暂停/恢复 |
| `:91` | `s_heap_timer`（10s） | **不暂停**，仅把周期拉长到 120s |
| `:92-96` | `s_batt_timer`（5s） | 暂停 |

**它不碰的东西**：
- `batt_log_cb`（60000ms，`launcher.c:916`）—— **返回值未保存**，句柄不存在，无法暂停
- `housekeep_cb`（1800000ms，`launcher.c:920`）—— **同样未保存句柄**
- **任何 app 自己创建的 timer**

**量化：熄屏时仍在周期的 timer**

| 来源 | 数量 | 说明 |
| --- | --- | --- |
| launcher 全局常驻 | **3** | `batt_log_cb`(60s)、`housekeep_cb`(30min)、`heap_cb`(120s，未暂停) |
| 当前 app 的 timer | **1~3** | 取决于开着哪个 app |

而绝大多数 app 是 `keep_awake = false`（**默认值**，未显式赋值即 false），即**熄屏时它们正开着**：

| app | `keep_awake` | 赋值处 |
| --- | --- | --- |
| `app_mouse`（现 `app_air` 同） | **true** | `app_mouse.c:1208`（运行期由 `emit_cb` 释放，`:763`） |
| `app_meet` | false | `app_meet.c:219`（显式） |
| `app_keys` | false | `app_keys.c:161`（显式） |
| `app_clock`/`app_settings`/`app_calc`/`app_games` | **默认 false** | 结构体未赋值 |

**最坏的组合是开着 Clock 熄屏**——`app_clock.c:27` + `app_timer.c:233` + `app_stopwatch.c:280` 三个 timer 全在跑（1000ms/1000ms/**47ms**），其中 **47ms 那个在屏灭时毫无产出却以 21Hz 持续唤醒 CPU**。

**这就是本次功耗优化的主要收益点。**

### 5.3 统一挂起方案（API 语义已核实）

**两条路，都已核实语义**：

**路线 A：遍历暂停（推荐）**

`lv_timer_get_next` 的正确用法（`sim/lvgl/src/misc/lv_timer.c:288-292`）：传 `NULL` 从链表头开始，返回 `NULL` 表示结束：

```c
for (lv_timer_t *t = lv_timer_get_next(NULL); t; t = lv_timer_get_next(t)) { … }
```

- **遍历期间调用 `lv_timer_pause` 是安全的**（`lv_timer.c:213-217` 只改 `timer->paused` 字段，不动链表节点）。危险的是 `lv_timer_delete`（`lv_timer.c:198-211` 会 `lv_ll_remove` + `lv_free`）
- **需要先快照「本来就暂停的 timer」**，否则恢复时会误恢复它们。有查询接口：`lv_timer_get_paused(t)`（`sim/lvgl/src/misc/lv_timer.c:310-313`），字段在 `lv_timer_private.h:40` 的 `volatile int paused`
- `lv_display_get_refr_timer`（`lv_display.c:1118-1124`）与 `lv_indev_get_read_timer`（`lv_indev.c:584-592`）返回的 timer **都在同一条全局链表里**，遍历能覆盖到

**路线 B：全局开关（更简洁但有代价）**

`lv_timer_enable(bool)` **确实是全局开关**，不是 per-timer：

```265:269:sim/lvgl/src/misc/lv_timer.c
void lv_timer_enable(bool en)
{
    state.lv_timer_run = en;
    if(en) lv_timer_handler_resume();
}
```

`state` 是全局单例 `LV_GLOBAL_DEFAULT()->timer_state`。禁用后 `lv_timer_handler()` 在 `lv_timer.c:75-78` **直接返回 1**，不算时间、不跑回调。

**代价**：把**所有** timer 一起停掉，**包括 `s_idle`（闹钟驱动）**。

**推荐路线 A**，理由：能保留白名单（见 5.4），且不需要迁移闹钟。路线 B 更干净但必须先把闹钟迁走。

### 5.4 A4 缺陷与本节强耦合：闹钟必须先迁移

**已核实：闹钟当前唯一驱动是 `s_idle`**：

```
launcher_start()          launcher.c:912   s_idle = lv_timer_create(idle_cb, 1000, NULL)
      ↓ 每 1s
idle_cb()                 launcher.c:234-254
      → alarm_tick()      launcher.c:242      ← 唯一调用点
            ↓
alarm_tick()              app_alarm.c:130-148
      → start_ring()      app_alarm.c:147 → :86-118
            ↓
start_ring()              app_alarm.c:91  launcher_screen_on()
                          app_alarm.c:92  launcher_keep_awake_by(AWAKE_RING, true)
                          app_alarm.c:117 s_beep_timer = lv_timer_create(beep_step, 70, NULL)
```

**而 `idle_timers(false)` 的 `launcher.c:76` 恰好暂停 `s_idle`。** 熄屏路径又都走它（`screen_off(false)` → `launcher.c:121` `idle_timers(false)`；全仓库**没有任何地方调用 `launcher_screen_off()`** 那个 `touch_wakes=true` 的变体）。

**所以当前固件一旦熄屏，闹钟就不响了**——这与 `launcher.c:237-241` 注释「闹钟由 launcher 每秒检查、熄屏也要检查」**自相矛盾**。

**修法**：把 `alarm_tick` 的驱动从 `lv_timer` 迁到 `esp_timer`。

- **必须在做统一挂起之前完成**，否则会把这个偶发缺陷变成必然缺陷
- `esp_timer` 不依赖 LVGL，功耗上比 1 秒 lv_timer 更优（且它能正确参与 light sleep 的时间补偿）
- 迁移后 `launcher.c:242` 的调用点删除，`s_idle` 仅供空闲检测（可照常被暂停）

**连带确认：响铃需要亮屏**（`app_alarm.c:91` `launcher_screen_on()`）。`launcher_screen_on()` 在 `launcher.c:223-226`，若 veil 置位则走 `screen_wake()`（`:142-170`）。迁移后这条链路不变。

### 5.5 白名单：这些 timer 不能停

统一挂起必须豁免下列 timer，否则会产生新的功能故障：

| timer | 位置 | 为什么不能停 |
| --- | --- | --- |
| `beep_step`（70ms） | `app_alarm.c:117`、`app_timer.c:81` | 响铃脉冲，必须连续跑到用户停止（`app_alarm.c:63-76`） |
| `tick`（1000ms） | `app_timer.c:233` | 倒计时本身。代码注释（`app_timer.c:142-146`）明确「屏灭 s_tick 仍跑，到点仍会响」 |
| `tick`（47ms） | `app_stopwatch.c:280` | 秒表计时（正常靠 `AWAKE_STOP` 不让息屏，`app_stopwatch.c:203`；被强制熄屏时 tick 内屏灭仅 `return` 不重绘，`:184-188`） |
| `emit_cb`（≈6-15ms）+ `poll_cb`（400ms） | `app_mouse.c:1175`/`:1178` | BLE HID 空中鼠标必须持续上报。它**已自带熄屏降频**（`:747-757`）与 `keep_awake` 动态释放（`:763`），属「睡也要发」的典型 |
| `poll_cb`（500ms） | `app_keys.c:125` | BLE/HID 连接状态轮询；停了状态不刷新、判断不出断连 |
| `batt_log_cb`（60s） | `launcher.c:916` | 设计上就不打算暂停（`:309-316` 注释「熄屏过夜也要留记录」）。**但应改为 `esp_timer` 并纳入统一管理**，而不是继续以「无句柄」的方式逃逸 |
| `housekeep_cb`（30min） | `launcher.c:920` | 时间自动同步（`:338-348`）；建议一并改 `esp_timer` |
| `tick`（1000ms） | `app_clock.c:27`、`lock.c:41` | 时钟面/锁屏显示；**屏灭时无产出，可以停**（不在白名单） |

**除白名单外，全部暂停。** 预期收益：熄屏时从 4~6 个周期 timer 降到 0~2 个。

### 5.6 其余优化项

**亮屏刷新周期**：`sdkconfig.defaults:27` 的 `CONFIG_LV_DEF_REFR_PERIOD=15`（15ms ≈ 67fps）对 AMOLED 过密。改 **30ms（33fps）** 可显著减少绘制功耗。注意 `launcher.c:64` 的注释写的是「refresh timer every 33 ms」，**与配置不符**，需一并修正。

**陀螺仪空闲自动关**：加速度计有 5 秒空闲关（`port_esp.c:1595` `port_imu_idle_check()`，检测函数 `imu_touch()` `:1578`），**陀螺仪没有**（`port_esp.c:1663-1667` 注释说明是有意为之，因为空中鼠标静止几秒是正常的）。但 `port.h:57-58` 原文写明：

> 🔋 But a gyro costs more than ten times the accelerometer (roughly 1.5 mA against 0.03). So it is off by default

**1.5mA 是加速度计的 50 倍**，且 `port_esp.c:1663-1667` 也承认「Left on, it keeps running with the display off」。建议加空闲检测（例如 30 秒无移动则关陀螺），在空中鼠标 app 内仍然保持开启。

**把非黑处变黑**（AMOLED 上像素越暗越省电）：
- `main/launcher.c:785` 的 `0x24242A` 面板色可以压到接近纯黑
- `main/launcher.c:599-600` 的分页指示点 `0xE8ECF0`（**接近纯白 = 全亮**）—— **单页化后这行代码直接删除，顺带省电**

**两段式熄屏**：现在是「熄屏」一刀切。改为「先降到约 10% 亮度撑若干秒（例如 5 秒），再真正关屏」。亮度线性影响 AMOLED 电流，这段过渡能让用户看清最后一眼，也不会有突然黑屏的不适。收益比改背景色更实在。

**待实测项（有历史风险，需谨慎）**：
- `sdkconfig.defaults:90` 显式关闭了 PSRAM 半睡（`# CONFIG_PM_SLP_SPIRAM_HALFSLEEP_ENABLED is not set`），原因是「LVGL 栈在 PSRAM 时唤醒踩内存」。**而该问题已通过把 LVGL 栈挪到内部 RAM 解决**，所以可以重新评估——但**必须实机验证长时间稳定性**，不能再踩一次
- `main/main.c:572` 的 `min_freq_mhz = 80` 是否可降到 40MHz。受 PSRAM 80MHz 约束（`sdkconfig.defaults:16` 的 `CONFIG_SPIRAM_SPEED_80M=y`），需实测

### 5.7 量化方式（用户有实机，可实测）

**必须用项目自带的测量手段，不做无法验证的功耗声明。**

- `main/port_esp.c:2187` `port_battery_journal_dump()`：**唯一输出亮屏/灭屏 mV/h 的接口**。它在 `:2255-2271` 把日志分成 display O / X 两组独立算斜率：

```2266:2267:main/port_esp.c
        ESP_LOGI("batt", "── display %s: %ld mV over %ld s -> %.0f mV/h (%d samples)",
                 mode ? "O" : "X", dt, dv, dv * 3600.0 / dt, seg);
```

  两者之差就是屏幕（及对应状态）的净功耗
- `main/port_esp.c:2453` `port_battery_mark(bool screen_on)` —— 在 `launcher.c:113`（灭屏前）与 `:169`（唤醒后）各写一行日志，**这是亮屏斜率能成立的前提**（`:2441-2452` 注释说明）
- `main/port_esp.c:2369` `port_battery_log(const char *what)` —— 周期采样，调用点 `launcher.c:315`（`"idle-on"/"idle-off"`）与 `rec_store.c:226`（`"rec"`）
- `main/port_esp.c:2341` `charge_track` / `:1868` `batt_track` —— 内部反推容量与放电率，不直接产出 mV/h 对比
- 工具：`tools/badge-diag.sh`（复位原因 + 电池日志，**不擦除**）、`tools/check-run.sh`

**验收标准**：熄屏后单位时间电压跌幅（mV/h）相对基线有可测量的下降，且**下降幅度与「被停掉的 timer 数量」正相关**。若测不出差异，说明挂起没生效或收益被高估，需回头查。

**注意**：无电流传感器（`port_esp.c:1971-1974` 注释说明 AXP2101 只暴露电压），所以只能用电压斜率反推，测量周期要足够长（建议每档跑 30 分钟以上）。

---

## 6. 录音重构与音频增强

### 6.1 现状与缺陷

**录音 UI 在 `main/apps/app_meet.c`**（不是 `usb_screen.c`）：

- `:144-206` `meet_enter()`：300×300 圆按钮 + 430×430 圆环进度 `s_ring`；标签 `s_big`(48)/`s_sub`(20)/`s_hint`(16)
- 四状态 `M_IDLE`/`M_REC`/`M_SAVED`/`M_FAIL`（`:15, 25-30`）
- `:96-111` 短按：`M_IDLE`→`start(false)`、`M_REC`→`port_rec_stop()`+`go(M_SAVED)`；`:110` 长按 → `start(true)`（英文录音）
- `:189-199` 右上角 64×40 USB 导出按钮
- **`:44` IDLE 提示写 `"hold = export"`，但长按实际是开始英文录音**（A5 缺陷）

**存储层 `main/rec_store.c` 的实测常量**：

| 常量 | 值 | 行号 |
| --- | --- | --- |
| `REC_SUBTYPE` | `0x40` | `:28` |
| `DIR_BYTES` | 4096 | `:29` |
| `DATA_START` | 4096 | `:30` |
| `SECTOR` | 4096 | `:31` |
| `REC_MAGIC` | `0x43455242`（"BREC"） | `:33` |
| `REC_MAX` | 12 | `:34` |
| `sizeof(rec_ent_t)` | **24 字节**（含 `int64_t` 故按 8 对齐） | `:36-44` |
| `sizeof(rec_dir_t)` | **296 字节** = 8 + 12×24 | `:46-50` |

**采集链路**（`rec_store.c:184-265` `mic_task()`）：

| 项 | 值 | 行号 |
| --- | --- | --- |
| 外设 | I2S + **ES7210** 麦克风（`bsp_audio_codec_microphone_init()`） | `:188`，`:177` 注释点名 |
| 采样率/位深/声道 | 16 kHz / 16 bit / 单声道 | `:194-196` |
| 每次读取 | 505 采样（正好一块） | `:204, 216` |
| 增益 | **固定 +30 dB** | `:202` |
| AGC/高通/降噪/去直流 | **全部没有** | —— |
| 省电锁 | 录音期间持 `port_pm_hold(true)` | `:212, 247` |

**编码**（`main/adpcm.c`）：标准 IMA-ADPCM，块 256B = 4B 头 + 252B 码 = **505 采样**（`adpcm.h:9-13`），压缩比约 3.94:1。**无去直流**。`adpcm.c:69` `adpcm_wav_header()` 是死代码，**但它算得是对的**（返回 60 字节含 fact 块，RIFF size = `4+8+20+8+4+8+data` = `52+data`，正确）。

**导出**（`main/usb_export.c`）：伪造只读 FAT16（512B 扇区、32KB 簇 `CLUSTER_SEC 64`、768 簇，`:39-59`）；`boot_sector()`(`:110-130`)、`fat_sector()`(`:132-154`)、`root_sector()`(`:156-172`，文件名 `REC%04d.WAV`、只读 0x01、**无时间戳**)；读回调(`:198-235`)。

**`rec_export_list()` / `rec_export_read()` 有且仅有一个消费者**：`usb_export.c:80` 与 `:229`。这是目前把音频取出来的唯一通路。

### 6.2 核心重构：改环形缓冲

**问题**：`main/rec_store.c:101-109` 的 `alloc_off()` 取所有条目 `off+bytes` 的**最大值**作为下一个写入点，所以**水位只增不减**——删掉旧录音水位也不回落。这个模型下 24MB 只能用一次，8 小时无从谈起。

**改法**：绝对偏移环形缓冲。

```c
/* 数据区是环形字节流。绝对偏移单调递增，物理位置 = DATA_START + (abs % data_size)。
 * write_abs 是写入头，uploaded_abs 是已确认上传头，两者之差即缓冲占用。
 * 条目不再带 sent 字段：是否已上传由全局 uploaded_abs 表达。 */
typedef struct {
    uint64_t start_abs;   /* 本条录音起点（绝对偏移） */
    uint32_t bytes;       /* 编码后字节数 */
    int64_t  started;     /* unix 秒；0 表示当时时钟未校 */
    uint8_t  lang, done, pad[2];
} rec_ent_t;

typedef struct {
    uint32_t  seq;          /* 每次落盘加一；A/B 两份取 seq 大且 crc 正确者 */
    uint32_t  crc;
    uint64_t  write_abs;
    uint64_t  uploaded_abs; /* 节流落盘：每前进 1~4MB 才写一次 */
    uint32_t  count;
    rec_ent_t ent[REC_MAX];
} ring_dir_t;
```

**该模型天然满足「上传确认后空间立即可复用」，同时修掉 A1 缺陷。**

### 6.3 容量与磨损核算（实测数字）

**分区**：`partitions.csv` 的 `rec, data, 0x40, 24M` = **25,165,824 字节**
- 现状数据区（`DATA_START=0x1000`）= 25,161,728 字节 = **6143 个 4KB 扇区**
- 新方案数据区（起点改 0x2000，目录占 2 扇区）= 25,157,632 字节 = **6142 个扇区**

**码率**：`16000/505 × 256 = 8110.9 B/s`

| 项 | 计算 | 结果 |
| --- | --- | --- |
| 数据区录满一次 | 25,161,728 ÷ 8110.9 | **3102 秒 = 51.7 分钟** |
| 每扇区擦一次消耗一轮 | 6143 次擦除 / 轮 | |
| 100,000 次寿命可跑轮数 | 100,000 轮 × 3102 秒 | **3.10×10⁸ 秒 ≈ 9.83 年** |

**结论：约 9.8 年连续录满才耗尽 flash。数据区磨损不是瓶颈。**

**真正的磨损风险在目录水位落盘频率**：`uploaded_abs` 若每确认一块就落盘，目录扇区会被写爆。**必须节流**到每前进 1~4MB 才落盘一次（8KB/s 下即 128~512 秒一次）。按双扇区轮换计算约 2.4 年以上。

### 6.4 目录掉电安全：双扇区交替写

**现状危险**：

```71:72:main/rec_store.c
    if (esp_partition_erase_range(s_part, 0, DIR_BYTES) != ESP_OK) return;
    esp_partition_write(s_part, 0, &s_dir, sizeof s_dir);
```

先擦 4KB 再写。**擦除与写入之间断电，整张目录就没了**，下次开机 magic 校验失败会重建空目录（`rec_store.c:85-90`），等于**所有录音索引一次性丢失**。

**改法**：A/B 两份副本，分别位于分区 `0x0000` 与 `0x1000`，各自带递增 `seq` 与 `crc32`。读取时取 `seq` 更大且校验通过的那份；写入时永远写「较旧」的那一份。这消除了擦写窗口。

**容量核算（已核实无压力）**：`2 × sizeof(rec_dir_t) = 2 × 296 = 592 字节`，放进 `2 × 4KB = 8192 字节`里，占用率仅 **7.2%**。即使 `REC_MAX` 提到 24 条（`sizeof` 变 584 字节），两份共 1168 字节，仍远小于 8192。

**配套改动**：`partitions.csv` 数据区起点由 `0x1000` 改 `0x2000`。

### 6.5 音频增强链路

**必须在 ADPCM 编码之前插入**——这是关键：直流偏置会白白吃掉 ADPCM 的量化步长范围，压缩效率与音质同时受损。

```c
typedef struct {
    float hp_x1, hp_y1;     /* 高通：100Hz 截止，16kHz 采样，系数约 0.9615 */
    float agc_gain;         /* 当前增益，钳位 0.25 至 4.0 */
    float agc_env;          /* 200ms 滑动窗有效值包络，目标 -18dBFS */
    float gate_env;         /* 噪声门包络，-45dBFS 以下衰减 12dB */
} dsp_state_t;

/* 就地处理 16 位单声道 PCM，必须在编码之前调用。
 * 直流偏置会白白吃掉编码的量化步长范围，这是先高通的原因。 */
void dsp_process(dsp_state_t *st, int16_t *pcm, size_t samples);
```

| 环节 | 参数 | 说明 |
| --- | --- | --- |
| 一阶高通 | `fc = 100Hz`，`a = exp(-2π·100/16000) ≈ 0.9615` | 去直流，运算量 O(1) |
| AGC | 200ms 滑窗 RMS，目标 **-18 dBFS**，起音 10ms / 释放 500ms，增益钳位 0.25~4× | 同时把硬件固定增益从 **+30dB 降到 +18dB** 留出 headroom，否则 AGC 只能往下压 |
| 噪声门 | -45 dBFS 以下渐进衰减 12dB | **不做硬切**，避免呼吸感 |
| 谱减（可选） | 256 点 FFT + 汉宁窗 + 50% 重叠，16kHz 下 16ms 一帧 | ESP32-S3@240MHz 理论约 0.5ms/帧 ≈ 3% CPU。**必须实机实测再决定是否默认开启** |

**ES7210 若支持硬件高通，优先用硬件**——但 BSP 未暴露该寄存器，需查数据手册确认。

**这部分必须实机调参收敛**，模拟器无法验证音质。

### 6.6 录音可用性（第 8 阶段）

| 功能 | 实现要点 |
| --- | --- |
| **ADPCM 解码** | `adpcm.c` 目前只有编码，需补解码。IMA-ADPCM 解码是编码的逆运算，`STEP[]`/`INDEX[]` 表可复用 |
| **回放** | 经 ES8311 输出。可复用 `port_tone_*` 已有的 codec 通路（`port_esp.c:380` 附近 `TONE_SR 16000`） |
| **暂停/续录** | `rec_store.c` 目前只有 start/stop 二态，需加暂停态（写入头保持不动，恢复时续写） |
| **电平指示** | `mic_task` 每块（505 采样）算一次 RMS 上报给 UI，UI 侧画条 |
| **录音列表** | `app_meet.c` 现在只显示 "N saved"，需加列表（时长 + 上传状态） |
| **单条删除** | 当前**没有任何用户可控的删除手段**。环形缓冲 + 绝对偏移模型下，删除语义需重新设计（例如「标记为已上传」而非真删） |

### 6.7 统一 WAV 头（同时修 A2）

现有两份实现在打架：

| 实现 | 位置 | 返回长度 | RIFF size 公式 | 正确性 |
| --- | --- | --- | --- | --- |
| `usb_export.c` 内联 `wav_header()` | `:175-195` | **48 字节**（无 fact 块） | `36 + data` | ✗ **少 4 字节** |
| `adpcm.c` 的 `adpcm_wav_header()` | `:69-93` | **60 字节**（含 fact 块） | `52 + data` | ✓ 正确 |

**处置：把两份合并成一份正确实现**，固件侧（USB 导出 + 上云）与 Python 服务端**共用同一份字段定义**，避免再次分叉。建议保留 60 字节含 fact 块的版本（压缩格式带 fact 块更规范）。

**同时修**：`usb_export.c` 的 `root_sector()`（`:156-172`）**目录项无时间戳**，而 `rec_ent_t.started` 里本来就有时间，应补上。另外 `rec_export_list()`（`rec_store.c:375`）不过滤已上传条目，导致已上传的每次重复出现。

---

## 7. 录音上云

### 7.1 为什么必须上云：先算账

| 项 | 数值 |
| --- | --- |
| 码率 | 16000/505 × 256 = **8110.9 B/s ≈ 8 KB/s ≈ 65 kbit/s** |
| 2 小时 | 58.4 MB |
| **8 小时** | **233.6 MB** |
| 本地 rec 分区 | **51.7 分钟** |

**8 小时本地物理上装不下，必须外部承接。** 用户判断正确。

### 7.2 关键决策：批量上传而非持续流式

**8 KB/s 是极低码率**：攒 10 分钟只有 4.9 MB，WiFi 全速传完约 **10 秒**。

**因此不应常驻 WiFi**，而是：

```
本地攒 N 分钟 → 连一次 WiFi → 猛传 → 立刻断开
```

WiFi 占空比约 **2%~3%**，网络侧耗电几乎可忽略。**这是电池下跑 8 小时的关键，也让「上云」与「省电」两个需求不再互相冲突。**

该策略延续了原作者写在 `main/port.h:141-142` 的省电哲学：

> 🚨 The badge does **not stay on WiFi** — it joins to set the clock or to upload and leaves again, for power.

### 7.3 上游曾实现过这个功能，但从未提交（重要发现）

**证据链（全部已核实）**：

| 证据 | 位置 |
| --- | --- |
| 模拟器留着三个上传函数桩：`port_rec_upload_try` / `port_rec_uploading` / `port_rec_upload_msg` | `sim/port_sim.c:372-374` |
| 构建脚本把 `rec_upload.c` 列入「只在板子编译」的名单 | `sim/build.py:61` |
| 回归脚本注释原文「on 09-10 **rec_upload.c** moved from badge_creds_wifi to badge_wifi_pick」 | `tools/regress.sh:198` |
| 同一脚本 grep `main/rec_upload.c` | `tools/regress.sh:207` |
| 注释提到「a slow clock sync at boot colliding with **the 30-second upload poll**」 | `main/port_esp.c:932-936` |
| 注释用**过去时**承认上传真的跑通过：「That is why a 24 MB area had 3.9 minutes left with all twelve recordings already taken off」 | `main/rec_store.c:330-338` |

**但 `main/rec_upload.c` 在 HEAD、`v1.0` 标签与全部 git 历史中都不存在**（`git log --all --diff-filter=AD --name-status -- main/rec_upload.c` 返回空），`main/port.h` 中也**没有那三个函数的声明**。

**结论**：该功能只存在于作者另一台机器上（`docs/SETUP.md:3-5` 说明作者在 home/work 两台机器间同步状态）。**本项目需要重新实现，但 `sent`/`purge_sent`/`rec_oldest_unsent`/WiFi 抢占锁全部是为它预留的。**

**因此新文件直接命名为 `main/rec_upload.c`** —— 让 `sim/build.py` 与 `regress.sh` 里现有的引用自动对上。

### 7.4 可直接复用的基础设施（已核实）

**WiFi 抢占锁 —— 这是最关键的可复用件**：

| 件 | 位置 | 说明 |
| --- | --- | --- |
| `badge_wifi_take(wait_ms)` | `main/port_esp.c:939-949` | 惰性创建互斥量（双检锁 + `portENTER_CRITICAL`）；创建失败返回 `true` 不阻塞 |
| `badge_wifi_give()` | `main/port_esp.c:951-954` | |
| `badge_wifi_netif_once()` | `main/port_esp.c:956-965` | netif 单例。**`esp_netif_create_default_wifi_sta()` 二次调用会 assert**（`:928-931` 注释），所以 netif 永不销毁 |

**`sync_task()` 是现成模板**（`main/port_esp.c:967-1108`）。逐段拆解：

| 段落 | 行号 | 内容 | 上传任务可否照抄 |
| --- | --- | --- | --- |
| 抢锁 | `969-975` | `badge_wifi_take(1000)`；抢不到记日志退出。⚠️ **注意这里用 1000ms，而 `scan_task`/`try_task` 用 8000ms** | ✅（建议改用 8000ms 一致） |
| 建 netif | `979` | `badge_wifi_netif_once()` | ✅ |
| 初始化 | `981-991` | `wifi_init_config_t` + `esp_wifi_init()` | ✅ |
| 模式/启动 | `993-994` | `esp_wifi_set_mode(WIFI_MODE_STA)` + `esp_wifi_start()` | ✅ |
| 选网 | `995-1003` | `badge_wifi_pick()`（**必须在 start 之后**，因为要扫描） | ✅ |
| 配置/连接 | `1005-1010` | `esp_wifi_set_config()` + `esp_wifi_connect()` | ✅ |
| 等 IP | `1030-1061` | 最多 15s 轮询 `esp_netif_get_ip_info`；链路掉线重连最多 2 次 | ✅（上传必须有 IP） |
| **SNTP** | `1062-1099` | 建 SNTP、等时间 | ❌ **替换为 HTTPS 上传，这是唯一需要改的段落** |
| 收尾 | `1101-1107` | `esp_sntp_stop` → `disconnect` → `stop` → `deinit` → `badge_wifi_give` | ✅ |

**骨架照抄链**：`969 → 979 → 981 → 993 → 994 → 995 → 1005 → 1009 → 1030~1050(等 IP) → 【换成 HTTP 上传】→ 1101~1107`。
结构上上传任务与时钟任务是**同一套模板**，只把「等 SNTP 回调」换成「发 HTTP、等响应」。

**`badge_wifi_pick()` 的选网策略**（`port_esp.c:858-905`）：一次阻塞扫描 → 在**三个已存槽位**里挑 RSSI 最大者（`best_rssi` 初值 -128）→ 都没匹配上则回退 `badge_creds_wifi_live()`。**避免逐个盲连**（`:607-617` 注释解释了动机）。用户要求用手机热点，凭据预先存入三个槽位，正好对上。

### 7.5 两个必须补的缺口

**缺口 1：WiFi 传输期间不持 `port_pm_hold`**

已通读 `sync_task`、`scan_task`、`try_task` 三段 WiFi 代码，**均未调用 `port_pm_hold`**。全仓库 `port_pm_hold` 的调用点只有：

- 录音：`rec_store.c:212`（开）/ `:247`（关）
- BLE：`ble/hid_mouse.c:319, 343, 391`
- 插线：`main.c:631`、`launcher.c:326`

`port_esp.c:110-114` 注释也明说「只有 BLE 和 I2S 录音持锁」。

**后果**：CPU 在等 IP / 等响应时可进 light sleep，可能打断 TCP 吞吐。**上云前需在传输段显式加 `port_pm_hold(true)/false()`**（`port.h:294`，可嵌套计数）。

**缺口 2：`sdkconfig.defaults` 里 TLS/HTTPS 配置一项都没有**

逐项核实（`sdkconfig.defaults` 共 91 行）：

| 配置项 | 状态 |
| --- | --- |
| `CONFIG_ESP_HTTP_CLIENT_ENABLE_HTTPS` | **未出现** |
| `CONFIG_MBEDTLS_*`（任意） | **未出现** |
| `CONFIG_ESP_TLS_*`（任意） | **未出现** |
| `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE` | **未出现** |
| `CONFIG_ESP_HTTPS_*` / `CONFIG_HTTPD_*` | **未出现** |

全仓库源码与构建脚本中搜不到任何 `mbedtls`/`esp_http_client`/`tls`/`https` 引用。**HTTPS 上传的依赖一个都还没接。**

**补充**：`main/CMakeLists.txt` **没有 `REQUIRES` 子句**，而 ESP-IDF 中 main 组件隐式依赖全部组件，所以 `esp_http_client`/`mbedtls` 无需显式声明即可链接。但**证书包与 HTTPS 使能是 sdkconfig 层面的事，必须补配置**。

**⚠️ 有个守门检查会拦你**：`tools/regress.sh:351-371` 校验「`sdkconfig.defaults` 里每个符号都必须出现在生成的 `sdkconfig` 里」。加 TLS 项时**写错符号名会被这条拦下**——这是好事，但要知道它的存在。

### 7.6 传输协议与断点续传

```
POST /api/v1/rec/session                   → {"session":"…","ack_offset":0}
PUT  /api/v1/rec/session/{id}?offset=N     → {"ack_offset": N+len}
     头: X-Chunk-CRC32: <hex>   体: 裸 ADPCM 字节
GET  /api/v1/rec/session/{id}              → {"ack_offset":N,"state":"open"}
POST /api/v1/rec/session/{id}/finalize     → {"file":"…wav","bytes":N}
```

| 设计点 | 取值 | 依据 |
| --- | --- | --- |
| 块大小 | **32KB**（≈4 秒音频） | = 128 个 ADPCM 块（256B）= 8 个 flash 扇区（4KB），**与编码块边界和扇区同时对齐** |
| 幂等 | 服务端以**自身文件长度**为 `ack_offset` | `offset == 文件长度` → 追加；`offset + len <= 文件长度` → 判为重放，直接回当前 ack **不重复写**；`offset > 文件长度` → 出现空洞，返 409 + 当前 ack，客户端从 ack 续传 |
| 完整性 | 每块带 CRC32 | 校验失败返 **422 且绝不落盘** |
| 会话标识 | 服务端生成 UUID | 与设备号、起始时间戳、语言一起登记在 `meta.json` |
| 服务端合并 | chunks 顺序追加到 `{session}.adpcm.part` | `finalize` 时补写 WAV 头并改名为 `{时间戳}_{设备}_{语言}.wav` |

**服务端选型**：Python 3 + 标准库 `ThreadingHTTPServer`（零第三方依赖，部署最简单）；nginx 做 TLS 终结与反向代理（**正式证书挂 nginx**，后端只监听 127.0.0.1）；systemd 托管；数据落 `data/`。

用户已确认：**公网服务器 + 正式证书**。因此固件侧用 `esp_crt_bundle`（ESP-IDF 内置 CA 包）匹配正式证书即可，**不需要烧自签根证书**——这省掉了证书轮换的麻烦。

### 7.7 两个必须实机验证的风险

**风险 1：内存（这是本项目最紧的资源）**

- `sdkconfig.defaults:32-33` 已说明 BLE 栈吃 IRAM 逼 LVGL 让出 IRAM
- `main/port.h:71-73` 记录了「内存不足导致 BLE 栈起不来，WiFi 放开后自愈」的历史
- **TLS 握手需 40~60KB 堆**

**处置**：
- 评估上传前 `port_hid_stop()`（`port.h:78`，只停广播保留连接）
- 下调 mbedTLS 收发缓冲（`CONFIG_MBEDTLS_SSL_IN_CONTENT_LEN` / `OUT_CONTENT_LEN`）
- **上传任务栈放内部 RAM**（TLS 对 PSRAM 栈敏感）
- **分块 32KB 而非整文件**，避免大段连续分配

**风险 2：flash 读写并发抖动**

录音每秒向 flash 写约 **2 个扇区**（8110.9 B/s ÷ 4096 ≈ 1.98），上传时读同一片 flash 会与写入串行化，**可能造成 I2S 丢帧**（音频出现卡顿/断音）。

**处置**：
- 上传侧**一次读 32KB 到 PSRAM 再分块发**（把读集中，减少与写交错）
- 录音侧把现有 4KB 页缓冲（`rec_store.c:62` `s_page`）加大
- 提高 `mic_task` 优先级

**此项必须实机验证**——这是整个上云方案最可能出问题的地方。

### 7.8 断电恢复语义

开机后需要判断：哪条录音已传完、哪条要重传。

环形缓冲 + `uploaded_abs` 模型下，语义很干净：

- `uploaded_abs <= start_abs` → **完全没传**，整条重传
- `start_abs < uploaded_abs < start_abs + bytes` → **传了一半**，从 `uploaded_abs - start_abs` 处续传
- `uploaded_abs >= start_abs + bytes` → **传完**，可回收

服务端的 `ack_offset` 是权威——客户端只需在重连后用 `GET /api/v1/rec/session/{id}` 问出当前 ack 即可。**这要求会话 ID 在断电后仍能恢复**，所以会话 ID 需与录音条目一起持久化（写进 `ring_dir_t` 或 `rec_ent_t`）。

---

## 8. 分阶段实施路线

| 阶段 | 内容 | 验证方式 | 依赖 | 预估 |
| --- | --- | --- | --- | --- |
| **0** | 搭 ESP-IDF 5.5 环境；记录功耗基线；`pip install fonttools`；确认 `npx lv_font_conv` 可用 | 实机 + `tools/badge-diag.sh` | 无 | 小 |
| **1** | A 类缺陷（除 A4）：A1 空间回收接线、A2 WAV 头统一、A3 双扇区目录、A5 文案、A6 定时器句柄、A7 文档、A8 死代码、A9 语言参数 | 模拟器 + 实机断电测试 | 0 | 中 |
| **2** | 字体管线 + 全界面中文化 | **模拟器出 PNG 逐屏核对** + regress 新的字体覆盖检查 | 1 | **大** |
| **3** | App 集合精简 + 首页单页 90px + 画质精修 | 模拟器出 PNG（图标锐度、标签布局）+ regress | 2 | 中 |
| **4** | 功耗：A4 闹钟迁 `esp_timer` → 定时器统一挂起（含白名单）→ 刷新周期 → 陀螺仪空闲 → 两段熄屏 | **实机 mV/h 对比**（`port_battery_journal_dump`） | 1 | 中 |
| **5** | 录音存储重构：环形缓冲 + 绝对偏移水位 + 磨损节流 | 实机 8 小时录制 + 断电测试 | 1 | 大 |
| **6** | 音频 DSP：高通 → AGC → 噪声门（谱减可选） | **实机听感 + 调参** | 5 | 中 |
| **7** | 上云：服务端 + 固件客户端 + 批量调度 + 断点续传 | **端到端实测**（含 flash 并发抖动验证） | 0, 5 | **大** |
| **8** | 录音可用性：解码回放、暂停、电平表、录音列表 | 模拟器 + 实机 | 5 | 中 |

### 8.1 阶段 0 的现实问题

**当前环境固件工具链未就绪**：

| 项 | 状态 |
| --- | --- |
| ESP-IDF | **未安装**（`idf.py` 不存在） |
| Python | 3.14.3，**偏新**（ESP-IDF 5.5 官方支持约到 3.11） |
| cmake | 4.4.0，**偏新**（可能触发 `cmake_minimum_required < 3.5` 兼容问题） |

阶段 0 需要解决这些，否则**阶段 1、4、5、6、7 的实机验证无法进行**。阶段 2、3、8 可以在模拟器上完成，不阻塞。

### 8.2 模拟器能验与不能验

**模拟器能验（已验证过一遍基线）**：
- 画质：出 PNG 直接比对（`sim/shots/*.png`，实测每张 652KB）
- **中文渲染与标签布局**：逐屏肉眼核对豆腐块/裁切/溢出
- 单页 6 图标布局与几何余量
- 录音界面流程（电平表/列表/上传状态）
- `tools/sim-exit-check.py`（每个 app 退出是否死机）、`tools/sim-stress.py`（晃动时钟与粗暴操作）稳定性

**模拟器验不了（必须实机）**：
- 真实功耗
- 真实麦克风音质与 DSP 调参
- 真实 WiFi 上传
- flash 断电行为

### 8.3 回归护栏：必须同步更新，不能靠改检查「通过」

**现有基线**：改动前 `tools/regress.sh` **全部通过**（已实测，exit 0）。

**本次会触及的检查**：

| 检查 | 位置 | 处置 |
| --- | --- | --- |
| 13 处 water/orb 检查 | `regress.sh:4-6, 67-78, 157-163, 257-259, 282-287, 321-324, 346-348` | 删除（对应功能已移除） |
| **两段会抛 FileNotFoundError 的 Python 块** | `regress.sh:269-280`、`:297-317` | 必须删除，否则**脚本直接中断** |
| **一处误判为通过的隐蔽失效** | `regress.sh:327-329`（`! grep -qi saturn orb.c orb.h`，grep 找不到文件返回 2，`!` 取反为真） | 必须删除，**这类失效最危险** |
| 「do the home pages still turn」 | 实测输出 | **改写为「首页 6 个图标都在且在圆内」**，不要直接删 |
| 「recording space / exported recordings come off the list」 | `regress.sh:331-336` | **强化**：现有检查只 grep `purge_sent` 的函数存在性，**抓不到「`rec_mark_sent` 无人调用」这个断裂**。需新增「回收链路上每个环节都有人调用」的不变量检查 |
| `sdkconfig.defaults` 符号与 `sdkconfig` 一致性 | `regress.sh:351-371` | 加 TLS 配置时会被它拦——这是正确行为，注意符号名拼写 |
| 「every source under main/ is in the build list」 | `regress.sh:373-386` | 删文件后重跑 `sync_cmake.py` 即可通过 |

**需新增的不变量检查**：

1. **回收链路完整性**：`purge_sent` ← `rec_mark_sent` ← 上传器，每一环都必须有调用者
2. **WAV 头字段正确**：RIFF size == 文件长度 − 8，且固件侧与 Python 服务端用同一份定义
3. **环形缓冲边界**：`uploaded_abs <= write_abs`、占用不超过数据区大小
4. **中文字体覆盖**：扫描 `main/` 下界面字符串提取汉字集合，断言全部在生成字体的字符集里

---

## 9. 不改清单与待确认项

### 9.1 明确不改的东西

| 项 | 理由 |
| --- | --- |
| **色深保持 16 位** | 用户已确认。升 24 位会带来 50% 显存与 QSPI 带宽增长。**代价**：RGB565 的渐变色带不会消除，本方案只改善缩放锯齿，**不承诺消除色带** |
| **AXP2101 电源轨配置** | `port_esp.c:1304` 已把 14 路关到只剩 2 路，作者已优化到位 |
| **面板关屏命令序列** | `display.c:248` 的 `0x28` + `0x10` 序列正确，是真正关屏而非调暗亮度 |
| **ADPCM 压缩格式** | USB 导出与上云服务端都依赖，改动会同时破坏两条通路 |
| **触摸供电轨** | `port_esp.c:1211-1226` 注释说明它与 LCD/VCC3V3 共用，**物理上无法断电**，只能软睡眠（已做） |
| **`main/nightsky.c`** | 首页壁纸，与 Orbit 无关 |
| **陀螺仪默认关闭** | `port.h:57-58` 的设计取舍正确（1.5mA），只补空闲检测 |
| **`main/apps/app_mouse.c` 的应用内切换逻辑** | 已完整实现，只删多余入口 |
| **LVGL 内置 Montserrat 字体** | 保留作符号兜底 + 大数字字形 |

### 9.2 待确认项（如无异议按推荐值执行）

| # | 问题 | 推荐值 | 影响 |
| --- | --- | --- | --- |
| 1 | Air Mouse 与 Trackpad 是否跨次记住模式 | **不记住**，进入即空中模式 | 若记住需存 NVS |
| 2 | 「录音」vs「会议」作为 Meet 的中文名 | **录音** | 只影响一个字符串 |
| 3 | `REC`/`OK`（48px）是否翻译 | **不译**（功能缩写） | 翻译则 48px 需额外生成字体 |
| 4 | 抖动是否启用 | **先 A/B 对比再定** | 风险：现实现是无条件加噪，可能加重颗粒感 |
| 5 | `ICON_D = 90` 的观感是否可接受 | 按用户要求 90 | 上游作者认为 6 图标挤一页「很难点」（`launcher.c:259-261`） |
| 6 | PSRAM 半睡是否重开 | **先实测稳定性再定** | 有历史踩坑记录（`sdkconfig.defaults:87-90`） |
| 7 | `min_freq_mhz` 是否降到 40MHz | **先实测再定** | 受 PSRAM 80MHz 约束 |

---

## 附：本文档的证据来源

| 结论 | 验证方式 |
| --- | --- |
| `regress.sh` 修改前全部通过 | 实跑 `bash tools/regress.sh`，exit 0 |
| 模拟器可出 PNG 基线 | 实跑 `./sim/badge_sim`，产出 10 张 652KB PNG 于 `sim/shots/` |
| 字体管线可行 | 实跑 fontTools 提取 + `lv_font_conv` 生成，181 汉字 100% 覆盖 |
| 字体体积 | 实测生成 `.c` 的位图数据字节数 |
| `.fallback` 字段可用 | 读生成代码确认 + 读 `sim/lvgl/src/font/lv_font.h:116` |
| 缩放走重采样路径 | 读 `sim/lvgl/src/draw/sw/lv_draw_sw_img.c:223-224` 与 `lv_draw_sw_transform.c:211, 656, 682-685` |
| 内置 CJK 字体不可用 | 读 `lv_font_source_han_sans_sc_16_cjk.c:4` 的生成参数与字符集 |
| `lv_timer_enable` 是全局开关 | 读 `sim/lvgl/src/misc/lv_timer.c:265-269` |
| 闹钟熄屏失效 | 沿 `launcher.c:912 → 242 → app_alarm.c:130` 调用链核实唯一驱动，对照 `launcher.c:76` |
| 死代码确认 | 全仓库检索（含头文件、extern、函数指针、构建脚本） |
| 目录扩容无压力 | 按字段计算 `sizeof`，交叉验证 |
| 磨损 9.8 年 | 按分区字节数与码率计算 |
| WiFi 模板可复用 | 逐段读 `port_esp.c:967-1108` |
| TLS 配置缺失 | 逐项核对 `sdkconfig.defaults` 全文 91 行 |

---

*本文档由代码实证撰写。所有行号基于 `main` 分支 HEAD `c994edd`（2026-09-14）。实施前请确认行号未因上游改动而漂移。*
