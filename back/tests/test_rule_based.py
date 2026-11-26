"""Tests for the deterministic rule-based masker.

Covers the cases the old GLiNER pipeline handled poorly: CAPSLOCK, inflected
names, several phone formats, email subdomains and organisation prefixes. The
pipeline is deterministic, so these are exact expectations rather than
tolerance-based assertions.
"""

from itertools import pairwise

import pytest

from src.masking.config import get_config
from src.masking.rule_based import DETECTOR_ORDER, Entity, RuleBasedMasker, _cheap_prefilter, _merge_spans


def _masker() -> RuleBasedMasker:
    return RuleBasedMasker()


def _labels(text: str) -> list[str]:
    return [entity.label for entity in _masker().detect(text)]


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


def _entity(label: str, start: int, end: int, score: float = 1.0) -> Entity:
    return Entity(label=label, text="x" * (end - start), start=start, end=end, score=score)


def test_merge_keeps_identical_spans_once() -> None:
    merged = _merge_spans([_entity("person", 0, 5), _entity("person", 0, 5)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 5)]


def test_merge_same_start_prefers_the_longest_span() -> None:
    merged = _merge_spans([_entity("phone", 0, 5), _entity("address", 0, 12)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 12)]


def test_merge_drops_spans_contained_in_a_kept_span() -> None:
    merged = _merge_spans([_entity("person", 0, 10), _entity("phone", 2, 5)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 10)]


def test_merge_partial_overlap_keeps_the_earliest_start() -> None:
    merged = _merge_spans([_entity("person", 0, 8), _entity("organization", 4, 12)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 8)]


def test_merge_touching_spans_are_both_kept() -> None:
    merged = _merge_spans([_entity("person", 0, 5), _entity("phone", 5, 10)])
    assert [(entity.start, entity.end) for entity in merged] == [(0, 5), (5, 10)]


def test_merge_full_overlap_tie_keeps_the_earliest_detector() -> None:
    # Equal (start, length, score): the stable sort keeps the first detector's span.
    merged = _merge_spans([_entity("person", 0, 5), _entity("organization", 0, 5)])
    assert [entity.label for entity in merged] == ["person"]


def test_merge_long_overlapping_span_loses_to_earlier_starts() -> None:
    # A long span starting later must not swallow earlier, smaller spans.
    entities = [_entity("person", index, index + 3) for index in range(0, 2000, 2)]
    entities.append(_entity("organization", 1, 1999))
    merged = _merge_spans(entities)
    assert [entity.start for entity in merged] == sorted(entity.start for entity in merged)
    assert merged[0].label == "person"
    assert all(entity.label == "person" for entity in merged[1:])


def test_empty_text_produces_no_entities() -> None:
    assert _masker().detect("") == []


# --- P1.4: address prefilter uses the validated lexicon -----------------------


@pytest.mark.parametrize(
    "text",
    ["г. Москва, ул. Ленина", "ул. Ленина", "пер. Смирнова", "наб. Реки", "Москва, 109012"],
)
def test_address_prefilter_admits_marker_fragments_without_digits(text: str) -> None:
    assert "address" in _cheap_prefilter(text)


@pytest.mark.parametrize(
    "text",
    ["Строймонтаж заключил договор", "Компания ш consulted", "Встреча состоялась в офисе."],
)
def test_address_prefilter_rejects_unrelated_prose(text: str) -> None:
    assert "address" not in _cheap_prefilter(text)


@pytest.mark.parametrize(
    "text",
    [
        "г. Москва, ул. Ленина",
        "ул. Ленина",
        "пер. Смирнова",
        "г. Москва, ул. Ленина, д. 12",
        "Встреча состоялась в офисе.",
        "Иван Петров, ivan@mail.ru",
    ],
)
def test_prefilter_and_full_detection_agree_on_supported_fixtures(text: str) -> None:
    masker = _masker()
    prefiltered = [(entity.label, entity.text) for entity in masker.detect(text, prefilter=True)]
    full = [(entity.label, entity.text) for entity in masker.detect(text, prefilter=False)]
    assert prefiltered == full
