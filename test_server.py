import http.client, json, threading, unittest
from http.server import ThreadingHTTPServer
import server

EAN = "4006381333931"

class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = server.connect(":memory:")
        server.ingest_csv(cls.db, "sample_feed.csv")
        server.seed_demo(cls.db)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.db))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_total_cents(self):
        self.assertEqual(server.total_cents(4999, 491, None), 5490)
        self.assertEqual(server.total_cents(5500, 490, 5000, 400), 5100)

    def test_offers_endpoint_et_promo(self):
        st, _, body = self.req("GET", f"/v1/products/{EAN}/offers")
        data = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual(len(data["offers"]), 3)
        demo = next(o for o in data["offers"] if o["merchant"] == "DemoMarket")
        self.assertEqual((demo["promoCode"], demo["promoDiscountCents"]), ("BIENVENUE10", 1000))
        self.assertIn("/out/", demo["trackedUrl"])
        self.assertEqual(self.req("GET", "/v1/products/1234567890123/offers")[0], 404)

    def test_out_redirige_et_compte_le_clic(self):
        st, headers, _ = self.req("GET", "/out/1")
        self.assertEqual(st, 302)
        self.assertIn(server.AFFILIATE_PARAMS, headers["Location"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM clicks").fetchone()[0], 1)

    def test_alerte_prix_une_seule_fois(self):
        self.assertEqual(self.req("POST", "/v1/watches", {"ean": EAN, "token": "tok12345", "targetCents": 8200})[0], 201)
        msgs = []
        self.assertEqual(server.check_watches(self.db, lambda t, m: msgs.append(m)), 1)  # 82,00 € avec le code promo
        self.assertEqual(server.check_watches(self.db, lambda t, m: msgs.append(m)), 0)
        self.assertIn("DemoMarket", msgs[0])

    def test_promo_ecartee_apres_votes_negatifs(self):
        pid = self.db.execute("SELECT id FROM promos WHERE code='BIENVENUE10'").fetchone()[0]
        for _ in range(3):
            self.req("POST", f"/v1/promos/{pid}/vote", {"ok": False})
        self.assertIsNone(server.best_promo(self.db, "DemoMarket", 9200))

class DeployTest(ServerTest):
    def test_health_et_https_derriere_proxy(self):
        self.assertEqual(self.req("GET", "/health")[0], 200)
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request("GET", f"/v1/products/{EAN}/offers", headers={"Host": "api.exemple.app", "X-Forwarded-Proto": "https"})
        url = json.loads(c.getresponse().read())["offers"][0]["trackedUrl"]
        self.assertTrue(url.startswith("https://api.exemple.app/out/"))

if __name__ == "__main__":
    unittest.main()
