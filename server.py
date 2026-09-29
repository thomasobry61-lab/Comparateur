"""Backend MVP (stdlib uniquement) : flux d'affiliation -> offres, promos, /out, alertes."""
import csv, json, os, re, sqlite3, threading, time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("DB_PATH", "comparateur.db")
# Paramètres de tracking de votre réseau d'affiliation, ex. "awc=1234_xyz" (Awin).
AFFILIATE_PARAMS = os.environ.get("AFFILIATE_PARAMS", "utm_source=comparateur")
LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(ean TEXT PRIMARY KEY, title TEXT, image_url TEXT);
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY, ean TEXT, merchant TEXT, price_cents INT,
  shipping_cents INT, free_over_cents INT, in_stock INT, url TEXT, updated_at REAL, UNIQUE(ean, merchant));
CREATE TABLE IF NOT EXISTS promos(id INTEGER PRIMARY KEY, merchant TEXT, code TEXT, discount_cents INT,
  min_cart_cents INT DEFAULT 0, up INT DEFAULT 0, down INT DEFAULT 0, UNIQUE(merchant, code));
CREATE TABLE IF NOT EXISTS clicks(id INTEGER PRIMARY KEY, offer_id INT, at REAL);
CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY, ean TEXT, token TEXT, target_cents INT, notified INT DEFAULT 0);
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def cents(v):
    v = re.sub(r"[^\d.]", "", v or "")
    return int((Decimal(v) * 100).to_integral_value()) if v else None


def ingest_rows(db, rows):
    """Colonnes (format proche Google Merchant) : ean,title,image,merchant,price,shipping,free_over,availability,link"""
    now = time.time()
    for r in rows:
        ean = r["ean"].strip()
        db.execute("INSERT INTO products VALUES(?,?,?) ON CONFLICT(ean) DO UPDATE SET "
                   "title=excluded.title, image_url=excluded.image_url", (ean, r["title"], r.get("image") or None))
        db.execute(
            "INSERT INTO offers(ean,merchant,price_cents,shipping_cents,free_over_cents,in_stock,url,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ean,merchant) DO UPDATE SET price_cents=excluded.price_cents, "
            "shipping_cents=excluded.shipping_cents, free_over_cents=excluded.free_over_cents, "
            "in_stock=excluded.in_stock, url=excluded.url, updated_at=excluded.updated_at",
            (ean, r["merchant"], cents(r["price"]), cents(r.get("shipping")), cents(r.get("free_over")),
             int((r.get("availability") or "in stock").lower().startswith("in")), r["link"], now))
    db.commit()


def ingest_csv(db, path):
    with open(path, newline="", encoding="utf-8") as f:
        ingest_rows(db, list(csv.DictReader(f)))


def seed_demo(db):
    db.execute("INSERT OR IGNORE INTO promos(merchant,code,discount_cents,min_cart_cents) "
               "VALUES('DemoMarket','BIENVENUE10',1000,3000)")
    db.commit()


def total_cents(price, shipping, free_over, promo=0):
    """Même règle que l'app Android (Offer.toPriced)."""
    discounted = max(price - promo, 0)
    free = free_over is not None and discounted >= free_over
    return discounted + (0 if free else (shipping or 0))


def best_promo(db, merchant, price):
    """Meilleur code applicable, écarté si la communauté le juge mort (down > up + 2)."""
    return db.execute("SELECT * FROM promos WHERE merchant=? AND min_cart_cents<=? AND down<=up+2 "
                      "ORDER BY discount_cents DESC LIMIT 1", (merchant, price)).fetchone()


def offers_for(db, ean, base):
    p = db.execute("SELECT * FROM products WHERE ean=?", (ean,)).fetchone()
    if not p:
        return None
    out = []
    for o in db.execute("SELECT * FROM offers WHERE ean=?", (ean,)):
        pr = best_promo(db, o["merchant"], o["price_cents"])
        out.append({"merchant": o["merchant"], "priceCents": o["price_cents"], "shippingCents": o["shipping_cents"],
                    "freeShippingOverCents": o["free_over_cents"], "inStock": bool(o["in_stock"]),
                    "trackedUrl": f"{base}/out/{o['id']}", "promoCode": pr["code"] if pr else None,
                    "promoDiscountCents": pr["discount_cents"] if pr else 0})
    return {"ean": ean, "title": p["title"], "imageUrl": p["image_url"], "offers": out}


def fcm_notify(token, message):
    # À brancher : Firebase Admin SDK (messaging.send) avec votre compte de service.
    print(f"[push -> {token[:8]}…] {message}")


def check_watches(db, notify=fcm_notify):
    sent = 0
    for w in db.execute("SELECT * FROM watches WHERE notified=0").fetchall():
        best = None
        for o in db.execute("SELECT * FROM offers WHERE ean=? AND in_stock=1", (w["ean"],)):
            pr = best_promo(db, o["merchant"], o["price_cents"])
            t = total_cents(o["price_cents"], o["shipping_cents"], o["free_over_cents"],
                            pr["discount_cents"] if pr else 0)
            if best is None or t < best[0]:
                best = (t, o["merchant"])
        if best and best[0] <= w["target_cents"]:
            notify(w["token"], f"Baisse de prix : {best[0] / 100:.2f} € chez {best[1]}")
            db.execute("UPDATE watches SET notified=1 WHERE id=?", (w["id"],))
            sent += 1
    db.commit()
    return sent


def locked(f):
    def wrapper(self):
        with LOCK:
            f(self)
    return wrapper


def make_handler(db):
    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _base(self):
            if os.environ.get("PUBLIC_URL"):
                return os.environ["PUBLIC_URL"]
            proto = self.headers.get("X-Forwarded-Proto", "http")  # derrière le proxy HTTPS de l'hébergeur
            return f"{proto}://{self.headers.get('Host')}"

        def _get(self):
            path = self.path.split("?")[0]
            if path == "/health":
                return self._json(200, {"ok": True})
            if m := re.fullmatch(r"/v1/products/(\d{8,14})/offers", path):
                r = offers_for(db, m[1], self._base())
                return self._json(200, r) if r else self._json(404, {"error": "produit inconnu"})
            if m := re.fullmatch(r"/out/(\d+)", path):
                o = db.execute("SELECT url FROM offers WHERE id=?", (m[1],)).fetchone()
                if not o:
                    return self._json(404, {"error": "offre inconnue"})
                db.execute("INSERT INTO clicks(offer_id, at) VALUES(?,?)", (m[1], time.time()))
                db.commit()
                self.send_response(302)
                self.send_header("Location", o["url"] + ("&" if "?" in o["url"] else "?") + AFFILIATE_PARAMS)
                self.end_headers()
                return
            self._json(404, {"error": "not found"})

        def _post(self):
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/v1/watches":
                cur = db.execute("INSERT INTO watches(ean,token,target_cents) VALUES(?,?,?)",
                                 (b["ean"], b["token"], int(b["targetCents"])))
                db.commit()
                return self._json(201, {"id": cur.lastrowid})
            if m := re.fullmatch(r"/v1/promos/(\d+)/vote", self.path):
                col = "up" if b.get("ok") else "down"
                db.execute(f"UPDATE promos SET {col}={col}+1 WHERE id=?", (m[1],))
                db.commit()
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

        do_GET, do_POST = locked(_get), locked(_post)

        def log_message(self, *a):
            pass

    return H


def alert_loop(every=900):
    db = connect()
    while True:
        time.sleep(every)
        check_watches(db)


if __name__ == "__main__":
    db = connect()
    if not db.execute("SELECT 1 FROM products").fetchone():
        ingest_csv(db, os.path.join(os.path.dirname(__file__), "sample_feed.csv"))
        seed_demo(db)
    threading.Thread(target=alert_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    print(f"API sur http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(db)).serve_forever()
