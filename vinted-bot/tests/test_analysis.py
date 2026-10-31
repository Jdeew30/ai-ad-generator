import unittest

from helpers import NOW, listing, make_watch

from vintedbot.analysis import (MarketStats, buy_cost, comparable_reason, decide, eligible_reason, evaluate,
                                find, market_stats)
from vintedbot.config import Costs
from vintedbot.models import condition_key, normalize

MARKET = MarketStats(count=40, median=300.0, p25=280.0, p75=320.0)


class TextTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("  Boîte VIDE !! PS5 "), "boite vide ps5")
        self.assertEqual(normalize("Réplique 1:1"), "replique 1 1")

    def test_find_whole_words_only(self):
        self.assertEqual(find("console hs", ["hs"]), ["hs"])
        self.assertEqual(find("hsbc ps5", ["hs"]), [])
        self.assertEqual(find("ne s allume pas", ["ne s'allume pas"]), ["ne s'allume pas"])

    def test_condition_labels(self):
        self.assertEqual(condition_key("Neuf avec étiquette"), "neuf_etiquette")
        self.assertEqual(condition_key("Neuf sans étiquette"), "neuf")
        self.assertEqual(condition_key("Très bon état"), "tres_bon")
        self.assertEqual(condition_key("Bon état"), "bon")
        self.assertEqual(condition_key("Satisfaisant"), "satisfaisant")
        self.assertEqual(condition_key("Very good"), "tres_bon")
        self.assertEqual(condition_key(None, 6), "neuf_etiquette")
        self.assertEqual(condition_key("???"), "inconnu")


class FilterTests(unittest.TestCase):
    def test_console_preset_keeps_bundles_and_drops_accessories(self):
        w = make_watch()
        self.assertIsNone(comparable_reason(listing(1, "PS5 + support vertical + 2 manettes", 350), w))
        self.assertIsNone(comparable_reason(listing(2, "PS5 slim avec coque et câble", 330), w))
        self.assertIsNotNone(comparable_reason(listing(3, "Boîte vide PS5", 120), w))
        self.assertIsNotNone(comparable_reason(listing(4, "Manette seule PS5 DualSense", 110), w))
        self.assertIsNotNone(comparable_reason(listing(5, "Coque pour PS5", 150), w))
        self.assertIsNotNone(comparable_reason(listing(6, "Je recherche une PS5", 300), w))
        self.assertIsNotNone(comparable_reason(listing(7, "PS5", 20), w))  # sous min_price

    def test_include_exclude_conditions_and_max_price(self):
        w = make_watch(include=["slim"], exclude=["digital"], conditions=["tres_bon"], max_price=250)
        self.assertIsNotNone(comparable_reason(listing(1, "PS5 standard", 300), w))
        self.assertIsNotNone(comparable_reason(listing(2, "PS5 slim digital", 300), w))
        self.assertIsNone(comparable_reason(listing(3, "PS5 Slim lecteur", 300), w))  # compte pour le marché...
        self.assertIn("au-dessus", eligible_reason(listing(3, "PS5 Slim lecteur", 300), w))  # ...pas d'alerte
        self.assertIn("état", eligible_reason(listing(4, "PS5 Slim", 200, status="Bon état"), w))
        self.assertIsNone(eligible_reason(listing(5, "PS5 Slim", 200), w))


class MarketTests(unittest.TestCase):
    def test_median_and_outliers(self):
        prices = [290, 300, 310, 295, 305, 300, 298, 302, 5, 2000]
        stats = market_stats(prices)
        self.assertEqual(stats.count, 8)
        self.assertAlmostEqual(stats.median, 300)
        self.assertIsNone(market_stats([]))

    def test_buy_cost_uses_vinted_total_when_available(self):
        costs = Costs()
        self.assertAlmostEqual(buy_cost(listing(1, "x", 100, total=106), costs, 4), 110)
        no_total = listing(2, "x", 100)
        no_total = no_total.__class__(**{**no_total.__dict__, "total_price": None})
        self.assertAlmostEqual(buy_cost(no_total, costs, 4), 100 * 1.05 + 0.7 + 4)


class EvaluationTests(unittest.TestCase):
    def test_good_deal_numbers(self):
        w = make_watch()
        ev = evaluate(listing(1, "PS5 slim très bon état", 180), w, MARKET, Costs(), 10, NOW)
        self.assertAlmostEqual(ev.reference, 300)
        self.assertAlmostEqual(ev.discount, 0.4)
        self.assertAlmostEqual(ev.cost, 180 * 1.05 + 0.7 + 8)  # port du profil console
        self.assertAlmostEqual(ev.profit, 270 - ev.cost)
        self.assertEqual(ev.score, 73)
        self.assertEqual(ev.label, "✅ BONNE AFFAIRE")
        self.assertIn("40 % sous le marché", decide(ev, w))

    def test_condition_adjusts_value(self):
        w = make_watch()
        new = evaluate(listing(1, "PS5", 250, status="Neuf avec étiquette"), w, MARKET, Costs(), 10, NOW)
        worn = evaluate(listing(2, "PS5", 250, status="Satisfaisant"), w, MARKET, Costs(), 10, NOW)
        self.assertGreater(new.reference, 300)
        self.assertLess(worn.reference, 300)
        self.assertGreater(new.score, worn.score)
        self.assertIn("état satisfaisant", " ".join(worn.flags))

    def test_too_good_to_be_true_and_counterfeit(self):
        w = make_watch()
        cheap = evaluate(listing(1, "PS5", 60), w, MARKET, Costs(), 10, NOW)
        self.assertTrue(cheap.suspicious)
        self.assertEqual(cheap.label, "⚠️ À VÉRIFIER")
        self.assertIsNotNone(decide(cheap, w, notify_suspicious=True))
        self.assertIsNone(decide(cheap, w, notify_suspicious=False))
        fake = evaluate(listing(2, "AirPods Pro 2 réplique 1:1", 200), w, MARKET, Costs(), 10, NOW)
        self.assertTrue(fake.suspicious)
        self.assertIn("contrefaçon", fake.flags[0])

    def test_red_flags_lower_the_score(self):
        w = make_watch()
        ok = evaluate(listing(1, "PS5 slim", 200), w, MARKET, Costs(), 10, NOW)
        worn = evaluate(listing(2, "PS5 slim rayures, léger drift", 200), w, MARKET, Costs(), 10, NOW)
        self.assertEqual(ok.score - worn.score, 15)
        self.assertIn("« drift »", worn.flags[0])
        self.assertFalse(worn.suspicious)

    def test_defective_items_are_excluded_unless_allowed(self):
        hs = listing(1, "PS5 slim HS ne s'allume pas", 120)
        w = make_watch()
        self.assertIn("défectueux", comparable_reason(hs, w))  # ne fausse pas le prix du marché
        self.assertIn("défectueux", eligible_reason(hs, w))
        repairer = make_watch(allow_defective=True)
        self.assertIsNotNone(comparable_reason(hs, repairer))
        self.assertIsNone(eligible_reason(hs, repairer))
        ev = evaluate(hs, repairer, MARKET, Costs(), 10, NOW)
        self.assertTrue(ev.suspicious)
        self.assertIn("réparation", " ".join(ev.flags))
        # le filtre HS ne s'applique pas aux vêtements (« casse », « bloque »... n'y ont pas ce sens)
        clothes = make_watch(preset="vetement", min_price=None)
        self.assertIsNone(comparable_reason(listing(2, "Veste HS collection", 50), clothes))

    def test_freshness_bonus(self):
        w = make_watch()
        fresh = evaluate(listing(1, "PS5", 200, age_minutes=3), w, MARKET, Costs(), 10, NOW)
        old = evaluate(listing(2, "PS5", 200, age_minutes=600), w, MARKET, Costs(), 10, NOW)
        self.assertEqual(fresh.score - old.score, 10)

    def test_learning_phase_and_target_price(self):
        w = make_watch(target_price=200)
        few = MarketStats(count=3, median=300, p25=290, p75=310)
        ev = evaluate(listing(1, "PS5", 190), w, few, Costs(), 10, NOW)
        self.assertIsNone(ev.reference)
        self.assertIn("apprentissage", ev.reference_source)
        self.assertIn("prix cible", decide(ev, w))
        self.assertIsNone(decide(evaluate(listing(2, "PS5", 250), w, few, Costs(), 10, NOW), w))

    def test_manual_market_price(self):
        w = make_watch(market_price=400)
        ev = evaluate(listing(1, "PS5", 250), w, None, Costs(), 10, NOW)
        self.assertEqual(ev.reference_source, "valeur fixée")
        self.assertAlmostEqual(ev.discount, 0.375)

    def test_thresholds(self):
        w = make_watch(min_discount=0.3, min_profit=60)
        ev = evaluate(listing(1, "PS5", 225), w, MARKET, Costs(), 10, NOW)  # -25 %
        self.assertIsNone(decide(ev, w))
        ev = evaluate(listing(2, "PS5", 205), w, MARKET, Costs(), 10, NOW)  # -32 % mais marge ≈ 46 €
        self.assertIsNone(decide(ev, w))
        ev = evaluate(listing(3, "PS5", 180), w, MARKET, Costs(), 10, NOW)
        self.assertIsNotNone(decide(ev, w))


if __name__ == "__main__":
    unittest.main()
