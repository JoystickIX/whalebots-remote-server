import asyncio
import pyautogui
import pygetwindow as gw
from PIL import ImageGrab
import os
import requests
import socket
import random
import logging
import ctypes
import tkinter as tk
from tkinter import messagebox

# =====================================
# LOGGING
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("client.log", encoding="utf-8")
    ]
)
log = logging.getLogger("WhaleBots")

DEBUG = False

# =====================================
# CONFIG
# =====================================

CONFIG_FILE = "config.txt"

def load_server_url():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            url = f.read().strip()
            if url:
                log.info(f"SERVER URL loaded: {url}")
                return url
    log.warning("config.txt not found. Using default SERVER_URL.")
    return "https://whalebots-remote-server.onrender.com"

SERVER_URL = load_server_url()
CLIENT_ID  = socket.gethostname()

# =====================================
# PAIR CODE
# =====================================

PAIR_FILE = "pair_code.txt"

def load_or_create_pair_code():
    if os.path.exists(PAIR_FILE):
        with open(PAIR_FILE, "r") as f:
            code = f.read().strip()
        if code.isdigit() and len(code) == 6:
            log.info(f"PAIR CODE loaded: {code}")
            return code
    code = str(random.randint(100000, 999999))
    with open(PAIR_FILE, "w") as f:
        f.write(code)
    log.info(f"PAIR CODE generated: {code}")
    return code

PAIR_CODE = load_or_create_pair_code()

# =====================================
# LICENSE
# =====================================

LICENSE_FILE = "license.txt"

def load_license_key():
    if not os.path.exists(LICENSE_FILE):
        show_popup_error(
            "License Missing",
            "license.txt not found.\n\nCreate license.txt and paste your license key inside."
        )
        raise SystemExit(1)

    with open(LICENSE_FILE, "r") as f:
        key = f.read().strip().upper()

    if not key:
        show_popup_error(
            "License Empty",
            "license.txt is empty.\n\nPaste your license key inside license.txt."
        )
        raise SystemExit(1)

    return key

# =====================================
# POPUPS
# =====================================

def show_popup_error(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(f"WhaleBots — {title}", message)
    root.destroy()

def show_popup_info(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(f"WhaleBots — {title}", message)
    root.destroy()

def show_pair_code(code):
    show_popup_info(
        "Pair Code",
        f"Your Pair Code:\n\n{code}\n\nSend this to the bot owner to get connected."
    )

# =====================================
# STARTUP LOG
# =====================================

LICENSE_KEY = load_license_key()

log.info("=" * 40)
log.info("WhaleBots Remote Client")
log.info("=" * 40)
log.info(f"PC NAME   : {CLIENT_ID}")
log.info(f"PAIR CODE : {PAIR_CODE}")
log.info(f"LICENSE   : ****-****-****-{LICENSE_KEY[-4:]}")
log.info("=" * 40)

# =====================================
# STATE
# =====================================

active_game = None

# =====================================
# SERVER COMMUNICATION
# =====================================

async def validate_license():
    log.info("Validating license...")
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.post(
                f"{SERVER_URL}/license/validate",
                json={
                    "key":       LICENSE_KEY,
                    "client_id": CLIENT_ID
                },
                timeout=10
            )
        )
        result = response.json()

        if result.get("valid"):
            expires  = result.get("expires", "lifetime")
            customer = result.get("customer", "")
            log.info("LICENSE VALID ✅")
            log.info(f"Customer : {customer}")
            log.info(f"Expires  : {expires}")
            return True
        else:
            reason = result.get("reason", "Unknown error.")
            log.critical(f"LICENSE REJECTED ❌ — {reason}")
            show_popup_error("License Rejected", f"License rejected:\n\n{reason}")
            return False

    except Exception as e:
        log.critical(f"LICENSE CHECK FAILED: {e}")
        show_popup_error("Connection Failed", f"Could not reach license server:\n\n{e}")
        return False

async def register_client(retries=3, delay=5):
    for attempt in range(1, retries + 1):
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.post(
                    f"{SERVER_URL}/register",
                    json={
                        "client_id": CLIENT_ID,
                        "pair_code": PAIR_CODE
                    },
                    timeout=10
                )
            )
            log.info(f"REGISTERED: {CLIENT_ID}")
            return True
        except Exception as e:
            log.warning(f"REGISTER attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(delay)
    log.error("REGISTER FAILED after all attempts.")
    return False

async def send_status(message):
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.post(
                f"{SERVER_URL}/status/{CLIENT_ID}",
                json={"message": message},
                timeout=10
            )
        )
        log.info(f"STATUS SENT: {message}")
    except Exception as e:
        log.error(f"SEND STATUS ERROR: {e}")

async def send_image(path):
    try:
        def _upload():
            with open(path, "rb") as f:
                requests.post(
                    f"{SERVER_URL}/upload/{CLIENT_ID}",
                    files={"file": f},
                    timeout=10
                )
        await asyncio.get_event_loop().run_in_executor(None, _upload)
        log.info(f"IMAGE SENT: {path}")
    except Exception as e:
        log.error(f"UPLOAD ERROR: {e}")

async def get_command():
    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.get(
                f"{SERVER_URL}/command/{CLIENT_ID}",
                timeout=10
            )
        )
        return response.json().get("command")
    except Exception as e:
        log.error(f"GET COMMAND ERROR: {e}")
        return None

# =====================================
# WINDOW UTILITIES
# =====================================

_hwnd_cache = {}

def force_foreground(hwnd):
    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9
    try:
        current_thread = kernel32.GetCurrentThreadId()
        fg_hwnd        = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        attached  = user32.AttachThreadInput(current_thread, fg_thread, True)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetFocus(hwnd)
        if attached:
            user32.AttachThreadInput(current_thread, fg_thread, False)
        return user32.GetForegroundWindow() == hwnd
    except Exception as e:
        log.error(f"FORCE FOREGROUND ERROR: {e}")
        return False

def get_hwnd_by_keywords(label, keywords):
    global _hwnd_cache
    user32 = ctypes.windll.user32

    cached = _hwnd_cache.get(label)
    if cached is not None:
        if user32.IsWindow(cached):
            return cached
        else:
            log.info(f"Cached HWND [{label}] invalid. Re-scanning.")
            _hwnd_cache[label] = None

    found_hwnd = []

    def enum_callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower()
                if any(kw in title for kw in keywords):
                    found_hwnd.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int)
    )
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

    if found_hwnd:
        _hwnd_cache[label] = found_hwnd[0]
        log.info(f"HWND CACHED [{label}]: {found_hwnd[0]}")
        return found_hwnd[0]

    return None

def get_emulator_window(number):
    all_titles = gw.getAllTitles()
    target     = str(number)

    if DEBUG:
        log.debug("ALL DETECTED WINDOWS:")
        for title in all_titles:
            log.debug(repr(title))

    for title in all_titles:
        clean = title.strip().lower()
        if not clean:
            continue
        if target in clean:
            windows = gw.getWindowsWithTitle(title)
            if windows:
                log.info(f"FOUND EMULATOR WINDOW: {title}")
                return windows[0]
    return None

async def focus_whalebots():
    all_titles    = gw.getAllTitles()
    target_window = None

    for title in all_titles:
        lower = title.lower()
        if "rise of kingdoms bot" in lower or "call of dragons bot" in lower:
            windows = gw.getWindowsWithTitle(title)
            if windows:
                target_window = windows[0]
                break

    if not target_window:
        log.warning("WHALEBOTS WINDOW NOT FOUND")
        return None

    hwnd = get_hwnd_by_keywords(
        "whalebots",
        ["rise of kingdoms bot", "call of dragons bot"]
    )
    if not hwnd:
        log.warning("WHALEBOTS HWND NOT FOUND")
        return None

    if target_window.isMinimized:
        target_window.restore()
        await asyncio.sleep(0.3)

    force_foreground(hwnd)
    await asyncio.sleep(0.4)

    pyautogui.moveTo(
        x=target_window.left + 120,
        y=target_window.top  + 120
    )
    await asyncio.sleep(0.2)

    return target_window

async def focus_launcher():
    all_titles    = gw.getAllTitles()
    target_window = None

    for title in all_titles:
        lower = title.lower()
        if (
            "whale" in lower and
            "rise of kingdoms bot" not in lower and
            "call of dragons bot"  not in lower
        ):
            windows = gw.getWindowsWithTitle(title)
            if windows:
                target_window = windows[0]
                break

    if not target_window:
        log.warning("LAUNCHER WINDOW NOT FOUND")
        return None

    hwnd = get_hwnd_by_keywords("launcher", ["whale bots", "whalebots"])

    if not hwnd:
        try:
            if target_window.isMinimized:
                target_window.restore()
                await asyncio.sleep(0.3)
            target_window.activate()
            await asyncio.sleep(0.8)
        except Exception as e:
            log.error(f"LAUNCHER FALLBACK FOCUS ERROR: {e}")
        return target_window

    if target_window.isMinimized:
        target_window.restore()
        await asyncio.sleep(0.3)

    force_foreground(hwnd)
    await asyncio.sleep(0.6)

    log.info(f"LAUNCHER FOCUSED: {target_window.title}")
    return target_window

async def focus_window_safe(window):
    try:
        if window.isMinimized:
            window.restore()
            await asyncio.sleep(0.3)
        window.activate()
        await asyncio.sleep(0.8)
        log.info(f"SAFE FOCUS: {window.title}")
    except Exception as e:
        log.error(f"SAFE FOCUS ERROR: {e}")

# =====================================
# WHALEBOTS FINDER
# =====================================

def find_whalebots():
    desktop       = os.path.join(os.path.expanduser("~"), "Desktop")
    possible_files = [
        "WhaleBots.lnk",
        "Whale Bots.lnk",
        "WhaleBots.exe",
        "Whale Bots.exe"
    ]
    for file in possible_files:
        full_path = os.path.join(desktop, file)
        if os.path.exists(full_path):
            return full_path
    return None

# =====================================
# COMMANDS
# =====================================

async def ROK():
    global active_game
    try:
        if active_game == "cod":
            await send_status("⚠️ COD is running. Closing everything first...")
            await close_all()
            await send_status("✅ Closed. Launching ROK now...")

        whalebot_path = find_whalebots()
        if not whalebot_path:
            await send_status("❌ WhaleBots not found on Desktop.")
            return

        os.startfile(whalebot_path)
        await asyncio.sleep(8)

        window = await focus_launcher()
        if not window:
            await send_status("❌ Launcher window not found.")
            return

        all_titles = gw.getAllTitles()
        still_open = any("whale" in t.lower() for t in all_titles)
        if not still_open:
            await send_status("❌ Launcher closed unexpectedly.")
            return

        click_x = window.left + 162
        click_y = window.top  + 194
        log.info(f"ROK CLICK → x={click_x}, y={click_y}")
        pyautogui.click(x=click_x, y=click_y)
        active_game = "rok"
        await send_status("✅ ROK launched.")

    except Exception as e:
        log.error(f"ROK ERROR: {e}")

async def COD():
    global active_game
    try:
        if active_game == "rok":
            await send_status("⚠️ ROK is running. Closing everything first...")
            await close_all()
            await send_status("✅ Closed. Launching COD now...")

        whalebot_path = find_whalebots()
        if not whalebot_path:
            await send_status("❌ WhaleBots not found on Desktop.")
            return

        os.startfile(whalebot_path)
        await asyncio.sleep(8)

        window = await focus_launcher()
        if not window:
            await send_status("❌ Launcher window not found.")
            return

        all_titles = gw.getAllTitles()
        still_open = any("whale" in t.lower() for t in all_titles)
        if not still_open:
            await send_status("❌ Launcher closed unexpectedly.")
            return

        click_x = window.left + 476
        click_y = window.top  + 191
        log.info(f"COD CLICK → x={click_x}, y={click_y}")
        pyautogui.click(x=click_x, y=click_y)
        active_game = "cod"
        await send_status("✅ COD launched.")

    except Exception as e:
        log.error(f"COD ERROR: {e}")

async def screen(target="bot"):
    try:
        if target == "bot":
            window = await focus_whalebots()
            if not window:
                await send_status("❌ WhaleBots window not found.")
                return
            screenshot = ImageGrab.grab(bbox=(
                window.left, window.top,
                window.right, window.bottom
            ))
            filename = "bot_window.png"
            screenshot.save(filename)
            await send_image(filename)
            await send_status("📸 WhaleBots screenshot sent.")
            return

        target_window = get_emulator_window(target)
        if not target_window:
            await send_status(f"❌ Emulator {target} not found.")
            return

        await focus_window_safe(target_window)
        screenshot = ImageGrab.grab(bbox=(
            target_window.left, target_window.top,
            target_window.right, target_window.bottom
        ))
        filename = f"emulator_{target}.png"
        screenshot.save(filename)
        await send_image(filename)
        await send_status(f"📸 Emulator {target} screenshot sent.")

    except Exception as e:
        log.error(f"SCREEN ERROR: {e}")

async def tick(number):
    try:
        log.info(f"TICK START — bot {number}")

        visible_rows = 6
        start_y      = 42
        spacing      = 22

        window = await focus_whalebots()
        if not window:
            await send_status("❌ Bot window not found.")
            return

        for _ in range(25):
            pyautogui.scroll(700)
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.3)

        successful_scrolls = 0

        if number > visible_rows:
            scroll_steps = number - visible_rows

            for _ in range(scroll_steps):
                before = ImageGrab.grab(bbox=(
                    window.right - 12, window.top   + 25,
                    window.right - 4,  window.bottom - 25
                ))

                pyautogui.scroll(-700)
                await asyncio.sleep(0.4)

                after = ImageGrab.grab(bbox=(
                    window.right - 12, window.top   + 25,
                    window.right - 4,  window.bottom - 25
                ))

                if list(before.getdata()) == list(after.getdata()):
                    existing = visible_rows + successful_scrolls
                    await send_status(
                        f"❌ Bot {number} does not exist.\n"
                        f"Only {existing} bots available."
                    )
                    return

                successful_scrolls += 1

        window = await focus_whalebots()
        if not window:
            await send_status("❌ Bot window lost before tick click.")
            return

        visible_index = visible_rows if number > visible_rows else number
        checkbox_y    = start_y + ((visible_index - 1) * spacing)
        click_x       = window.left + 14
        click_y       = window.top  + checkbox_y

        log.info(f"TICK CLICK → x={click_x}, y={click_y} (row {visible_index}, bot {number})")

        await asyncio.sleep(0.2)
        pyautogui.click(x=click_x, y=click_y)
        await asyncio.sleep(0.2)

        await send_status(f"✅ Ticked bot {number}")

    except Exception as e:
        log.error(f"TICK ERROR: {e}")

async def kill_process(name):
    try:
        await asyncio.create_subprocess_shell(
            f'taskkill /f /im "{name}"',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
    except Exception as e:
        log.error(f"KILL PROCESS ERROR ({name}): {e}")

async def close_all():
    log.info("CLOSE ALL — killing all processes")
    await asyncio.gather(
        kill_process("WhaleBots.exe"),
        kill_process("HD-Player.exe"),
        kill_process("Bluestacks.exe"),
        kill_process("BlueStacks.exe"),
        kill_process("BlueStacks X.exe")
    )
    await asyncio.sleep(3)
    log.info("CLOSE ALL — done")

async def close_account(window, number, visible_rows, start_y, spacing):
    visible_index = number

    if number > visible_rows:
        scroll_steps = number - visible_rows
        for _ in range(scroll_steps):
            pyautogui.scroll(-700)
            await asyncio.sleep(0.2)
        visible_index = visible_rows

    row_y   = start_y + ((visible_index - 1) * spacing)
    click_x = window.left + 40
    click_y = window.top  + row_y

    pyautogui.rightClick(x=click_x, y=click_y)
    await asyncio.sleep(0.3)

    pyautogui.click(x=click_x + 40, y=click_y + 65)
    await asyncio.sleep(0.5)

    pyautogui.click(x=window.left + 260, y=window.top + 360)
    await send_status(f"🛑 Closed bot {number}")

async def close(target="all"):
    global active_game
    try:
        if target == "all":
            await close_all()
            active_game = None
            await send_status("🛑 All WhaleBots and BlueStacks windows closed.")
            return

        window = await focus_whalebots()
        if not window:
            await send_status("❌ Bot window not found.")
            return

        visible_rows = 6
        start_y      = 42
        spacing      = 22

        pyautogui.moveTo(window.left + 120, window.top + 120)
        await asyncio.sleep(0.5)

        for _ in range(25):
            pyautogui.scroll(700)
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.5)

        await close_account(window, int(target), visible_rows, start_y, spacing)

    except Exception as e:
        log.error(f"CLOSE ERROR: {e}")

# =====================================
# MAIN LOOP
# =====================================

async def main():
    log.info(f"CLIENT ONLINE: {CLIENT_ID}")

    # Step 1 — Validate license
    license_ok = await validate_license()
    if not license_ok:
        log.critical("Shutting down — license check failed.")
        return

    # Step 2 — Register with server
    registered = await register_client()
    if not registered:
        log.critical("Shutting down — could not register with server.")
        return

    # Step 3 — Show pair code AFTER registration
    show_pair_code(PAIR_CODE)

    last_register_time  = asyncio.get_event_loop().time()
    REREGISTER_INTERVAL = 300

    log.info("Listening for commands...")

    try:
        while True:
            now = asyncio.get_event_loop().time()
            if now - last_register_time >= REREGISTER_INTERVAL:
                log.info("Re-registering with server...")
                await register_client()
                last_register_time = now

            command = await get_command()

            if command:
                log.info(f"COMMAND RECEIVED: {command}")

                if command == "rok":
                    await ROK()

                elif command == "cod":
                    await COD()

                elif command.startswith("screen"):
                    split = command.split(" ")
                    await screen(split[1] if len(split) > 1 else "bot")

                elif command.startswith("tick"):
                    split = command.split(" ")
                    if len(split) > 1:
                        await tick(int(split[1]))

                elif command.startswith("close"):
                    split = command.split(" ")
                    await close(split[1] if len(split) > 1 else "all")

            await asyncio.sleep(2)

    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Client shutting down. Goodbye.")

asyncio.run(main())
