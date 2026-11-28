"""Tests for the deterministic rule-based masker.

Covers the cases the old GLiNER pipeline handled poorly: CAPSLOCK, inflected
names, several phone formats, email subdomains and organisation prefixes. The
pipeline is deterministic, so these are exact expectations rather than
tolerance-based assertions.
"""

from itertools import pairwise

import pytest
from rule_based_fixtures import _masker

from src.masking.config import get_config
from src.masking.rule_based import DETECTOR_ORDER, RuleBasedMasker


def test_masks_full_name_in_caps() -> None:
    masked = _masker().mask("ПЕТР СИДОРОВ позвонил вчера.")
    assert masked == "[PERSON] позвонил вчера."


def test_masks_names_in_inflected_cases() -> None:
    masked = _masker().mask("Письмо от Ивана Петрова для Петра Сидоровича.")
    assert masked == "Письмо от [PERSON] для [PERSON]."


def test_does_not_mask_lone_words_or_prepositions() -> None:
    # `NamesExtractor` alone fires on bare surnames/prepositions; the masker
    # requires a given name plus a surname/patronymic.
    text = "Текст без персональных данных вообще."
    assert _masker().detect(text) == []


def test_masks_full_fio_with_out_of_vocabulary_surname() -> None:
    # natasha has no `Surn` grammeme for these surnames (they parse as ordinary
    # nouns / possessive adjectives), but a verified given name is enough
    # evidence alongside a surname-shaped word — with or without a patronymic.
    for text in (
        "Поповцев Алексей Владимирович",
        "Старухин Всеволод Анатольевич",
        "Алексей Владимирович Поповцев",
        "Старухин Александр",
        "Александр Старухин",
        "Поповцев Алексей",
    ):
        assert _masker().mask(text) == "[PERSON]", text


def test_masks_initials_with_surname_including_after_a_role() -> None:
    # natasha glues a role word onto the initials and drops the real surname,
    # so the FIO core is recovered from the original text.
    cases = {
        "Т.Ф. Гилязов": "[PERSON]",
        "Гилязов Т. Ф.": "[PERSON]",
        "Секретарь Т.Ф. Гилязов": "Секретарь [PERSON]",
        "Секретарь Т. Ф. Гилязов": "Секретарь [PERSON]",
        "Секретарь Иван Петров": "Секретарь [PERSON]",
    }
    for text, expected in cases.items():
        assert _masker().mask(text) == expected, text


def test_fio_recovery_does_not_create_false_positives() -> None:
    # The initials/pair fallback must not swallow role words, common nouns,
    # address abbreviations or a bare city name.
    for text in (
        "из Сторон",
        "Оператор ЭДО",
        "Г. Москва",
        "Директор ООО Ромашка",
        "Привет Иван",
        "Здравствуйте Иван",
        "Уважаемый Иван",
        "Дорогой Иван",
        "Новый Иван",
        "Письмо Ивану",
        "Отчёт Иванов",
        "Приказ Иван",
        "Договор Иван",
        "Работа Алексей",
        "Россия Иван",
        "Город Москва",
    ):
        assert all(entity.label != "person" for entity in _masker().detect(text)), text
    # A trailing role word must not be swallowed into the person span.
    assert _masker().mask("Секретарь Т.Ф. Гилязов: секретарь") == "Секретарь [PERSON]: секретарь"


def test_masks_phone_with_country_code() -> None:
    masked = _masker().mask("Тел +7 900 123-45-67, звоните.")
    assert masked == "Тел [PHONE], звоните."


def test_masks_phone_with_eight_and_parentheses() -> None:
    masked = _masker().mask("Тел 8 (495) 123-45-67, звоните.")
    assert masked == "Тел [PHONE], звоните."


def test_masks_email_including_subdomains() -> None:
    masked = _masker().mask("Пишите на a.b+c@sub.mail.corp.ru или test@example.org.")
    assert masked == "Пишите на [EMAIL] или [EMAIL]."


def test_masks_address() -> None:
    masked = _masker().mask("Адрес: г. Москва, ул. Ленина, д. 5, кв. 10.")
    assert "[ADDRESS]" in masked
    assert "Москва" not in masked


def test_masks_organisation_prefixes() -> None:
    masked = _masker().mask('ООО "Ромашка" и ПАО Сбербанк заключили договор.')
    assert masked.count("[ORGANIZATION]") == 2
    assert "Ромашка" not in masked
    assert "Сбербанк" not in masked


def test_organisation_full_names() -> None:
    # Spelled-out legal forms must be masked too, not only the abbreviations.
    single = [
        'Акционерное общество "Росхим"',
        "Акционерное общество «Росхим»",
        'Общество с ограниченной ответственностью "Ромашка"',
        "Публичное акционерное общество «Сбербанк»",
        "Индивидуальный предприниматель Иванов",
    ]
    for text in single:
        assert _masker().mask(text) == "[ORGANIZATION]", text


def test_organisation_full_names_in_any_case() -> None:
    # The form is declinable ("Акционерным обществом"), so matching is done on
    # lemmas rather than a hand-written case table. The brand must never leak.
    cases = [
        ('Я восхищаюсь акционерным обществом "Росхим"', "Росхим"),
        ('Письмо от акционерного общества "Росхим"', "Росхим"),
        ('Акционерному обществу "Росхим"', "Росхим"),
        ('Обществом с ограниченной ответственностью "Ромашка"', "Ромашка"),
        ('Общества с ограниченной ответственностью "Ромашка"', "Ромашка"),
        ('Публичным акционерным обществом "Сбербанк"', "Сбербанк"),
    ]
    for text, brand in cases:
        masked = _masker().mask(text)
        assert "[ORGANIZATION]" in masked, text
        assert brand not in masked, text


def test_organisation_full_names_with_typo() -> None:
    # A misspelt form still matches via lemma similarity, but an unrelated
    # adjective in the same position does not.
    assert _masker().mask('Я восхищаюсь акциноерным обществом "Росхим"') == "Я восхищаюсь [ORGANIZATION]"
    assert "Росхим" not in _masker().mask('Восхищаюсь красивым обществом "Росхим"')


def test_organisation_quoting_and_separator_variants() -> None:
    # Every common quote style plus punctuation between prefix and name must be
    # a single organisation span.
    single = [
        "АО «Росхим»",
        'АО "Росхим"',
        "АО “Росхим”",
        "АО „Росхим“",
        "АО 'Росхим'",
        "АО, «Росхим»",
        "АО - «Росхим»",
        "АО Росхим",
    ]
    for text in single:
        masked = _masker().mask(text)
        assert masked == "[ORGANIZATION]", text
    # Adjacent organisations stay distinct (no accidental gluing).
    assert _masker().mask("АО «Росхим» и ООО «Ромашка»") == "[ORGANIZATION] и [ORGANIZATION]"


def test_digit_only_sequences_are_not_addresses() -> None:
    # `natasha` emits digit-only AddrParts for passport/index-like numbers; a
    # bare number must not be masked (and passport numbers are out of scope).
    for text in ["Паспорт РФ №6402 270023", "Паспорт РФ № 6402 270023", "код 6402 270023"]:
        assert _masker().mask(text) == text


def test_address_still_masks_letters_with_digits() -> None:
    masked = _masker().mask("Адрес: г. Москва, ул. Ленина, д. 5, кв. 10.")
    assert "[ADDRESS]" in masked
    assert "Москва" not in masked


def test_full_address_line_is_masked_whole() -> None:
    # natasha drops parts of a long, comma-separated address; a line introduced
    # by "адрес:" must be masked in full.
    address = (
        "121151, г. Москва, вн. тер. г. муниципальный округ Дрогомилово, "
        "наб. Тараса Шевченко, д. 23А, этаж 12, помещ. I, ком. 77"
    )
    masked = _masker().mask(f"Юр. адрес: {address}.")
    assert masked == "Юр. адрес: [ADDRESS]."
    assert "Дрогомилово" not in masked
    assert "23А" not in masked


def test_address_line_stops_at_the_sentence_end() -> None:
    # Trailing prose after the address must not be swallowed by the mask.
    masked = _masker().mask("Адрес: г. Москва, ул. Ленина, д. 5. Пишите Ивану Петрову.")
    assert "[ADDRESS]" in masked
    assert "Пишите" in masked
    assert "Москва" not in masked


def test_address_line_keeps_the_whole_value_before_the_boundary() -> None:
    # A trailing room number must stay inside the mask (no stray digit leaks).
    masked = _masker().mask("Адрес: г. Москва, ул. Ленина, д. 5, ком. 77. Пишите нам.")
    assert masked == "Адрес: [ADDRESS]. Пишите нам."


def test_masks_links() -> None:
    masked = _masker().mask("Сайт: https://example.com/path?q=1 и http://test.ru.")
    assert masked == "Сайт: [LINK] и [LINK]."


def test_does_not_mask_common_phrases_as_persons() -> None:
    # `NamesExtractor` labels any two adjacent words; these are not names.
    for text in ["у каждой из Сторон", "Оператор ЭДО", "Сторон света"]:
        assert _masker().mask(text) == text


def test_masks_combined_pii_in_one_pass() -> None:
    text = "Иван Петров, тел +7 900 123-45-67, почта ivan.petrov@mail.ru"
    masked = _masker().mask(text)
    assert masked == "[PERSON], тел [PHONE], почта [EMAIL]"


def test_entities_are_non_overlapping_and_sorted() -> None:
    entities = _masker().detect("Иван Петров, ivan.petrov@mail.ru, +7 900 123-45-67")
    spans = sorted((entity.start, entity.end) for entity in entities)
    for (_, first_end), (second_start, _) in pairwise(spans):
        assert first_end <= second_start


def test_marker_is_upper_cased_label() -> None:
    entities = _masker().detect("Иван Петров")
    assert entities
    assert entities[0].label == "person"
    assert _masker().mask("Иван Петров") == "[PERSON]"


def test_labels_override_limits_detection() -> None:
    only_email = RuleBasedMasker.for_labels(["email"])
    text = "Иван Петров, ivan@mail.ru"
    labels = [entity.label for entity in only_email.detect(text)]
    assert labels == ["email"]


def test_unknown_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="banana"):
        RuleBasedMasker.for_labels(["person", "banana"])


def test_duplicate_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="больше одного раза"):
        RuleBasedMasker.for_labels(["person", "phone", "person"])


def test_default_labels_come_from_the_canonical_catalogue() -> None:
    assert RuleBasedMasker().labels == get_config().label_ids()
    assert DETECTOR_ORDER == ("email", "link", "phone", "person", "address", "organization")
