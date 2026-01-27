import fonts.vga2_16x32 as font
import fonts.vga2_8x8 as small_font
import fonts.vga2_bold_16x32 as font_bold
import json
import machine
import micropython_time
import st7789 as st7789 
import tft_config as tft_config
import utime

from diyables import diyables_button
from totp import totp

LED_BLINK_UNDER_SECONDS = const(6)
VOLT_COMPENSATOR = const(3)
LIGHTSLEEP_TIME_MS = const(500)

def sync_time(use_ntp: bool) -> bool:
    from TPicoESPC3 import ESPC3

    esp = ESPC3()
    rtc = machine.RTC()

    try:
        if use_ntp:
            # disable BLE
            esp.send('AT+BLEINIT=0')

            # station mode
            esp.send('AT+CWMODE=1')

            known_aps = json.loads(open("wifi.json", "r").read())
            available_aps = esp.get_AP()

            for ap in available_aps:
                found_ap = None
                for x in known_aps:
                    if str(x['ssid']).lower() == str(ap[1]).lower():
                        found_ap = x
                        break

                if found_ap:
                    esp.join_ap(found_ap['ssid'], found_ap['key'])
                    if esp.is_connected:
                        break

            if not esp.is_connected:
                return False

            # set sntp settings
            esp.send('AT+CIPSNTPCFG=1,0,"0.pool.ntp.org","1.pool.ntp.org","time.google.com"')

            # wait for time sync
            utime.sleep_ms(2500)

            # disconnect from AP and disable WiFi
            esp.send('AT+CWQAP')
            esp.send('AT+CWMODE=0')        

        # query the time and sync RTC if possible
        sntp_response = esp.send('AT+CIPSNTPTIME?')
        for line in sntp_response.split(b"\r\n"):
            if line.startswith(b'+CIPSNTPTIME:'):
                str_time = str(line[13:], "utf-8")
                if str_time[8] == ' ':
                    str_time = str_time[:8] + '0' + str_time[9:]

                parsed_dt = micropython_time.strptime(str_time, "%a %b %d %H:%M:%S %Y")
                if parsed_dt:
                    rtc.datetime((parsed_dt[0], parsed_dt[1] + 1, parsed_dt[2], parsed_dt[6], parsed_dt[3], parsed_dt[4], parsed_dt[5], 0))
                break     

    except Exception as e:
        print('Error:', e)
        return False

    return True

adc = machine.ADC(26)
codes = json.loads(open("codes.json", "r").read())

selected_idx = 0
progress_width = 0
should_be_cleared = True
switched_off = False
text_color = st7789.GREEN # default text color

tft = tft_config.config(1)

def center(text, tcolor = st7789.GREEN):
    length = len(text)
    tft.text(
        font,
        text,
        tft.width() // 2 - length // 2 * font.WIDTH,
        tft.height() // 2 - font.HEIGHT,
        tcolor,
        st7789.BLACK)

tft.init()
display_width = tft.width()

center('WAIT', st7789.WHITE)
time_synced = sync_time(True)

tft.fill(st7789.BLACK)
if not time_synced:
    center('FAILED', st7789.RED)
else:
    center('SUCCESS')

utime.sleep_ms(2000)
tft.fill(st7789.BLACK)

if not time_synced:
    switched_off = True
    tft.off()

text_color = st7789.GREEN if time_synced else st7789.WHITE
    
button6 = diyables_button.Button(6)
button7 = diyables_button.Button(7)

rtc = machine.RTC()

while True:
    button6.loop()
    if button6.is_pressed():
        if switched_off:
            should_be_cleared = True
            tft.on()
        else:
            tft.fill(st7789.BLACK)
            tft.off()
        
        switched_off = not switched_off

    # do nothing if the display is switched off or the time is not synced
    if not switched_off:
        button7.loop()
        if button7.is_pressed():
            selected_idx = (selected_idx + 1) % len(codes)
            should_be_cleared = True

        # TODO: Check for correct VOLT_COMPENSATOR value
        vc = adc.read_u16() * 3.3 * 3 / 65535 - VOLT_COMPENSATOR

        dt = rtc.datetime()

        code = codes[selected_idx]
        synchronised_time = int(utime.time_ns() // 1000000000)

        (password, expiry) = totp(synchronised_time,
                                code['key'],
                                step_secs=code['step'],
                                digits=code['digits'])

        if progress_width == display_width:
            tft.fill_rect(0, 125, display_width, 10, st7789.BLACK)

        if should_be_cleared:
            should_be_cleared = False
            tft.fill(st7789.BLACK)

        tft.text(small_font, f'{dt[4]:02d}:{dt[5]:02d}:{dt[6]:02d}, Vsys {vc:.1f}V', 80, 0, text_color, st7789.BLACK)

        tft.text(font, code['name'], 10, 20, text_color, st7789.BLACK)
        if expiry <= LED_BLINK_UNDER_SECONDS and expiry % 2:
            tft.text(font_bold, password, 10, 70, st7789.RED, st7789.BLACK)
        else:
            tft.text(font_bold, password, 10, 70, text_color, st7789.BLACK)

        progress_width = display_width - (display_width // code['step'] * (expiry - 1))
        tft.fill_rect(0, 125, progress_width, 10, text_color)
    
        utime.sleep_ms(250)
    else:
        machine.lightsleep(LIGHTSLEEP_TIME_MS)
