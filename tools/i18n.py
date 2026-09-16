#!/usr/bin/env python3
"""The interface's Chinese strings, and the pass that puts them in.

🚨 Why this is a script and not a one-off edit. The interface is 19 files and
   about 150 strings; doing it by hand means roughly 150 chances to mistype a
   character that will then need a font rebake to show up, and no record of what
   was decided. Keeping the table here makes the translation reviewable in one
   place and re-appliable if a file is ever taken from upstream again.

🚨 Words that stay in Latin, on purpose:
     - the function keys: F1 F2 F4 F5 F12 Esc Tab Ent Del. They are the names of
       the keys printed on the keyboard; translating them would make the app
       harder to use, not easier.
     - REC and OK on the recorder's 48 px display. They read as status lights.
     - the keypad's input sets and the calculator's keys.
     - LV_SYMBOL_* glyphs, the hardware model string, the time-zone list and the
       splash word "badge".
   tools/regress.sh holds the line on the first two: 32/40/48 have no Chinese
   font baked, so a Chinese word there would render as boxes.

  python3 tools/i18n.py --check     report what would change, write nothing
  python3 tools/i18n.py             apply
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🚨 Exact source text on the left, including the quotes, so a replacement can
#    never land in the middle of a longer string by accident. Every entry is
#    looked for and reported if it does not turn up, which is how a typo here
#    gets caught instead of silently doing nothing.
TABLE = {
    # ── the home ring ────────────────────────────────────────
    '.name = "Games"': '.name = "游戏"',
    '.name = "Air Mouse"': '.name = "空中鼠标"',
    '.name = "Clock"': '.name = "时钟"',
    '.name = "Calc"': '.name = "计算器"',
    '.name = "Meet"': '.name = "录音"',
    '.name = "Keys"': '.name = "按键"',
    '.name = "Settings"': '.name = "设置"',
    '"power off"': '"关机"',

    # ── the clock face ───────────────────────────────────────
    '"SEC"': '"秒"',
    '"BAT"': '"电量"',
    '"UP"': '"运行"',
    '"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"':
        '"周日", "周一", "周二", "周三", "周四", "周五", "周六"',
    '"JAN", "FEB", "MAR", "APR", "MAY", "JUN",\n'
    '                                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"':
        '"1月", "2月", "3月", "4月", "5月", "6月",\n'
        '                                      "7月", "8月", "9月", "10月", "11月", "12月"',
    # 🚨 The format has to move with the words: "%s %s %02d" would print
    #    "周四 9月 15" with a stray space where the day belongs.
    '"%s  %s %02d",\n                 WD': '"%s %s%d日",\n                 WD',
    # 🚨 20 bytes held "THU SEP 11" with room to spare. Chinese is three bytes a
    #    character and this line is now about 18 of them.
    'char buf[20];': 'char buf[32];',

    # ── settings ─────────────────────────────────────────────
    '"Brightness"': '"亮度"',
    '"Sound"': '"声音"',
    '"Screen off"': '"息屏"',
    '"Time"': '"时间"',
    '"Wi-Fi"': '"无线网络"',
    '"Bluetooth"': '"蓝牙"',
    '"Battery"': '"电池"',
    '"auto-rotate ON"': '"自动旋转 开"',
    '"auto-rotate OFF"': '"自动旋转 关"',
    '"sync now"': '"立即校时"',
    '"not set"': '"未设置"',
    '"idle"': '"空闲"',
    '"synced"': '"已同步"',
    '"failed"': '"失败"',
    '"connecting..."': '"连接中…"',
    '"15s"': '"15 秒"',
    '"30s"': '"30 秒"',
    '"60s"': '"60 秒"',
    '"never"': '"从不"',

    # ── the WiFi screen ──────────────────────────────────────
    '"looking around..."': '"搜索中…"',
    '"nothing around"': '"附近没有网络"',
    '"connected"': '"已连接"',
    '"failed - wrong key?"': '"失败 —— 密码不对？"',
    '"no answer - tap to retry"': '"没有回应 —— 点一下重试"',
    '"radio busy - retrying"': '"无线被占用 —— 正在重试"',
    '"scan stalled - tap to retry"': '"扫描卡住 —— 点一下重试"',
    '"tap to join  -  hold to forget"': '"轻点加入  -  长按忘记"',

    # ── hosts ────────────────────────────────────────────────
    '"Hosts"': '"设备"',
    '"no paired host yet"': '"还没有配对过的设备"',
    '"accept any"': '"接受任何设备"',
    '"name this host"': '"给这台设备起名"',
    '"tap to switch  -  " LV_SYMBOL_EDIT " to name"':
        '"轻点切换  -  " LV_SYMBOL_EDIT " 改名"',

    # ── the recorder ─────────────────────────────────────────
    '"REC"': '"REC"',                       # stays: reads as a status light
    '"recording"': '"录音中"',
    '"tap = stop"': '"轻点停止"',
    '"hold = export"': '"长按切换语言"',    # what the long press actually does
    '"saved  %d on device"': '"已保存 %d 条"',
    '"%lu min free"': '"剩余 %lu 分钟"',
    '"tap = retry"': '"轻点重试"',
    '"no space / mic failed"': '"空间不足 / 麦克风失败"',
    '"no recording partition"': '"没有录音分区"',
    '"export over USB"': '"通过 USB 导出"',

    # ── USB export ───────────────────────────────────────────
    '"USB export"': '"USB 导出"',
    '"%d recordings"': '"%d 条录音"',
    '"%d files"': '"%d 个文件"',
    '"nothing to send"': '"没有可导出的"',
    '"ejected"': '"已弹出"',
    '"waiting"': '"等待中"',
    '"plug into a PC"': '"插到电脑上"',
    '"tap to become a USB drive"': '"轻点变身为 U 盘"',
    '"record something first"': '"先录一段"',
    '"tap to go back to COM (restarts)"': '"轻点回到串口（会重启）"',
    '"eject on the PC, or tap to finish"': '"在电脑上弹出，或轻点结束"',
    '"could not switch to USB"': '"无法切换到 USB"',

    # ── the air mouse ────────────────────────────────────────
    '"aim to move"': '"指向即移动"',
    '"tap twice, then drag"': '"点两下再拖动"',
    '"hold still"': '"保持不动"',
    '"speed %d/%d"': '"速度 %d/%d"',
    '"forgot %d device%s"': '"已忘记 %d 台设备"',
    '"right click"': '"右键"',
    '"two-finger scroll"': '"双指滚动"',
    '"2 fingers = right click"': '"双指 = 右键"',
    '"match this on your phone"': '"在手机上核对这串数字"',
    '"drag"': '"拖动"',
    '"scroll"': '"滚动"',
    '"move"': '"移动"',
    '"click"': '"单击"',
    '"advertising"': '"等待连接"',

    # ── the Excel keys ───────────────────────────────────────
    '"Excel keys"': '"Excel 按键"',
    '"not connected"': '"未连接"',

    # ── text injection ───────────────────────────────────────
    '"Type"': '"文字注入"',
    '"no snippets"': '"没有片段"',

    # ── presentation remote ──────────────────────────────────
    '"next"': '"下一页"',
    '"prev"': '"上一页"',
    '"black"': '"黑屏"',
    '"start"': '"开始"',

    # ── clock, timer, stopwatch, alarm ───────────────────────
    '"tap to start"': '"轻点开始"',
    '"tap to stop"': '"轻点停止"',
    '"done"': '"完成"',
    '"tap to pause"': '"轻点暂停"',
    '"tap to start - hold to reset"': '"轻点开始 - 长按归零"',
    '"tap to stop - hold to lap"': '"轻点停止 - 长按计次"',
    '"tap to go - hold to reset"': '"轻点继续 - 长按归零"',
    '"lap"': '"计次"',
    '"on"': '"开"',
    '"off"': '"关"',
    '"hour"': '"时"',
    '"min"': '"分"',

    # ── the games ────────────────────────────────────────────
    '"Games"': '"游戏"',
    '"Bricks"': '"打砖块"',
    '"Pinball"': '"弹珠台"',
    '"Marble"': '"滚珠迷宫"',
    '"Pop"': '"气泡纸"',
    '"game over"': '"游戏结束"',
    '"all clear!"': '"全部通过！"',

    # ── the second pass: what the audit turned up still in Latin ──
    # 🚨 These were missed the first time, and why is worth recording: they are
    #    format strings, so they read like code rather than like copy. Every one
    #    here was found by walking the sources for string literals containing
    #    two or more letters — which is how to check this, rather than looking at
    #    screenshots and hoping.
    '"%d saved"': '"%d 条已保存"',
    '"%d paired"': '"%d 台已配对"',
    '"%d%%  charging"': '"%d%%  充电中"',
    '"%d%%  measuring"': '"%d%%  测量中"',
    '"%d%%  %dh %02dm"': '"%d%%  %d 时 %02d 分"',
    '"%ds"': '"%d 秒"',
    '"%d dBm  -  connected"': '"%d dBm  -  已连接"',
    '"%d dBm  -  saved"': '"%d dBm  -  已保存"',
    # the settings page's own title, a separate string from the app's .name
    '"Settings"': '"设置"',
    '"ON"': '"开"',
    '"OFF"': '"关"',

    # ── the third pass: the Excel keys' hints ──────────────────
    # 🚨 The key names themselves stay Latin on purpose — they are the letters
    #    printed on a keyboard, and "F4" rendered as "功能键四" would be worse
    #    than useless when someone is looking for the key. What follows a press
    #    is a description, and that is copy like any other.
    '"absolute ref"': '"绝对引用"',
    '"edit cell"': '"编辑单元格"',
    '"go to"': '"定位"',
    '"save as"': '"另存为"',
    '"cancel"': '"取消"',
    '"today"': '"今天"',
    '"trace prec"': '"追踪引用"',
    '"help"': '"帮助"',
    '"close"': '"关闭"',
    '"sent  %s"': '"已发送 %s"',
}


def files():
    out = []
    for root, _, names in os.walk(os.path.join(REPO, "main")):
        for n in names:
            if n.endswith(".c"):
                out.append(os.path.join(root, n))
    return sorted(out)


def main():
    check = "--check" in sys.argv
    applied = {k: 0 for k in TABLE}
    pending = {k: 0 for k in TABLE}
    touched = []

    for path in files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        orig = text
        for src, dst in TABLE.items():
            if dst != src and dst in text:
                applied[src] += text.count(dst)
            # 🚨 Count rather than just replace: an entry that matches nothing at
            #    all is either a typo or a string that has moved, and both are
            #    worth being told about.
            if src in text and dst != src:
                pending[src] += text.count(src)
                text = text.replace(src, dst)
        if text != orig:
            touched.append(os.path.relpath(path, REPO))
            if not check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)

    # 🚨 Idempotent on purpose. The first version reported every entry as a
    #    problem once the pass had been run, because the English it was looking
    #    for was of course gone — and a check that cries wolf after doing its job
    #    is one people learn to ignore. An entry counts as fine if its Chinese is
    #    already in place.
    # 🚨 And an entry that maps a string to itself is not a mistake: that is how
    #    "this one stays Latin" is written down. REC is one.
    unknown = [k for k in TABLE
               if TABLE[k] != k and not applied[k] and not pending[k]]
    print("  %d files %s · %d strings %s · %d already done"
          % (len(touched), "would change" if check else "changed",
             sum(pending.values()), "to apply" if check else "applied",
             sum(applied.values())))
    if unknown:
        print("  ✗ these entries match nothing and their translation is not in the")
        print("    sources either — a typo in the table, or the string moved:")
        for k in unknown:
            print("      " + k)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
