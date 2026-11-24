#!/usr/bin/env python3
"""
HEADSHOT ARENA - Cliente (Pygame)
=================================
Pantallas: Login/Registro -> Menú (elige modo) -> Partida / Ranking.

Controles en partida:
  A/D o flechas  mover        W / Espacio / ↑  saltar      S / ↓  atravesar plataforma
  Mouse          apuntar      Click izquierdo  disparar / atacar
  1-8 o rueda    cambiar de arma              ESC  volver al menú

Uso:  python client.py [host[:puerto]]
"""
import json
import math
import os
import queue
import random
import socket
import sys
import threading
import time
from array import array

import pygame

W, H = 1280, 720
DEFAULT_PORT = 5555
NECK_H, SHOULDER_H = 54, 50

# Paleta
BG_TOP, BG_BOT = (12, 15, 24), (24, 28, 44)
PANEL = (22, 26, 40)
PANEL_LIGHT = (34, 40, 62)
TEXT = (232, 236, 245)
MUTED = (140, 150, 175)
ACCENT = (255, 196, 61)
ME_COL = (84, 200, 255)
FOE_COL = (255, 96, 96)
BODY_COL = (236, 238, 244)
GOOD = (96, 220, 140)
BAD = (255, 96, 96)
MODE_COLORS = {"only200": (255, 90, 90), "pvp": (84, 170, 255), "melee": (255, 170, 60)}

# Gráficos de armas: partes (desde, hasta, grosor, color, desplazamiento perpendicular)
DARK, STEEL, WOOD, GRIP = (46, 48, 58), (205, 214, 228), (150, 100, 56), (28, 28, 34)
WEAPON_GFX = {
    "hs": [(-6, 6, 7, GRIP, 0), (0, 30, 5, (230, 190, 70), 0)],
    "pistol": [(-5, 5, 6, GRIP, 3), (-2, 22, 5, DARK, 0)],
    "smg": [(-8, 30, 6, DARK, 0), (4, 11, 8, GRIP, 6), (-8, -2, 8, GRIP, 3)],
    "shotgun": [(-10, 12, 7, WOOD, 1), (10, 44, 5, DARK, 0), (16, 30, 3, GRIP, 4)],
    "sniper": [(-12, 12, 7, DARK, 1), (10, 58, 4, GRIP, 0), (16, 28, 5, (60, 90, 120), -5)],
    "knife": [(-4, 6, 5, GRIP, 0), (6, 28, 4, STEEL, 0)],
    "sword": [(-7, 4, 5, GRIP, 0), (4, 6, 4, (230, 190, 70), 0), (3, 7, 16, (230, 190, 70), 0), (7, 52, 5, STEEL, 0)],
    "bat": [(-8, 12, 5, WOOD, 0), (12, 46, 9, WOOD, 0)],
    "axe": [(-8, 44, 5, WOOD, 0), (34, 46, 20, STEEL, 7)],
}
GUN_TIP = {"hs": 36, "pistol": 22, "smg": 30, "shotgun": 44, "sniper": 58}
MELEE_SWING_TIME = 0.24


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def angdiff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def wrap_text(text, font, width):
    lines, line = [], ""
    for word in text.split():
        test = (line + " " + word).strip()
        if font.size(test)[0] <= width:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


# ----------------------------------------------------------------------------
# Red
# ----------------------------------------------------------------------------
class Net:
    def __init__(self):
        self.sock = None
        self.addr = None
        self.inbox = queue.Queue()
        self.wlock = threading.Lock()

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, host, port):
        self.close()
        s = socket.create_connection((host, port), timeout=5)
        s.settimeout(None)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock, self.addr = s, (host, port)
        threading.Thread(target=self._reader, args=(s,), daemon=True).start()

    def _reader(self, sock):
        buf = b""
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line:
                        self.inbox.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            if sock is self.sock:
                self.inbox.put({"t": "_disc"})

    def send(self, obj):
        if not self.sock:
            return
        try:
            with self.wlock:
                self.sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())
        except OSError:
            if self.sock:
                self.inbox.put({"t": "_disc"})

    def close(self):
        s, self.sock, self.addr = self.sock, None, None
        if s:
            try:
                s.close()
            except OSError:
                pass


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
class TextBox:
    def __init__(self, rect, label, text="", secret=False, maxlen=40):
        self.rect, self.label, self.text = pygame.Rect(rect), label, text
        self.secret, self.maxlen = secret, maxlen

    def key(self, e):
        if e.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
        elif e.unicode and e.unicode.isprintable() and len(self.text) < self.maxlen:
            self.text += e.unicode


class Button:
    def __init__(self, rect, text, color=PANEL_LIGHT):
        self.rect, self.text, self.color = pygame.Rect(rect), text, color

    def hit(self, pos):
        return self.rect.collidepoint(pos)


def make_sounds():
    snd = {}
    try:
        pygame.mixer.init(22050, -16, 1, 512)
    except pygame.error:
        return snd

    def tone(f0, f1, ms, vol, noise=0.0):
        n = int(22050 * ms / 1000)
        buf, ph = array("h"), 0.0
        for i in range(n):
            f = f0 + (f1 - f0) * i / n
            ph += 2 * math.pi * f / 22050
            v = math.sin(ph) * (1 - noise) + random.uniform(-1, 1) * noise
            buf.append(int(v * ((1 - i / n) ** 2) * vol * 32767))
        return pygame.mixer.Sound(buffer=buf.tobytes())

    try:
        snd["shot"] = tone(220, 70, 110, 0.45, 0.85)
        snd["big"] = tone(130, 40, 240, 0.7, 0.85)
        snd["swing"] = tone(420, 140, 150, 0.22, 0.6)
        snd["head"] = tone(880, 1320, 170, 0.35)
        snd["kill"] = tone(520, 980, 240, 0.4, 0.1)
        snd["hurt"] = tone(210, 90, 130, 0.4, 0.3)
    except Exception:
        return {}
    return snd


# ----------------------------------------------------------------------------
# Aplicación
# ----------------------------------------------------------------------------
class App:
    def __init__(self, host_arg=None):
        pygame.init()
        pygame.display.set_caption("HEADSHOT ARENA")
        try:
            self.screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
        except pygame.error:
            self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        self.f_title = pygame.font.Font(None, 96)
        self.f_big = pygame.font.Font(None, 56)
        self.f_h = pygame.font.Font(None, 40)
        self.f_m = pygame.font.Font(None, 28)
        self.f_s = pygame.font.Font(None, 22)
        self.sounds = make_sounds()
        self._fonts = {}
        self.net = Net()
        self.running = True
        self.t = 0.0
        self.dt = 0.016
        self.scene = "login"
        self.status, self.status_col, self.busy = "", MUTED, False
        self.user, self.stats = None, {}
        self.modes_info, self.last_modes_poll = [], 0.0
        self.ranking, self.rank_mode = [], "all"
        host = host_arg or os.environ.get("HEADSHOT_HOST", "127.0.0.1")
        self.boxes = [TextBox((440, 250, 400, 48), "Servidor", host),
                      TextBox((440, 340, 400, 48), "Usuario", "", maxlen=16),
                      TextBox((440, 430, 400, 48), "Contraseña", "", secret=True, maxlen=64)]
        self.focus = 1
        self.btn_login = Button((440, 510, 195, 52), "Iniciar sesión", (52, 110, 190))
        self.btn_reg = Button((645, 510, 195, 52), "Registrarse", (60, 140, 90))
        self.reset_game()

    # --- estado de partida --------------------------------------------------
    def reset_game(self):
        self.players, self.my_id = {}, None
        self.platforms, self.weapons, self.weapon_idx = [], [], 0
        self.minfo = {}
        self.phase, self.winner, self.timer = "play", None, 0
        self.particles, self.tracers, self.popups, self.feed = [], [], [], []
        self.shake, self.flash, self.last_input = 0.0, 0.0, 0.0
        pygame.mouse.set_visible(True)

    def font(self, size):
        if size not in self._fonts:
            self._fonts[size] = pygame.font.Font(None, size)
        return self._fonts[size]

    def play(self, name, vol=1.0):
        s = self.sounds.get(name)
        if s:
            s.set_volume(clamp(vol, 0, 1))
            s.play()

    def set_status(self, msg, col=MUTED, busy=False):
        self.status, self.status_col, self.busy = msg, col, busy

    # --- bucle ----------------------------------------------------------------
    def frame(self):
        self.dt = min(self.clock.tick(60) / 1000.0, 0.05)
        self.t += self.dt
        for e in pygame.event.get():
            self.on_event(e)
        self.process_net()
        self.update()
        self.draw()
        pygame.display.flip()
        return self.running

    def run(self):
        while self.frame():
            pass
        self.net.close()
        pygame.quit()

    # --- eventos --------------------------------------------------------------
    def on_event(self, e):
        if e.type == pygame.QUIT:
            self.running = False
        elif self.scene == "login":
            self.ev_login(e)
        elif self.scene == "menu":
            self.ev_menu(e)
        elif self.scene == "ranking":
            self.ev_ranking(e)
        elif self.scene == "game":
            self.ev_game(e)

    def ev_login(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, b in enumerate(self.boxes):
                if b.rect.collidepoint(e.pos):
                    self.focus = i
            if self.btn_login.hit(e.pos):
                self.start_auth("login")
            elif self.btn_reg.hit(e.pos):
                self.start_auth("register")
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_TAB:
                self.focus = (self.focus + 1) % len(self.boxes)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start_auth("login")
            else:
                self.boxes[self.focus].key(e)

    def start_auth(self, mode):
        if self.busy:
            return
        host_txt, user, pw = self.boxes[0].text.strip(), self.boxes[1].text.strip(), self.boxes[2].text
        if not user or not pw:
            return self.set_status("Escribe usuario y contraseña", BAD)
        host, _, port = host_txt.partition(":")
        try:
            port = int(port) if port else DEFAULT_PORT
        except ValueError:
            return self.set_status("Puerto inválido", BAD)
        self.set_status("Conectando…", MUTED, True)

        def work():
            try:
                if self.net.addr != (host or "127.0.0.1", port):
                    self.net.connect(host or "127.0.0.1", port)
                self.net.send({"t": mode, "u": user, "p": pw})
            except OSError as ex:
                self.net.inbox.put({"t": "_err", "msg": f"No se pudo conectar: {ex}"})
        threading.Thread(target=work, daemon=True).start()

    def ev_menu(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for key, rect in self.mode_rects():
                if rect.collidepoint(e.pos):
                    self.net.send({"t": "join", "mode": key})
                    self.set_status("Entrando a la sala…", MUTED, True)
            if pygame.Rect(70, 610, 220, 56).collidepoint(e.pos):
                self.open_ranking("all")
            elif pygame.Rect(310, 610, 160, 56).collidepoint(e.pos):
                self.running = False

    def mode_rects(self):
        keys = ["only200", "pvp", "melee"]
        return [(k, pygame.Rect(70 + i * 400, 230, 380, 340)) for i, k in enumerate(keys)]

    def open_ranking(self, mode):
        self.scene, self.rank_mode, self.ranking = "ranking", mode, []
        self.net.send({"t": "ranking", "mode": mode})

    def rank_tabs(self):
        names = [("all", "Global"), ("only200", "Only 200"), ("pvp", "PvP"), ("melee", "Cuerpo a cuerpo")]
        return [(k, n, pygame.Rect(70 + i * 210, 130, 196, 46)) for i, (k, n) in enumerate(names)]

    def ev_ranking(self, e):
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            self.scene = "menu"
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for k, _n, r in self.rank_tabs():
                if r.collidepoint(e.pos):
                    self.open_ranking(k)
            if pygame.Rect(70, 640, 180, 50).collidepoint(e.pos):
                self.scene = "menu"

    def ev_game(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.net.send({"t": "leave"})
                self.reset_game()
                self.scene = "menu"
            elif pygame.K_1 <= e.key <= pygame.K_9:
                i = e.key - pygame.K_1
                if i < len(self.weapons):
                    self.weapon_idx = i
        elif e.type == pygame.MOUSEWHEEL and self.weapons:
            self.weapon_idx = (self.weapon_idx - e.y) % len(self.weapons)

    # --- red ------------------------------------------------------------------
    def process_net(self):
        while True:
            try:
                m = self.net.inbox.get_nowait()
            except queue.Empty:
                break
            t = m.get("t")
            if t == "state":
                self.on_state(m)
            elif t == "auth":
                if m["ok"]:
                    self.user, self.stats = m["user"], m["stats"]
                    self.scene = "menu"
                    self.set_status("")
                    self.net.send({"t": "modes"})
                else:
                    self.set_status(m["msg"], BAD)
            elif t == "stats":
                self.stats = m["stats"]
            elif t == "modes":
                self.modes_info = m["rows"]
            elif t == "ranking":
                self.ranking, self.rank_mode = m["rows"], m["mode"]
            elif t == "joined":
                self.reset_game()
                self.my_id = m["id"]
                self.minfo, self.platforms, self.weapons = m, m["platforms"], m["weapons"]
                self.scene = "game"
                self.set_status("")
                pygame.mouse.set_visible(False)
            elif t in ("_disc", "_err"):
                self.net.close()
                self.reset_game()
                self.scene, self.user = "login", None
                self.set_status(m.get("msg", "Conexión perdida con el servidor"), BAD)

    def on_state(self, m):
        if self.scene != "game":
            return
        seen = set()
        for e in m["p"]:
            seen.add(e["i"])
            d = self.players.get(e["i"])
            if d is None:
                d = self.players[e["i"]] = {"dx": e["x"], "dy": e["y"], "dr": e["hr"], "da": e["a"],
                                            "ph": 0.0, "swing": -9, "last_shot": -9, "swing_w": e["w"]}
            d.update(e)
        for i in list(self.players):
            if i not in seen:
                del self.players[i]
        self.phase, self.winner, self.timer = m["ph"], m["win"], m["tm"]
        for ev in m["ev"]:
            self.on_event_game(ev)

    def tip_pos(self, d, weapon, ang):
        length = GUN_TIP.get(weapon, 30)
        return d["dx"] + math.cos(ang) * length, d["dy"] - SHOULDER_H + math.sin(ang) * length

    def on_event_game(self, ev):
        k = ev["k"]
        mine = ev.get("id") == self.my_id
        if k == "shot":
            d = self.players.get(ev["id"])
            if d:
                d["last_shot"] = self.t
                for ex, ey, hit in ev["e"]:
                    ang = math.atan2(ey - ev["y"], ex - ev["x"])
                    x1, y1 = self.tip_pos(d, ev["w"], ang)
                    self.tracers.append([x1, y1, ex, ey, 0.10, hit])
                    if hit:
                        for _ in range(4):
                            self.particles.append(self.spark(ex, ey, (255, 220, 120), 160, 0.25))
                ang = math.atan2(ev["e"][0][1] - ev["y"], ev["e"][0][0] - ev["x"])
                x1, y1 = self.tip_pos(d, ev["w"], ang)
                for _ in range(3):
                    self.particles.append(self.spark(x1, y1, (255, 240, 170), 220, 0.12))
            self.play("big" if ev["w"] in ("shotgun", "sniper") else "shot", 0.7 if mine else 0.25)
        elif k == "swing":
            d = self.players.get(ev["id"])
            if d:
                d["swing"], d["swing_w"] = self.t, ev["w"]
            self.play("swing", 0.6 if mine else 0.2)
        elif k == "hit":
            only_head = self.minfo.get("only_head")
            if only_head:
                self.popups.append([ev["x"], ev["y"] - 24, "HEADSHOT", ACCENT, 0.9, 26])
            else:
                col = ACCENT if ev["h"] else (255, 255, 255)
                self.popups.append([ev["x"], ev["y"] - 10, str(ev["d"]) + ("!" if ev["h"] else ""), col, 0.8, 24 if ev["h"] else 20])
            if ev["h"]:
                self.play("head", 0.5)
            if ev["v"] == self.my_id:
                self.flash, self.shake = 0.35, max(self.shake, 7)
                self.play("hurt", 0.6)
        elif k == "kill":
            for _ in range(26):
                self.particles.append(self.spark(ev["x"], ev["y"], (200, 30, 40) if random.random() < 0.7 else (255, 255, 255), 420, 0.7))
            self.feed.append([ev["a"], ev["v"], ev["w"], ev["h"], ev["pts"], 6.0, ev["ai"] == self.my_id, ev["vi"] == self.my_id])
            self.feed = self.feed[-6:]
            if ev["ai"] == self.my_id:
                self.shake = max(self.shake, 10)
                self.popups.append([ev["x"], ev["y"] - 50, f"+{ev['pts']}", GOOD, 1.2, 40])
                if self.minfo.get("streak") and ev["s"] >= 2:
                    me = self.players.get(self.my_id)
                    if me:
                        self.popups.append([me["dx"], me["dy"] - 130, f"RACHA x{ev['s']}", ACCENT, 1.4, 38])
                self.play("kill", 0.7)
            if ev["vi"] == self.my_id:
                self.shake = max(self.shake, 14)

    @staticmethod
    def spark(x, y, col, speed, life):
        a, s = random.uniform(0, math.tau), random.uniform(0.2, 1.0) * speed
        return [x, y, math.cos(a) * s, math.sin(a) * s - 60, life, life, col]

    # --- actualización ------------------------------------------------------------
    def update(self):
        dt = self.dt
        self.shake = max(0.0, self.shake - 30 * dt)
        self.flash = max(0.0, self.flash - dt)
        if self.scene == "menu" and self.t - self.last_modes_poll > 2.0:
            self.last_modes_poll = self.t
            self.net.send({"t": "modes"})
        if self.scene != "game":
            return
        for d in self.players.values():
            k = min(1.0, 18 * dt)
            d["dx"] += (d["x"] - d["dx"]) * k
            d["dy"] += (d["y"] - d["dy"]) * k
            d["dr"] += (d["hr"] - d["dr"]) * min(1.0, 10 * dt)
            if d["i"] != self.my_id:
                d["da"] += angdiff(d["a"], d["da"]) * min(1.0, 20 * dt)
            if d["g"] and abs(d["vx"]) > 15:
                d["ph"] += abs(d["vx"]) * dt * 0.045
        for lst in (self.tracers, self.popups, self.particles):
            for it in lst:
                it[4] -= dt
        for p in self.particles:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] += 700 * dt
        self.particles = [p for p in self.particles if p[4] > 0]
        self.tracers = [x for x in self.tracers if x[4] > 0]
        for pu in self.popups:
            pu[1] -= 40 * dt
        self.popups = [x for x in self.popups if x[4] > 0]
        for f in self.feed:
            f[5] -= dt
        self.feed = [f for f in self.feed if f[5] > 0]
        self.send_input()

    def send_input(self):
        me = self.players.get(self.my_id)
        if not me or self.t - self.last_input < 1 / 30:
            return
        self.last_input = self.t
        k = pygame.key.get_pressed()
        mx, my = pygame.mouse.get_pos()
        aim = math.atan2(my - (me["dy"] - SHOULDER_H), mx - me["dx"])
        me["da"] = aim
        self.net.send({"t": "input",
                       "l": k[pygame.K_a] or k[pygame.K_LEFT], "r": k[pygame.K_d] or k[pygame.K_RIGHT],
                       "j": k[pygame.K_w] or k[pygame.K_SPACE] or k[pygame.K_UP],
                       "d": k[pygame.K_s] or k[pygame.K_DOWN],
                       "fire": pygame.mouse.get_pressed()[0] and pygame.mouse.get_focused(),
                       "aim": round(aim, 3), "w": self.weapons[self.weapon_idx]["id"] if self.weapons else None})

    # --- dibujo: utilidades -------------------------------------------------------
    def text(self, s, font, pos, col=TEXT, anchor="topleft", shadow=False):
        img = font.render(s, True, col)
        r = img.get_rect(**{anchor: pos})
        if shadow:
            self.screen.blit(font.render(s, True, (0, 0, 0)), r.move(2, 2))
        self.screen.blit(img, r)
        return r

    def draw_bg(self):
        self.screen.fill(BG_TOP)
        for i in range(0, H, 4):
            f = i / H
            c = tuple(int(BG_TOP[j] + (BG_BOT[j] - BG_TOP[j]) * f) for j in range(3))
            pygame.draw.rect(self.screen, c, (0, i, W, 4))

    def draw_button(self, b, font=None, hover=True):
        mp = pygame.mouse.get_pos()
        col = tuple(min(255, c + 25) for c in b.color) if (hover and b.rect.collidepoint(mp)) else b.color
        pygame.draw.rect(self.screen, col, b.rect, border_radius=10)
        self.text(b.text, font or self.f_m, b.rect.center, TEXT, "center")

    def draw(self):
        if self.scene == "login":
            self.draw_login()
        elif self.scene == "menu":
            self.draw_menu()
        elif self.scene == "ranking":
            self.draw_ranking()
        elif self.scene == "game":
            self.draw_game()

    # --- pantallas ------------------------------------------------------------------
    def draw_login(self):
        self.draw_bg()
        self.text("HEADSHOT", self.f_title, (W // 2, 90), ACCENT, "center", True)
        self.text("A R E N A", self.f_big, (W // 2, 150), TEXT, "center")
        pygame.draw.rect(self.screen, PANEL, (400, 200, 480, 400), border_radius=16)
        for i, b in enumerate(self.boxes):
            self.text(b.label, self.f_s, (b.rect.x, b.rect.y - 22), MUTED)
            pygame.draw.rect(self.screen, (14, 17, 28), b.rect, border_radius=8)
            pygame.draw.rect(self.screen, ACCENT if i == self.focus else (60, 68, 95), b.rect, 2, border_radius=8)
            shown = "•" * len(b.text) if b.secret else b.text
            if i == self.focus and int(self.t * 2) % 2 == 0:
                shown += "|"
            self.text(shown, self.f_m, (b.rect.x + 12, b.rect.centery), TEXT, "midleft")
        self.draw_button(self.btn_login)
        self.draw_button(self.btn_reg)
        if self.status:
            self.text(self.status, self.f_m, (W // 2, 585), self.status_col, "center")
        self.text("Tab: cambiar campo  ·  Enter: iniciar sesión", self.f_s, (W // 2, 680), MUTED, "center")

    def draw_menu(self):
        self.draw_bg()
        self.text("HEADSHOT ARENA", self.f_big, (70, 40), ACCENT)
        s = self.stats or {}
        self.text(f"{self.user}", self.f_h, (70, 100), TEXT)
        self.text(f"Victorias {s.get('wins', 0)}   ·   Puntos {s.get('score', 0)}   ·   Kills {s.get('kills', 0)}   ·   "
                  f"Headshots {s.get('headshots', 0)}   ·   Mejor racha {s.get('best', 0)}", self.f_m, (70, 148), MUTED)
        self.text("Elige un modo de juego", self.f_h, (70, 190), TEXT)
        info = {r["id"]: r for r in self.modes_info}
        mp = pygame.mouse.get_pos()
        for key, rect in self.mode_rects():
            col = MODE_COLORS[key]
            hov = rect.collidepoint(mp)
            pygame.draw.rect(self.screen, PANEL_LIGHT if hov else PANEL, rect, border_radius=16)
            pygame.draw.rect(self.screen, col, rect, 3 if hov else 1, border_radius=16)
            pygame.draw.rect(self.screen, col, (rect.x, rect.y, rect.w, 8), border_top_left_radius=16, border_top_right_radius=16)
            r = info.get(key, {})
            self.text(r.get("name", key), self.f_big, (rect.x + 24, rect.y + 34), col)
            for i, ln in enumerate(wrap_text(r.get("desc", ""), self.f_m, rect.w - 48)):
                self.text(ln, self.f_m, (rect.x + 24, rect.y + 100 + i * 28), TEXT)
            self.text(f"{r.get('players', 0)} jugando  ·  meta {r.get('target', '?')} pts", self.f_m, (rect.x + 24, rect.bottom - 40), MUTED)
        for b in (Button((70, 610, 220, 56), "Ranking", (52, 110, 190)), Button((310, 610, 160, 56), "Salir", (120, 50, 60))):
            self.draw_button(b, self.f_h)
        if self.status:
            self.text(self.status, self.f_m, (520, 628), self.status_col)

    def draw_ranking(self):
        self.draw_bg()
        self.text("RANKING", self.f_big, (70, 40), ACCENT)
        self.text("Top 10 por victorias y puntos", self.f_m, (70, 92), MUTED)
        for k, n, r in self.rank_tabs():
            pygame.draw.rect(self.screen, (52, 110, 190) if k == self.rank_mode else PANEL, r, border_radius=10)
            self.text(n, self.f_m, r.center, TEXT, "center")
        cols = [("#", 70), ("Jugador", 130), ("Victorias", 560), ("Puntos", 700), ("Kills", 830), ("Headshots", 940), ("Mejor racha", 1090)]
        pygame.draw.rect(self.screen, PANEL, (60, 196, 1160, 430), border_radius=12)
        for n, x in cols:
            self.text(n, self.f_m, (x, 206), MUTED)
        for i, row in enumerate(self.ranking):
            y = 246 + i * 37
            if row[0] == self.user:
                pygame.draw.rect(self.screen, PANEL_LIGHT, (66, y - 4, 1148, 34), border_radius=6)
            vals = [str(i + 1), row[0], row[1], row[2], row[3], row[4], row[5]]
            for (n, x), v in zip(cols, vals):
                self.text(str(v), self.f_m, (x, y), ACCENT if i == 0 else TEXT)
        if not self.ranking:
            self.text("Aún no hay partidas registradas en esta categoría", self.f_m, (W // 2, 400), MUTED, "center")
        self.draw_button(Button((70, 640, 180, 50), "Volver", (60, 66, 95)))

    # --- dibujo: partida ---------------------------------------------------------------
    def draw_game(self):
        off = (random.uniform(-1, 1) * self.shake, random.uniform(-1, 1) * self.shake) if self.shake > 0.5 else (0, 0)
        world = pygame.Surface((W, H))
        scr, self.screen = self.screen, world
        self.draw_arena()
        order = sorted(self.players.values(), key=lambda d: d["i"] == self.my_id)
        for d in order:
            self.draw_stickman(d, d["i"] == self.my_id)
        for x1, y1, x2, y2, life, hit in self.tracers:
            a = clamp(life / 0.10, 0, 1)
            c = (255, int(210 * a + 40), int(120 * a))
            pygame.draw.line(world, c, (x1, y1), (x2, y2), 3 if hit else 2)
        for x, y, vx, vy, life, total, col in self.particles:
            pygame.draw.circle(world, col, (int(x), int(y)), max(1, int(3 * life / total) + 1))
        for x, y, s, col, life, size in self.popups:
            font = self.font(size)
            img = font.render(s, True, col)
            img.set_alpha(int(255 * clamp(life / 0.5, 0, 1)))
            shadow = font.render(s, True, (0, 0, 0))
            shadow.set_alpha(img.get_alpha())
            r = img.get_rect(center=(x, y))
            world.blit(shadow, r.move(2, 2))
            world.blit(img, r)
        self.screen = scr
        self.screen.blit(world, off)
        if self.flash > 0:
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((255, 30, 30, int(110 * self.flash / 0.35)))
            self.screen.blit(ov, (0, 0))
        self.draw_hud()
        mx, my = pygame.mouse.get_pos()
        pygame.draw.circle(self.screen, (255, 255, 255), (mx, my), 11, 2)
        pygame.draw.line(self.screen, (255, 255, 255), (mx - 17, my), (mx - 6, my), 2)
        pygame.draw.line(self.screen, (255, 255, 255), (mx + 6, my), (mx + 17, my), 2)
        pygame.draw.line(self.screen, (255, 255, 255), (mx, my - 17), (mx, my - 6), 2)
        pygame.draw.line(self.screen, (255, 255, 255), (mx, my + 6), (mx, my + 17), 2)

    def draw_arena(self):
        self.draw_bg()
        for x in range(0, W, 80):
            pygame.draw.line(self.screen, (30, 35, 54), (x, 0), (x, H))
        for y in range(0, H, 80):
            pygame.draw.line(self.screen, (30, 35, 54), (0, y), (W, y))
        for px, py, pw, ph in self.platforms:
            pygame.draw.rect(self.screen, (52, 58, 84), (px, py, pw, ph))
            pygame.draw.rect(self.screen, (96, 108, 150), (px, py, pw, 4))
            pygame.draw.rect(self.screen, (34, 38, 58), (px, py + ph - 3, pw, 3))

    def limb(self, a, b, w, col):
        pygame.draw.line(self.screen, col, a, b, w)
        pygame.draw.circle(self.screen, col, (int(a[0]), int(a[1])), w // 2)
        pygame.draw.circle(self.screen, col, (int(b[0]), int(b[1])), w // 2)

    def draw_weapon(self, wid, hand, ang, flip):
        dx, dy = math.cos(ang), math.sin(ang)
        nx, ny = -dy, dx
        for a0, a1, th, col, perp in WEAPON_GFX.get(wid, []):
            perp *= flip
            pts = []
            for a, s in ((a0, -th / 2), (a1, -th / 2), (a1, th / 2), (a0, th / 2)):
                pts.append((hand[0] + dx * a + nx * (perp + s), hand[1] + dy * a + ny * (perp + s)))
            pygame.draw.polygon(self.screen, col, pts)

    def draw_stickman(self, d, me):
        if not d["al"]:
            return
        if d["pr"] and int(self.t * 14) % 2 == 0:
            return
        x, y, r, aim = d["dx"], d["dy"], d["dr"], d["da"]
        accent = ME_COL if me else FOE_COL
        flip = 1 if math.cos(aim) >= 0 else -1
        wid = d["w"]
        melee = next((w["kind"] == "melee" for w in self.weapons if w["id"] == wid), False)
        # sombra
        pygame.draw.ellipse(self.screen, (8, 10, 16), (x - 18, y - 4, 36, 8))
        hip, neck, sh = (x, y - 30), (x, y - NECK_H), (x, y - SHOULDER_H)
        # piernas
        moving = d["g"] and abs(d["vx"]) > 15
        for sgn in (1, -1):
            off = 0.0 if sgn == 1 else math.pi
            if not d["g"]:
                a1 = 0.55 * sgn
                a2 = a1 - 0.5 * (1 if sgn > 0 else 0.2)
            elif moving:
                a1 = math.sin(d["ph"] + off) * 0.8
                a2 = a1 - 0.5 * (0.5 + 0.5 * math.cos(d["ph"] + off))
            else:
                a1 = a2 = 0.12 * sgn
            knee = (hip[0] + math.sin(a1) * 16, hip[1] + math.cos(a1) * 15)
            foot = (knee[0] + math.sin(a2) * 15, knee[1] + math.cos(a2) * 15)
            self.limb(hip, knee, 4, BODY_COL)
            self.limb(knee, foot, 4, BODY_COL)
        # torso
        self.limb(hip, neck, 5, BODY_COL)
        # armas / brazos
        recoil = max(0.0, 1 - (self.t - d["last_shot"]) / 0.08) * 5
        ah = aim
        if melee:
            prog = (self.t - d["swing"]) / MELEE_SWING_TIME
            if 0 <= prog <= 1:
                ah = aim + flip * (-1.5 + 3.0 * (1 - (1 - prog) ** 2))
                reach = next((w["reach"] for w in self.weapons if w["id"] == d["swing_w"]), 70)
                pts = []
                for s in range(8):
                    pp = prog * s / 7
                    ang = aim + flip * (-1.5 + 3.0 * (1 - (1 - pp) ** 2))
                    pts.append((sh[0] + math.cos(ang) * reach * 0.95, sh[1] + math.sin(ang) * reach * 0.95))
                if len(pts) > 1 and prog > 0.1:
                    pygame.draw.lines(self.screen, (225, 235, 255), False, pts, 3)
            else:
                ah = aim - flip * 1.0
        hand = (sh[0] + math.cos(ah) * (20 - recoil), sh[1] + math.sin(ah) * (20 - recoil))
        nx, ny = -math.sin(ah), math.cos(ah)
        elbow = (sh[0] + math.cos(ah) * 9 + nx * 4 * flip, sh[1] + math.sin(ah) * 9 + ny * 4 * flip)
        if melee:
            off_hand = (x - 9 * flip, y - 33)
            self.limb(sh, (x - 6 * flip, y - 42), 3, BODY_COL)
            self.limb((x - 6 * flip, y - 42), off_hand, 3, BODY_COL)
        else:
            off_hand = (sh[0] + math.cos(aim) * (14 - recoil), sh[1] + math.sin(aim) * (14 - recoil) + 3)
            self.limb(sh, off_hand, 3, (190, 194, 206))
        self.draw_weapon(wid, hand, ah, flip)
        self.limb(sh, elbow, 3, BODY_COL)
        self.limb(elbow, hand, 3, BODY_COL)
        # aura por racha
        hc = (x, y - NECK_H - r)
        s = d["s"]
        if self.minfo.get("streak") and s >= 3:
            col = (255, 210, 60) if s < 6 else ((255, 140, 40) if s < 9 else (255, 60, 110))
            R = int(r + 12 + 3 * math.sin(self.t * 6))
            glow = pygame.Surface((R * 2, R * 2), pygame.SRCALPHA)
            for k in range(4):
                pygame.draw.circle(glow, (*col, 38 + 10 * k), (R, R), R - k * 3)
            self.screen.blit(glow, (hc[0] - R, hc[1] - R))
        # cabeza
        pygame.draw.circle(self.screen, accent, hc, int(r) + 3)
        pygame.draw.circle(self.screen, (238, 238, 244), hc, int(r))
        pygame.draw.circle(self.screen, (212, 214, 224), (hc[0] - flip * r * 0.2, hc[1] + r * 0.25), int(r * 0.7))
        pygame.draw.circle(self.screen, (238, 238, 244), (hc[0] + flip * r * 0.1, hc[1] - r * 0.1), int(r * 0.75))
        ex, ey = hc[0] + flip * r * 0.42, hc[1] - r * 0.12
        pygame.draw.circle(self.screen, (24, 26, 34), (ex, ey), max(2, int(r * 0.13)))
        if s >= 3 and self.minfo.get("streak"):
            pygame.draw.line(self.screen, (24, 26, 34), (ex - flip * r * 0.25, ey - r * 0.32 - 2), (ex + flip * r * 0.22, ey - r * 0.12 - 3), 3)
        # nombre / vida
        ty = hc[1] - r - 22
        self.text(d["n"], self.f_s, (x, ty), accent, "center", True)
        if self.minfo.get("streak"):
            if s >= 1:
                self.text(f"x{s}", self.f_s, (x, ty - 16), ACCENT, "center", True)
        else:
            bw = 44
            pygame.draw.rect(self.screen, (10, 12, 18), (x - bw / 2 - 1, ty + 12, bw + 2, 7))
            pygame.draw.rect(self.screen, GOOD if d["hp"] > 35 else BAD, (x - bw / 2, ty + 13, bw * d["hp"] / 100.0, 5))

    # --- HUD -------------------------------------------------------------------------------
    def draw_hud(self):
        mi = self.minfo
        target = mi.get("target", 200)
        col = MODE_COLORS.get(mi.get("mode"), ACCENT)
        ranked = sorted(self.players.values(), key=lambda d: -d["sc"])
        # marcador
        y = 14
        self.text("MARCADOR", self.f_s, (16, y), MUTED, shadow=True)
        for i, d in enumerate(ranked[:8]):
            c = ME_COL if d["i"] == self.my_id else TEXT
            self.text(f"{i + 1}. {d['n']}", self.f_m, (16, y + 20 + i * 22), c, shadow=True)
            self.text(f"{d['sc']}  ({d['k']})", self.f_m, (230, y + 20 + i * 22), c, "topright", True)
        # progreso hacia la meta
        lead = ranked[0]["sc"] if ranked else 0
        bar = pygame.Rect(W // 2 - 220, 16, 440, 14)
        pygame.draw.rect(self.screen, (10, 12, 20), bar.inflate(4, 4), border_radius=8)
        pygame.draw.rect(self.screen, col, (bar.x, bar.y, bar.w * clamp(lead / target, 0, 1), bar.h), border_radius=7)
        self.text(f"{mi.get('mname', '')}  ·  meta {target}", self.f_m, (W // 2, 46), TEXT, "center", True)
        # kill feed
        for i, (a, v, w, h, pts, _ttl, am, vm) in enumerate(reversed(self.feed)):
            wname = next((x["name"] for x in self.weapons if x["id"] == w), w)
            line = f"{a}  [{wname}{' · HEADSHOT' if h else ''}]  {v}  (+{pts})"
            c = GOOD if am else (BAD if vm else TEXT)
            self.text(line, self.f_m, (W - 16, 16 + i * 24), c, "topright", True)
        me = self.players.get(self.my_id)
        # armas
        n = len(self.weapons)
        if n:
            sw, gap = 100, 6
            x0 = W // 2 - (n * sw + (n - 1) * gap) // 2
            for i, w in enumerate(self.weapons):
                r = pygame.Rect(x0 + i * (sw + gap), H - 38, sw, 34)
                sel = i == self.weapon_idx
                pygame.draw.rect(self.screen, PANEL_LIGHT if sel else (14, 17, 28), r, border_radius=8)
                pygame.draw.rect(self.screen, ACCENT if sel else (50, 56, 80), r, 2, border_radius=8)
                self.text(str(i + 1), self.f_s, (r.x + 6, r.y + 3), MUTED)
                self.text(w["name"], self.f_s, (r.centerx + 4, r.centery + 2), TEXT if sel else MUTED, "center")
        if me:
            if mi.get("streak"):
                self.draw_streak_panel(me)
            else:
                pygame.draw.rect(self.screen, (10, 12, 20), (16, H - 32, 190, 22), border_radius=6)
                pygame.draw.rect(self.screen, GOOD if me["hp"] > 35 else BAD, (18, H - 30, 186 * me["hp"] / 100.0, 18), border_radius=5)
                self.text(f"VIDA {me['hp']}" + (f"  ·  racha {me['s']}" if me["s"] >= 2 else ""), self.f_s, (111, H - 21), TEXT, "center")
            if not me["al"]:
                self.text("ELIMINADO", self.f_big, (W // 2, H // 2 - 40), BAD, "center", True)
                self.text(f"Reapareces en {me['rt']:.1f} s", self.f_h, (W // 2, H // 2 + 8), TEXT, "center", True)
        if self.phase == "over":
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 150))
            self.screen.blit(ov, (0, 0))
            self.text("PARTIDA TERMINADA", self.f_big, (W // 2, H // 2 - 60), TEXT, "center", True)
            self.text(f"Ganador: {self.winner}", self.f_title, (W // 2, H // 2 + 6), ACCENT, "center", True)
            self.text(f"Nueva partida en {int(self.timer) + 1} s   ·   ESC para salir al menú", self.f_h, (W // 2, H // 2 + 76), TEXT, "center", True)

    def draw_streak_panel(self, me):
        s, m = me["s"], me["m"]
        base = self.minfo.get("head", [10, 34])[0]
        rows = [(f"RACHA  {s}", ACCENT, self.f_h),
                (f"Cabeza  +{int((me['hr'] / base - 1) * 100)}%", TEXT, self.f_m),
                (f"Velocidad  x{m[0]:.2f}", TEXT, self.f_m),
                (f"Salto  x{m[3]:.2f}", TEXT, self.f_m),
                (f"Cadencia  x{m[2]:.2f}", TEXT, self.f_m),
                (f"Puntos  x{m[1]}", GOOD if m[1] > 1 else TEXT, self.f_m)]
        pr = pygame.Rect(W - 264, 170, 250, 224)
        ov = pygame.Surface(pr.size, pygame.SRCALPHA)
        pygame.draw.rect(ov, (10, 12, 20, 170), ov.get_rect(), border_radius=12)
        self.screen.blit(ov, pr)
        y = pr.y + 10
        for txt, col, font in rows:
            self.text(txt, font, (pr.x + 14, y), col)
            y += 36 if font is self.f_h else 28
        nxt = 3 - (s % 3)
        if m[1] < 5:
            self.text(f"Puntos x{m[1] + 1} en {nxt} kill{'s' if nxt != 1 else ''}", self.f_s, (pr.x + 14, pr.bottom - 26), MUTED)


def main():
    App(sys.argv[1] if len(sys.argv) > 1 else None).run()


if __name__ == "__main__":
    main()
