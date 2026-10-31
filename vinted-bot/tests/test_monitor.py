import unittest

from helpers import NOW, FakeClient, FakeNotifier, listing, make_settings, make_watch

from vintedbot.client import Blocked
from vintedbot.monitor import Monitor
from vintedbot.store import Store


def market_page(start_id: int, n: int = 20, price: float = 300.0) -> list:
    return [listing(start_id + i, f"PS5 slim n°{i}", price + (i % 5) * 5, age_minutes=600) for i in range(n)]


class Harness:
    def __init__(self, pages, **watch_kwargs) -> None:
        self.watch = make_watch(**watch_kwargs)
        self.settings = make_settings(self.watch, per_page=48, bootstrap_pages=2)
        self.client = FakeClient(pages)
        self.store = Store()
        self.notifier = FakeNotifier()
        self.now = NOW
        self.monitor = Monitor(self.settings, self.client, self.store, [self.notifier], clock=lambda: self.now)


class MonitorTests(unittest.TestCase):
    def test_bootstrap_learns_market_and_sends_digest_only(self):
        bargain = listing(500, "PS5 slim très bon état", 180, age_minutes=900)
        h = Harness([market_page(1) + [bargain]])
        result = h.monitor.check(h.watch)
        self.assertTrue(result.bootstrap)
        self.assertEqual(result.stats.count, 20)  # l'affaire à 180 € est écartée du calcul (valeur aberrante)
        self.assertEqual(h.client.calls[0][1], 1)
        self.assertEqual(len(h.client.calls), 1)  # moins de 48 annonces : pas de 2e page
        self.assertIn("Marché : prix médian", h.notifier.texts[0])
        self.assertIn("Meilleures offres déjà en ligne", h.notifier.texts[1])
        self.assertEqual([ev.listing.id for ev, _ in h.notifier.deals], [500])

    def test_new_deal_alert_once(self):
        h = Harness([market_page(1)])
        h.monitor.check(h.watch)
        deal = listing(900, "PS5 slim + 2 manettes", 190, age_minutes=2)
        normal = listing(901, "PS5 slim", 300, age_minutes=2)
        h.client.pages = [[deal, normal], [deal, normal]]
        result = h.monitor.check(h.watch)
        self.assertEqual(result.fresh, 2)
        self.assertEqual([ev.listing.id for ev, _ in h.notifier.deals], [900])
        h.monitor.check(h.watch)
        self.assertEqual(len(h.notifier.deals), 1)  # pas de doublon

    def test_price_drop_is_reevaluated(self):
        h = Harness([market_page(1) + [listing(700, "PS5 slim", 290)]])
        h.monitor.check(h.watch)
        h.client.pages = [[listing(700, "PS5 slim", 180)]]
        h.monitor.check(h.watch)
        ev, _ = h.notifier.deals[-1]
        self.assertEqual((ev.listing.id, ev.previous_price), (700, 290))

    def test_learning_phase_message(self):
        h = Harness([market_page(1, n=5)])
        h.monitor.check(h.watch)
        self.assertIn("apprentissage en cours : 5/10", h.notifier.texts[0])

    def test_filters_apply_before_alerting(self):
        h = Harness([market_page(1)], max_price=200)
        h.monitor.check(h.watch)
        h.client.pages = [[listing(800, "PS5 slim", 210), listing(801, "Boîte vide PS5 slim", 150),
                           listing(802, "PS5 slim", 185)]]
        h.monitor.check(h.watch)
        self.assertEqual([ev.listing.id for ev, _ in h.notifier.deals], [802])

    def test_suspicious_offers_come_after_real_deals(self):
        h = Harness([market_page(1)])
        h.monitor.check(h.watch)
        h.client.pages = [[listing(950, "PS5 slim neuve sous blister", 100, status="Neuf avec étiquette"),
                           listing(951, "PS5 slim", 200)]]
        h.monitor.check(h.watch)
        labels = [ev.label for ev, _ in h.notifier.deals]
        self.assertEqual([ev.listing.id for ev, _ in h.notifier.deals], [951, 950])
        self.assertEqual(labels[1], "⚠️ À VÉRIFIER")

    def test_max_alerts_per_check_and_sorting(self):
        h = Harness([market_page(1)])
        h.settings.max_alerts_per_check = 2
        h.monitor.check(h.watch)
        h.client.pages = [[listing(900 + i, "PS5 slim", 150 + 5 * i, age_minutes=1) for i in range(5)]]
        h.monitor.check(h.watch)
        self.assertEqual([ev.listing.id for ev, _ in h.notifier.deals], [900, 901])

    def test_run_backs_off_when_blocked_and_recovers(self):
        h = Harness([market_page(1)])
        h.client.error = Blocked("HTTP 429")
        h.monitor.run(max_cycles=1)
        self.assertEqual(h.monitor.backoff, 60)
        self.assertTrue(any("limite temporairement" in t for t in h.notifier.texts))
        h.client.error = None
        h.monitor.run(max_cycles=1)
        self.assertEqual(h.monitor.backoff, 0)
        self.assertTrue(any("rétabli" in t for t in h.notifier.texts))

    def test_run_survives_network_errors(self):
        h = Harness([])
        h.client.error = OSError("réseau")
        h.monitor.run(max_cycles=1)
        self.assertTrue(h.notifier.texts[0].startswith("🟢"))
        self.assertTrue(h.notifier.texts[-1].startswith("🔴"))


if __name__ == "__main__":
    unittest.main()
