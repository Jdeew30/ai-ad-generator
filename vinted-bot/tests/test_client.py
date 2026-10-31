import gzip
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from helpers import FakeTransport, api_item, search_response

from vintedbot.client import Blocked, Response, UrllibTransport, VintedClient, VintedError, parse_item, \
    parse_search_url


def client(transport):
    return VintedClient("www.vinted.fr", transport=transport, min_interval=0, sleep=lambda s: None)


class SearchTests(unittest.TestCase):
    def test_session_then_search_with_cookies_and_anon_id(self):
        t = FakeTransport([search_response([api_item(1, "PS5 Slim", 300)])])
        items = client(t).search({"search_text": "ps5", "attribute_ids[catalog]": "3025"}, per_page=20)
        self.assertEqual([i.title for i in items], ["PS5 Slim"])
        home_url, _ = t.requests[0]
        api_url, headers = t.requests[1]
        self.assertEqual(home_url, "https://www.vinted.fr/")
        parts = urlsplit(api_url)
        self.assertEqual((parts.netloc, parts.path), ("api.vinted.fr", "/svc-catalogue/items"))
        query = parse_qs(parts.query)
        self.assertEqual(query["order"], ["newest_first"])
        self.assertEqual(query["per_page"], ["20"])
        self.assertEqual(query["attribute_ids[catalog]"], ["3025"])
        self.assertIn("access_token_web=token1", headers["Cookie"])
        self.assertEqual(headers["X-Anon-Id"], "anon-1")
        self.assertEqual(headers["Locale"], "fr-FR")

    def test_expired_session_is_renewed_once(self):
        t = FakeTransport([Response(401, {}, b""), search_response([api_item(1, "a", 10)])])
        self.assertEqual(len(client(t).search({"search_text": "a"})), 1)
        self.assertEqual(t.resets, 2)
        t = FakeTransport([Response(401, {}, b""), Response(401, {}, b"")])
        with self.assertRaises(VintedError):
            client(t).search({"search_text": "a"})

    def test_blocked_and_errors(self):
        with self.assertRaises(Blocked):
            client(FakeTransport([Response(429, {}, b"")])).search({"search_text": "a"})
        with self.assertRaises(Blocked):
            client(FakeTransport(home_status=403)).search({"search_text": "a"})
        with self.assertRaises(VintedError):
            client(FakeTransport([Response(200, {}, b"<html>captcha</html>")])).search({"search_text": "a"})

    def test_throttle_spaces_requests(self):
        waits, now = [], [0.0]
        c = VintedClient("www.vinted.fr", transport=FakeTransport([search_response([])] * 2), min_interval=3,
                         clock=lambda: now[0], sleep=lambda s: (waits.append(s), now.__setitem__(0, now[0] + s)))
        c.search({"search_text": "a"})
        c.search({"search_text": "a"})
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(w >= 3 for w in waits))


class ParsingTests(unittest.TestCase):
    def test_current_and_legacy_price_formats(self):
        for legacy in (False, True):
            item = parse_item(api_item(5, "Switch OLED", 199.9, legacy_price=legacy, total=210.6), "www.vinted.fr")
            self.assertAlmostEqual(item.price, 199.9)
            self.assertAlmostEqual(item.total_price, 210.6)
            self.assertEqual(item.currency, "EUR")
            self.assertEqual(item.condition, "tres_bon")
            self.assertTrue(item.photo_url.endswith("5.jpg"))
            self.assertIsNotNone(item.created_at)

    def test_unusable_items_are_skipped(self):
        self.assertIsNone(parse_item({"id": 1, "title": "sans prix"}, "www.vinted.fr"))
        self.assertIsNone(parse_item(api_item(2, "masquée", 10, visible=False), "www.vinted.fr"))
        minimal = parse_item({"id": 3, "price": "12", "path": "/items/3-x"}, "www.vinted.be")
        self.assertEqual(minimal.url, "https://www.vinted.be/items/3-x")
        self.assertEqual(minimal.condition, "inconnu")

    def test_parse_search_url(self):
        host, params = parse_search_url(
            "https://www.vinted.fr/catalog?search_text=jordan%204&catalog[]=1231&brand_ids[]=53"
            "&size_ids[]=1196&size_ids[]=1197&status_ids[]=6&price_to=150&order=relevance&page=2")
        self.assertEqual(host, "www.vinted.fr")
        self.assertEqual(params, {"search_text": "jordan 4", "attribute_ids[catalog]": "1231",
                                  "attribute_ids[brand]": "53", "attribute_ids[size]": "1196,1197",
                                  "attribute_ids[status]": "6", "price_to": "150"})
        self.assertEqual(parse_search_url("https://vinted.it/catalog?search_text=x")[0], "www.vinted.it")
        with self.assertRaises(ValueError):
            parse_search_url("https://www.leboncoin.fr/recherche?text=ps5")


class UrllibTransportTests(unittest.TestCase):
    """Vrai HTTP en local : cookies conservés et réponses gzip décodées."""

    def test_cookies_and_gzip(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = gzip.compress(json.dumps({"cookie": self.headers.get("Cookie")}).encode())
                self.send_response(200)
                self.send_header("Set-Cookie", "access_token_web=abc; Path=/")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            t = UrllibTransport()
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            first = t.get(url, {})
            self.assertEqual(first.cookies, {"access_token_web": "abc"})
            self.assertEqual(json.loads(t.get(url, {}).body)["cookie"], "access_token_web=abc")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
