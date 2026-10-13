import ctypes
import io
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# =====================================================================
# 1. AUTO-INSTALL DEPENDENCIES (PyBoy, Pillow, NumPy)
# =====================================================================
def ensure_dependencies():
    required_packages = {
        "pyboy": "pyboy",
        "PIL": "pillow",
        "numpy": "numpy",
    }
    for module_name, pip_name in required_packages.items():
        try:
            __import__(module_name)
        except ImportError:
            print(f"[Cat's GB Emulator 0.1] Auto-installing '{pip_name}'...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name]
            )


ensure_dependencies()

from PIL import Image, ImageTk
from pyboy import PyBoy

try:
    import sdl2
    HAS_SDL2 = True
except ImportError:
    HAS_SDL2 = False


# =====================================================================
# 2. EMULATOR CONFIGURATION CONSTANTS
# =====================================================================
APP_TITLE = "Cat's GB Emulator 0.1"
WINDOW_WIDTH = 600
WINDOW_HEIGHT = 400

GB_NATIVE_W = 160
GB_NATIVE_H = 144
DEFAULT_SCALE = 2  # 320x288 fits cleanly inside the 600x400 DeSmuME viewport

TARGET_FPS = 60
EMULATION_SPEED = 1  # 1 = Native Game Boy Speed
AUDIO_ENGINE = True  # Enable PyBoy APU + SDL2 stereo streaming


# =====================================================================
# 3. DESMUME-STYLE TKINTER GUI & PYBOY HOST
# =====================================================================
class CatsGBEmulator:
    def __init__(self, root: tk.Tk, initial_rom: str | None = None):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.configure(bg="#D4D0C8")  # Classic DeSmuME Win32 gray

        # Emulator state
        self.pyboy: PyBoy | None = None
        self.rom_path: str | None = None
        self.running: bool = False
        self.paused: bool = False
        self.speed: int = EMULATION_SPEED
        self.target_fps: int = TARGET_FPS
        self.audio_enabled: bool = AUDIO_ENGINE
        self.scale: int = DEFAULT_SCALE
        self.filter_nearest: bool = True

        # Save state slot in memory
        self.memory_state: io.BytesIO | None = None

        # Timing & FPS tracking
        self.last_frame_time: float = time.perf_counter()
        self.fps_counter: int = 0
        self.fps_timer: float = time.perf_counter()
        self.current_fps: float = 0.0

        # Audio device handle (SDL2)
        self.audio_device: int = 0

        # Input key mapping (Keyboard -> Game Boy buttons)
        self.key_map = {
            "Up": "up",
            "Down": "down",
            "Left": "left",
            "Right": "right",
            "z": "a",
            "Z": "a",
            "x": "b",
            "X": "b",
            "Return": "start",
            "BackSpace": "select",
            "Shift_R": "select",
        }
        self.pressed_keys: set[str] = set()

        # Build DeSmuME UI components
        self._build_menu()
        self._build_toolbar()
        self._build_viewport()
        self._build_statusbar()
        self._bind_inputs()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if initial_rom and os.path.isfile(initial_rom):
            self.load_rom(initial_rom)

    # -----------------------------------------------------------------
    # DeSmuME GUI Layout Construction
    # -----------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Open ROM...", accelerator="Ctrl+O", command=self.open_rom_dialog
        )
        file_menu.add_command(
            label="Close ROM", command=self.stop_emulation
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Quick Save State", accelerator="F5", command=self.save_state
        )
        file_menu.add_command(
            label="Quick Load State", accelerator="F8", command=self.load_state
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Save Screenshot...", accelerator="F12", command=self.save_screenshot
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # Emulation Menu
        emu_menu = tk.Menu(menubar, tearoff=0)
        emu_menu.add_command(
            label="Pause / Resume", accelerator="Space", command=self.toggle_pause
        )
        emu_menu.add_command(
            label="Reset GameBoy", accelerator="Ctrl+R", command=self.reset_rom
        )
        emu_menu.add_separator()

        speed_menu = tk.Menu(emu_menu, tearoff=0)
        speed_menu.add_command(
            label="1x - GameBoy Native (60 FPS)", command=lambda: self.set_speed(1)
        )
        speed_menu.add_command(
            label="2x - Fast Forward (120 FPS)", command=lambda: self.set_speed(2)
        )
        speed_menu.add_command(
            label="0x - Unlimited (Uncapped)", command=lambda: self.set_speed(0)
        )
        emu_menu.add_cascade(label="Speed (Frame Limit)", menu=speed_menu)
        menubar.add_cascade(label="Emulation", menu=emu_menu)

        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(
            label="LCD Size 1x (160x144)", command=lambda: self.set_scale(1)
        )
        view_menu.add_command(
            label="LCD Size 2x (320x288 - Default)", command=lambda: self.set_scale(2)
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Toggle Nearest / Bilinear Filter", command=self.toggle_filter
        )
        menubar.add_cascade(label="View", menu=view_menu)

        # Config Menu
        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(
            label="Toggle Audio Engine (ON/OFF)", command=self.toggle_audio
        )
        config_menu.add_command(
            label="Controls Info", command=self.show_controls_dialog
        )
        menubar.add_cascade(label="Config", menu=config_menu)

        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_toolbar(self):
        toolbar = tk.Frame(
            self.root, bg="#D4D0C8", bd=1, relief=tk.RAISED, padx=4, pady=3
        )
        toolbar.pack(side=tk.TOP, fill=tk.X)

        btn_style = {
            "bg": "#E4E0D8",
            "fg": "#000000",
            "activebackground": "#EFECE6",
            "relief": tk.RAISED,
            "bd": 1,
            "font": ("Tahoma", 8),
            "padx": 6,
            "pady": 2,
        }

        tk.Button(toolbar, text="Open ROM", command=self.open_rom_dialog, **btn_style).pack(
            side=tk.LEFT, padx=2
        )
        self.btn_pause = tk.Button(
            toolbar, text="Pause", command=self.toggle_pause, state=tk.DISABLED, **btn_style
        )
        self.btn_pause.pack(side=tk.LEFT, padx=2)

        self.btn_reset = tk.Button(
            toolbar, text="Reset", command=self.reset_rom, state=tk.DISABLED, **btn_style
        )
        self.btn_reset.pack(side=tk.LEFT, padx=2)

        # Vertical Divider
        tk.Frame(toolbar, width=2, bd=1, relief=tk.SUNKEN, bg="#ACA899").pack(
            side=tk.LEFT, fill=tk.Y, padx=5, pady=2
        )

        tk.Button(toolbar, text="Save State", command=self.save_state, **btn_style).pack(
            side=tk.LEFT, padx=2
        )
        tk.Button(toolbar, text="Load State", command=self.load_state, **btn_style).pack(
            side=tk.LEFT, padx=2
        )

        # Vertical Divider
        tk.Frame(toolbar, width=2, bd=1, relief=tk.SUNKEN, bg="#ACA899").pack(
            side=tk.LEFT, fill=tk.Y, padx=5, pady=2
        )

        self.btn_speed = tk.Button(
            toolbar,
            text="Speed: GameBoy (1x)",
            command=self.cycle_speed,
            **btn_style,
        )
        self.btn_speed.pack(side=tk.LEFT, padx=2)

        self.btn_audio = tk.Button(
            toolbar,
            text=f"Audio: {'ON' if self.audio_enabled else 'OFF'}",
            command=self.toggle_audio,
            **btn_style,
        )
        self.btn_audio.pack(side=tk.LEFT, padx=2)

    def _build_viewport(self):
        # Outer recessed container like DeSmuME's main panel
        outer_frame = tk.Frame(
            self.root, bg="#808080", bd=2, relief=tk.SUNKEN
        )
        outer_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.canvas = tk.Canvas(
            outer_frame,
            bg="#181818",
            highlightthickness=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.tk_image = None
        self.canvas_image_id = None
        self.root.after(50, self._draw_idle_screen)

    def _build_statusbar(self):
        status_frame = tk.Frame(self.root, bg="#D4D0C8", bd=1, relief=tk.FLAT)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=1)

        cell_style = {
            "bg": "#D4D0C8",
            "fg": "#000000",
            "bd": 1,
            "relief": tk.SUNKEN,
            "font": ("Tahoma", 8),
            "anchor": tk.W,
            "padx": 6,
        }

        self.lbl_state = tk.Label(status_frame, text="Status: NO ROM", width=16, **cell_style)
        self.lbl_state.pack(side=tk.LEFT, fill=tk.X)

        self.lbl_fps = tk.Label(
            status_frame, text="FPS: 0.0 / 60 (Speed: GameBoy)", width=28, **cell_style
        )
        self.lbl_fps.pack(side=tk.LEFT, fill=tk.X)

        self.lbl_audio = tk.Label(
            status_frame,
            text=f"Audio Engine: {'TRUE' if self.audio_enabled else 'FALSE'}",
            width=18,
            **cell_style,
        )
        self.lbl_audio.pack(side=tk.LEFT, fill=tk.X)

        self.lbl_rom = tk.Label(status_frame, text="ROM: None", **cell_style)
        self.lbl_rom.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _draw_idle_screen(self):
        if self.running:
            return
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 588
        h = self.canvas.winfo_height() or 320
        cx, cy = w // 2, h // 2

        # Draw classic Game Boy screen frame placeholder
        disp_w = GB_NATIVE_W * self.scale
        disp_h = GB_NATIVE_H * self.scale
        x0, y0 = cx - disp_w // 2, cy - disp_h // 2
        x1, y1 = cx + disp_w // 2, cy + disp_h // 2

        self.canvas.create_rectangle(
            x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#4A5A40", width=2
        )
        self.canvas.create_rectangle(
            x0, y0, x1, y1, fill="#9BBC0F", outline="#0F380F"
        )
        self.canvas.create_text(
            cx,
            cy - 18,
            text="CAT'S GB EMULATOR 0.1",
            fill="#0F380F",
            font=("Courier", 12, "bold"),
        )
        self.canvas.create_text(
            cx,
            cy + 8,
            text="DeSmuME Tkinter Edition | 60 FPS | Audio Engine: TRUE",
            fill="#306230",
            font=("Tahoma", 8),
        )
        self.canvas.create_text(
            cx,
            cy + 30,
            text="Click 'Open ROM' (Ctrl+O) to load a .gb or .gbc cartridge",
            fill="#0F380F",
            font=("Tahoma", 8, "bold"),
        )

    # -----------------------------------------------------------------
    # Audio Engine Setup (PyBoy Sound Buffer -> SDL2 Audio Device)
    # -----------------------------------------------------------------
    def _init_audio_device(self):
        self._close_audio_device()
        if not HAS_SDL2 or not self.pyboy:
            return
        try:
            if sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_AUDIO) != 0:
                return
            sample_rate = getattr(self.pyboy.sound, "sample_rate", 48000)
            desired = sdl2.SDL_AudioSpec(
                freq=sample_rate,
                aformat=sdl2.AUDIO_S8,
                channels=2,
                samples=1024,
            )
            obtained = sdl2.SDL_AudioSpec(0, 0, 0, 0)
            self.audio_device = sdl2.SDL_OpenAudioDevice(
                None, 0, desired, obtained, 0
            )
            if self.audio_device > 0:
                sdl2.SDL_PauseAudioDevice(self.audio_device, 0)
        except Exception:
            self.audio_device = 0

    def _queue_frame_audio(self):
        if not self.audio_enabled or self.audio_device <= 0 or not self.pyboy:
            return
        try:
            snd_array = self.pyboy.sound.ndarray
            if snd_array is not None and snd_array.size > 0:
                # Keep audio latency tight (~120ms max queue)
                queued = sdl2.SDL_GetQueuedAudioSize(self.audio_device)
                if queued > 16384:
                    sdl2.SDL_ClearQueuedAudio(self.audio_device)
                raw_bytes = snd_array.tobytes()
                sdl2.SDL_QueueAudio(self.audio_device, raw_bytes, len(raw_bytes))
        except Exception:
            pass

    def _close_audio_device(self):
        if HAS_SDL2 and self.audio_device > 0:
            try:
                sdl2.SDL_CloseAudioDevice(self.audio_device)
            except Exception:
                pass
        self.audio_device = 0

    # -----------------------------------------------------------------
    # Core PyBoy Lifecycle & 60 FPS Emulation Loop
    # -----------------------------------------------------------------
    def open_rom_dialog(self):
        path = filedialog.askopenfilename(
            title="Open Game Boy ROM",
            filetypes=[
                ("Game Boy ROMs", "*.gb *.gbc *.rom"),
                ("All Files", "*.*"),
            ],
        )
        if path:
            self.load_rom(path)

    def load_rom(self, path: str):
        self.stop_emulation()
        try:
            # Initialize PyBoy in headless window="null" mode so Tkinter owns rendering,
            # while enabling PyBoy's internal sound emulation engine.
            try:
                self.pyboy = PyBoy(
                    path,
                    window="null",
                    sound_emulated=True,
                    sound_volume=100 if self.audio_enabled else 0,
                )
            except TypeError:
                # Fallback for older PyBoy 1.x API signatures
                self.pyboy = PyBoy(
                    path,
                    window_type="headless",
                    sound=self.audio_enabled,
                )

            self.pyboy.set_emulation_speed(self.speed)
            self._init_audio_device()

            self.rom_path = path
            self.running = True
            self.paused = False
            self.pressed_keys.clear()

            rom_name = os.path.basename(path)
            self.btn_pause.config(state=tk.NORMAL, text="Pause")
            self.btn_reset.config(state=tk.NORMAL)
            self.lbl_state.config(text="Status: RUNNING")
            self.lbl_rom.config(text=f"ROM: {rom_name}")
            self.root.title(f"{APP_TITLE} - {rom_name}")

            self.canvas.delete("all")
            self.canvas_image_id = None
            self.last_frame_time = time.perf_counter()
            self.fps_timer = time.perf_counter()
            self.fps_counter = 0

            self._emulation_step()
        except Exception as exc:
            messagebox.showerror(
                "ROM Load Error", f"Failed to load ROM:\n{exc}"
            )
            self.stop_emulation()

    def _emulation_step(self):
        if not self.running or not self.pyboy:
            return

        if not self.paused:
            frame_start = time.perf_counter()

            # Advance PyBoy by 1 frame (or 2 frames in 2x mode)
            ticks = 2 if self.speed == 2 else 1
            keep_going = True
            for _ in range(ticks):
                keep_going = self.pyboy.tick()
                if not keep_going:
                    break
                self._queue_frame_audio()

            if not keep_going:
                self.stop_emulation()
                return

            # Render Game Boy LCD frame onto the DeSmuME canvas
            self._render_lcd_frame()

            # Update FPS counter
            self.fps_counter += ticks
            now = time.perf_counter()
            if now - self.fps_timer >= 0.5:
                self.current_fps = self.fps_counter / (now - self.fps_timer)
                speed_label = (
                    "GameBoy 1x"
                    if self.speed == 1
                    else ("2x Fast" if self.speed == 2 else "Unlimited")
                )
                self.lbl_fps.config(
                    text=f"FPS: {self.current_fps:4.1f} / {self.target_fps} ({speed_label})"
                )
                self.fps_counter = 0
                self.fps_timer = now

            # Frame pacing for 60 FPS (16.666ms per frame)
            elapsed = time.perf_counter() - frame_start
            if self.speed == 0:
                delay_ms = 1
            else:
                target_frame_time = 1.0 / self.target_fps
                remaining = max(0.001, target_frame_time - elapsed)
                delay_ms = max(1, int(remaining * 1000))

            self.root.after(delay_ms, self._emulation_step)
        else:
            self.root.after(50, self._emulation_step)

    def _render_lcd_frame(self):
        if not self.pyboy:
            return

        pil_img = self.pyboy.screen.image
        if pil_img is None:
            return

        # Convert RGBA -> RGB for faster Tkinter PhotoImage blit
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

        disp_w = GB_NATIVE_W * self.scale
        disp_h = GB_NATIVE_H * self.scale
        resample_mode = (
            Image.Resampling.NEAREST
            if self.filter_nearest
            else Image.Resampling.BILINEAR
        )
        scaled_img = pil_img.resize((disp_w, disp_h), resample_mode)

        self.tk_image = ImageTk.PhotoImage(scaled_img)
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(
                cw // 2, ch // 2, image=self.tk_image, anchor=tk.CENTER
            )
        else:
            self.canvas.coords(self.canvas_image_id, cw // 2, ch // 2)
            self.canvas.itemconfig(self.canvas_image_id, image=self.tk_image)

    # -----------------------------------------------------------------
    # Emulator Controls & Features
    # -----------------------------------------------------------------
    def toggle_pause(self):
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.config(text="Resume")
            self.lbl_state.config(text="Status: PAUSED")
            if HAS_SDL2 and self.audio_device > 0:
                sdl2.SDL_PauseAudioDevice(self.audio_device, 1)
        else:
            self.btn_pause.config(text="Pause")
            self.lbl_state.config(text="Status: RUNNING")
            if HAS_SDL2 and self.audio_device > 0:
                sdl2.SDL_PauseAudioDevice(self.audio_device, 0)

    def reset_rom(self):
        if self.rom_path and os.path.isfile(self.rom_path):
            self.load_rom(self.rom_path)

    def stop_emulation(self):
        self.running = False
        self.paused = False
        self._close_audio_device()
        if self.pyboy:
            try:
                self.pyboy.stop(save=False)
            except Exception:
                pass
            self.pyboy = None
        self.btn_pause.config(state=tk.DISABLED, text="Pause")
        self.btn_reset.config(state=tk.DISABLED)
        self.lbl_state.config(text="Status: NO ROM")
        self.lbl_fps.config(text="FPS: 0.0 / 60 (Speed: GameBoy)")
        self.root.title(APP_TITLE)
        self._draw_idle_screen()

    def set_speed(self, speed_val: int):
        self.speed = speed_val
        if self.pyboy:
            self.pyboy.set_emulation_speed(speed_val)
        label_map = {
            1: "Speed: GameBoy (1x)",
            2: "Speed: Fast (2x)",
            0: "Speed: Unlimited",
        }
        self.btn_speed.config(text=label_map.get(speed_val, "Speed: 1x"))

    def cycle_speed(self):
        next_map = {1: 2, 2: 0, 0: 1}
        self.set_speed(next_map.get(self.speed, 1))

    def set_scale(self, scale_val: int):
        self.scale = scale_val
        if not self.running:
            self._draw_idle_screen()

    def toggle_filter(self):
        self.filter_nearest = not self.filter_nearest

    def toggle_audio(self):
        self.audio_enabled = not self.audio_enabled
        self.btn_audio.config(text=f"Audio: {'ON' if self.audio_enabled else 'OFF'}")
        self.lbl_audio.config(
            text=f"Audio Engine: {'TRUE' if self.audio_enabled else 'FALSE'}"
        )
        if HAS_SDL2 and self.audio_device > 0:
            sdl2.SDL_ClearQueuedAudio(self.audio_device)

    def save_state(self):
        if not self.pyboy:
            return
        self.memory_state = io.BytesIO()
        self.pyboy.save_state(self.memory_state)
        self.lbl_state.config(text="Status: STATE SAVED")

    def load_state(self):
        if not self.pyboy or not self.memory_state:
            return
        self.memory_state.seek(0)
        self.pyboy.load_state(self.memory_state)
        self.lbl_state.config(text="Status: STATE LOADED")

    def save_screenshot(self):
        if not self.pyboy or not self.pyboy.screen.image:
            return
        out_path = filedialog.asksaveasfilename(
            title="Save Screenshot",
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
        )
        if out_path:
            self.pyboy.screen.image.save(out_path)

    # -----------------------------------------------------------------
    # Keyboard Input Handling
    # -----------------------------------------------------------------
    def _bind_inputs(self):
        self.root.bind("<KeyPress>", self._on_key_down)
        self.root.bind("<KeyRelease>", self._on_key_up)
        self.root.bind("<Control-o>", lambda e: self.open_rom_dialog())
        self.root.bind("<Control-r>", lambda e: self.reset_rom())
        self.root.bind("<space>", lambda e: self.toggle_pause())
        self.root.bind("<F5>", lambda e: self.save_state())
        self.root.bind("<F8>", lambda e: self.load_state())
        self.root.bind("<F12>", lambda e: self.save_screenshot())

    def _on_key_down(self, event: tk.Event):
        if not self.running or self.paused or not self.pyboy:
            return
        btn = self.key_map.get(event.keysym)
        if btn and btn not in self.pressed_keys:
            self.pressed_keys.add(btn)
            if hasattr(self.pyboy, "button_press"):
                self.pyboy.button_press(btn)

    def _on_key_up(self, event: tk.Event):
        if not self.running or not self.pyboy:
            return
        btn = self.key_map.get(event.keysym)
        if btn and btn in self.pressed_keys:
            self.pressed_keys.discard(btn)
            if hasattr(self.pyboy, "button_release"):
                self.pyboy.button_release(btn)

    # -----------------------------------------------------------------
    # Dialogs & Shutdown
    # -----------------------------------------------------------------
    def show_controls_dialog(self):
        messagebox.showinfo(
            "Controls - Cat's GB Emulator 0.1",
            "Game Boy Controls:\n"
            "  • D-Pad : Arrow Keys (Up, Down, Left, Right)\n"
            "  • A Button : Z\n"
            "  • B Button : X\n"
            "  • Start : Enter (Return)\n"
            "  • Select : Backspace or Right Shift\n\n"
            "Hotkeys:\n"
            "  • Ctrl+O : Open ROM\n"
            "  • Space : Pause / Resume\n"
            "  • F5 / F8 : Quick Save / Load State\n"
            "  • F12 : Save Screenshot",
        )

    def show_about_dialog(self):
        messagebox.showinfo(
            "About Cat's GB Emulator 0.1",
            "Cat's GB Emulator 0.1\n"
            "GUI Style: DeSmuME Classic (600x400 Tkinter)\n"
            "Core: PyBoy Game Boy / Game Boy Color Engine\n"
            "Target Speed: 60 FPS (GameBoy Native 1x)\n"
            "Audio Engine: TRUE (48000 Hz Stereo)",
        )

    def on_close(self):
        self.stop_emulation()
        self.root.destroy()


if __name__ == "__main__":
    rom_arg = sys.argv[1] if len(sys.argv) > 1 else None
    app_root = tk.Tk()
    emulator = CatsGBEmulator(app_root, initial_rom=rom_arg)
    app_root.mainloop()
