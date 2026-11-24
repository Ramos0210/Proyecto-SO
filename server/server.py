#!/usr/bin/env python3
"""
HEADSHOT ARENA - Servidor
=========================
* Sockets TCP + hilos (threading). Protocolo: JSON, un mensaje por línea.
* SQLite para usuarios (contraseñas con PBKDF2), estadísticas y ranking por modo.
* Servidor autoritativo: física, disparos, daño, rachas y puntos se calculan aquí.

Modos (cada uno es una sala independiente):
  only200 -> Solo cuentan los headshots (1 tiro = muerte). Racha = cabeza más grande
             + multiplicadores de velocidad, salto, cadencia y puntos. Gana quien llegue a 200.
  pvp     -> PvP clásico con armas de fuego y cuerpo a cuerpo (vida 100, headshot x2).
  melee   -> Solo armas cuerpo a cuerpo.

Uso:  python server.py [--host 0.0.0.0] [--port 5555] [--db ruta.db]
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import queue
import random
import re
import secrets
import socket
import sqlite3
import threading
import time

HOST = "0.0.0.0"
PORT = 5555
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

# ----------------------------------------------------------------------------
# Configuración del juego
# ----------------------------------------------------------------------------
WORLD_W, WORLD_H = 1280, 720
SIM_HZ = 60
SEND_EVERY = 2            # se envía el estado a 30 Hz
RESPAWN_TIME = 2.0
PROTECT_TIME = 1.5
INTERMISSION = 8.0
GRAVITY = 1900.0
RUN_SPEED = 260.0
JUMP_V = 800.0
HEAD_BASE = 10.0
HEAD_GROW = 2.0
HEAD_MAX = 34.0
NECK_H = 54.0             # altura del cuello sobre los pies
SHOULDER_H = 50.0
BODY_HALF_W = 11.0
MAX_HP = 100.0
BASE_POINTS = 10
HEAD_BONUS = 5
RANGE = 1800.0

# (x, y_superior, ancho, alto). Todas son plataformas de "un solo sentido".
PLATFORMS = [
    (0, 680, 1280, 40),
    (160, 540, 260, 20), (860, 540, 260, 20),
    (510, 420, 260, 20),
    (40, 400, 180, 20), (1060, 400, 180, 20),
    (280, 280, 220, 20), (780, 280, 220, 20),
    (540, 160, 200, 20),
]
SPAWNS = [(290, 540), (990, 540), (640, 420), (130, 400), (1150, 400),
          (390, 280), (890, 280), (640, 160), (200, 680), (1080, 680), (640, 680)]

WEAPONS = {
    "hs":      dict(name="Revólver HS", kind="gun", dmg=1000, cd=0.50, spread=0.0, pellets=1, rng=RANGE, hm=1.0),
    "pistol":  dict(name="Pistola", kind="gun", dmg=28, cd=0.35, spread=0.02, pellets=1, rng=1500, hm=2.0),
    "smg":     dict(name="Subfusil", kind="gun", dmg=13, cd=0.085, spread=0.06, pellets=1, rng=1100, hm=2.0),
    "shotgun": dict(name="Escopeta", kind="gun", dmg=11, cd=0.85, spread=0.16, pellets=8, rng=520, hm=2.0),
    "sniper":  dict(name="Francotirador", kind="gun", dmg=85, cd=1.30, spread=0.0, pellets=1, rng=RANGE, hm=2.0),
    "knife":   dict(name="Cuchillo", kind="melee", dmg=34, cd=0.32, reach=62, kb=120, hm=1.5),
    "sword":   dict(name="Espada", kind="melee", dmg=52, cd=0.60, reach=92, kb=200, hm=1.5),
    "bat":     dict(name="Bate", kind="melee", dmg=45, cd=0.75, reach=78, kb=820, hm=1.5),
    "axe":     dict(name="Hacha", kind="melee", dmg=80, cd=1.05, reach=84, kb=300, hm=1.5),
}

MODES = {
    "only200": dict(name="Only 200", only_head=True, streak=True, target=200, weapons=["hs"],
                    desc="Solo cuentan los headshots. Cada kill seguida agranda tu cabeza y te da "
                         "multiplicadores. Gana el primero en llegar a 200 puntos."),
    "pvp": dict(name="PvP clásico", only_head=False, streak=False, target=200,
                weapons=["pistol", "smg", "shotgun", "sniper", "knife", "sword", "bat", "axe"],
                desc="Todas las armas: pistola, subfusil, escopeta, francotirador y cuerpo a cuerpo. "
                     "Headshot x2. Primero a 200 puntos."),
    "melee": dict(name="Cuerpo a cuerpo", only_head=False, streak=False, target=100,
                  weapons=["knife", "sword", "bat", "axe"],
                  desc="Solo armas cuerpo a cuerpo: cuchillo, espada, bate y hacha. Primero a 100 puntos."),
}


# Multiplicadores por racha (solo modo Only 200)
def speed_mult(s): return 1.0 + 0.05 * min(s, 10)
def jump_mult(s): return 1.0 + 0.02 * min(s, 10)
def fire_mult(s): return 1.0 + 0.06 * min(s, 10)
def score_mult(s): return min(1 + s // 3, 5)


# ----------------------------------------------------------------------------
# Geometría
# ----------------------------------------------------------------------------
def ray_circle(ox, oy, dx, dy, cx, cy, r):
    ocx, ocy = cx - ox, cy - oy
    t = ocx * dx + ocy * dy
    if t < 0:
        return None
    d2 = ocx * ocx + ocy * ocy - t * t
    if d2 > r * r:
        return None
    return max(0.0, t - math.sqrt(r * r - d2))


def ray_aabb(ox, oy, dx, dy, x0, y0, x1, y1):
    tmin, tmax = 0.0, 1e9
    for o, d, lo, hi in ((ox, dx, x0, x1), (oy, dy, y0, y1)):
        if abs(d) < 1e-9:
            if o < lo or o > hi:
                return None
        else:
            t1, t2 = (lo - o) / d, (hi - o) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax:
                return None
    return tmin


# ----------------------------------------------------------------------------
# Base de datos
# ----------------------------------------------------------------------------
USER_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex() + "$" + h.hex()


def verify_password(password, stored):
    try:
        salt_hex, _ = stored.split("$", 1)
        candidate = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate, stored)
    except ValueError:
        return False


class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS users(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                       password_hash TEXT NOT NULL)""")
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS mode_stats(
                       user_id INTEGER NOT NULL,
                       mode TEXT NOT NULL,
                       wins INTEGER NOT NULL DEFAULT 0,
                       total_score INTEGER NOT NULL DEFAULT 0,
                       kills INTEGER NOT NULL DEFAULT 0,
                       headshots INTEGER NOT NULL DEFAULT 0,
                       best_streak INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY(user_id, mode))""")
            self.conn.commit()

    def register(self, username, password):
        if not USER_RE.match(username):
            return False, "Usuario: 3-16 caracteres (letras, números o _)", None
        if not (4 <= len(password) <= 64):
            return False, "La contraseña debe tener entre 4 y 64 caracteres", None
        with self.lock:
            try:
                self.conn.execute("INSERT INTO users(username, password_hash) VALUES(?, ?)",
                                  (username, hash_password(password)))
                self.conn.commit()
            except sqlite3.IntegrityError:
                return False, "Ese usuario ya existe", None
        return True, "Cuenta creada", username

    def login(self, username, password):
        with self.lock:
            row = self.conn.execute("SELECT username, password_hash FROM users WHERE username = ?",
                                    (username,)).fetchone()
        if not row or not verify_password(password, row[1]):
            return False, "Usuario o contraseña incorrectos", None
        return True, "Bienvenido", row[0]

    def add_match(self, username, mode, score, kills, headshots, best, won):
        with self.lock:
            row = self.conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                return
            uid = row[0]
            self.conn.execute("INSERT OR IGNORE INTO mode_stats(user_id, mode) VALUES(?, ?)", (uid, mode))
            self.conn.execute(
                """UPDATE mode_stats SET wins = wins + ?, total_score = total_score + ?,
                       kills = kills + ?, headshots = headshots + ?,
                       best_streak = MAX(best_streak, ?)
                   WHERE user_id = ? AND mode = ?""",
                (1 if won else 0, int(score), int(kills), int(headshots), int(best), uid, mode))
            self.conn.commit()

    def stats(self, username):
        with self.lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(m.wins),0), COALESCE(SUM(m.total_score),0), COALESCE(SUM(m.kills),0),
                          COALESCE(SUM(m.headshots),0), COALESCE(MAX(m.best_streak),0)
                   FROM users u LEFT JOIN mode_stats m ON m.user_id = u.id WHERE u.username = ?""",
                (username,)).fetchone()
        keys = ("wins", "score", "kills", "headshots", "best")
        return dict(zip(keys, row)) if row else dict.fromkeys(keys, 0)

    def ranking(self, mode="all", limit=10):
        sql = """SELECT u.username, SUM(m.wins), SUM(m.total_score), SUM(m.kills),
                        SUM(m.headshots), MAX(m.best_streak)
                 FROM users u JOIN mode_stats m ON m.user_id = u.id"""
        args = []
        if mode in MODES:
            sql += " WHERE m.mode = ?"
            args.append(mode)
        sql += " GROUP BY u.id ORDER BY SUM(m.wins) DESC, SUM(m.total_score) DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [list(r) for r in rows]


# ----------------------------------------------------------------------------
# Lógica de partida
# ----------------------------------------------------------------------------
class Player:
    def __init__(self, pid, name, client, weapon):
        self.id, self.name, self.client = pid, name, client
        self.x = self.y = 0.0
        self.vx = self.vy = self.kvx = 0.0
        self.on_ground = False
        self.inp = {"l": False, "r": False, "j": False, "d": False, "fire": False}
        self.aim = 0.0
        self.alive = False
        self.hp = MAX_HP
        self.respawn_at = 0.0
        self.protect_until = 0.0
        self.next_shot = 0.0
        self.weapon = weapon
        self.streak = self.best = self.kills = self.hs = self.score = 0
        self.flushed = False


class Game:
    def __init__(self, key, db, target=None):
        self.key, self.mode, self.db = key, MODES[key], db
        self.target = target or self.mode["target"]
        self.lock = threading.RLock()
        self.players = {}
        self.next_id = 1
        self.phase = "play"
        self.winner = None
        self.phase_end = 0.0
        self.events = []

    # --- helpers ----------------------------------------------------------
    def default_weapon(self):
        return self.mode["weapons"][0]

    def head_r(self, p):
        if self.mode["streak"]:
            return min(HEAD_BASE + HEAD_GROW * p.streak, HEAD_MAX)
        return HEAD_BASE

    def info(self):
        return {"id": self.key, "name": self.mode["name"], "desc": self.mode["desc"],
                "players": len(self.players), "target": self.target}

    def join_msg(self, pid):
        return {"t": "joined", "id": pid, "mode": self.key, "mname": self.mode["name"],
                "only_head": self.mode["only_head"], "streak": self.mode["streak"],
                "target": self.target, "platforms": PLATFORMS, "w": WORLD_W, "h": WORLD_H,
                "head": [HEAD_BASE, HEAD_MAX],
                "weapons": [{"id": w, "name": WEAPONS[w]["name"], "kind": WEAPONS[w]["kind"],
                             "reach": WEAPONS[w].get("reach", 0)} for w in self.mode["weapons"]]}

    # --- jugadores --------------------------------------------------------
    def add(self, client, name):
        with self.lock:
            pid = self.next_id
            self.next_id += 1
            p = Player(pid, name, client, self.default_weapon())
            self.players[pid] = p
            self._spawn(p, time.monotonic())
            return pid

    def remove(self, pid):
        with self.lock:
            p = self.players.pop(pid, None)
            if p and not p.flushed and (p.score or p.kills):
                self._flush(p, False)

    def set_input(self, pid, m):
        with self.lock:
            p = self.players.get(pid)
            if not p:
                return
            i = p.inp
            for k in ("l", "r", "j", "d", "fire"):
                i[k] = bool(m.get(k))
            try:
                a = float(m.get("aim", 0.0))
                if math.isfinite(a):
                    p.aim = a
            except (TypeError, ValueError):
                pass
            w = m.get("w")
            if isinstance(w, str) and w != p.weapon and w in self.mode["weapons"]:
                p.weapon = w
                p.next_shot = max(p.next_shot, time.monotonic() + 0.2)

    def _spawn(self, p, now):
        others = [q for q in self.players.values() if q is not p and q.alive]
        best, best_d = random.choice(SPAWNS), -1
        for sx, sy in random.sample(SPAWNS, len(SPAWNS)):
            d = min((math.hypot(sx - q.x, sy - q.y) for q in others), default=1e9)
            if d > best_d:
                best, best_d = (sx, sy), d
        p.x, p.y = best
        p.vx = p.vy = p.kvx = 0.0
        p.on_ground = True
        p.alive = True
        p.hp = MAX_HP
        p.protect_until = now + PROTECT_TIME

    # --- simulación -------------------------------------------------------
    def step(self, dt, now):
        with self.lock:
            playing = self.phase == "play"
            for p in self.players.values():
                if not p.alive:
                    if playing and now >= p.respawn_at:
                        self._spawn(p, now)
                    continue
                self._move(p, dt, playing)
                if playing and p.inp["fire"] and now >= p.next_shot:
                    w = WEAPONS[p.weapon]
                    cd = w["cd"] / (fire_mult(p.streak) if self.mode["streak"] else 1.0)
                    p.next_shot = now + cd
                    if w["kind"] == "gun":
                        self._fire_gun(p, now, w)
                    else:
                        self._melee(p, now, w)
            if self.phase == "over" and now >= self.phase_end:
                self._new_match(now)

    def _move(self, p, dt, playing):
        inp = p.inp if playing else {"l": False, "r": False, "j": False, "d": False, "fire": False}
        st = p.streak if self.mode["streak"] else 0
        d = (1 if inp["r"] else 0) - (1 if inp["l"] else 0)
        p.vx = d * RUN_SPEED * speed_mult(st) + p.kvx
        p.kvx *= max(0.0, 1.0 - 8.0 * dt)
        if inp["j"] and p.on_ground:
            p.vy = -JUMP_V * jump_mult(st)
            p.on_ground = False
        p.vy += GRAVITY * dt
        prev_y = p.y
        p.x = min(max(p.x + p.vx * dt, 15.0), WORLD_W - 15.0)
        p.y += p.vy * dt
        p.on_ground = False
        if p.vy >= 0:
            for px, py, pw, _ph in PLATFORMS:
                if inp["d"] and py < 680:
                    continue
                if px - BODY_HALF_W <= p.x <= px + pw + BODY_HALF_W and prev_y <= py + 0.5 and p.y >= py:
                    p.y, p.vy, p.on_ground = float(py), 0.0, True
                    break

    # --- combate ----------------------------------------------------------
    def _targets(self, att, now):
        return [q for q in self.players.values() if q is not att and q.alive and now >= q.protect_until]

    def _ray_target(self, att, ox, oy, dx, dy, rng, now):
        best = None
        for q in self._targets(att, now):
            r = self.head_r(q)
            th = ray_circle(ox, oy, dx, dy, q.x, q.y - NECK_H - r, r)
            if th is not None and th <= rng and (best is None or th < best[0]):
                best = (th, q, True)
            if not self.mode["only_head"]:
                tb = ray_aabb(ox, oy, dx, dy, q.x - BODY_HALF_W, q.y - NECK_H, q.x + BODY_HALF_W, q.y)
                if tb is not None and tb <= rng and (best is None or tb < best[0]):
                    best = (tb, q, False)
        return best

    def _fire_gun(self, p, now, w):
        ox, oy = p.x, p.y - SHOULDER_H
        ends, dmgmap = [], {}
        for _ in range(w["pellets"]):
            a = p.aim + (random.uniform(-w["spread"], w["spread"]) if w["spread"] else 0.0)
            dx, dy = math.cos(a), math.sin(a)
            hit = self._ray_target(p, ox, oy, dx, dy, w["rng"], now)
            if hit:
                t, q, head = hit
                dmg = w["dmg"] * (w["hm"] if head else 1.0)
                entry = dmgmap.setdefault(q.id, [q, 0.0, False])
                entry[1] += dmg
                entry[2] = entry[2] or head
                ends.append([round(ox + dx * t, 1), round(oy + dy * t, 1), 1])
            else:
                ends.append([round(ox + dx * w["rng"], 1), round(oy + dy * w["rng"], 1), 0])
        self.events.append({"k": "shot", "id": p.id, "w": p.weapon, "x": round(ox, 1), "y": round(oy, 1), "e": ends})
        for q, dmg, head in dmgmap.values():
            self._damage(p, q, dmg, head, now)

    def _melee(self, p, now, w):
        ox, oy = p.x, p.y - SHOULDER_H
        dx, dy = math.cos(p.aim), math.sin(p.aim)
        reach = w["reach"]
        self.events.append({"k": "swing", "id": p.id, "w": p.weapon})

        def facing(px, py):
            vx, vy = px - ox, py - oy
            d = math.hypot(vx, vy)
            return d < 18 or (vx * dx + vy * dy) / d >= 0.25

        for q in self._targets(p, now):
            r = self.head_r(q)
            hx, hy = q.x, q.y - NECK_H - r
            head_ok = math.hypot(hx - ox, hy - oy) - r <= reach and facing(hx, hy)
            cx = min(max(ox, q.x - BODY_HALF_W), q.x + BODY_HALF_W)
            cy = min(max(oy, q.y - NECK_H), q.y)
            body_ok = math.hypot(cx - ox, cy - oy) <= reach and facing(cx, cy)
            if not (head_ok or body_ok):
                continue
            dmg = w["dmg"] * (w["hm"] if head_ok else 1.0)
            direction = 1.0 if (q.x - p.x) >= 0 else -1.0
            q.kvx += direction * w["kb"]
            if w["kb"] > 300:
                q.vy, q.on_ground = -280.0, False
            self._damage(p, q, dmg, head_ok, now)

    def _damage(self, att, vic, dmg, head, now):
        if not vic.alive:
            return
        vic.hp -= dmg
        self.events.append({"k": "hit", "v": vic.id, "d": int(min(dmg, 999)), "h": 1 if head else 0,
                            "x": round(vic.x, 1), "y": round(vic.y - NECK_H - self.head_r(vic), 1)})
        if vic.hp <= 0:
            self._kill(att, vic, head, now)

    def _kill(self, att, vic, head, now):
        r_old = self.head_r(vic)
        lost = vic.streak
        vic.alive, vic.hp = False, 0.0
        vic.respawn_at = now + RESPAWN_TIME
        vic.streak = 0
        att.streak += 1
        att.best = max(att.best, att.streak)
        att.kills += 1
        if head:
            att.hs += 1
        if self.mode["streak"]:
            pts = BASE_POINTS * score_mult(att.streak)
        else:
            pts = BASE_POINTS + (HEAD_BONUS if head else 0)
        att.score += pts
        self.events.append({"k": "kill", "a": att.name, "ai": att.id, "v": vic.name, "vi": vic.id,
                            "w": att.weapon, "h": 1 if head else 0, "pts": pts, "s": att.streak,
                            "ls": lost, "r": r_old,
                            "x": round(vic.x, 1), "y": round(vic.y - NECK_H - r_old, 1)})
        if self.phase == "play" and att.score >= self.target:
            self._end_match(att, now)

    # --- partidas ---------------------------------------------------------
    def _flush(self, p, won):
        if p.flushed or p.client is None:
            p.flushed = True
            return
        p.flushed = True
        try:
            self.db.add_match(p.name, self.key, p.score, p.kills, p.hs, p.best, won)
            p.client.send({"t": "stats", "stats": self.db.stats(p.name)})
        except Exception as ex:  # la base de datos nunca debe tumbar el juego
            print("[db] error:", ex)

    def _end_match(self, winner, now):
        self.phase, self.winner = "over", winner.name
        self.phase_end = now + INTERMISSION
        for p in self.players.values():
            p.inp["fire"] = False
            self._flush(p, p is winner)

    def _new_match(self, now):
        self.phase, self.winner = "play", None
        for p in self.players.values():
            p.score = p.kills = p.hs = p.streak = p.best = 0
            p.flushed = False
            p.weapon = self.default_weapon()
            self._spawn(p, now)

    # --- estado -----------------------------------------------------------
    def broadcast(self, now):
        with self.lock:
            if not self.players:
                self.events = []
                return
            plist = []
            for p in self.players.values():
                st = p.streak if self.mode["streak"] else 0
                plist.append({
                    "i": p.id, "n": p.name, "x": round(p.x, 1), "y": round(p.y, 1),
                    "a": round(p.aim, 3), "al": 1 if p.alive else 0, "s": p.streak,
                    "hr": round(self.head_r(p), 1), "sc": p.score, "k": p.kills,
                    "g": 1 if p.on_ground else 0, "vx": round(p.vx), "hp": int(max(p.hp, 0)),
                    "pr": 1 if now < p.protect_until else 0, "w": p.weapon,
                    "rt": round(max(0.0, p.respawn_at - now), 1) if not p.alive else 0,
                    "m": [round(speed_mult(st), 2), score_mult(st) if self.mode["streak"] else 1,
                          round(fire_mult(st), 2), round(jump_mult(st), 2)],
                })
            snap = {"t": "state", "ph": self.phase, "win": self.winner,
                    "tm": round(max(0.0, self.phase_end - now), 1) if self.phase == "over" else 0,
                    "p": plist, "ev": self.events}
            self.events = []
            clients = [p.client for p in self.players.values() if p.client]
        data = (json.dumps(snap, separators=(",", ":")) + "\n").encode()
        for c in clients:
            c.send_raw(data)


# ----------------------------------------------------------------------------
# Red
# ----------------------------------------------------------------------------
GAMES = {}
ONLINE = set()
ONLINE_LOCK = threading.Lock()


class Client:
    def __init__(self, sock, addr):
        self.sock, self.addr = sock, addr
        self.q = queue.Queue(maxsize=400)
        self.user = None
        self.game = None
        self.pid = None
        threading.Thread(target=self._writer, daemon=True).start()

    def send(self, obj):
        self.send_raw((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def send_raw(self, data):
        try:
            self.q.put_nowait(data)
        except queue.Full:
            pass  # cliente lento: se descarta

    def _writer(self):
        while True:
            data = self.q.get()
            if data is None:
                break
            try:
                self.sock.sendall(data)
            except OSError:
                break

    def leave_game(self):
        if self.game and self.pid is not None:
            self.game.remove(self.pid)
        self.game, self.pid = None, None

    def close(self):
        self.leave_game()
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def handle_message(c, m, db):
    t = m.get("t")
    if c.user is None:
        if t in ("login", "register"):
            user, pw = str(m.get("u", "")).strip(), str(m.get("p", ""))
            ok, msg, name = (db.register if t == "register" else db.login)(user, pw)
            if ok:
                with ONLINE_LOCK:
                    if name.lower() in ONLINE:
                        ok, msg = False, "Ese usuario ya está conectado"
                    else:
                        ONLINE.add(name.lower())
                        c.user = name
            c.send({"t": "auth", "ok": ok, "msg": msg, "user": c.user,
                    "stats": db.stats(c.user) if ok else None})
        return
    if t == "input":
        if c.game:
            c.game.set_input(c.pid, m)
    elif t == "join":
        key = m.get("mode")
        if key in GAMES and c.game is None:
            c.game = GAMES[key]
            c.pid = c.game.add(c, c.user)
            c.send(c.game.join_msg(c.pid))
    elif t == "leave":
        c.leave_game()
        c.send({"t": "stats", "stats": db.stats(c.user)})
    elif t == "modes":
        c.send({"t": "modes", "rows": [g.info() for g in GAMES.values()]})
    elif t == "ranking":
        mode = m.get("mode", "all")
        c.send({"t": "ranking", "mode": mode if mode in MODES else "all",
                "rows": db.ranking(mode if mode in MODES else "all")})
    elif t == "stats":
        c.send({"t": "stats", "stats": db.stats(c.user)})


def serve_client(sock, addr, db):
    c = Client(sock, addr)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buf = b""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            buf += data
            if len(buf) > 65536:
                break
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                    if isinstance(m, dict):
                        handle_message(c, m, db)
                except (ValueError, TypeError, KeyError):
                    continue
    except OSError:
        pass
    finally:
        if c.user:
            with ONLINE_LOCK:
                ONLINE.discard(c.user.lower())
        c.close()


def game_loop():
    dt = 1.0 / SIM_HZ
    nxt = time.perf_counter()
    tick = 0
    while True:
        now = time.monotonic()
        for g in GAMES.values():
            g.step(dt, now)
        tick += 1
        if tick % SEND_EVERY == 0:
            for g in GAMES.values():
                g.broadcast(now)
        nxt += dt
        sleep = nxt - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        else:
            nxt = time.perf_counter()


def main():
    ap = argparse.ArgumentParser(description="Servidor de HEADSHOT ARENA")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--target", type=int, default=None, help="puntos para ganar (anula todos los modos; útil para pruebas)")
    args = ap.parse_args()

    db = Database(args.db)
    for key in MODES:
        GAMES[key] = Game(key, db, args.target)
    threading.Thread(target=game_loop, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(32)
    print(f"[server] escuchando en {args.host}:{args.port}  (db: {args.db})", flush=True)
    try:
        while True:
            sock, addr = srv.accept()
            threading.Thread(target=serve_client, args=(sock, addr, db), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[server] cerrando…")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
