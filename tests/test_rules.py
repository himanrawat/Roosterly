"""Offline tests for the deterministic client safety rules."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.rules import (  # noqa: E402
    apply_rules, check_grounding, check_translated_reply, contains_payment_data,
    detect_mandatory_handoff, detect_reply_language, is_spam_or_empty,
    sanitize_for_log, scrub_payment_data,
)
from agent.rules import _version_problems  # noqa: E402
from agent.schema import Decision  # noqa: E402


def _decision(**changes) -> Decision:
    values = dict(language="nl", category="question", priority="P4", action="reply",
                  reason="Password help requested; FAQ F01 answers it.",
                  draft_reply='Ga naar app.roosterly.nl/login en klik op "Wachtwoord vergeten".',
                  faq_ids=["F01"])
    values.update(changes)
    return Decision(**values)


class MandatoryHandoffTests(unittest.TestCase):
    def test_all_categories_in_dutch(self):
        cases = {
            "refund_or_dispute": "Ik wil mijn geld terug; dit is onterecht afgeschreven.",
            "cancellation_or_contract_change": "Ik wil mijn abonnement stopzetten en het contract opzeggen.",
            "personal_data_request": "Volgens de AVG wil ik inzage en mijn gegevens verwijderen.",
            "security_incident": "Mijn account is gehackt en er is een onbekende login.",
            "legal_threat": "Mijn advocaat neemt contact op; ik neem juridische stappen.",
            "churn_threat": "Ik stap over naar een concurrent.",
        }
        for expected, body in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, detect_mandatory_handoff("Vraag", body))

    def test_english_and_german(self):
        cases = [
            ("refund", "refund_or_dispute"), ("I want a chargeback", "refund_or_dispute"),
            ("Please cancel my subscription", "cancellation_or_contract_change"),
            ("GDPR data deletion request", "personal_data_request"),
            ("My account was hacked", "security_incident"),
            ("My lawyer will sue", "legal_threat"),
            ("I will switch to a competitor", "churn_threat"),
            ("Rückerstattung bitte", "refund_or_dispute"),
            ("Ich möchte kündigen", "cancellation_or_contract_change"),
            ("DSGVO: Meine Daten löschen", "personal_data_request"),
            ("Mein Anwalt meldet sich", "legal_threat"),
            ("Unbekannte Anmeldung in meinem Konto", "security_incident"),
            ("Ich werde zu einem Konkurrenten wechseln", "churn_threat"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertIn(expected, detect_mandatory_handoff("", body))

    def test_faq_how_to_does_not_trigger(self):
        cases = [
            "Ik ben mijn wachtwoord vergeten, hoe herstel ik dit?",
            "Hoe kan ik upgraden naar Plus?",
            "Hoe download ik een factuur of wijzig ik mijn factuurgegevens?",
            "Waar worden mijn gegevens opgeslagen?",
            "How do I reset my forgotten password?",
            "How can I upgrade my plan?",
            "Where is my data stored?",
            "Wie lade ich eine Rechnung herunter?",
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(detect_mandatory_handoff("", body), [])


class SpamTests(unittest.TestCase):
    def test_empty_and_spam(self):
        for subject, body in [("", " \n!? "), ("test", ""), ("", "https://spam.example/path"),
                              ("SEO services", "Boost your rankings and traffic"),
                              ("Crypto promotion", "Invest in bitcoin for profits")]:
            with self.subTest(subject=subject):
                self.assertTrue(is_spam_or_empty(subject, body))

    def test_conservative(self):
        for body in ("Testing the app gives an error", "How do I add an employee?", "Is crypto accepted as payment?"):
            self.assertFalse(is_spam_or_empty("Question", body))


class PaymentTests(unittest.TestCase):
    def test_ibans_and_cards(self):
        examples = [
            ("NL91 ABNA 0417 1643 00", "[IBAN removed]"),
            ("NL91ABNA0417164300", "[IBAN removed]"),
            ("NL00 ABNA 0417 1643 00", "[IBAN removed]"),
            ("DE89 3704 0044 0532 0130 00", "[IBAN removed]"),
            ("BE00 6851 5399 5755", "[IBAN removed]"),
            ("4111 1111 1111 1111", "[card number removed]"),
            ("4111111111111111", "[card number removed]"),
            ("1234-5678-9012-3456", "[card number removed]"),
        ]
        for source, replacement in examples:
            with self.subTest(source=source):
                self.assertTrue(contains_payment_data(source))
                self.assertEqual(scrub_payment_data(source), replacement)
                self.assertEqual(scrub_payment_data(replacement), replacement)

    def test_non_payment_numbers(self):
        text = "Phone +31 6 1234 5678; phone 0031612345674; date 17-11-2026; invoice 123456789012; ticket T12345; €4."
        self.assertEqual(scrub_payment_data(text), text)
        self.assertFalse(contains_payment_data(text))

    def test_log_sanitization(self):
        self.assertEqual(sanitize_for_log("  IBAN NL91ABNA0417164300\n  thanks  "), "IBAN [IBAN removed] thanks")
        self.assertEqual(sanitize_for_log("one two three", 7), "one two")


class GroundingTests(unittest.TestCase):
    def test_faithful_faq_reply(self):
        reply = ('Go to app.roosterly.nl/login and click "Wachtwoord vergeten". '
                 'The reset link is valid for 60 minutes. '
                 'Managers go to Team → Medewerker toevoegen, enter name, email address and contract hours.')
        self.assertTrue(check_grounding(reply, ["F01", "F02"]).ok)
        self.assertTrue(check_translated_reply(reply, ["F01", "F02"]).ok)
        self.assertTrue(check_grounding("Go to Team → Medewerker toevoegen and click Uitnodigen.", ["F02"]).ok)
        self.assertTrue(check_grounding("Go to https://app.roosterly.nl/login.", ["F01"]).ok)

    def test_invented_price_url_menu_and_label(self):
        for reply in ("It costs €99 per month.", "Visit https://evil.example/help.",
                      "Go to Instellingen → Betalingen.",
                      "Go to Instellingen → Abonnement → Fraud.", 'Click "Geheim knopje".'):
            with self.subTest(reply=reply):
                self.assertFalse(check_grounding(reply, ["F01"]).ok)
        self.assertFalse(check_grounding("Use F01", ["F99"]).ok)
        self.assertFalse(check_grounding(" ").ok)

    def test_translation_checks_each_claim(self):
        result = check_translated_reply("Hello! The reset link is valid for 60 minutes. We guarantee a free laptop tomorrow.", ["F01"])
        self.assertFalse(result.ok)
        self.assertTrue(any("free laptop" in p for p in result.problems))
        self.assertFalse(check_translated_reply("Thanks! Kind regards.", ["F01"]).ok)

    def test_plan_price_is_tied_to_named_plan(self):
        for reply in ("Plus costs €4 per employee per month.",
                      "Basis costs €6 per employee per month.",
                      "€4 per employee per month for Plus.",
                      "Plus kost €4 per actieve medewerker per maand.",
                      "Basis is €6 and Plus is €4 per employee per month.",
                      "€6 for Basis and €4 for Plus per employee per month."):
            with self.subTest(reply=reply):
                result = check_grounding(reply, ["F05"])
                self.assertFalse(result.ok)
                self.assertTrue(any("wrong price for" in p for p in result.problems))
        for reply in ("Basis is €4 and Plus is €6 per employee per month.",
                      "Basis costs €4 and Plus costs €6 per active employee per month.",
                      "Basis kost €4 en Plus €6 per actieve medewerker per maand.",
                      "Basis kostet 4 € und Plus kostet 6 € pro Mitarbeiter.",
                      "Upgrading from Basis to Plus raises the price from €4 to €6.",
                      "Van Basis naar Plus gaat de prijs van €4 naar €6.",
                      "Both Basis and Plus are billed per active employee: €4 and €6 respectively.",
                      "Basis kost per medewerker per maand €4 plus btw.",
                      "Plus costs €6 per employee, so 25 employees cost €150 per month."):
            with self.subTest(reply=reply):
                self.assertTrue(check_grounding(reply, ["F05"], ticket_text="Price for 25 employees?").ok)

    def test_wrong_totals_compounds_and_abbreviated_prices(self):
        ticket = "Wat kost Plus voor 25 medewerkers?"
        for reply in ("Plus costs €6 per employee. Basis costs €4, so for 25 employees on Plus you pay €100.",
                      "Op basis van 25 medewerkers kost Plus €100 per maand.",
                      "Het Plusabonnement kost €4 per medewerker.",
                      "Het pluspakket kost €4.",
                      "Plus is the upgrade from Basis and costs €4 per employee.",
                      "Plus kost 4,- per medewerker.",
                      "Plus kost excl. btw €4 per medewerker."):
            with self.subTest(reply=reply):
                self.assertFalse(check_grounding(reply, ["F05"], ticket_text=ticket).ok)


class ReplyLanguageTests(unittest.TestCase):
    def test_quoted_dutch_ui_labels_do_not_change_detected_language(self):
        cases = [
            ("Go to app.roosterly.nl/login and click \"Wachtwoord vergeten\" to reset your password.", "en"),
            ("Gehen Sie zu Instellingen → Abonnement und klicken Sie auf \"Wachtwoord vergeten\".", "de"),
            ("Ga naar app.roosterly.nl/login en klik op \"Wachtwoord vergeten\" om je wachtwoord te herstellen.", "nl"),
        ]
        for reply, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(detect_reply_language(reply), expected)

    def test_short_ambiguous_reply_is_not_blocked(self):
        self.assertIsNone(detect_reply_language("Click the button."))
        self.assertIsNone(detect_reply_language('"Wachtwoord vergeten"'))

    def test_clear_wrong_language_forces_handoff(self):
        reply = 'Go to app.roosterly.nl/login and click "Wachtwoord vergeten" to reset your password.'
        decision = apply_rules({"subject": "Wachtwoord vergeten"}, _decision(draft_reply=reply),
                               translate=lambda text, language: text)
        self.assertEqual(decision.action, "handoff")
        self.assertIn("reply_language_mismatch", decision.overrides)
        self.assertEqual(decision.draft_reply, "")

    def test_wrong_plan_price_forces_handoff(self):
        decision = apply_rules({"subject": "Wat kost Plus?"}, _decision(
            category="billing", draft_reply="Plus kost €4 per actieve medewerker per maand.", faq_ids=["F05"]),
            translate=lambda text, language: "Plus costs €4 per active employee per month.")
        self.assertEqual(decision.action, "handoff")
        self.assertIn("ungrounded_reply", decision.overrides)

    def test_short_foreign_and_mixed_replies_are_withheld(self):
        cases = [
            ("See status.roosterly.nl for current outages and planned maintenance.", "F11"),
            ("Die App funktioniert ab iOS 16 und Android 10.", "F12"),
            ("Alle Daten werden in der EU (Frankfurt, Deutschland) gespeichert.", "F09"),
            ('Ga naar app.roosterly.nl/login en klik op "Wachtwoord vergeten". '
             'The reset link is valid for 60 minutes; no email within 10 minutes? Check your spam folder.', "F01"),
        ]
        for reply, faq_id in cases:
            with self.subTest(reply=reply):
                decision = apply_rules({}, _decision(draft_reply=reply, faq_ids=[faq_id]),
                                       translate=lambda text, language: text)
                self.assertEqual(decision.action, "handoff")
                self.assertIn("reply_language_mismatch", decision.overrides)

    def test_model_language_label_is_checked_against_ticket(self):
        ticket = {"subject": "Wachtwoord vergeten", "body": "Ik ben mijn wachtwoord vergeten, hoe herstel ik mijn wachtwoord?"}
        reply = 'Go to app.roosterly.nl/login and click "Wachtwoord vergeten" to reset your password.'
        decision = apply_rules(ticket, _decision(language="en", draft_reply=reply))
        self.assertEqual(decision.action, "handoff")
        self.assertIn("ticket_language_mismatch", decision.overrides)

    def test_faithful_replies_and_tickets_in_three_languages_stay_replies(self):
        cases = [
            ("nl", "Ik ben mijn wachtwoord vergeten, hoe herstel ik mijn wachtwoord?",
             'Ga naar app.roosterly.nl/login en klik op "Wachtwoord vergeten" om je wachtwoord te herstellen.'),
            ("en", "I need help with my password. How can I reset it?",
             'Go to app.roosterly.nl/login and click "Wachtwoord vergeten" to reset your password.'),
            ("de", "Wie kann ich mein Passwort zurücksetzen?",
             'Gehen Sie zu app.roosterly.nl/login und klicken Sie auf "Wachtwoord vergeten".'),
        ]
        for language, body, reply in cases:
            with self.subTest(language=language):
                decision = apply_rules({"body": body}, _decision(language=language, draft_reply=reply),
                                       translate=lambda text, source: "Go to app.roosterly.nl/login and click "
                                                                      "Wachtwoord vergeten to reset your password.")
                self.assertEqual(decision.action, "reply")
                self.assertEqual(decision.overrides, [])


class ApplyRulesTests(unittest.TestCase):
    def test_refund_overrides_reply(self):
        decision = apply_rules({"subject": "Terugbetaling", "body": "Ik wil mijn geld terug."}, _decision())
        self.assertEqual(decision.action, "handoff")
        self.assertEqual(decision.priority, "P2")
        self.assertIn("mandatory_handoff:refund_or_dispute", decision.overrides)
        self.assertIn("Original proposed action: reply", decision.handoff_note)
        self.assertEqual(decision.draft_reply, "")
        self.assertEqual(decision.problems(), [])

    def test_security_gets_p1_and_close_is_overridden(self):
        decision = apply_rules({"subject": "Account gehackt"}, _decision(action="close", draft_reply=""))
        self.assertEqual((decision.action, decision.priority), ("handoff", "P1"))

    def test_close_normal_ticket_becomes_handoff(self):
        decision = apply_rules({"body": "Hoe voeg ik een medewerker toe?"}, _decision(action="close", draft_reply=""))
        self.assertEqual(decision.action, "handoff")
        self.assertIn("close_not_allowed", decision.overrides)

    def test_close_spam_stays_close(self):
        decision = apply_rules({"body": "test"}, _decision(action="close", draft_reply=""))
        self.assertEqual(decision.action, "close")

    def test_unsubstantiated_reply_and_translation_error(self):
        invented = apply_rules({}, _decision(draft_reply="The price is €99."))
        self.assertIn("ungrounded_reply", invented.overrides)
        def fail_translate(reply, language):
            raise RuntimeError("model unavailable")
        failed = apply_rules({}, _decision(), translate=fail_translate)
        self.assertIn("translation_check_failed", failed.overrides)
        self.assertEqual(failed.action, "handoff")

    def test_reply_with_iban_is_scrubbed(self):
        decision = apply_rules({}, _decision(language="en", draft_reply='For NL91ABNA0417164300 go to app.roosterly.nl/login and click "Wachtwoord vergeten".'))
        self.assertEqual(decision.action, "reply")
        self.assertIn("payment_data_scrubbed", decision.overrides)
        self.assertNotIn("NL91", decision.draft_reply)

    def test_invalid_decision_repaired(self):
        decision = apply_rules({}, _decision(language="fr", category="nonsense", priority="P0", action="unknown", confidence=2))
        self.assertEqual((decision.language, decision.category, decision.priority, decision.action), ("nl", "other", "P3", "handoff"))
        self.assertIn("invalid_decision", decision.overrides)
        self.assertEqual(decision.problems(), [])


class CalibrationTests(unittest.TestCase):
    """Cases found when running the engine and the safety layer together."""

    def test_customer_numbers_and_computed_total_are_grounded(self):
        ticket = "Wat kost Plus voor 30 medewerkers?"
        reply = "Plus kost €6 per actieve medewerker per maand. Voor 30 medewerkers is dat €180 per maand."
        self.assertTrue(check_grounding(reply, ["F05"], ticket_text=ticket).ok)
        wrong = reply.replace("180", "170")
        self.assertIn("unsupported number: 170", check_grounding(wrong, ["F05"], ticket_text=ticket).problems)
        self.assertFalse(check_grounding(reply, ["F05"]).ok)  # without the ticket, 30 and 180 are unknown

    def test_menu_path_at_end_of_sentence(self):
        reply = "Je kunt dit uitzetten onder Instellingen → Rooster."
        self.assertTrue(check_grounding(reply, ["F03"]).ok)
        self.assertFalse(check_grounding("Ga naar Instellingen → Kortingen.", ["F03"]).ok)

    def test_apostrophe_is_not_a_quoted_label(self):
        reply = "Collega's met dezelfde functie kunnen de ruil accepteren, ook collega's van een andere dienst."
        self.assertTrue(check_grounding(reply, ["F03"]).ok)
        self.assertFalse(check_grounding("Tik op 'Dienst verkopen'.", ["F03"]).ok)

    def test_dutch_crypto_spam_can_be_closed(self):
        self.assertTrue(is_spam_or_empty("Investeringskans", "Verdubbel je geld in 7 dagen met ons cryptofonds."))
        self.assertFalse(is_spam_or_empty("Factuur", "Ik heb dubbel betaald, kan ik mijn geld terugkrijgen?"))

    def test_translated_check_tolerates_synonyms_but_not_promises(self):
        faithful = "See status.roosterly.nl for current outages and scheduled maintenance."
        self.assertTrue(check_translated_reply(faithful, ["F11"]).ok)
        price = "The Plus price is €6 per active employee per month (excl. VAT). For 25 employees that costs €150 per month."
        self.assertTrue(check_translated_reply(price, ["F05"]).ok)
        for invented in ("Our support team will call you tomorrow.", "We are sorry, a fix will be released next week.",
                         "You can cancel at any time without notice."):
            self.assertFalse(check_translated_reply(invented, ["F11"]).ok, invented)

    def test_forced_handoff_keeps_model_reason_and_records_priority(self):
        ticket = {"subject": "Terugbetaling", "body": "Kunnen we die maand terugkrijgen?"}
        final = apply_rules(ticket, _decision(reason="Billing question about one month. Low urgency."))
        self.assertEqual(final.action, "handoff")
        self.assertEqual(final.reason, "question ticket at P2: Billing question about one month. "
                                       "Safety rule mandatory_handoff:refund_or_dispute forced a handoff.")
        self.assertIn("priority_raised:P2", final.overrides)
        self.assertEqual(final.problems(), [])


class CrossReviewTests(unittest.TestCase):
    """Cases raised when Codex (author) and Claude (reviewer) compared notes on this file."""

    def test_topic_words_alone_do_not_force_handoff(self):
        harmless = [
            "Ik wil inzage in het rooster", "Ik heb bezwaar tegen de roosterwijziging",
            "De waterleiding heeft een lek", "Is deze dienst verdacht lang?", "I use the court roster template",
            "Hoe kan ik overstappen van Basis naar Plus?", "Wij willen overstappen naar het Plus abonnement",
            "Waar vind ik de verwerkersovereenkomst onder Instellingen → Juridisch?",
            "Waar worden onze persoonsgegevens opgeslagen?", "Are concurrent logins allowed?",
            "Does Roosterly work with another payroll tool?",
        ]
        for text in harmless:
            with self.subTest(text=text):
                self.assertEqual(detect_mandatory_handoff("", text), [])

    def test_same_words_in_a_request_or_threat_do(self):
        cases = {
            "Ik wil inzage in mijn gegevens": "personal_data_request",
            "Ik maak bezwaar tegen de factuur van oktober": "refund_or_dispute",
            "Volgens mij is er een datalek": "security_incident",
            "Ik zie een verdachte inlogpoging": "security_incident",
            "We will take you to court": "legal_threat",
            "Ik stel jullie aansprakelijk voor de schade": "legal_threat",
            "Anders stappen we over naar Shiftbase": "churn_threat",
            "Wij overwegen over te stappen": "churn_threat",
            "We kijken naar een andere aanbieder": "churn_threat",
            "We are moving to a competitor": "churn_threat",
            "Wij zeggen ons abonnement per direct op": "cancellation_or_contract_change",
        }
        for text, rule in cases.items():
            with self.subTest(text=text):
                self.assertIn(rule, detect_mandatory_handoff("", text))

    def test_price_total_needs_the_whole_sum_in_the_reply(self):
        ticket = "Plus for 25 employees?"
        self.assertTrue(check_grounding("Plus is €6 per employee, so 25 employees cost €150 per month.", ["F05"], ticket_text=ticket).ok)
        self.assertFalse(check_grounding("Your 25 employees get 150 free locations.", ["F05"], ticket_text=ticket).ok)
        self.assertFalse(check_grounding("The price is €24 per employee.", ["F05"], ticket_text="We have 4 employees").ok)

    def test_ticket_words_do_not_support_a_claim(self):
        self.assertFalse(check_translated_reply("Refunds are guaranteed.", ["F11"]).ok)
        self.assertFalse(check_translated_reply("No refunds.", ["F11"]).ok)
        self.assertFalse(check_translated_reply("Thanks, refunds are guaranteed.", ["F05"]).ok)
        self.assertFalse(check_translated_reply("The Plus plan includes unlimited locations and free refunds.", ["F05"]).ok)

    def test_courtesy_sentences_are_skipped_but_not_what_follows(self):
        reply = "Hello Anouk, the reset link is valid for 60 minutes. Let us know if you have any questions. Kind regards, Roosterly Support"
        self.assertTrue(check_translated_reply(reply, ["F01"]).ok)
        self.assertFalse(check_translated_reply("Hello Anouk, we will call you tomorrow.", ["F01"]).ok)

    def test_only_promotions_can_be_closed(self):
        self.assertFalse(is_spam_or_empty("", "Ik wil investeren in een crypto-casino locatie voor mijn medewerkers."))

    def test_card_after_phone_label_is_still_scrubbed(self):
        self.assertEqual(scrub_payment_data("phone: 4111 1111 1111 1111"), "phone: [card number removed]")
        self.assertEqual(scrub_payment_data("4111 1111-1111 1112"), "[card number removed]")

    def test_dutch_reply_without_translator_is_handed_off(self):
        final = apply_rules({}, _decision())
        self.assertEqual(final.action, "handoff")
        self.assertIn("translation_check_missing", final.overrides)
        checked = apply_rules({}, _decision(), translate=lambda text, lang: 'Go to app.roosterly.nl/login and click "Wachtwoord vergeten".')
        self.assertEqual(checked.action, "reply")

    def test_invalid_priority_is_recorded_even_when_a_rule_overwrites_it(self):
        final = apply_rules({"body": "Ik wil mijn geld terug"}, _decision(priority="P0"))
        self.assertIn("invalid_decision", final.overrides)
        self.assertEqual(final.problems(), [])

    def test_round_two_findings(self):
        d = detect_mandatory_handoff
        for harmless in ("How do I cancel a shift swap request?", "Hoe stap ik over naar een andere vestiging?",
                         "Werkt Roosterly ook met een andere leverancier voor salaris?"):
            with self.subTest(text=harmless):
                self.assertEqual(d("", harmless), [])
        self.assertIn("cancellation_or_contract_change", d("", "Please cancel our subscription"))
        self.assertIn("churn_threat", d("", "We gaan naar een andere aanbieder"))
        self.assertFalse(is_spam_or_empty("", "I want to invest in bitcoin for our company account."))

        ticket = "What does Plus cost for 25 employees?"
        self.assertFalse(check_grounding("Plus costs €4 per employee, so 25 employees cost €100 per month.", ["F05"], ticket_text=ticket).ok)
        self.assertFalse(check_translated_reply("Plus costs €6 per employee. For 25 employees you receive a €150 bonus.", ["F05"]).ok)

    def test_paraphrase_passes_but_promise_words_never_do(self):
        for faithful in ("You can view the scheduled maintenance moments on status.roosterly.nl.",
                         "There you can enter the new btw-nummer; the change will be applied to future invoices."):
            with self.subTest(text=faithful):
                self.assertTrue(check_translated_reply(faithful, ["F11", "F06"]).ok)
        result = check_translated_reply("You can upgrade at any time and the first invoice is free.", ["F05"])
        self.assertIn("promise or commitment not in FAQ (fre)", result.problems[0])
        # the same word is fine when the cited FAQ uses it: F01 says "within 10 minutes"
        self.assertTrue(check_translated_reply("No email within 10 minutes? Check your spam folder.", ["F01"]).ok)

    def test_minimum_version_polarity(self):
        self.assertFalse(check_translated_reply("The app works on iOS 15.", ["F12"]).ok)
        self.assertTrue(check_translated_reply("The app works on iOS 16 and later.", ["F12"]).ok)
        self.assertEqual(_version_problems("iOS 15 is not supported.", "The app works on iOS 16 and later"), [])

    def test_round_three_findings(self):
        d = detect_mandatory_handoff
        for harmless in ("We export approved hours to another provider.", "Hoe exporteer ik uren naar een andere aanbieder?"):
            with self.subTest(text=harmless):
                self.assertEqual(d("", harmless), [])
        self.assertIn("churn_threat", d("", "We are switching to another provider next month"))
        self.assertEqual(scrub_payment_data("0012345678901237"), "[card number removed]")

        f12 = "The app works on iOS 16 and later and Android 10 and later."
        wrong = ["The app works on iOS 15 if you upgrade.", "iOS 16 is not supported.", "It runs fine on Android 9."]
        right = ["iOS 15 is not supported, which can cause crashes.", "The app requires iOS 16 or later.",
                 "Please update your iPhone to iOS 16 or later.", "Versions older than iOS 16 are not supported.",
                 "iOS 15 is not supported, so you need iOS 16."]
        for sentence in wrong:
            with self.subTest(sentence=sentence):
                self.assertTrue(_version_problems(sentence, f12))
        for sentence in right:
            with self.subTest(sentence=sentence):
                self.assertEqual(_version_problems(sentence, f12), [])

        self.assertFalse(check_translated_reply("Basis supports unlimited locations without limits.", ["F05"]).ok)
        signed = "See status.roosterly.nl for current outages and planned maintenance. With kind regards, Roosterly Support"
        self.assertTrue(check_translated_reply(signed, ["F11"]).ok)
        self.assertTrue(check_translated_reply("See status.roosterly.nl for current outages.\nRoosterly Support", ["F11"]).ok)

    def test_sales_pitch_and_reply_prefix_only_tickets_are_closed(self):
        self.assertTrue(is_spam_or_empty("Hoger in Google met uw restaurant", "Beste eigenaar, wij zijn een SEO-bureau en brengen uw "
                                         "website binnen 30 dagen bovenaan in Google. Reageer voor een gratis analyse."))
        self.assertTrue(is_spam_or_empty("Re:", ""))
        self.assertTrue(is_spam_or_empty("RE: Fwd: ", "  "))
        for real in (("Re: Hoe voeg ik een medewerker toe?", ""), ("", "Kan ik een SEO-bureau als medewerker toevoegen?"),
                     ("", "Hoe kom ik hoger in Google met mijn website?"), ("Re:", "Nog steeds geen antwoord gehad.")):
            with self.subTest(ticket=real):
                self.assertFalse(is_spam_or_empty(*real))

    def test_faithful_replies_with_joined_words_and_courtesy_pass(self):
        ok = [
            ("If you within 10 minutes no e\u2011mail see, then check your spamfolder.", ["F01"]),
            ("Fill in the e\u2011mail address of the manager account.", ["F01"]),
            ("Thank you for your suggestion! We read every idea.", ["F10"]),
        ]
        for sentence, ids in ok:
            with self.subTest(sentence=sentence):
                self.assertTrue(check_translated_reply(sentence, ids).ok)
        # joined words only count when both halves are FAQ words
        self.assertFalse(check_translated_reply("You receive a refundlink and a cashbonus.", ["F01"]).ok)
        self.assertFalse(check_translated_reply("Thank you for your suggestion, we will build it next week.", ["F10"]).ok)

    def test_normalisation_does_not_carry_new_claims(self):
        for wrong in ("Your e\u2011mail address is public.", "The spamfolder is public."):
            with self.subTest(sentence=wrong):
                self.assertFalse(check_translated_reply(wrong, ["F01"]).ok)
        self.assertTrue(check_translated_reply("A worker can be linked to more than one location.", ["F07"]).ok)
        self.assertFalse(check_translated_reply("Basis includes unlimited locations for every worker.", ["F07", "F05"]).ok)
        self.assertFalse(check_translated_reply("With Basis you can use the Nmbrs integration.", ["F04", "F05"]).ok)
        for right in ("Basis is €4 per active employee per month, one location.",
                      "The Nmbrs, Loket.nl and AFAS integrations are part of the Plus plan.",
                      "Basis does not include unlimited locations.",
                      "Plus is €6 per active employee per month, unlimited locations, API access and the payroll integrations."):
            with self.subTest(sentence=right):
                self.assertTrue(check_translated_reply(right, ["F04", "F05"]).ok)


if __name__ == "__main__":
    unittest.main()
